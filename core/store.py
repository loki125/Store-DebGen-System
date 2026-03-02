from contextlib import contextmanager
import os
import json
import shutil
import subprocess
import zipfile
from dataclasses import dataclass

from config import *

"""
explanation what is happening in this file:
update func is the main workflow 

we preaper the "ingredients", downloading the file and following the recipe instructions
the recipe instruction have requiered mount in a topo sort order so we recursivly install each one 
after that we create the base forest using the symlink_forest instructions 


then we create the sandbox enveiorment after all the setup has completed meaning the package has access to her dependencies and has the root FHS 
we create the overlayfs mounting env by the command:

    lower = f"{paths.forest}:{self.base_rootfs}"
    opts = f"lowerdir={lower},upperdir={paths.upper},workdir={paths.work}"
    subprocess.run(["mount", "-t", "overlay", "overlay", "-o", opts, str(paths.merged)], check=True)

after that we bind the system APIs and the store into the sandbox so the package can find its dependencies and the symlinks work correctly
after we mount aka "glue" the ingridiance we run the installation script previded by the package using chroot to keep the changes isolated from the host system
after the installation we check if the package has access to all her dependencies by running ldd on the binaries and checking if any dependencies are missing

next stage is commiting the changes to the actual store by moving the upper layer to the store path, this is the "freeze" stage 
where we make the changes permanent and visible to other packages
"""

logger = logging.getLogger("Store")

@dataclass
class TransactionPaths:
    """Holds the specific paths for a single installation transaction."""
    stage: Path
    forest: Path
    upper: Path
    work: Path
    merged: Path
    download: Path

