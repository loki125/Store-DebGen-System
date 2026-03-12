import json
import time
import shutil
import subprocess
from pprint import pformat

from config import *
from .store import Store
from .health import Health
from .utils import GenManifest, Layer, HealthInfo

class Generation:
    def __init__(self, store : Store):
        self.store = store
        self.logger = logging.getLogger(self.__class__.__name__)

    def initialize_system(self):
        """Creates the first generation if it doesn't exist."""
        
        # Create the environment
        if not os.path.exists(GEN_ROOT):
            print(f"Initializing DDLS directory at {GEN_ROOT}")
            os.makedirs(GEN_ROOT, exist_ok=True)
        
        # Since this modifies system files, it requires root/sudo
        try:
            if os.path.exists("/etc/profile.d"):
                if not os.path.exists(PROFILE_SCRIPT_PATH):
                    self.logger.info(f"Adding DDLS to system environment via {PROFILE_SCRIPT_PATH}")
                    with open(PROFILE_SCRIPT_PATH, "w") as f:
                        f.write("# DDLS Package Manager Environment\n")
                        f.write(EXPORTS)
            else:
                # Fallback: Safely append to /etc/profile directly if profile.d doesn't exist
                profile_path = "/etc/profile"
                with open(profile_path, "r") as f:
                    profile_content = f.read()
                
                # Check so we don't append it 100 times if the script runs multiple times
                if "/var/store/active/bin" not in profile_content:
                    self.logger.info("Appending DDLS Environment to /etc/profile")
                    with open(profile_path, "a") as f:
                        f.write("\n# DDLS Package Manager Environment\n")
                        f.write(EXPORTS)

        except PermissionError:
            self.logger.warning("Permission denied: Could not update profiles. Ensure you run this as root!")
            raise

        # Check if we already have a current generation
        if os.path.exists(CURRENT_MANIFEST_LINK):
            return

        # Create The Generation Foundation
        timestamp = int(time.time())
        
        return GenManifest(
            timestamp_id=timestamp,
            prev_id=None,
            roots=[],
            active_layers=[], # Base usually has priority 0
            relations={},
            active=True,
            health=HealthInfo(status="healthy", logs="Initial System Creation")
        )

    def get_current_manifest(self) -> GenManifest:
        """Always points to the symlink."""
        if not os.path.exists(CURRENT_MANIFEST_LINK):
            return self.initialize_system()
            
        with open(CURRENT_MANIFEST_LINK, "r") as f:
            data = json.load(f)
            return GenManifest.from_dict(data)

    def create_manifest(self, to_add: List[str] = None, to_rm: List[str] = None) -> Tuple[GenManifest, GenManifest]:
        """
        Main entry point for changing the system state.
        Global Priority Algorithem:

        - find current manifest and copy it
        remove_pkgs = []

        for pkg each remove_pkgs
            for each relation in relations of pkg
                relation.pkg priority - relation.pkg isolated_priority
                delete relation

            if relation.pkg priority < 0 -> remove it and add the relationed pkg to remove_pkgs

        add_pkgs = []
        mounts_added = []

        for each add_pkgs
            get pkg recipe
            for each mount in mout_instructions
                if mount not exist :
                add him with global priority 0
                get mount recipy mout_instructions and add them to his relations

            add to global priority the isolated priority 
                
        """
        current: GenManifest = self.get_current_manifest()
        new_gen: GenManifest = current
        
        new_gen.prev_id = current.timestamp_id
        new_gen.timestamp_id = int(time.time())
        new_gen.active = False

        # REMOVE LOGIC
        if to_rm:
            # The queue starts with the hashes the user explicitly wants gone
            remove_queue = to_rm.copy()
            
            while remove_queue:
                target_hash = remove_queue.pop(0)
                
                # If this package exists in our relations map (it has dependencies)
                if target_hash in new_gen.relations:
                    # Look at every dependency this package points to
                    for dep_hash, isolated_p in list(new_gen.relations[target_hash].items()):
                        
                        # Find the dependency's layer to update its global priority
                        dep_layer = next((l for l in new_gen.active_layers if l.h == dep_hash), None)
                        
                        if dep_layer:
                            # Subtract the isolated weight from the global priority
                            dep_layer.p -= isolated_p
                            
                            # Delete the relation link
                            del new_gen.relations[target_hash][dep_hash]

                            # IF PRIORITY <= 0: The package is an orphan. 
                            # Add it to the queue to clean up ITS dependencies.
                            if dep_layer.p <= 0:
                                remove_queue.append(dep_hash)

                # Finally, remove the package layer from the manifest
                new_gen.active_layers = [l for l in new_gen.active_layers if l.h != target_hash]
                if target_hash in new_gen.relations:
                    del new_gen.relations[target_hash]
                
                # Remove from roots if it was there
                new_gen.roots = [r for r in new_gen.roots if r != target_hash]

        # ADDITION LOGIC
        if to_add:
            add_queue = to_add.copy()
            
            while add_queue:
                current_hash = add_queue.pop(0)
                
                # Get the recipe directly via hash_path
                recipe = self.store.get_recipe(current_hash)
                if not recipe: 
                    print(f"[!] Skip: Recipe for {current_hash} not found in store.")
                    continue
                
                # Ensure the layer exists in the new generation
                pkg_layer = next((l for l in new_gen.active_layers if l.h == current_hash), None)
                if not pkg_layer:
                    # Initialize with global priority 0 (it will be boosted by relations)
                    # Or 1000 if it's a Top-Level requested package
                    prio = 1000 if current_hash in to_add else 0
                    pkg_layer = Layer(h=current_hash, p=prio)
                    new_gen.active_layers.append(pkg_layer)

                if current_hash not in new_gen.relations:
                    new_gen.relations[current_hash] = {}

                # Process the "mount_instructions" (The dependencies)
                # Assumes structure: recipe['mount_instructions']['required_mounts']
                # Which is a list of { "hash": "...", "isolated_priority": int }
                mounts = recipe.get("mount_instructions", {}).get("required_mounts", [])
                
                for mount in mounts:
                    dep_hash = mount["hash"]
                    isolated_p = mount["isolated_priority"]

                    # 1. If the dependency layer doesn't exist, create it and queue it
                    dep_layer = next((l for l in new_gen.active_layers if l.h == dep_hash), None)
                    if not dep_layer:
                        dep_layer = Layer(h=dep_hash, p=0)
                        new_gen.active_layers.append(dep_layer)
                        # Queue this dependency to find its own children
                        add_queue.append(dep_hash)

                    # 2. Link them in the relations map if not already linked
                    if dep_hash not in new_gen.relations[current_hash]:
                        new_gen.relations[current_hash][dep_hash] = isolated_p
                        
                        # 3. Add to the dependency's global priority
                        dep_layer.p += isolated_p

        return current, new_gen
    
    def _calculate_diff(self, old_manifest: GenManifest, new_manifest: GenManifest) -> Tuple[Set[str], Set[str]]:
        self.logger.info("Calculating differences...")
        
        # Get names from 'name=version' format
        old_pkgs = set(r.split("=")[0] for r in old_manifest.roots)
        new_pkgs = set(r.split("=")[0] for r in new_manifest.roots)

        return old_pkgs - new_pkgs, new_pkgs - old_pkgs
    
    def execute(self, new_manifest: GenManifest, current_manifest: GenManifest, overwrite_flag: bool=False) -> bool:
        """
        The Master Workflow: Orchestrates the move from one generation to the next.
        """
        self.logger.info(f"=== STARTING TRANSITION: Gen {current_manifest.timestamp_id} -> Gen {new_manifest.timestamp_id} ===")
        healther = Health()
        new_path = None
        
        try:
            # 1. Create the new gen
            new_path = self._create_new_gen(new_manifest)

            # 2. Verify Health
            if not healther.gen_health(new_path):
                raise Exception(f"New generation is broken. Aborting.\n{pformat(healther.report, indent=4)}")

            # 3. Calculate Diff
            to_remove, to_add = self._calculate_diff(current_manifest, new_manifest)
            current_path = os.path.join(GEN_ROOT, str(current_manifest.timestamp_id))

            # 4. The Atomic Switch
            self._atomic_switch(new_path)

            # 5. Shutdown Old (Pre-Flight)
            self._shutdown_services(to_remove, current_path)

            # 6. Activate New (Startup)
            self._activate_services(to_add, new_path)

            # 7. Cleanup Old
            if overwrite_flag:
                self.store.reset_target(current_path)

            self.logger.info("=== TRANSITION COMPLETE SUCCESSFULLY ===")
        except Exception as e:
            if new_path is not None:
                self.store.reset_target(new_path)

            self.logger.error(f"Failed to create new generation:\n{e}")
            return False
        return True
    
    def _create_new_gen(self, new_manifest: GenManifest) -> str:
        gen_path = os.path.join(GEN_ROOT, str(new_manifest.timestamp_id)) # e.g., /var/store/generations/12345
        gen_bin_dir = os.path.join(gen_path, "bin")
        gen_lib_dir = os.path.join(gen_path, "lib") 
        
        os.makedirs(gen_bin_dir, exist_ok=True)
        os.makedirs(gen_lib_dir, exist_ok=True)
        self.logger.info(f"Building Generation Forest at {gen_path}")
        
        for layer in new_manifest.active_layers:
            pkg_store_path = layer.h

            # Handle BIN 
            pkg_bin_source = os.path.join(pkg_store_path, "user/bin") 
            if not os.path.exists(pkg_bin_source):
                continue

            src, dst = self._generate_wrapper_script(pkg_store_path, pkg_bin_source, gen_bin_dir)
            os.symlink(src, dst)

            # Handle LIB 
            pkg_lib_source = os.path.join(pkg_store_path, "user/lib")
            if os.path.exists(pkg_lib_source):
                for lib_file in os.listdir(pkg_lib_source):
                    src_path = os.path.join(pkg_lib_source, lib_file)
                    dst_path = os.path.join(gen_lib_dir, lib_file)
                    
                    # If a lower-priority package already put this lib here, remove it
                    if os.path.exists(dst_path) or os.path.islink(dst_path):
                        os.remove(dst_path)                        
                    os.symlink(src_path, dst_path)

        return gen_path

    def _generate_wrapper_script(self, store_path: str, bin_src: str, gen_bin_dir: str) -> Tuple[str, str]:
        """
        store_path: the path of the package in the store
        bin_src: the full path to the executable in the package
        gen_bin_dir: the bin directory of the generation where the symlink will live

        returns (wrapper_path, symlink_destination_path)
        """
        bin_name = os.path.basename(bin_src)
        pkg_name = os.path.basename(store_path)
        gen_path = os.path.dirname(gen_bin_dir)
        
        # Setup the wrappers output directory
        wrappers_dir = os.path.join(gen_path, "wrappers")
        os.makedirs(wrappers_dir, exist_ok=True)
        
        wrapper_path = os.path.join(wrappers_dir, f"{pkg_name}_{bin_name}")
        symlink_dst = os.path.join(gen_bin_dir, bin_name)
        
        # Read the literal .sh template
        with open(PACKAGE_WRAPPER_PATH, "r") as f:
            script_content = f.read()

        # 2. Inject the variables ("biting" into the template)
        script_content = script_content.replace("@@PKG_NAME@@", pkg_name)
        script_content = script_content.replace("@@BIN_NAME@@", bin_name)
        script_content = script_content.replace("@@FOREST_PATH@@", gen_path)
        script_content = script_content.replace("@@BIN_SRC@@", bin_src)

        # 3. Write out the customized wrapper script
        with open(wrapper_path, "w") as f:
            f.write(script_content)
            
        # 4. Make it executable
        os.chmod(wrapper_path, 0o755)
        
        return wrapper_path, symlink_dst

    def _atomic_switch(self, gen_path: str):
        self.logger.info(f"Flipping the global symlink to {gen_path}...")

        parent_dir = os.path.dirname(ACTIVE_LINK)
        os.makedirs(parent_dir, exist_ok=True)

        if os.path.exists(ACTIVE_LINK) and os.path.isdir(ACTIVE_LINK) and not os.path.islink(ACTIVE_LINK):
            self.logger.warning(f"{ACTIVE_LINK} is a directory, not a symlink. Removing it to allow atomic switch.")
            shutil.rmtree(ACTIVE_LINK)

        # Prepare the temporary symlink
        temp_link = f"{ACTIVE_LINK}.tmp"
        
        # Clean up old temp link if it exists (handles both files and directories)
        if os.path.islink(temp_link) or os.path.exists(temp_link):
            if os.path.isdir(temp_link) and not os.path.islink(temp_link):
                shutil.rmtree(temp_link)
            else:
                os.remove(temp_link)
            
        os.symlink(gen_path, temp_link)
        
        # Atomically move the temp link over the active link
        # os.rename is guaranteed to be an atomic operation on POSIX systems.
        os.rename(temp_link, ACTIVE_LINK)
        
        self.logger.info(f"Successfully switched active profile to {gen_path}")
    
    def _shutdown_services(self, pkgs_to_remove: Set[str], old_mount_point: str):
        self.logger.info("Shutting down old services...")
        
        for pkg in pkgs_to_remove:
            # 1. Stop live systemd service
            self.logger.info(f"  - Stopping service: {pkg}")
            subprocess.run(["systemctl", "stop", pkg], check=False, capture_output=True)
            
            # 2. Run prerm script in the context of the OLD mount
            prerm_rel_path = f"var/lib/dpkg/info/{pkg}.prerm"
            script_path = os.path.join(old_mount_point, prerm_rel_path)
            
            if os.path.exists(script_path):
                self.logger.info(f"  - Executing prerm for {pkg}")
                # We chroot so the script sees the environment it was built for
                subprocess.run(["chroot", old_mount_point, f"/{prerm_rel_path}"], check=False)

    
    def _activate_services(self, pkgs_to_add: Set[str], new_mount_point: str):
        self.logger.info("Activating new services...")
        
        # Tell systemd to look for new unit files in the updated /system/current
        subprocess.run(["systemctl", "daemon-reload"], check=False)
        
        for pkg in pkgs_to_add:
            # Check for systemd service file existence in new mount
            service_rel_path = f"usr/lib/systemd/system/{pkg}.service"
            unit_file = os.path.join(new_mount_point, service_rel_path)
            
            if os.path.exists(unit_file):
                self.logger.info(f"  - Starting new service: {pkg}")
                subprocess.run(["systemctl", "start", pkg], check=False)

    
