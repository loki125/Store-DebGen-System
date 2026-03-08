import json
import time
import shutil
import subprocess

from config import *
from .utils import GenManifest, Layer, HealthInfo

class Generation:
    def __init__(self, store):
        self.store = store
        self.logger = logging.getLogger(self.__class__.__name__)

    def initialize_system(self):
        """Creates the first generation if it doesn't exist."""
        
        # Create the environment
        if not os.path.exists(GEN_ROOT):
            print(f"Initializing DDLS directory at {GEN_ROOT}")
            os.makedirs(GEN_ROOT, exist_ok=True)

        # Check if we already have a current generation
        if os.path.exists(CURRENT_MANIFEST_LINK):
            return

        # Create The Generation Foundation
        timestamp = int(time.time())
        
        return GenManifest(
            id=timestamp,
            prev_id=None,
            roots=[],
            active_layers=[], # Base usually has priority 0
            relations={{}},
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

    def create_new_gen(self, to_add: List[str] = None, to_rm: List[str] = None) -> Tuple[GenManifest, GenManifest]:
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
        new_gen: GenManifest = shutil.copy.deepcopy(current)
        
        new_gen.prev_id = current.id
        new_gen.id = int(time.time())
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
    
    def execute(self, new_manifest: GenManifest, current_manifest: GenManifest) -> bool:
        """
        The Master Workflow: Orchestrates the move from one generation to the next.
        """
        self.logger.info(f"=== STARTING TRANSITION: Gen {current_manifest.id} -> Gen {new_manifest.id} ===")
        
        # 1. Mount New (Invisible to user)
        try:
            new_path = self._mount_new_gen(new_manifest)
        except Exception as e:
            self.logger.error(f"Failed to mount new generation: {e}")
            return False

        # 2. Verify Health
        if not self._verify_health(new_path):
            self.logger.error("New generation is broken. Aborting.")
            # Cleanup the failed mount
            subprocess.run(["umount", "-l", new_path])
            return False

        # 3. Calculate Diff
        to_remove, to_add = self._calculate_diff(current_manifest, new_manifest)
        current_path = os.path.join(GEN_MOUNT_BASE, str(current_manifest.id))

        # 4. Shutdown Old (Pre-Flight)
        self._shutdown_old_services(to_remove, current_path)

        # 5. The Atomic Switch
        try:
            self._atomic_switch(new_path)
        except OSError as e:
            self.logger.error(f"Atomic switch failed! Error: {e}")
            return False

        # 6. Activate New (Startup)
        self._activate_new_services(to_add, new_path)

        # 7. Cleanup Old
        self._cleanup_old_gen(current_manifest)
        
        self.logger.info("=== TRANSITION COMPLETE SUCCESSFULLY ===")
        return True
    
    
    def _mount_new_gen(self, new_manifest: GenManifest) -> str:
        mount_point = os.path.join(GEN_MOUNT_BASE, str(new_manifest.id))
        os.makedirs(mount_point, exist_ok=True)

        # Predictable Sort: Priority DESC, then Hash ASC for deterministic mounting
        sorted_layers = sorted(new_manifest.active_layers, key=lambda x: (-x.p, x.h))
        lower_dirs = ":".join([str(STORE_ROOT / layer.h) for layer in sorted_layers])

        self.logger.info(f"Mounting Gen {new_manifest.id} at {mount_point}")
        
        # overlayfs mount command
        cmd = [
            "mount", "-t", "overlay", "overlay",
            "-o", f"lowerdir={lower_dirs}",
            mount_point
        ]
        subprocess.run(cmd, check=True)
        return mount_point

    
    def _verify_health(self, mount_point: str) -> bool:
        self.logger.info(f"Verifying health of {mount_point}")
        
        # Standard essential binaries for a sane Linux environment
        essential_binaries = ["/bin/sh", "/etc", "/usr/bin"]
        
        for f in essential_binaries:
            if not os.path.exists(mount_point + f):
                self.logger.warning(f"Critical file missing in new mount: {f}")
                return False
        return True
    
    
    def _calculate_diff(self, old_manifest: GenManifest, new_manifest: GenManifest) -> Tuple[Set[str], Set[str]]:
        self.logger.info("Calculating differences...")
        
        # Get names from 'name=version' format
        old_pkgs = set(r.split("=")[0] for r in old_manifest.roots)
        new_pkgs = set(r.split("=")[0] for r in new_manifest.roots)

        return old_pkgs - new_pkgs, new_pkgs - old_pkgs
    
    
    def _shutdown_old_services(self, pkgs_to_remove: Set[str], old_mount_point: str):
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

    
    def _activate_new_services(self, pkgs_to_add: Set[str], new_mount_point: str):
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

    
    def _atomic_switch(self, new_mount_point: str):
        self.logger.info("Flipping the global symlink...")
        
        # Atomic swap using rename (standard Linux behavior)
        tmp_link = f"{CURRENT_SYSTEM_LINK}.tmp"
        
        if os.path.exists(tmp_link):
            os.remove(tmp_link)
            
        os.symlink(new_mount_point, tmp_link)
        os.rename(tmp_link, CURRENT_SYSTEM_LINK) 
        self.logger.info(f"System pointer successfully moved to {new_mount_point}")


    
    def _cleanup_old_gen(self, old_manifest: GenManifest):
        if not old_manifest:
            return
            
        old_path = os.path.join(GEN_MOUNT_BASE, str(old_manifest.id))
        self.logger.info(f"Lazy unmounting old generation at {old_path}")
        
        # -l (lazy) unmounts immediately from the tree, cleans up when files close
        subprocess.run(["umount", "-l", old_path], check=False)