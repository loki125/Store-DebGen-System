import os
import json
import shutil
import subprocess
import copy
import time
from operator import attrgetter
from pprint import pformat
from typing import Optional, List, Tuple, Dict, Set
from pathlib import Path
import logging

from config import *
from .store import Store
from .health import Health
from .utils import GenManifest, Layer, HealthInfo, GenPath

class Generation:
    def __init__(self, store: Store):
        self.store = store
        self.logger = logging.getLogger(self.__class__.__name__)

    def initialize_system(self) -> Optional[GenManifest]:
        """Creates the first generation if it doesn't exist."""
        if not GEN_DIR.exists():
            self.logger.info(f"Initializing DDLS directory at {GEN_DIR}")
            GEN_DIR.mkdir(parents=True, exist_ok=True)
        
        # Since this modifies system files, it requires root/sudo
        try:
            if PROFILE_D_DIR.exists():
                if not PROFILE_SCRIPT_PATH.exists():
                    self.logger.info(f"Adding DDLS to system environment via {PROFILE_SCRIPT_PATH}")
                    with open(PROFILE_SCRIPT_PATH, "w") as f:
                        f.write(ETC_PROFILE_COMMENT)
                        f.write(EXPORTS)
            else:
                # Fallback: Safely append to /etc/profile directly if profile.d doesn't exist
                if SYS_PROFILE_PATH.exists():
                    with open(SYS_PROFILE_PATH, "r") as f:
                        profile_content = f.read()
                    
                    if ACTIVE_BIN_EXPORT_STR not in profile_content:
                        self.logger.info("Appending DDLS Environment to /etc/profile")
                        with open(SYS_PROFILE_PATH, "a") as f:
                            f.write(f"\n{ETC_PROFILE_COMMENT}")
                            f.write(EXPORTS)

        except PermissionError:
            self.logger.warning("Permission denied: Could not update profiles. Ensure you run this as root!")
            raise

        if CURRENT_LINK.exists():
            return None
        
        return GenManifest(
            prev_id=None,
            active_layers=[], 
            relations={},
            active=True,
            health=HealthInfo(status="healthy", logs="Initial System Creation")
        )

    def get_current_manifest(self) -> GenManifest:
        """Always points to the symlink."""
        if not CURRENT_LINK.exists():
            return self.initialize_system()
            
        with open(CURRENT_LINK, "r") as f:
            data = json.load(f)
            return GenManifest.from_dict(data)

    def _switch_current_manifest(self, new_manifest: GenManifest):
        target = Path(GenPath.manifest(new_manifest.timestamp_id))

        with open(target, "w") as f:
            f.write(new_manifest.to_json())

        if CURRENT_LINK.exists() or CURRENT_LINK.is_symlink():
            CURRENT_LINK.unlink()

        os.symlink(target, CURRENT_LINK)

    def create_manifest(self, to_add: Optional[List[str]] = None, to_rm: Optional[List[str]] = None) -> Tuple[GenManifest, GenManifest]:
        current: GenManifest = self.get_current_manifest()
        new_gen: GenManifest = copy.deepcopy(current)
        
        new_gen.prev_id = current.timestamp_id
        new_gen.active = False

        # REMOVE LOGIC
        if to_rm:
            remove_queue = to_rm.copy()
            
            while remove_queue:
                target_hash = remove_queue.pop(0)
                
                if target_hash in new_gen.relations:
                    for dep_hash, isolated_p in list(new_gen.relations[target_hash].items()):
                        dep_layer = next((l for l in new_gen.active_layers if l.h == dep_hash), None)
                        
                        if dep_layer:
                            dep_layer.p -= isolated_p
                            del new_gen.relations[target_hash][dep_hash]

                            if dep_layer.p <= 0:
                                remove_queue.append(dep_hash)

                new_gen.active_layers = [l for l in new_gen.active_layers if l.h != target_hash]
                if target_hash in new_gen.relations:
                    del new_gen.relations[target_hash]

        # ADDITION LOGIC
        if to_add:
            add_queue = to_add.copy()
            
            while add_queue:
                current_hash = add_queue.pop(0)
                
                recipe: Dict = self.store.get_recipe(STORE_ROOT / current_hash)
                if not recipe: 
                    self.logger.warning(f"Skip: Recipe for {current_hash} not found in store.")
                    continue
                
                pkg_layer = next((l for l in new_gen.active_layers if l.h == current_hash), None)
                if not pkg_layer:
                    pkg_layer = Layer(h=current_hash, p=0)
                    new_gen.active_layers.append(pkg_layer)

                if current_hash not in new_gen.relations:
                    new_gen.relations[current_hash] = {}

                mounts: Dict = recipe.get("mount_instructions", {})
                if not mounts:
                    continue

                # Iterate required_mounts exactly as provided (Topological Order)
                required_mounts: List[str] = mounts.get("required_mounts", [])
                
                for isolated_p, dep_hash in enumerate(required_mounts):
                    dep_layer = next((l for l in new_gen.active_layers if l.h == dep_hash), None)
                    
                    if not dep_layer:
                        dep_layer = Layer(h=dep_hash, p=0)
                        new_gen.active_layers.append(dep_layer)
                        add_queue.append(dep_hash)

                    if dep_hash not in new_gen.relations[current_hash]:
                        new_gen.relations[current_hash][dep_hash] = isolated_p
                        dep_layer.p += isolated_p

        self.logger.debug(pformat(new_gen.to_json(), indent=4))
        return current, new_gen
    
    def _calculate_diff(self, old_manifest: GenManifest, new_manifest: GenManifest) -> Tuple[Set[Layer], Set[Layer]]:
        self.logger.info("Calculating differences...")
        old_pkgs = set(old_manifest.active_layers)
        new_pkgs = set(new_manifest.active_layers)
        return old_pkgs - new_pkgs, new_pkgs - old_pkgs

    def execute(self, new_manifest: GenManifest, current_manifest: GenManifest, overwrite_flag: bool = False) -> bool:
        self.logger.info(f"=== STARTING TRANSITION: Gen {current_manifest.timestamp_id} -> Gen {new_manifest.timestamp_id} ===")
        healther = Health()
        new_path = None
        
        try:
            new_path = self._create_new_gen(new_manifest)

            if not healther.gen_health(new_path):
                raise Exception(f"New generation is broken. Aborting.\n{pformat(healther.report, indent=4)}")

            to_remove, to_add = self._calculate_diff(current_manifest, new_manifest)
            current_path = GEN_DIR / str(current_manifest.timestamp_id)

            sorted_remove = sorted(to_remove, key=attrgetter('p'))
            self._shutdown_processes(sorted_remove)

            self._atomic_switch(new_manifest)

            sorted_add = sorted(to_add, key=attrgetter('p'), reverse=True)
            self._activate_processes(
                sorted_add, 
                Path(GenPath.root_bin(new_manifest.timestamp_id))
            )

            if overwrite_flag:
                self.store.reset_target(current_path)

            self.logger.info("=== TRANSITION COMPLETE SUCCESSFULLY ===")
            return True
            
        except Exception as e:
            if new_path is not None:
                self.store.reset_target(Path(new_path))

            self.logger.error(f"Failed to create new generation:\n{e}")
            return False
    
    def _create_new_gen(self, new_manifest: GenManifest) -> str:
        manifest_id = new_manifest.timestamp_id
        gen_bin_dir = Path(GenPath.root_bin(manifest_id))
        gen_lib_dir = Path(GenPath.root_lib(manifest_id))
        gen_lib64_dir = Path(GenPath.root_lib64(manifest_id))
        
        gen_bin_dir.mkdir(parents=True, exist_ok=True)
        gen_lib_dir.mkdir(parents=True, exist_ok=True)
        gen_lib64_dir.mkdir(parents=True, exist_ok=True)
        
        base_path_str = GenPath.base(manifest_id)
        self.logger.info(f"Building Generation Forest at {base_path_str}")
        
        sorted_layers = sorted(new_manifest.active_layers, key=attrgetter('p'), reverse=True)
        for layer in sorted_layers:
            pkg_store_path = STORE_ROOT / layer.h

            # If it's a system package without a wrapper, this safely ignores it.
            self._link_wrappers_to_bin(layer.h, gen_bin_dir)

            for lib_path in LIB_PATHS:
                self._handle_lib_symlinking(pkg_store_path, gen_lib_dir, lib_path)
                
            for lib64_path in LIB64_PATHS:  
                self._handle_lib_symlinking(pkg_store_path, gen_lib64_dir, lib64_path)

        return base_path_str
    
    def _link_wrappers_to_bin(self, hash_str: str, target_dir: Path):
        wrapper_root = WRAPPER_DIR / hash_str
        target_dir.mkdir(parents=True, exist_ok=True)
        
        if not wrapper_root.exists():
            return

        for entry in wrapper_root.rglob('*'):
            if entry.is_file():
                symlink_path = target_dir / entry.name
                try:
                    if symlink_path.exists() or symlink_path.is_symlink():
                        symlink_path.unlink()
                        
                    os.symlink(entry, symlink_path)
                    self.logger.debug(f"Linked: {symlink_path} -> {entry}")
                except OSError as e:
                    self.logger.error(f"Failed to create symlink for {entry.name}: {e}")

    def _handle_lib_symlinking(self, pkg_store_path: Path, gen_lib_dir: Path, isolated_lib: str):
        pkg_lib_source = pkg_store_path / isolated_lib
        
        if pkg_lib_source.exists() and pkg_lib_source.is_dir():
            for lib_file in pkg_lib_source.iterdir():
                dst_path = gen_lib_dir / lib_file.name
                
                if dst_path.exists() or dst_path.is_symlink():
                    if dst_path.is_dir() and not dst_path.is_symlink():
                        shutil.rmtree(dst_path)
                    else:
                        dst_path.unlink()
                        
                os.symlink(lib_file, dst_path)

    def _atomic_switch(self, manifest: GenManifest):
        gen_path = GenPath.base(manifest.timestamp_id)
        gen_root_path = GenPath.root(manifest.timestamp_id)
        self.logger.info(f"Flipping the global symlink to {gen_path}...")

        ACTIVE_LINK.parent.mkdir(parents=True, exist_ok=True)

        if ACTIVE_LINK.exists() and ACTIVE_LINK.is_dir() and not ACTIVE_LINK.is_symlink():
            self.logger.warning(f"{ACTIVE_LINK} is a directory, not a symlink. Removing it to allow atomic switch.")
            shutil.rmtree(ACTIVE_LINK)

        temp_link = ACTIVE_LINK.with_suffix('.tmp')
        
        if temp_link.is_symlink() or temp_link.exists():
            if temp_link.is_dir() and not temp_link.is_symlink():
                shutil.rmtree(temp_link)
            else:
                temp_link.unlink()
            
        os.symlink(gen_root_path, temp_link)
        self._switch_current_manifest(manifest)
        
        os.rename(temp_link, ACTIVE_LINK)
        self.logger.info(f"Successfully switched active profile to {gen_path}")
    
    def _shutdown_processes(self, pkgs_to_remove: Set[Layer]):
        if not pkgs_to_remove:
            return

        self.logger.info(f"Shutting down processes for {len(pkgs_to_remove)} removed packages...")

        for layer in pkgs_to_remove:
            pkg_store_path = str(STORE_ROOT / layer.h)
            
            self.logger.debug(f"Sending SIGTERM to processes containing: {pkg_store_path}")
            subprocess.run(["pkill", "-TERM", "-f", pkg_store_path], check=False)

        time.sleep(2)

        for layer in pkgs_to_remove:
            pkg_store_path = str(STORE_ROOT / layer.h)
            
            res = subprocess.run(["pgrep", "-f", pkg_store_path], capture_output=True, text=True)
            if res.stdout.strip():
                self.logger.warning(f"Force killing stubborn processes for {pkg_store_path}")
                subprocess.run(["pkill", "-KILL", "-f", pkg_store_path], check=False)

    def _activate_processes(self, pkgs_to_add: Set[Layer], gen_bin_dir: Path):
        if not pkgs_to_add:
            return
            
        self.logger.info(f"Scanning {len(pkgs_to_add)} new packages for background services...")

        for pkg in pkgs_to_add:
            init_dir = STORE_ROOT / pkg.h / INIT_D_REL_PATH
            
            if not init_dir.exists():
                continue

            for service_script in init_dir.iterdir():
                if service_script.name in ["README", "skeleton", "functions"]:
                    continue

                wrapper_path = gen_bin_dir / service_script.name
                
                if wrapper_path.exists():
                    self.logger.info(f"Starting new background service: {service_script.name}")
                    
                    try:
                        subprocess.Popen(
                            [str(wrapper_path)],
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                            start_new_session=True 
                        )
                    except Exception as e:
                        self.logger.error(f"Failed to start service {service_script.name}: {e}")