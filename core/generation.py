import shutil
import json
import time
import copy

from .health import HealthChecker
from config import *
from utils import GenManifest, Layer, HealthInfo

class GenerationBuilder:
    def __init__(self, manifest_dir, store):
        self.manifest_dir = manifest_dir
        self.store = store

    def initialize_system(self, base_layer_path: str):
        """Creates the first generation if it doesn't exist."""
        
        # Create the environment
        if not os.path.exists(self.gen_dir):
            print(f"Initializing DDLS directory at {self.gen_dir}")
            os.makedirs(self.gen_dir, exist_ok=True)

        # Check if we already have a current generation
        if os.path.exists(self.current_link):
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
        filepath = os.path.join(self.gen_dir, filename)
        
        with open(filepath, "w") as f:
            f.write(gen0.to_json())

        # Point 'current.json' to Gen 0
        if os.path.exists(self.current_link):
            os.remove(self.current_link)
        
        os.symlink(filename, self.current_link)
        print(f"[+] System Ready. Current Gen: {timestamp}")

    def get_current_manifest(self) -> GenManifest:
        """Always points to the symlink."""
        if not os.path.exists(self.current_link):
            raise RuntimeError("System not initialized!")
            
        with open(self.current_link, "r") as f:
            data = json.load(f)
            return GenManifest.from_dict(data)

    def create_new_gen(self, to_add=None, to_remove=None):
        """Main entry point for changing the system state."""
        current = self.get_current_gen()
        new_gen = copy.deepcopy(current)
        
        # Update IDs
        new_gen["prev_id"] = current["id"]
        new_gen["id"] = int(time.time())
        new_gen["gen_number"] += 1
        new_gen["active"] = False

        # Processing Removals
        if to_remove:
            for pkg_name in to_remove:
                self._recursive_remove(new_gen, pkg_name)

        # Processing Additions 
        if to_add:
            for pkg_name in to_add:
                self._recursive_add(new_gen, pkg_name)

        return new_gen

    def _recursive_remove(self, gen, pkg_name):
        # Find the hash for this pkg_name in the roots/active_layers
        pkg_hash = self._find_hash(gen, pkg_name)
        
        # Check relations this package has
        if pkg_hash in gen["relations"]:
            for dep_hash, isolated_p in list(gen["relations"][pkg_hash].items()):
                # Subtract the weight
                self._update_priority(gen, dep_hash, -isolated_p)
                
                # Cleanup relation link
                del gen["relations"][pkg_hash][dep_hash]
                
                # If priority <= 0, an orphan -> Remove
                if self._get_priority(gen, dep_hash) <= 0:
                    self._recursive_remove_by_hash(gen, dep_hash)

    def _recursive_add(self, gen, pkg_name):
        # Get recipe from store
        recipe = self.store.get_recipe(pkg_name)
        pkg_hash = recipe["hash_path"]
        
        # Add to active_layers if not there, otherwise update priority
        self._update_priority(gen, pkg_hash, recipe["global_priority"])
        
        # Handle Mount Instructions (Dependencies)
        for dep in recipe["mount_instructions"]:
            dep_hash = dep["hash"]
            isolated_p = dep["isolated_priority"]
            
            # Record the relation
            if pkg_hash not in gen["relations"]:
                gen["relations"][pkg_hash] = {}
            gen["relations"][pkg_hash][dep_hash] = isolated_p
            
            # Recurse: adds the dependency's weight to the global stack
            self._recursive_add(gen, dep["name"])

    