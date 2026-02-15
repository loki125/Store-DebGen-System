import json
import time

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

    def create_new_gen(self, to_add=None, to_remove=None):
        """
        Main entry point for changing the system state.
        Algorithem:

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
        new_gen: GenManifest = current.copy()
        
        # Update IDs
        new_gen.prev_id = current.id
        new_gen.id = int(time.time())
        new_gen.gen_number += 1
        new_gen.active = False

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
        pass

    def _recursive_add(self, gen, pkg_name):
        pass
    