class Store:
    def __init__(self, fetcher, root=STORE_ROOT, transient=TRANS_ROOT, base_rootfs=BASE_ROOTFS):
        self.root = Path(root)
        self.transient = Path(transient)
        self.base_rootfs = Path(base_rootfs)
        self.fetcher = fetcher

        self.root.mkdir(parents=True, exist_ok=True)
        self.transient.mkdir(parents=True, exist_ok=True)

    def update(self, pkg: Dict) -> bool:
        pkg_hash = pkg["SHA256"]
        pkg_name = pkg["Package"]
        store_path = Path(pkg["Store_Path"])

        if store_path.exists():
            return True

        # Generate unique paths for this installation transaction
        tx_id = pkg_hash[:12]
        paths = self._get_transaction_paths(tx_id)

        try:
            # Phase 1: Preparation (The Ingredients)
            self._prepare_ingredients(pkg, paths)

            # Phase 2 & 3: Building the Site and Live Installation
            self._run_sandbox_install(pkg_name, paths)

            # Phase 4: The Freeze (Commitment)
            self._commit_package(paths.upper, store_path)
            return True

        except Exception as e:
            logger.error(f"Failed to install {pkg_name}: {e}")
            return False
        finally:
            self._cleanup_transaction(paths)

    def _prepare_ingredients(self, pkg: Dict, paths: TransactionPaths):
        # Download the main zip containing the .deb
        paths.download.mkdir(parents=True, exist_ok=True)
        zip_path = self.fetcher.download_file(
            save_path=paths.download, 
            store_path=pkg["Store_Path"]
        )

        # Extraction: Pull the DNA (deb contents) into the stage folder
        paths.stage.mkdir(parents=True, exist_ok=True)
        self._extract_deb_to_stage(zip_path, paths.stage)

        # Forestry: Read the recipe and integrate dependencies into the forest
        recipe = self._get_recipe(paths.stage)
        mount_instructions = recipe.get("mount_instructions", {})
        required_mounts = mount_instructions.get("required_mounts", [])

        paths.forest.mkdir(parents=True, exist_ok=True)
        
        # Integrate required mounts one by one from top to bottom
        for dep_pkg_data in required_mounts:
            dep_store_name = dep_pkg_data['Package']
            dep_store_path = self.root / dep_pkg_data["Store_Path"]
            
            # If a dependency is missing from the store, install it recursively
            if not dep_store_path.exists():
                logger.info(f"Missing dependency: {dep_store_name}. Starting sub-installation...")
                self.update(dep_pkg_data)

        # Plant the Symlink Forest
        paths.forest.mkdir(parents=True, exist_ok=True)
        for link_entry in recipe.get("symlinks_forest", []):
            # link_entry structure: {"src": "jail/path/lib.so", "dst": "/host/store/path/lib.so"}
            link_path = self._src_root_adapter(paths.forest / link_entry["src"].lstrip("/"))
            target_path = Path(link_entry["dst"])

            link_path.parent.mkdir(parents=True, exist_ok=True)
            if link_path.is_symlink() or link_path.exists():
                link_path.unlink()
            
            os.symlink(target_path, link_path)

    def _src_root_adapter(self, link_path: Path) -> Path:
        if link_path.startswith("lib/"):
            # Check our Base RootFS to see what exists
            if (self.base_rootfs / "lib64").exists():
                return "lib64" / Path(link_path).relative_to("lib")
        
        # If it's already usr/bin or similar, keep it as is
        return Path(link_path)
        
    @contextmanager
    def _mount_stack(self, paths: TransactionPaths):
        """Context Manager: Handles all mounts and guarantees cleanup (unmounts)."""
        mounts = []
        try:
            # A. OverlayFS (Base + Forest + Upper)
            lower = f"{paths.forest}:{self.base_rootfs}"
            opts = f"lowerdir={lower},upperdir={paths.upper},workdir={paths.work}"
            subprocess.run(["mount", "-t", "overlay", "overlay", "-o", opts, str(paths.merged)], check=True)
            mounts.append(paths.merged)

            # B. System API Bind Mounts (The 'Pulse')
            for api in ["proc", "sys", "dev"]:
                target = paths.merged / api
                subprocess.run(["mount", "--bind", f"/{api}", str(target)], check=True)
                mounts.append(target)

            # C. THE STORE BRIDGE: Mount host store into the jail so symlinks work
            store_in_jail = paths.merged / str(self.root).lstrip("/")
            store_in_jail.mkdir(parents=True, exist_ok=True)
            subprocess.run(["mount", "--bind", "-o", "ro", str(self.root), str(store_in_jail)], check=True)
            mounts.append(store_in_jail)

            yield # Control returns to _run_sandbox_install
            
        finally:
            # Unmount in REVERSE order (Last In, First Out)
            for target in reversed(mounts):
                subprocess.run(["umount", "-l", str(target)], check=False)
                
    def _run_healthcheck(self, merged_path: Path):
        """Simple check: Can the binaries find their libraries?"""
        # We look for any ELF binaries in usr/bin
        bin_dir = merged_path / "usr/bin"
        if not bin_dir.exists(): return

        for b in bin_dir.iterdir():
            if b.is_file() and not b.is_symlink():
                # Run ldd inside the sandbox
                res = subprocess.run(["chroot", str(merged_path), "ldd", str(b.relative_to(merged_path))], 
                                     capture_output=True, text=True)
                if "not found" in res.stdout:
                    raise RuntimeError(f"Healthcheck failed: {b.name} is missing dependencies!")

    def _run_sandbox_install(self, pkg_name: str, paths: TransactionPaths):
        # Phase 2: Building the Construction Site (The Mount)
        paths.upper.mkdir()
        paths.work.mkdir()
        paths.merged.mkdir()

        with self._mount_stack(paths):
            # 1. Project the .deb files into the Upper layer via the Merged view
            subprocess.run(["cp", "-a", f"{paths.stage}/.", str(paths.merged)], check=True)

            subprocess.run(["chroot", str(paths.merged), "/sbin/ldconfig", "-X"], check=True)

            # 2. Execute post-install
            postinst_rel = "var/lib/dpkg/info/postinst"
            if (paths.merged / postinst_rel).exists():
                logger.info(f"Running postinst for {pkg_name}...")
                subprocess.run(["chroot", str(paths.merged), f"/{postinst_rel}", "configure"], check=True)

            # 3. Verify the result before committing
            self._run_healthcheck(paths.merged)

    def _commit_package(self, upper_path: Path, store_path: Path):
        # Phase 4: The Freeze
        if not any(os.scandir(upper_path)):
            raise RuntimeError("Installation failed: Upper directory is empty")
        store_path.parent.mkdir(parents=True, exist_ok=True)
        
        # Atomic Move
        shutil.move(str(upper_path), str(store_path))
        logger.info(f"Package committed to store at {store_path}")

    def _extract_deb_to_stage(self, zip_path: Path, stage_path: Path):
        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
            zip_ref.extractall(stage_path)

        deb_files = list(stage_path.glob("*.deb"))
        if not deb_files:
            raise FileNotFoundError(f"No .deb found in {zip_path}")
        
        deb_file = deb_files[0]

        # Extract data
        subprocess.run(["dpkg-deb", "-x", str(deb_file), str(stage_path)], check=True)

        # Extract control scripts
        control_dir = stage_path / "var/lib/dpkg/info"
        control_dir.mkdir(parents=True, exist_ok=True)
        subprocess.run(["dpkg-deb", "-e", str(deb_file), str(control_dir)], check=True)

        deb_file.unlink()
        if zip_path.exists():
            zip_path.unlink()

    def _get_transaction_paths(self, tx_id: str) -> TransactionPaths:
        return TransactionPaths(
            stage=self.transient / f"stage_{tx_id}",
            forest=self.transient / f"forest_{tx_id}",
            upper=self.transient / f"upper_{tx_id}",
            work=self.transient / f"work_{tx_id}",
            merged=self.transient / f"merged_{tx_id}",
            download=self.transient / "downloads"
        )

    def _get_recipe(self, stage_path: Path) -> Dict:
        recipe_path = stage_path / "recipe.json"
        if not recipe_path.exists():
            return {}
        with open(recipe_path, "r") as f:
            return json.load(f)

    def _cleanup_transaction(self, paths: TransactionPaths):
        # Delete the temporary construction folders
        cleanup_targets = [paths.stage, paths.forest, paths.work, paths.merged]
        
        for path in cleanup_targets:
            if path.exists():
                shutil.rmtree(path)