import json
import shutil
import subprocess
import copy
import time
from operator import attrgetter
from pprint import pformat

from config import *
from .store import Store
from .health import Health
from .utils import GenManifest, Layer, HealthInfo, WrapperConfig

class Generation:
    def __init__(self, store : Store):
        self.store = store
        self.logger = logging.getLogger(self.__class__.__name__)

    def initialize_system(self):
        """Creates the first generation if it doesn't exist."""
        
        # Create the environment
        if not os.path.exists(GEN_DIR):
            self.logger.info(f"Initializing DDLS directory at {GEN_DIR}")
            os.makedirs(GEN_DIR, exist_ok=True)
        
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
        if os.path.exists(CURRENT_LINK):
            return
        
        return GenManifest(
            prev_id=None,
            roots=[],
            active_layers=[], # Base usually has priority 0
            relations={},
            active=True,
            health=HealthInfo(status="healthy", logs="Initial System Creation")
        )

    def get_current_manifest(self) -> GenManifest:
        """Always points to the symlink."""
        if not os.path.exists(CURRENT_LINK):
            return self.initialize_system()
            
        with open(CURRENT_LINK, "r") as f:
            data = json.load(f)
            return GenManifest.from_dict(data)

    def _switch_current_manifest(self, new_manifest : GenManifest):
        target = GenPath.manifest(new_manifest.timestamp_id)

        with open(target, "w") as f:
            f.write(new_manifest.to_json())

        if os.path.lexists(CURRENT_LINK):
            os.remove(CURRENT_LINK)

        # Create the new link
        os.symlink(target, CURRENT_LINK)


    def create_manifest(self, to_add: List[str] = None, to_rm: List[str] = None) -> Tuple[GenManifest, GenManifest]:
        """
        Main entry point for changing the system state.
        Global Priority Algorithem:

        - find current manifest and copy it
        remove_pkgs = []

        for each pkg in remove_pkgs
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
        # Pseudo: find current manifest and copy it
        current: GenManifest = self.get_current_manifest()
        new_gen: GenManifest = copy.deepcopy(current)
        
        new_gen.prev_id = current.timestamp_id
        new_gen.active = False

        # REMOVE LOGIC
        if to_rm:
            # Pseudo: remove_pkgs =[]  (Initialized here from user input)
            remove_queue = to_rm.copy()
            
            # Pseudo: for each pkg in remove_pkgs
            while remove_queue:
                target_hash = remove_queue.pop(0)
                
                if target_hash in new_gen.relations:
                    # Pseudo: for each relation in relations of pkg
                    for dep_hash, isolated_p in list(new_gen.relations[target_hash].items()):
                        dep_layer = next((l for l in new_gen.active_layers if l.h == dep_hash), None)
                        
                        if dep_layer:
                            # Pseudo: relation.pkg priority - relation.pkg isolated_priority
                            dep_layer.p -= isolated_p
                            
                            # Pseudo: delete relation
                            del new_gen.relations[target_hash][dep_hash]

                            # Pseudo: if relation.pkg priority <= 0 -> remove it and add the relationed pkg to remove_pkgs
                            if dep_layer.p <= 0:
                                remove_queue.append(dep_hash)

                # Removing the actual package itself from layers, relations, and roots
                new_gen.active_layers =[l for l in new_gen.active_layers if l.h != target_hash]
                if target_hash in new_gen.relations:
                    del new_gen.relations[target_hash]
                # new_gen.roots =[r for r in new_gen.roots if r != target_hash]

        # ADDITION LOGIC
        if to_add:
            # Pseudo: add_pkgs =[] (Initialized here from user input)
            add_queue = to_add.copy()
            
            # Pseudo: for each add_pkgs
            while add_queue:
                current_hash = add_queue.pop(0)
                
                # Pseudo: get pkg recipe
                recipe : Dict = self.store.get_recipe(STORE_ROOT / current_hash)
                if not recipe: 
                    self.logger.warning(f"Skip: Recipe for {current_hash} not found in store.")
                    continue
                
                # Ensure the current package layer exists in the manifest
                pkg_layer = next((l for l in new_gen.active_layers if l.h == current_hash), None)
                if not pkg_layer:
                    prio = 0
                    pkg_layer = Layer(h=current_hash, p=prio)
                    new_gen.active_layers.append(pkg_layer)

                if current_hash not in new_gen.relations:
                    new_gen.relations[current_hash] = {}

                mounts : Dict = recipe.get("mount_instructions")
                if not mounts:
                    continue

                required_mounts : List = mounts["required_mounts"]
                
                # Pseudo: for each mount in mout_instructions
                for isolated_p, dep_hash in enumerate(required_mounts):

                    dep_layer = next((l for l in new_gen.active_layers if l.h == dep_hash), None)
                    
                    # Pseudo: if mount not exist : add him with global priority 0
                    if not dep_layer:
                        dep_layer = Layer(h=dep_hash, p=0)
                        new_gen.active_layers.append(dep_layer)
                        
                        # Pseudo: get mount recipy mout_instructions and add them to his relations
                        # (Done by adding to queue, which loops around to fetch the recipe)
                        add_queue.append(dep_hash)

                    if dep_hash not in new_gen.relations[current_hash]:
                        new_gen.relations[current_hash][dep_hash] = isolated_p
                        
                        # Pseudo: add to global priority the isolated priority 
                        dep_layer.p += isolated_p

        pformat(new_gen.to_json(), indent=4)
        return current, new_gen
    
    def _calculate_diff(self, old_manifest: GenManifest, new_manifest: GenManifest) -> Tuple[Set[Layer], Set[Layer]]:
        self.logger.info("Calculating differences...")
        
        # Get hashes
        old_pkgs = set(sorted(old_manifest.active_layers, key=attrgetter('p'), reverse=True))
        new_pkgs = set(sorted(new_manifest.active_layers, key=attrgetter('p'), reverse=True))

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
            current_path = os.path.join(GEN_DIR, str(current_manifest.timestamp_id))

            # 4. Shutdown Old (Pre-Flight)
            self._shutdown_processes(to_remove)

            # 5. The Atomic Switch
            self._atomic_switch(new_manifest)

            # 6. Activate New (Startup)
            self._activate_processes(to_add, GenPath.root_bin(new_manifest.timestamp_id))

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
        manifest_id = new_manifest.timestamp_id
        gen_bin_dir = GenPath.root_bin(manifest_id)
        gen_lib_dir = GenPath.root_lib(manifest_id)
        gen_lib64_dir = GenPath.root_lib64(manifest_id)
        
        os.makedirs(gen_bin_dir, exist_ok=True)
        os.makedirs(gen_lib_dir, exist_ok=True)
        os.makedirs(gen_lib64_dir, exist_ok=True)
        self.logger.info(f"Building Generation Forest at {GenPath.base(manifest_id)}")
        
        sorted_layers = sorted(new_manifest.active_layers, key=attrgetter('p'), reverse=True)
        for layer in sorted_layers:
            pkg_store_path = layer.h

            # Handle BIN 
            for bin_path in BIN_PATHS:
                pkg_bin_source = os.path.join(pkg_store_path, bin_path) 
                if not os.path.exists(pkg_bin_source):
                    continue

                src, dst = self._generate_wrapper_script(pkg_store_path, pkg_bin_source, gen_bin_dir)
                if os.path.lexists(dst) or os.path.islink(dst):
                        os.remove(dst) 
                os.symlink(src, dst)

            # Handle LIB and Lib64
            for lib_path in LIB_PATHS:
                self._handle_lib_symlinking(pkg_store_path, gen_lib_dir, lib_path)
                
            for lib64_path in LIB64_PATHS:  
                self._handle_lib_symlinking(pkg_store_path, gen_lib64_dir, lib64_path)

        return GenPath.base(manifest_id)
    
    @staticmethod
    def _handle_lib_symlinking(pkg_store_path, gen_lib_dir, isolated_lib):
        pkg_lib_source = os.path.join(pkg_store_path, isolated_lib)
        if os.path.exists(pkg_lib_source):
            for lib_file in os.listdir(pkg_lib_source):
                src_path = os.path.join(pkg_lib_source, lib_file)
                dst_path = os.path.join(gen_lib_dir, lib_file)
                
                # If a lower-priority package already put this lib here, remove it
                if os.path.lexists(dst_path) or os.path.islink(dst_path):
                    os.remove(dst_path)                        
                os.symlink(src_path, dst_path)

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
        context = WrapperConfig(
            pkg_name=pkg_name,
            bin_name=bin_name,
            gen_path=gen_path,
            bin_src=bin_src
        )
        
        with open(PACKAGE_WRAPPER_PATH, "r") as f:
            template = f.read()

        # Using .format(**dict) allows you to pass all variables at once
        with open(wrapper_path, "w") as f:
            f.write(template.format(**(context.to_dict())))
            
        # 4. Make it executable
        os.chmod(wrapper_path, 0o755)
        
        return wrapper_path, symlink_dst

    def _atomic_switch(self, manifest: GenManifest):
        gen_path = GenPath.base(manifest.timestamp_id)
        gen_root_path = GenPath.root(manifest.timestamp_id)
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
            
        os.symlink(gen_root_path, temp_link)
        
        # create and switch to new gen's manifest
        self._switch_current_manifest(manifest)

        # Atomically move the temp link over the active link
        os.rename(temp_link, ACTIVE_LINK)

        self.logger.info(f"Successfully switched active profile to {gen_path}")
    
    def _shutdown_processes(self, pkgs_to_remove: Set[Layer]):
        """
        Gracefully terminates all processes associated with removed packages.
        """
        if not pkgs_to_remove:
            return

        self.logger.info(f"Shutting down processes for {len(pkgs_to_remove)} removed packages...")

        # Step 1: Send SIGTERM (Graceful Shutdown)
        for layer in pkgs_to_remove:
            pkg_store_path = STORE_ROOT / layer.h  # e.g., /var/lib/.../store/hash-pkg-version
            
            # pkill -f matches the full execution string. 
            # Because your path has a unique hash, this will NEVER kill the wrong app!
            self.logger.debug(f"Sending SIGTERM to processes containing: {pkg_store_path}")
            subprocess.run(["pkill", "-TERM", "-f", pkg_store_path], check=False)

        # Step 2: Give processes a moment to save data and close databases
        time.sleep(2)

        # Step 3: Send SIGKILL (Force Shutdown) to stubborn processes
        for layer in pkgs_to_remove:
            pkg_store_path = layer.h
            
            # Check if any survived
            res = subprocess.run(["pgrep", "-f", pkg_store_path], capture_output=True, text=True)
            if res.stdout.strip():
                self.logger.warning(f"Force killing stubborn processes for {pkg_store_path}")
                subprocess.run(["pkill", "-KILL", "-f", pkg_store_path], check=False)


    def _activate_processes(self, pkgs_to_add: Set[Layer], gen_bin_dir: str):
        """
        Detects and starts background daemons for newly added packages.
        """
        if not pkgs_to_add:
            return
            
        self.logger.info(f"Scanning {len(pkgs_to_add)} new packages for background services...")

        for pkg in pkgs_to_add:
            pkg_store_path = STORE_ROOT / pkg.h
            # How do we know if a package is a "Service" and not just an "App"?
            # In Debian, services put initialization scripts in /etc/init.d/
            init_dir = os.path.join(pkg_store_path, "etc", "init.d")
            
            if not os.path.exists(init_dir):
                continue # It's just a normal app (like 'tree'), nothing to background

            # It's a daemon! Let's start it.
            for service_script in os.listdir(init_dir):
                # Ignore standard Debian boilerplate files
                if service_script in ["README", "skeleton", "functions"]:
                    continue

                wrapper_path = os.path.join(gen_bin_dir, service_script)
                
                # If we generated a wrapper for this daemon in the /bin directory:
                if os.path.exists(wrapper_path):
                    self.logger.info(f"Starting new background service: {service_script}")
                    
                    try:
                        # start_new_session=True detaches it from the Python script
                        # so it doesn't die when Python finishes the generation switch.
                        subprocess.Popen(
                            [wrapper_path],
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                            start_new_session=True 
                        )
                    except Exception as e:
                        self.logger.error(f"Failed to start service {service_script}: {e}")

    
