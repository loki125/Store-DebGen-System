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
from .utils import GenManifest, Layer, HealthInfo, GenPath

class Generation:
    """
    @brief Manages the lifecycle of system generations, including manifest creation and activation.
    """
    def __init__(self, store: Store):
        """
        @brief Initializes the Generation manager with a reference to the package store.
        @param store The Store instance used to access package metadata.
        """
        self.store = store
        self.logger = logging.getLogger(self.__class__.__name__)

    def initialize_system(self) -> Optional[GenManifest]:
        """
        @brief Creates the initial system generation manifest if one does not exist.
        @return A new GenManifest instance or None if a current generation already exists.
        """
        if not GEN_DIR.exists():
            self.logger.info(f"Initializing DDLS directory at {GEN_DIR}")
            GEN_DIR.mkdir(parents=True, exist_ok=True)

        if CURRENT_LINK.exists():
            return None
        
        return GenManifest(
            prev_id=None,
            active_layers=[], 
            relations={},
            active=True,
            health=HealthInfo(status=GEN_INIT_STATUS, logs=GEN_INIT_LOGS)
        )

    def get_current_manifest(self) -> GenManifest:
        """
        @brief Retrieves the manifest of the currently active system generation.
        @return The GenManifest representing the current system state.
        """
        if not CURRENT_LINK.exists():
            return self.initialize_system()
            
        with open(CURRENT_LINK, "r") as f:
            data = json.load(f)
            return GenManifest.from_dict(data)

    def _switch_current_manifest(self, new_manifest: GenManifest):
        """
        @brief Persists a new manifest and updates the global 'current' symlink.
        @param new_manifest The manifest to be promoted to current.
        """
        target = Path(GenPath.manifest(new_manifest.timestamp_id))

        with open(target, "w") as f:
            f.write(new_manifest.to_json())

        if CURRENT_LINK.exists() or CURRENT_LINK.is_symlink():
            CURRENT_LINK.unlink()

        os.symlink(target, CURRENT_LINK)

    def create_manifest(self, to_add: List[Path] = None, to_rm: List[Path] = None) -> Tuple[GenManifest, GenManifest]:
        """
        @brief Calculates a new generation manifest based on requested additions and removals.
        @param to_add List of package paths to add to the generation.
        @param to_rm List of package paths to remove from the generation.
        @return A tuple containing the (new_manifest, current_manifest).
        """
        current: GenManifest = self.get_current_manifest()
        new_gen: GenManifest = copy.deepcopy(current)
        
        new_gen.prev_id = current.timestamp_id
        new_gen.active = False

        # REMOVE LOGIC
        if to_rm:
            remove_queue : List[Path] = to_rm.copy()
            
            while remove_queue:
                target_hash = remove_queue.pop(0).name
                
                if target_hash in new_gen.relations:
                    for dep_hash, isolated_p in list(new_gen.relations[target_hash].items()):
                        dep_layer = next((l for l in new_gen.active_layers if l.h == dep_hash), None)
                        
                        if dep_layer:
                            dep_layer.p -= isolated_p
                            del new_gen.relations[target_hash][dep_hash]

                            if dep_layer.p <= 0:
                                remove_queue.append(STORE_ROOT / dep_hash)

                new_gen.active_layers = [l for l in new_gen.active_layers if l.h != target_hash]
                if target_hash in new_gen.relations:
                    del new_gen.relations[target_hash]

        # ADDITION LOGIC
        if to_add:
            add_queue : List[Path] = to_add.copy()
            
            while add_queue:
                current_hash = add_queue.pop(0).name
                
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
                        add_queue.append(STORE_ROOT / dep_hash)

                    if dep_hash not in new_gen.relations[current_hash]:
                        new_gen.relations[current_hash][dep_hash] = isolated_p
                        dep_layer.p += isolated_p

        self.logger.debug(pformat(new_gen.to_json(), indent=4))
        return new_gen, current
        
    def _calculate_diff(self, old_manifest: GenManifest, new_manifest: GenManifest) -> Tuple[Set[Layer], Set[Layer]]:
            """
            @brief Determines the sets of layers added and removed between two manifests.
            @param old_manifest The starting manifest state.
            @param new_manifest The target manifest state.
            @return A tuple containing (added_layers_set, removed_layers_set).
            """
            self.logger.info("Calculating differences...")
            
            old_map = {l.h: l for l in old_manifest.active_layers}
            new_map = {l.h: l for l in new_manifest.active_layers}
            
            added_hashes = set(new_map.keys()) - set(old_map.keys())
            removed_hashes = set(old_map.keys()) - set(new_map.keys())
            
            to_add = {new_map[h] for h in added_hashes}
            to_remove = {old_map[h] for h in removed_hashes}
            
            return to_add, to_remove

    def execute(self, new_manifest: GenManifest, current_manifest: GenManifest, overwrite_flag: bool = False) -> bool:
        """
        @brief Performs the actual transition between generations by setting up the forest and flipping links.
        @param new_manifest The manifest to transition to.
        @param current_manifest The manifest currently in use.
        @param overwrite_flag If True, deletes the old generation directory upon success.
        @return True if the transition was successful, False otherwise.
        """
        self.logger.info(f"=== STARTING TRANSITION: Gen {current_manifest.timestamp_id} -> Gen {new_manifest.timestamp_id} ===")
        new_path = None
        
        try:
            new_path = self._create_new_gen(new_manifest)

            to_add, to_remove = self._calculate_diff(current_manifest, new_manifest)
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
        """
        @brief Populates the directory structure for a new generation with binary and library symlinks.
        @param new_manifest The manifest describing the generation to build.
        @return The base path string of the newly created generation.
        """
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

            self._link_wrappers_to_bin(layer.h, gen_bin_dir)
            for lib_paths in DEV_PATHS:
                for lib_path in lib_paths:
                    self._handle_lib_symlinking(pkg_store_path, gen_lib_dir, lib_path)
                
            for lib64_path in LIB64_PATHS:  
                self._handle_lib_symlinking(pkg_store_path, gen_lib64_dir, lib64_path)

        return str(base_path_str)

    def _link_wrappers_to_bin(self, hash_str: str, target_dir: Path):
        """
        @brief Scans a package's wrapper directory and symlinks executable wrappers into the generation bin.
        @param hash_str The hash identifier of the package.
        @param target_dir The target bin directory for symlinks.
        """
        wrapper_root = WRAPPER_DIR / hash_str
        target_dir.mkdir(parents=True, exist_ok=True)
        
        if not wrapper_root.exists():
            return
        
        for entry in wrapper_root.rglob(GLOB_ALL):
            if entry.is_file() and os.access(entry, os.X_OK):
                symlink_path = target_dir / entry.name
                try:
                    if symlink_path.exists() or symlink_path.is_symlink():
                        if symlink_path.is_dir() and not symlink_path.is_symlink():
                            shutil.rmtree(symlink_path)
                        else:
                            symlink_path.unlink()
                        
                    os.symlink(entry, symlink_path)
                    self.logger.debug(f"Linked wrapper: {symlink_path} -> {entry}")
                except OSError as e:
                    self.logger.error(f"Failed to create wrapper symlink for {entry.name}: {e}")

    def _handle_lib_symlinking(self, pkg_store_path: Path, gen_lib_dir: Path, isolated_lib: str):
        """
        @brief Symlinks libraries from a package store path into the generation library directory.
        @param pkg_store_path Path to the package in the store.
        @param gen_lib_dir Target generation library directory.
        @param isolated_lib The specific library subdirectory to link.
        """
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
        """
        @brief Atomically updates the active system profile symlink.
        @param manifest The manifest to switch to.
        """
        gen_path = GenPath.base(manifest.timestamp_id)
        gen_root_path = GenPath.root(manifest.timestamp_id)
        self.logger.info(f"Flipping the global symlink to {gen_path}...")

        ACTIVE_LINK.parent.mkdir(parents=True, exist_ok=True)

        if ACTIVE_LINK.exists() and ACTIVE_LINK.is_dir() and not ACTIVE_LINK.is_symlink():
            self.logger.warning(f"{ACTIVE_LINK} is a directory, not a symlink. Removing it to allow atomic switch.")
            shutil.rmtree(ACTIVE_LINK)

        temp_link = ACTIVE_LINK.with_suffix(EXT_TMP)
        
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
        """
        @brief Signals processes belonging to removed packages to terminate.
        @param pkgs_to_remove Set of layers (packages) being removed.
        """
        if not pkgs_to_remove:
            return

        self.logger.info(f"Shutting down processes for {len(pkgs_to_remove)} removed packages...")

        for layer in pkgs_to_remove:
            pkg_store_path = str(STORE_ROOT / layer.h)
            
            self.logger.debug(f"Sending SIGTERM to processes containing: {pkg_store_path}")
            subprocess.run([PKILL_CMD, SIGTERM_FLAG, PROC_MATCH_FLAG, pkg_store_path], check=False)

        time.sleep(SHUTDOWN_WAIT_TIME)

        for layer in pkgs_to_remove:
            pkg_store_path = str(STORE_ROOT / layer.h)
            
            res = subprocess.run([PGREP_CMD, PROC_MATCH_FLAG, pkg_store_path], capture_output=True, text=True)
            if res.stdout.strip():
                self.logger.warning(f"Force killing stubborn processes for {pkg_store_path}")
                subprocess.run([PKILL_CMD, SIGKILL_FLAG, PROC_MATCH_FLAG, pkg_store_path], check=False)

    def _activate_processes(self, pkgs_to_add: Set[Layer], gen_bin_dir: Path):
        """
        @brief Scans for background service scripts in new packages and starts them.
        @param pkgs_to_add Set of layers (packages) being added.
        @param gen_bin_dir Path to the generation bin directory for finding executable wrappers.
        """
        if not pkgs_to_add:
            return
            
        self.logger.info(f"Scanning {len(pkgs_to_add)} new packages for background services...")

        for pkg in pkgs_to_add:
            init_dir = STORE_ROOT / pkg.h / INIT_D_REL_PATH
            
            if not init_dir.exists():
                continue

            for service_script in init_dir.iterdir():
                if service_script.name in SERVICE_IGNORE_FILES:
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