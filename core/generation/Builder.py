import json
import time
import shutil

from config import *
from utils import GenManifest, Layer, HealthInfo

class GenerationBuilder:
    def __init__(self, manifest_dir, store):
        self.manifest_dir = manifest_dir
        self.store = store

    def initialize_system(self, base_layer_path: str):
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
        
        gen0 = GenManifest(
            id=timestamp,
            gen_number=0,
            prev_id=None,
            roots=["base-os=1.0"],
            active_layers=[Layer(h=base_layer_path, p=0)], # Base usually has priority 0
            relations={base_layer_path: {}},
            active=True,
            health=HealthInfo(status="healthy", logs="Initial System Creation")
        )

        filename = f"gen_{timestamp}.json"
        filepath = os.path.join(GEN_ROOT, filename)
        
        with open(filepath, "w") as f:
            f.write(gen0.to_json())

        # Point 'current.json' to Gen 0
        if os.path.exists(CURRENT_MANIFEST_LINK):
            os.remove(CURRENT_MANIFEST_LINK)
        
        os.symlink(filename, CURRENT_MANIFEST_LINK)
        print(f"[+] System Ready. Current Gen: {timestamp}")

    def get_current_manifest(self) -> GenManifest:
        """Always points to the symlink."""
        if not os.path.exists(CURRENT_MANIFEST_LINK):
            raise RuntimeError("System not initialized!")
            
        with open(CURRENT_MANIFEST_LINK, "r") as f:
            data = json.load(f)
            return GenManifest.from_dict(data)

    def create_new_gen(self, to_add: List[str] = None, to_rm: List[str] = None):
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
        new_gen.gen_number += 1
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

        return new_gen
    