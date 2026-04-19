import os
import json
import pprint
import shutil
import subprocess
import zipfile
import struct
import fcntl
from contextlib import contextmanager
from typing import List, Dict, Any, Optional, Tuple
from pathlib import Path
import logging

from .utils import TransactionPaths, WrapperConfig
from config import *

class Store:
    def __init__(self, fetcher, root=STORE_ROOT, transient=TRANS_ROOT, base_rootfs=BASE_ROOTFS, pkg_map=PKG_MAP_PATH):
        self.root = Path(root).resolve()
        self.transient = Path(transient).resolve()
        self.base_rootfs = Path(base_rootfs).resolve()
        self.pkg_map = Path(pkg_map)
        self.fetcher = fetcher

        self.logger = logging.getLogger(self.__class__.__name__)

        with open(PACKAGE_WRAPPER_PATH, "r") as f:
            self.wrapper_template = f.read()

        self.root.mkdir(parents=True, exist_ok=True)
        self.transient.mkdir(parents=True, exist_ok=True)
        STORE_TMP_ROOT.mkdir(parents=True, exist_ok=True)

        self._init_map()

        self._active_tx_paths: List[TransactionPaths] = []
        self._created_wrappers: set = set()

    def _init_map(self):
        if not self.pkg_map.exists():
            with open(self.pkg_map, "wb") as f:
                f.write(b'\x00' * (SLOT_COUNT * SLOT_SIZE))
            self.logger.info(f"Package map created at: {self.pkg_map}")

    @staticmethod
    def _hash_djb2(s: str) -> int:
        h = 5381
        for char in s:
            h = ((h << 5) + h) + ord(char)
        return h % SLOT_COUNT



    def _save_package_to_map(self, hash_path: Path, pkg_ver: str) -> bool:
        if not self.pkg_map.exists(): return False
        index = self._hash_djb2(pkg_ver)
        key_bytes = pkg_ver.encode('utf-8')[:KEY_SIZE].ljust(KEY_SIZE, b'\x00')
        val_bytes = str(hash_path).encode('utf-8')[:VALUE_SIZE]

        with open(self.pkg_map, "rb+") as f:
            attempt = 0
            first_tombstone_offset = None
            while attempt < SLOT_COUNT:
                offset = index * SLOT_SIZE
                f.seek(offset)
                data = f.read(SLOT_SIZE)
                status = data[0]
                existing_key = data[1:1+KEY_SIZE]
                
                if status == STATUS_OCCUPIED and existing_key == key_bytes:
                    f.seek(offset)
                    f.write(struct.pack(f"B{KEY_SIZE}s{VALUE_SIZE}s", STATUS_OCCUPIED, key_bytes, val_bytes))
                    return True
                if status == STATUS_DELETED and first_tombstone_offset is None:
                    first_tombstone_offset = offset
                if status == STATUS_EMPTY:
                    target_offset = first_tombstone_offset if first_tombstone_offset is not None else offset
                    f.seek(target_offset)
                    f.write(struct.pack(f"B{KEY_SIZE}s{VALUE_SIZE}s", STATUS_OCCUPIED, key_bytes, val_bytes))
                    return True
                index = (index + 1) % SLOT_COUNT
                attempt += 1
        return False

    def get_package(self, pkg_ver: str) -> Optional[str]:
        if not self.pkg_map.exists(): return None
        index = self._hash_djb2(pkg_ver)
        key_bytes = pkg_ver.encode('utf-8')[:KEY_SIZE].ljust(KEY_SIZE, b'\x00')
        with open(self.pkg_map, "rb") as f:
            attempt = 0
            while attempt < SLOT_COUNT:
                f.seek(index * SLOT_SIZE)
                data = f.read(SLOT_SIZE)
                status = data[0]
                if status == STATUS_EMPTY: return None
                if status == STATUS_OCCUPIED:
                    existing_key = data[1:1+KEY_SIZE]
                    if existing_key == key_bytes:
                        return data[1+KEY_SIZE:].rstrip(b'\x00').decode('utf-8')
                index = (index + 1) % SLOT_COUNT
                attempt += 1
        return None
    
    def _erase_package(self, pkg_path: Path, pkg_ver: str) -> bool:
        if pkg_path.exists(): shutil.rmtree(pkg_path)
        if not self.pkg_map.exists(): return False
        index = self._hash_djb2(pkg_ver)
        key_bytes = pkg_ver.encode('utf-8')[:KEY_SIZE].ljust(KEY_SIZE, b'\x00')
        with open(self.pkg_map, "rb+") as f:
            attempt = 0
            while attempt < SLOT_COUNT:
                offset = index * SLOT_SIZE
                f.seek(offset)
                data = f.read(SLOT_SIZE)
                status = data[0]
                if status == STATUS_EMPTY: return False
                if status == STATUS_OCCUPIED and data[1:1+KEY_SIZE] == key_bytes:
                    f.seek(offset)
                    f.write(struct.pack("B", STATUS_DELETED)) 
                    return True
                index = (index + 1) % SLOT_COUNT
                attempt += 1
        return False

    def _umount_tree(self, path: Path):
        """Force unmount all mounts under a given path."""
        try:
            subprocess.run(
                ["umount", "-R", str(path)],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except subprocess.CalledProcessError:
            # fallback to lazy unmount if something is still busy
            subprocess.run(
                ["umount", "-l", "-R", str(path)],
                check=False,
            )

    def update_sys(self, sys_pkg_path: Path) -> bool:
        """Dedicated flow to install System Bundles (Base Layers) into the store."""
        store_path = self.root / sys_pkg_path

        if store_path.exists():
            self.logger.info(f"System package {sys_pkg_path} already exists.")
            return True

        with self._transaction_lock():
            paths = self._get_transaction_paths(sys_pkg_path)
            try:
                # Step 1: Download and Extract
                zip_path = self.fetcher.download_file(save_path=paths.download, relative_store_path=sys_pkg_path)
                if not zip_path: raise Exception(f"Download failed for {sys_pkg_path}")
                
                deb_paths = self._extract_zip_to_stage(zip_path, paths.stage)
                recipe = self.get_recipe(paths.stage)
                map_key = KEY_STR.format(name=recipe["package_name"], version=recipe["version"])

                # Step 2: Sandbox Installation (Native dpkg)
                with self._mount_stack(paths, sys_pkg_lowers=[]):
                    self.logger.info(f"Installing system bundle {sys_pkg_path} via dpkg...")
                    self._upgrade_system_libs(paths.merged, sys_pkg_path, deb_paths)

                # Step 3: lazy unmount the system package to be used as lower 
                self._umount_tree(paths.merged)

                # Step 4: Atomic Commit to Store
                if not self._commit_package(paths.upper, store_path, map_key):
                    raise Exception(f"Commit failed for {sys_pkg_path}")

                return True
            except Exception as e:
                self.logger.error(f"System update failed: {e}")
                self.reset_target(store_path)
                return False
            finally:
                self._cleanup_transaction()

    def update(self, pkg: Dict[str, Any]) -> bool:
        """Regular package update. Expects system dependencies to already exist in store."""
        pkg_name = pkg["Package"]
        store_path = self.root / Path(pkg["Store_Path"])

        if store_path.exists():
            return True

        with self._transaction_lock():
            self._active_tx_paths = []
            self._created_wrappers.clear()
            main_paths = self._get_transaction_paths(store_path.name)
            current_store_path = None
            
            try:
                # 1. Prepare layers (Finds system paths and regular deps)
                mounting_list, sys_pkg_lowers = self._prepare_ingredients(pkg, main_paths)

                # 2. Install packages in topological order
                for current_store_path, map_key, paths, name, deb_paths in mounting_list:
                    self._run_sandbox_install(name, paths, deb_paths, sys_pkg_lowers)

                    # 3. Freeze the result into the store
                    if not self._commit_package(paths.upper, Path(current_store_path), map_key):
                        raise Exception(f"Atomic commit failed for {name}")

                    self._created_wrappers.discard(str(WRAPPER_DIR / current_store_path.name))

                return True

            except Exception as e:
                self.logger.error(f"Failed to install {pkg_name}, initiating cleanup.")
                self.logger.exception(e)
                
                if current_store_path:
                    self.reset_target(Path(current_store_path))
                    self.logger.info("Cleanup successful.")
                
                for wrapper in self._created_wrappers:
                    if Path(wrapper).exists():
                        shutil.rmtree(wrapper)
                        self.logger.debug(f"Cleaned up orphaned wrapper: {wrapper}")
                        
                return False
                
            finally:
                self._cleanup_transaction()

    def _prepare_ingredients(self, pkg: Dict[str, Any], paths: TransactionPaths) -> Tuple[List[Tuple], List[Path]]:
        relative_store_path = Path(pkg["Store_Path"])
        
        # Initialize lists to be populated
        sys_pkg_lowers: List[Path] = []
        recipes_to_process: Dict[Path, Dict[str, Any]] = {}
        mounting_list: List[Tuple] = []

        # HELPER 1: Fetches and unpacks recipe
        def _fetch_and_get_recipe(rel_path: Path, t_paths: TransactionPaths) -> Tuple[List[Path], Dict[str, Any]]:
            t_paths.download.mkdir(parents=True, exist_ok=True)
            t_paths.stage.mkdir(parents=True, exist_ok=True)
            t_paths.forest.mkdir(parents=True, exist_ok=True)

            zip_path = self.fetcher.download_file(save_path=t_paths.download, relative_store_path=rel_path)
            if zip_path is None:
                raise Exception(f"Package/Dependency {rel_path} failed to download")

            deb_paths = self._extract_zip_to_stage(zip_path, t_paths.stage)
            recipe = self.get_recipe(t_paths.stage)
            return deb_paths, recipe

        # HELPER 2: integrates the packages we fetched
        def _integrate(rel_path: Path, t_paths: TransactionPaths, deb_paths: List[Path], recipe: Dict[str, Any]):
            pkg_name = recipe["package_name"]
            version = recipe["version"]
            map_key = KEY_STR.format(name=pkg_name, version=version)

            for deb_path in deb_paths:
                self._extract_deb_to_stage(deb_path, t_paths.stage)
            
            recipes_to_process[t_paths.forest] = recipe
            mounting_list.append((self.root / rel_path, map_key, t_paths, pkg_name, []))
            
            self._create_wrapper(rel_path, recipe.get("provider_map", []), sys_pkg_lowers)

        # 1. Fetch main package first to read its recipe
        main_deb_path, main_recipe = _fetch_and_get_recipe(relative_store_path, paths)
        pprint.pprint(main_recipe)

        # 2. Resolve system package layers beforehand.
        sys_reqs = main_recipe.get("mount_instructions", {}).get("system_mounts", [])
        if isinstance(sys_reqs, str): sys_reqs = [sys_reqs]
        
        for sys_rel_path in sys_reqs:
            sys_path = self.root / Path(sys_rel_path)
            if sys_path.exists():
                if sys_path not in sys_pkg_lowers:
                    sys_pkg_lowers.append(sys_path)
            else:
                # Fail fast if a required system package is not in the store.
                raise FileNotFoundError(f"System requirement not found. Please run 'ddls system {sys_rel_path}' for it first.")

        # 3. Process regular dependencies IN TOPOLOGICAL ORDER
        required_mounts = main_recipe.get("mount_instructions", {}).get("required_mounts", [])
        
        for rel_dep_path_str in required_mounts:
            rel_dep_path = Path(rel_dep_path_str)
            store_target = self.root / rel_dep_path
            
            if store_target.exists():
                continue
                
            dep_paths = self._get_transaction_paths(rel_dep_path.name)
            dep_deb_path, dep_recipe = _fetch_and_get_recipe(rel_dep_path, dep_paths)
            _integrate(rel_dep_path, dep_paths, dep_deb_path, dep_recipe)

        # 4. Process the main package LAST
        _integrate(relative_store_path, paths, main_deb_path, main_recipe)

        # 5. Create the symlink forest 
        if recipes_to_process:
            self._plant_symlink_forest(recipes_to_process)
            
        return mounting_list, sys_pkg_lowers

    def _create_wrapper(self, hash_path: Path, provide_list: List[str], sys_pkg_lowers: List[Path]):
        wrapper_path = WRAPPER_DIR / hash_path
        wrapper_path.mkdir(parents=True, exist_ok=True)

        # Pre-calculate runtime lowers
        runtime_lowers = [str(self.root / hash_path)]
        runtime_lowers.extend([str(p) for p in sys_pkg_lowers])
        runtime_lowers.append(str(self.base_rootfs))
        lower_dirs_str = ":".join(runtime_lowers)

        for provide in provide_list:
            if provide.endswith('.so') or '.so.' in provide:
                continue

            target_path = (wrapper_path / provide).resolve()
            if not str(target_path).startswith(str(wrapper_path.resolve())):
                continue

            target_path.parent.mkdir(parents=True, exist_ok=True)
            
            context = WrapperConfig(
                store_path=str(self.root / hash_path),
                bin_src=provide,
                lower_dirs=lower_dirs_str
            )

            tmp_path = target_path.with_suffix('.tmp')
            with open(tmp_path, "w") as f:
                f.write(self.wrapper_template.format(**context.to_dict()))
            
            os.replace(tmp_path, target_path)
            os.chmod(target_path, 0o755)

            self._created_wrappers.add(str(wrapper_path))

    def _plant_symlink_forest(self, recipes_to_process: Dict[Path, Dict[str, Any]]):
        for forest_root, recipe in recipes_to_process.items():
            for jail_path, store_path in recipe.get("symlink_forest", {}).items():
                link_name = forest_root / jail_path.lstrip("/")
                if '.so' in os.path.basename(store_path):
                    target_data = STORE_ROOT / store_path
                else:
                    target_data = WRAPPER_DIR / store_path

                link_name.parent.mkdir(parents=True, exist_ok=True)
                if link_name.is_symlink() or link_name.exists():
                    link_name.unlink()
                
                os.symlink(target_data, link_name)

    def reset_target(self, target_path: Path):
        if not target_path.exists(): return
        resolved_target = target_path.resolve()
        mounts = []
        with open('/proc/mounts', 'r') as f:
            for line in f:
                mount_point = Path(line.split()[1]).resolve()
                if mount_point == resolved_target or resolved_target in mount_point.parents:
                    mounts.append(str(mount_point))

        mounts.sort(key=len, reverse=True)
        for mount in mounts:
            if subprocess.run(["umount", mount], stderr=subprocess.DEVNULL).returncode != 0:
                subprocess.run(["umount", "-l", mount])
        try:
            if os.path.ismount(str(resolved_target)): return
            shutil.rmtree(resolved_target)
        except OSError as e:
            self.logger.critical(f"Cleanup of {resolved_target} failed: {e}")
        
    @contextmanager
    def _mount_stack(self, paths: TransactionPaths, sys_pkg_lowers: List[Path] = None):
        mounts = []
        try:
            lower_dirs = [str(paths.forest)]
            if sys_pkg_lowers:
                pass
                #lower_dirs.extend([str(p) for p in sys_pkg_lowers])
            lower_dirs.append(str(self.base_rootfs))

            for p in lower_dirs + [str(paths.upper), str(paths.work), str(paths.merged)]:
                if not os.path.exists(p):
                    raise FileNotFoundError(f"MOUNT BLOCKED: Path does not exist: {p}")
                if not os.path.isdir(p):
                    raise NotADirectoryError(f"MOUNT BLOCKED: Path is a file, but OverlayFS requires a directory: {p}")
            
            lower_str = ":".join(lower_dirs)
            opts = f"lowerdir={lower_str},upperdir={paths.upper},workdir={paths.work}"

            print(f"Options length: {len(opts)} bytes") # Check length constraint

            try:
                # Capture output so we can see what mount complains about
                result = subprocess.run(
                    ["mount", "-t", "overlay", "overlay", "-o", opts, str(paths.merged)], 
                    check=True,
                    capture_output=True,
                    text=True
                )
                print("Mount successful!")
            except subprocess.CalledProcessError as e:
                self.logger.info(f"Mount failed with code {e.returncode}")
                self.logger.info(f"Stderr: {e.stderr}")
                subprocess.run(f"stat -f {paths.work}", shell=True)

                # Fetch the last 5 lines of kernel logs to see the REAL OverlayFS error
                self.logger.info("\n--- Kernel logs (dmesg) ---")
                subprocess.run("dmesg | tail -n 30", shell=True)
                raise

            for api in ["proc", "sys", "dev"]:
                target = paths.merged / api
                subprocess.run(["mount", "--bind", f"/{api}", str(target)], check=True)
                mounts.append(target)

            store_in_jail = paths.merged / str(self.root).lstrip("/")
            store_in_jail.mkdir(parents=True, exist_ok=True)
            subprocess.run(["mount", "--bind", "-o", "ro", str(self.root), str(store_in_jail)], check=True)
            mounts.append(store_in_jail)

            yield 
            
        finally:
            for target in reversed(mounts):
                subprocess.run(["umount", "-l", str(target)], check=False)

    def _upgrade_system_libs(self, merged_path: Path, pkg_name: str, deb_paths: List[Path]):
        tmp_dir_in_root = merged_path / "tmp"
        tmp_dir_in_root.mkdir(parents=True, exist_ok=True)
        
        chroot_deb_paths = []
        for deb in deb_paths:
            target_deb = tmp_dir_in_root / deb.name
            shutil.copy2(deb, target_deb)
            chroot_deb_paths.append(f"/tmp/{deb.name}")
        
        cmd = ["chroot", str(merged_path), DPKG_CMD, "--auto-deconfigure", "-i"] + chroot_deb_paths
        res = subprocess.run(cmd, capture_output=True, text=True)
        
        if res.returncode != 0:
            self.logger.error(f"dpkg failed for bundle {pkg_name}.\nSTDOUT:\n{res.stdout}\nSTDERR:\n{res.stderr}")
            raise RuntimeError(f"Failed to cleanly install system bundle for {pkg_name}")
        
        # Cleanup isolated copies
        for p in chroot_deb_paths:
            deb_file = merged_path / p.lstrip("/")
            if deb_file.exists():
                deb_file.unlink()

    def _run_sandbox_install(self, pkg_name: str, paths: TransactionPaths, deb_paths: List[Path], sys_pkg_lowers: List[Path]):
        """Installs a regular package into the overlay stack."""
        with self._mount_stack(paths, sys_pkg_lowers):
            # 1. Copy staged files into the merged view
            subprocess.run(["cp", "-a", f"{paths.stage}/.", str(paths.merged)], check=True)
            
            # 2. Update shared library cache
            subprocess.run(["chroot", str(paths.merged), LDCONFIG_PATH, "-X"], check=True)

            # 3. Run the debian post-installation script if it exists
            postinst_rel = DPKG_POSTINST_PATH
            if (paths.merged / postinst_rel).exists():
                self.logger.info(f"Running postinst for {pkg_name}...")
                subprocess.run(["chroot", str(paths.merged), f"/{postinst_rel}", "configure"], check=True)

    def _commit_package(self, upper_path: Path, store_path: Path, pkg_map_key: str) -> bool:
        if not any(os.scandir(upper_path)):
            raise RuntimeError("Installation failed: Upper directory is empty")
        
        store_path.parent.mkdir(parents=True, exist_ok=True)

        if not self._save_package_to_map(store_path, pkg_map_key):
            return False

        tmp_store_target = STORE_TMP_ROOT / f"{store_path.name}.tmp"
        try:
            shutil.move(str(upper_path), str(tmp_store_target))
            os.replace(str(tmp_store_target), str(store_path))
            self.logger.info(f"Package committed to store at {store_path}")
        except Exception as e:
            self._erase_package(store_path, pkg_map_key)
            if tmp_store_target.exists():
                shutil.rmtree(tmp_store_target, ignore_errors=True)
            self.logger.exception(e)
            return False
        
        return True

    def _extract_zip_to_stage(self, zip_path: Path, stage_path: Path) -> List[Path]:
        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
            zip_ref.extractall(stage_path)
        deb_files = list(stage_path.glob("*.deb"))
        if not deb_files: raise FileNotFoundError(f"No .deb found in {zip_path}")
        if zip_path.exists(): zip_path.unlink()
        return deb_files

    def _extract_deb_to_stage(self, deb_file: Path, stage_path: Path):
        subprocess.run(["dpkg-deb", "-x", str(deb_file), str(stage_path)], check=True)
        control_dir = stage_path / DPKG_INFO_PATH
        control_dir.mkdir(parents=True, exist_ok=True)
        subprocess.run(["dpkg-deb", "-e", str(deb_file), str(control_dir)], check=True)
        deb_file.unlink()

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
        self._active_tx_paths.append(tx)
        return tx

    def get_recipe(self, stage_path: Path) -> Dict[str, Any]:
        recipe_path = stage_path / RECIPE
        if not recipe_path.exists(): return {}
        with open(recipe_path, "r") as f:
            return json.load(f)
        
    @contextmanager
    def _transaction_lock(self):
        lock_path = self.transient / ".update.lock"
        self.transient.mkdir(parents=True, exist_ok=True)
        
        with open(lock_path, 'w') as lock_file:
            self.logger.info("Acquiring transaction lock (waiting if busy)...")
            fcntl.flock(lock_file, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock_file, fcntl.LOCK_UN)

    def _cleanup_transaction(self):
        for tx in self._active_tx_paths:
            for directory in [tx.stage, tx.forest, tx.upper, tx.work, tx.merged]:
                if directory.exists():
                    self.reset_target(directory)
                    shutil.rmtree(directory, ignore_errors=True)