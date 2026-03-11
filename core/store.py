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

        self.logger = logging.getLogger(self.__class__.__name__)

    def update(self, pkg: Dict) -> bool:
        pkg_name = pkg["Package"]
        store_path = Path(pkg["Store_Path"])

        if store_path.exists():
            return True

        # Generate unique paths for this installation transaction
        main_paths = self._get_transaction_paths(store_path)
        current_store_path = ""
        try:
            # Phase 1: Preparation (The Ingredients)
            mounting_dict = self._prepare_ingredients(pkg, main_paths)

            # Phase 2 & 3: Building the Site and Live Installation
            for current_store_path, paths in mounting_dict.items():
                self._run_sandbox_install(current_store_path, paths)

                # Phase 4: The Freeze (Commitment)
                if not self._commit_package(paths.upper, current_store_path):
                    self.reset_target(Path(current_store_path))
                    raise Exception(f"commit failed for {current_store_path}, cleaned failed atomic logic")

            return True

        except Exception as e:
            self.logger.error(f"Failed to install {pkg_name}, cleaning broken package {current_store_path}")
            self.logger.debug(e)
            return False
        finally:
            self._cleanup_transaction()

    def _prepare_ingredients(self, pkg: Dict, paths: TransactionPaths):
        # Download the main zip containing the .deb and recipe
        relative_store_path = Path(pkg["Store_Path"])
        main_store_path= self.root / relative_store_path

        paths.download.mkdir(parents=True, exist_ok=True)
        zip_path = self.fetcher.download_file(
            save_path=paths.download, 
            relative_store_path=relative_store_path
        )

        if zip_path is None:
            raise Exception(f"package: {pkg['Package']} failed to download")

        # Extraction: Pull the DNA (deb contents) into the stage folder
        paths.stage.mkdir(parents=True, exist_ok=True)
        self._extract_deb_to_stage(zip_path, paths.stage)

        main_recipe: Dict = self._get_recipe(paths.stage)
        mount_instructions: Dict = main_recipe.get("mount_instructions", {})
        required_mounts = mount_instructions.get("required_mounts", [])

        paths.forest.mkdir(parents=True, exist_ok=True)
        
        # We will collect recipes in order: Dependencies first, Main Package last
        recipes_to_process: Dict[Path, Dict] = {}
        mounting_dict: Dict[str, TransactionPaths] = {}

        # Iterate flatend & recursive mounts (Integrate dependencies one by one)
        for relative_dep_path in required_mounts:
            dep_paths = self._get_transaction_paths(relative_dep_path)
            dep_store_path = self.root / relative_dep_path
            
            # If dependency is missing, download and extract it iteratively
            if dep_store_path.exists():
                self.logger.debug(f"dependencie {relative_dep_path} exists, continuing")
                continue

            self.logger.info(f"Missing dependency: {relative_dep_path}. Integrating iteratively...")
            
            dep_zip = self.fetcher.download_file(
                save_path=dep_paths.download, 
                relative_store_path=relative_dep_path
            )
            
            if dep_zip is None:
                raise Exception(f"Dependency {relative_dep_path} failed to download")
            
            dep_paths.stage.mkdir(parents=True, exist_ok=True)
            self._extract_deb_to_stage(dep_zip, dep_paths.stage)

            # Get the dependency's recipe so we can process its symlinks
            dep_recipe = self._get_recipe(dep_paths.stage)

            recipes_to_process[dep_paths.forest] = dep_recipe
            mounting_dict[dep_store_path] = dep_paths

        self.logger.info(f"Starting symlink forest planting for {pkg['Package']} with {len(recipes_to_process)} dependencies...")

        # Append the main package recipe last and process all symlink instructions
        recipes_to_process[paths.forest] = main_recipe
        mounting_dict[main_store_path] = paths
        self._plant_symlink_forest(recipes_to_process)

        return mounting_dict

    def _plant_symlink_forest(self, recipes_to_process: Dict[Path, Dict]):
        for forest_root, recipe in recipes_to_process.items():
            for jail_path, store_path in recipe.get("symlink_forest", {}).items():
                # link_name: The "shortcut" we create in the temporary forest like /transient/forest_tree/lib/x86_64-linux-gnu/libc.so.6
                link_name = self._src_root_adapter(forest_root / jail_path.lstrip("/"))
                
                # target_data: The real file sitting in our immutable store like /var/lib/manager/store/hash-libc/lib/x86_64-linux-gnu/libc.so.6
                target_data = Path(store_path)

                link_name.parent.mkdir(parents=True, exist_ok=True)
                if link_name.is_symlink() or link_name.exists():
                    link_name.unlink()
                
                # Standard Log format: SHORTCUT -> REAL_FILE
                self.logger.debug(f"Forest Link: {link_name} -> {target_data}")
                os.symlink(target_data, link_name)

    def reset_target(self, target_path: Path):
        """
        Safely unmounts all nested mounts inside target_path (deepest first) 
        and deletes the directory tree, handling weird filenames gracefully.
        """
        if not os.path.exists(target_path):
            self.logger.warning(f"Path not found: {target_path}")
            return

        # Identify all mounts underneath the target directory
        mounts = []
        with open('/proc/mounts', 'r') as f:
            for line in f:
                path = line.split()[1]
                # Match exact path or sub-paths
                if path == target_path or path.startswith(str(target_path) + os.sep):
                    mounts.append(path)

        # Sort by length descending to unmount deep children before parents
        mounts.sort(key=len, reverse=True)

        # 3erform Unmounts
        for mount in mounts:
            self.logger.debug(f"Unmounting: {mount}")
            # Try normal unmount; if it fails, try lazy unmount (-l)
            if subprocess.run(["umount", mount], stderr=subprocess.DEVNULL).returncode != 0:
                self.logger.warning(f"  -> Busy, forcing lazy unmount on {mount}")
                subprocess.run(["umount", "-l", mount])

        # Delete the directory tree
        try:
            shutil.rmtree(target_path)
            self.logger.info("Cleanup successful.")
        except OSError as e:
            self.logger.critical(f"cleanup of {target_path} failed, pkg manager is corrupt, please run \"ddls reset\"")

    def _src_root_adapter(self, link_path: Path) -> Path:
        return Path(*["lib64" if part == "lib" else part for part in link_path.parts])
        
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

        with self._mount_stack(paths):
            # 1. Project the .deb files into the Upper layer via the Merged view
            subprocess.run(["cp", "-a", f"{paths.stage}/.", str(paths.merged)], check=True)

            subprocess.run(["chroot", str(paths.merged), "/sbin/ldconfig", "-X"], check=True)

            # 2. Execute post-install
            postinst_rel = "var/lib/dpkg/info/postinst"
            if (paths.merged / postinst_rel).exists():
                self.logger.info(f"Running postinst for {pkg_name}...")
                subprocess.run(["chroot", str(paths.merged), f"/{postinst_rel}", "configure"], check=True)

            # 3. Verify the result before committing
            self._run_healthcheck(paths.merged)

    def _commit_package(self, upper_path: Path, store_path: Path) -> bool:
        # Phase 4: The Freeze
        if not any(os.scandir(upper_path)):
            raise RuntimeError("Installation failed: Upper directory is empty")
        store_path.parent.mkdir(parents=True, exist_ok=True)
        
        # Atomic Move
        try:
            shutil.move(str(upper_path), str(store_path))
            self.logger.info(f"Package committed to store at {store_path}")
        except Exception as e:
            self.logger.debug(e)
            return False
        
        return True

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
        tx = TransactionPaths(
            stage=self.transient / f"stage_{tx_id}",
            forest=self.transient / f"forest_{tx_id}",
            upper=self.transient / f"upper_{tx_id}",
            work=self.transient / f"work_{tx_id}",
            merged=self.transient / f"merged_{tx_id}",
            download=self.transient / "downloads"
        )

        for path in [tx.stage, tx.forest, tx.upper, tx.work, tx.merged, tx.download]:
            path.mkdir(parents=True, exist_ok=True)

        return tx

    def _get_recipe(self, stage_path: Path) -> Dict:
        recipe_path = stage_path / "recipe.json"
        if not recipe_path.exists():
            return {}
        with open(recipe_path, "r") as f:
            return json.load(f)

    def _cleanup_transaction(self):
        shutil.rmtree(TRANS_ROOT, ignore_errors=True)
        try:
            TRANS_ROOT.mkdir(exist_ok=False)
        except FileExistsError as e:
            self.logger.critical(f"cleanup of {TRANS_ROOT} failed, pkg manager is corrupt, please run \"ddls reset\"")
            raise e