import os
import json
import shutil
import subprocess
import zipfile
import struct
import fcntl
from contextlib import contextmanager

from .utils import TransactionPaths, WrapperConfig
from .bootstrapper import Bootstrapper as Bbrfs
from config import *

class Store:
    def __init__(self, fetcher, root=STORE_ROOT, transient=TRANS_ROOT, base_rootfs=BASE_ROOTFS, pkg_map=PKG_MAP_PATH):
        self.root = Path(root)
        self.transient = Path(transient)
        self.base_rootfs = Path(base_rootfs)
        self.pkg_map = Path(pkg_map)
        self.fetcher = fetcher

        self.bootstrapper = Bbrfs()
        self.logger = logging.getLogger(self.__class__.__name__)

        with open(PACKAGE_WRAPPER_PATH, "r") as f:
            self.wrapper_template = f.read()

        self.root.mkdir(parents=True, exist_ok=True)
        self.transient.mkdir(parents=True, exist_ok=True)
        STORE_TMP_ROOT.mkdir(parents=True, exist_ok=True)

        self._init_map()

        # State tracking for failure cleanup
        self._active_tx_paths: List[TransactionPaths] =[]
        self._created_wrappers: set = set()

    def _init_map(self):
        """Creates an empty file filled with null bytes if it doesn't exist."""
        if not self.pkg_map.exists():
            with open(self.pkg_map, "wb") as f:
                f.write(b'\x00' * (SLOT_COUNT * SLOT_SIZE))
            self.logger.info(f"Package map created at: {self.pkg_map}")

    @staticmethod
    def _hash_djb2(s: str) -> int:
        """Simple hash function to turn a string into a slot index."""
        h = 5381
        for char in s:
            h = ((h << 5) + h) + ord(char)
        return h % SLOT_COUNT

    @contextmanager
    def _transaction_lock(self):
        """Context Manager: Ensure only one package manager process runs updates at a time."""
        lock_path = self.transient / ".update.lock"
        self.transient.mkdir(parents=True, exist_ok=True)
        
        with open(lock_path, 'w') as lock_file:
            self.logger.info("Acquiring transaction lock (waiting if busy)...")
            fcntl.flock(lock_file, fcntl.LOCK_EX) # This blocks and waits if another process is using it
            try:
                yield
            finally:
                fcntl.flock(lock_file, fcntl.LOCK_UN)

    def _save_package_to_map(self, hash_path: Path, pkg_ver: str) -> bool:
        if not self.pkg_map.exists(): 
            return False
        
        index = self._hash_djb2(pkg_ver)
        key_bytes = pkg_ver.encode('utf-8')[:KEY_SIZE]
        val_bytes = str(hash_path).encode('utf-8')[:VALUE_SIZE]

        with open(self.pkg_map, "rb+") as f:
            attempt = 0
            first_tombstone_offset = None
            
            while attempt < SLOT_COUNT:
                offset = index * SLOT_SIZE
                f.seek(offset)
                data = f.read(SLOT_SIZE)
                status = data[0]
                
                # Exact byte comparison (don't strip nulls here, otherwise shorter keys collide)
                existing_key = data[1:1+KEY_SIZE]
                if status == STATUS_OCCUPIED and existing_key == key_bytes:
                    f.seek(offset)
                    f.write(struct.pack(f"<B{KEY_SIZE}s{VALUE_SIZE}s", STATUS_OCCUPIED, key_bytes, val_bytes))
                    return True

                if status == STATUS_DELETED and first_tombstone_offset is None:
                    first_tombstone_offset = offset

                if status == STATUS_EMPTY:
                    target_offset = first_tombstone_offset if first_tombstone_offset is not None else offset
                    f.seek(target_offset)
                    f.write(struct.pack(f"<B{KEY_SIZE}s{VALUE_SIZE}s", STATUS_OCCUPIED, key_bytes, val_bytes))
                    return True
                
                index = (index + 1) % SLOT_COUNT
                attempt += 1
        return False

    def get_package(self, pkg_ver: str) -> Optional[str]:
        if not self.pkg_map.exists(): 
            return None
        
        index = self._hash_djb2(pkg_ver)
        key_bytes = pkg_ver.encode('utf-8')[:KEY_SIZE]

        with open(self.pkg_map, "rb") as f:
            attempt = 0
            while attempt < SLOT_COUNT:
                f.seek(index * SLOT_SIZE)
                data = f.read(SLOT_SIZE)
                status = data[0]

                if status == STATUS_EMPTY: 
                    return None
                
                if status == STATUS_OCCUPIED:
                    existing_key = data[1:1+KEY_SIZE]
                    if existing_key == key_bytes:
                        return data[1+KEY_SIZE:].strip(b'\x00').decode('utf-8')

                index = (index + 1) % SLOT_COUNT
                attempt += 1
        return None
    
    def _erase_package(self, pkg_path: Path, pkg_ver: str) -> bool:
        """Marks a package as DELETED (Tombstone) so the chain isn't broken."""
        if pkg_path.exists():
            shutil.rmtree(pkg_path)

        if not self.pkg_map.exists(): 
            return False
        
        index = self._hash_djb2(pkg_ver)
        key_bytes = pkg_ver.encode('utf-8')[:KEY_SIZE]

        with open(self.pkg_map, "rb+") as f:
            attempt = 0
            while attempt < SLOT_COUNT:
                offset = index * SLOT_SIZE
                f.seek(offset)
                data = f.read(SLOT_SIZE)
                status = data[0]

                if status == STATUS_EMPTY: 
                    return False
                
                if status == STATUS_OCCUPIED:
                    existing_key = data[1:1+KEY_SIZE]
                    if existing_key == key_bytes:
                        f.seek(offset)
                        f.write(struct.pack("B", STATUS_DELETED)) 
                        return True

                index = (index + 1) % SLOT_COUNT
                attempt += 1
        return False

    def update(self, pkg: Dict[str, Any]) -> bool:
        pkg_name = pkg["Package"]
        store_path = self.root / Path(pkg["Store_Path"])

        if store_path.exists():
            return True

        # Wrap everything in our robust concurrency lock
        with self._transaction_lock():
            # Reset transaction tracking state
            self._active_tx_paths =[]
            self._created_wrappers.clear()
            current_store_path = ""
            main_paths = self._get_transaction_paths(store_path.name)
            
            try:
                # Phase 1: Preparation (The Ingredients)
                mounting_list = self._prepare_ingredients(pkg, main_paths)

                # Phase 2 & 3: Building the Site and Live Installation
                # (Dependencies are processed in topological order automatically by _prepare_ingredients recursion)
                for current_store_path, pkg_map_key, paths in mounting_list:
                    self._run_sandbox_install(pkg_name, paths)

                    # Phase 4: The Freeze (Commitment)
                    if not self._commit_package(paths.upper, Path(current_store_path), pkg_map_key):
                        raise Exception(f"Commit failed for {current_store_path}, atomic logic aborted.")

                    self._created_wrappers.discard(str(WRAPPER_DIR / current_store_path.name))

                return True

            except Exception as e:
                self.logger.error(f"Failed to install {pkg_name}, initiating cleanup.")
                self.logger.exception(e)
                
                if current_store_path:
                    self.reset_target(Path(current_store_path))
                    self.logger.info("Cleanup successful.")
                
                for wrapper in self._created_wrappers:
                    if wrapper.exists():
                        shutil.rmtree(wrapper)
                        self.logger.debug(f"Cleaned up orphaned wrapper: {wrapper}")
                        
                return False
                
            finally:
                self._cleanup_transaction()
    
    def _prepare_ingredients(self, pkg: Dict[str, Any], paths: TransactionPaths) -> List[Tuple[Path, str, TransactionPaths]]:
        relative_store_path = Path(pkg["Store_Path"])
        
        recipes_to_process: Dict[Path, Dict[str, Any]] = {}
        mounting_list: List[Tuple[Path, str, TransactionPaths]] =[]

        def _fetch_and_get_recipe(rel_path: Path, t_paths: TransactionPaths) -> Tuple[Path, Dict[str, Any]]:
            t_paths.download.mkdir(parents=True, exist_ok=True)
            t_paths.stage.mkdir(parents=True, exist_ok=True)
            t_paths.forest.mkdir(parents=True, exist_ok=True)

            zip_path = self.fetcher.download_file(save_path=t_paths.download, relative_store_path=rel_path)
            if zip_path is None:
                raise Exception(f"Package/Dependency {rel_path} failed to download")

            deb_path = self._extract_zip_to_stage(zip_path, t_paths.stage)
            recipe = self.get_recipe(t_paths.stage)
            return deb_path, recipe

        def _integrate(rel_path: Path, t_paths: TransactionPaths, deb_path: Path, recipe: Dict[str, Any]) -> bool:
            pkg_name = recipe["package_name"]
            version = recipe["version"]

            if self.bootstrapper.is_system_version_newer(new_version=version, pkg_name=pkg_name):                
                new_sys_deb_path = SYS_PKGS / deb_path.name
                shutil.move(str(deb_path), str(new_sys_deb_path))
                self.logger.debug(f"Moved system package to pending upgrades: {new_sys_deb_path}")
                return False
            else:
                self.logger.debug(f"pkg {deb_path.name[HASH_LENGTH:]} isnt a system pkg continuing process")

            self._extract_deb_to_stage(deb_path, t_paths.stage)
            recipes_to_process[t_paths.forest] = recipe
            
            mounting_list.append((
                self.root / rel_path, 
                KEY_STR.format(name=pkg_name, version=version), 
                t_paths
            ))
            
            self._create_wrapper(rel_path, recipe.get("provider_map",[]))
            return True

        # Fetch Main Package 
        main_deb_path, main_recipe = _fetch_and_get_recipe(relative_store_path, paths)

        # Process Dependencies Iteratively (Lowest to Highest Topological Sort respected by recursive calling)
        required_mounts = main_recipe.get("mount_instructions", {}).get("required_mounts",[])
        
        for rel_dep_path in required_mounts:
            rel_dep_path = Path(rel_dep_path)
            dep_paths = self._get_transaction_paths(rel_dep_path.name)
            
            if (self.root / rel_dep_path).exists():
                self.logger.debug(f"Dependency {rel_dep_path} exists, continuing")
                continue
                
            self.logger.info(f"Missing dependency: {rel_dep_path}. Integrating iteratively...")
            dep_deb_path, dep_recipe = _fetch_and_get_recipe(rel_dep_path, dep_paths)
            _integrate(rel_dep_path, dep_paths, dep_deb_path, dep_recipe)

        # Process Main Package Integration
        self.logger.info(f"Starting symlink forest planting for {pkg['Package']} with {len(recipes_to_process)} dependencies...")
        
        if _integrate(relative_store_path, paths, main_deb_path, main_recipe):
            self._plant_symlink_forest(recipes_to_process)
            
        return mounting_list
    
    def _create_wrapper(self, hash_path: Path, provide_list: List[str]):
        wrapper_path = WRAPPER_DIR / hash_path
        wrapper_path.mkdir(parents=True, exist_ok=True)

        for provide in provide_list:
            if provide.endswith('.so') or '.so.' in provide:
                continue

            target_path = (wrapper_path / provide).resolve()
            if not str(target_path).startswith(str(wrapper_path.resolve())):
                self.logger.warning(f"Security violation: Provider {provide} escapes wrapper directory!")
                continue

            target_path.parent.mkdir(parents=True, exist_ok=True)

            context = WrapperConfig(
                store_path=str(self.root / hash_path),
                bin_src=provide
            )

            tmp_path = target_path.with_suffix('.tmp')
            with open(tmp_path, "w") as f:
                f.write(self.wrapper_template.format(**context.to_dict()))
            
            os.replace(tmp_path, target_path)
            os.chmod(target_path, 0o755)

            # Track for cleanup on failure
            self._created_wrappers.add(str(wrapper_path))
            self.logger.debug(f"Found executable: {provide} -> Wrapped to: {target_path}")

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
                
                self.logger.debug(f"Forest Link:\n{link_name}\n|\nV\n{target_data}")
                os.symlink(target_data, link_name)

    def reset_target(self, target_path: Path):
        """Safely unmounts and deletes target, preventing recursive destruction."""
        if not target_path.exists():
            return

        # Resolve real paths so we don't accidentally match parent folders
        resolved_target = target_path.resolve()
        mounts =[]
        
        with open('/proc/mounts', 'r') as f:
            for line in f:
                mount_point = Path(line.split()[1]).resolve()
                # Check if it IS the target or a direct CHILD of the target
                if mount_point == resolved_target or resolved_target in mount_point.parents:
                    mounts.append(str(mount_point))

        mounts.sort(key=len, reverse=True)

        for mount in mounts:
            self.logger.debug(f"Unmounting: {mount}")
            if subprocess.run(["umount", mount], stderr=subprocess.DEVNULL).returncode != 0:
                self.logger.warning(f"  -> Busy, forcing lazy unmount on {mount}")
                subprocess.run(["umount", "-l", mount])

        try:
            # Check if it's completely unmounted before we recursively delete
            if os.path.ismount(str(resolved_target)):
                self.logger.error(f"Failed to unmount {resolved_target}. Skipping deletion to protect host.")
                return
            shutil.rmtree(resolved_target)
        except OSError as e:
            self.logger.critical(f"Cleanup of {resolved_target} failed: {e}")
        
    @contextmanager
    def _mount_stack(self, paths: TransactionPaths):
        mounts =[]
        try:
            lower = f"{paths.forest}:{self.base_rootfs}"
            opts = f"lowerdir={lower},upperdir={paths.upper},workdir={paths.work}"
            subprocess.run(["mount", "-t", "overlay", "overlay", "-o", opts, str(paths.merged)], check=True)
            mounts.append(paths.merged)

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
                
    def _run_healthcheck(self, merged_path: Path):
        bin_dir = merged_path / USR_BIN_PATH
        if not bin_dir.exists(): 
            return

        for b in bin_dir.iterdir():
            if b.is_file() and not b.is_symlink():
                res = subprocess.run(["chroot", str(merged_path), "ldd", str(b.relative_to(merged_path))], 
                                     capture_output=True, text=True)
                
                # returncode, stdout error messages vary by architecture/linker version
                if res.returncode != 0 or "not found" in res.stdout:
                    raise RuntimeError(f"Healthcheck failed: {b.name} is missing dependencies!\n{res.stdout}")

    def _run_sandbox_install(self, pkg_name: str, paths: TransactionPaths):
        with self._mount_stack(paths):
            subprocess.run(["cp", "-a", f"{paths.stage}/.", str(paths.merged)], check=True)
            subprocess.run(["chroot", str(paths.merged), LDCONFIG_PATH, "-X"], check=True)

            postinst_rel = DPKG_POSTINST_PATH
            if (paths.merged / postinst_rel).exists():
                self.logger.info(f"Running postinst for {pkg_name}...")
                result = subprocess.run(["chroot", str(paths.merged), f"/{postinst_rel}", "configure"],           
                    check=True,
                    capture_output=True,
                    text=True
                )
                self.logger.debug(f"STDOUT:{result.stdout}\nSTDERR:{result.stderr}")

            #self._run_healthcheck(paths.merged)

    def _commit_package(self, upper_path: Path, store_path: Path, pkg_map_key: str) -> bool:
        if not any(os.scandir(upper_path)):
            raise RuntimeError("Installation failed: Upper directory is empty")
        
        store_path.parent.mkdir(parents=True, exist_ok=True)

        if not self._save_package_to_map(store_path, pkg_map_key):
            self.logger.error(f"Could not save package: {pkg_map_key}")
            return False

        # We move it to a tmp dir on the SAME target filesystem (STORE_ROOT) first, then atomic rename
        tmp_store_target = STORE_TMP_ROOT / f"{store_path.name}.tmp"
        
        try:
            # 1. Shutil moves across filesystems (Transient RAM Disk -> Hard Drive Store TMP)
            shutil.move(str(upper_path), str(tmp_store_target))
            
            # 2. OS Replace is highly atomic inside the same filesystem (Store TMP -> Store Path)
            os.replace(str(tmp_store_target), str(store_path))
            self.logger.info(f"Package committed to store at {store_path}")
            
        except Exception as e:
            self._erase_package(store_path, pkg_map_key)
            if tmp_store_target.exists():
                shutil.rmtree(tmp_store_target, ignore_errors=True)
            self.logger.exception(e)
            return False
        
        return True

    def _extract_zip_to_stage(self, zip_path: Path, stage_path: Path) -> Path:
        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
            zip_ref.extractall(stage_path)

        deb_files = list(stage_path.glob("*.deb"))
        if not deb_files:
            raise FileNotFoundError(f"No .deb found in {zip_path}")
        
        if zip_path.exists():
            zip_path.unlink()
        
        return deb_files[0]

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

        for path in[tx.stage, tx.forest, tx.upper, tx.work, tx.merged, tx.download]:
            path.mkdir(parents=True, exist_ok=True)

        # Track this transaction so it gets cleaned up later
        self._active_tx_paths.append(tx)
        return tx

    def get_recipe(self, stage_path: Path) -> Dict[str, Any]:
        recipe_path = stage_path / RECIPE
        if not recipe_path.exists():
            return {}
        with open(recipe_path, "r") as f:
            return json.load(f)

    def _cleanup_transaction(self):
        """Clean only the directories used by this specific transaction."""
        for tx in self._active_tx_paths:
            for directory in[tx.stage, tx.forest, tx.upper, tx.work, tx.merged]:
                if directory.exists():
                    self.reset_target(directory)  # Safer than rmtree if lingering mounts exist
                    shutil.rmtree(directory, ignore_errors=True)