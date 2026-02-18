import json
import zipfile
import subprocess
import shutil

from core.sandbox import SandBox

from .fetcher import Fetcher
from config import *
from utils import View

logger = logging.getLogger("Store")
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

class Store:
    def __init__(self, fetcher : Fetcher, root="/opt/my-store"):
        self.root = root
        os.makedirs(self.root, exist_ok=True)

        self.fetcher = fetcher

    def full_path(self, path):
        return os.path.join(self.root, path)


    def update(self, pkg : Dict) -> str:

        store_path = pkg["Store_Path"]
        target_dir = self.full_path(store_path)
        
        if os.path.exists(target_dir):
            return "pkg already exists"
        os.makedirs(os.path.dirname(target_dir), exist_ok=True)
        
        zip_name = self.fetcher.download_file(save_path=target_dir)
        if zip_name is None:
            return "download failed"

        try:
            os.makedirs(target_dir, exist_ok=False)

            self._extract_pkg(zip_name, target_dir)
            with open(os.path.join(target_dir, RECIPE), "r") as f:
                recipe = json.load(f)

            mount_instructions = recipe.get("mount_instructions", {})
            for hash_path in mount_instructions["required_mounts"]:
                f_hash_path = self.full_path(hash_path)
                if os.path.exists(f_hash_path):
                    continue
                os.makedirs(f_hash_path, exist_ok=False)
                
                zip_name = self.fetcher.download_file(f_hash_path) 
                if zip_name is None:
                    raise RuntimeError(f"download depend {hash_path} failed")
                
                self._extract_pkg(zip_name, f_hash_path)
                self._integrate(f_hash_path)

            self._integrate(target_dir)

            return "success"

        except Exception as e:
            # Atomic failure: if anything goes wrong, wipe the dir
            if os.path.exists(target_dir):
                shutil.rmtree(target_dir)
            return f"failed during extraction: {str(e)}"
        
    def _integrate(self, hash_path : str):
        # read recipe.json, make symlinks and create mount namespace
        with open(os.path.join(hash_path, RECIPE), "r") as f:
            recipe = json.load(f)
        
        link_data : Dict[str, str]
        created_symlinks = []
        for link_data in recipe.get("symlinks_forest", []):

            link_path = Path(self.root) / link_data["src"]
            target_path = link_data["dst"]

            link_path.parent.mkdir(parents=True, exist_ok=True)
            if link_path.exists():
                link_path.unlink()

            os.symlink(target_path, link_path)
            created_symlinks.append(link_path)
        
        mounts = list(recipe.get("mount_instructions", {}).get("required_mounts", []))
        mounts.append(hash_path) # ensure the pkg itself is mounted

        try:
            # Create a mount namespace and mount the required paths
            with SandBox(mounts, View(hash_path)) as root_fs:
                logger.info(f"Running postinst script for {recipe.get('package_name')}-{recipe.get('version')} in sandbox...")
                root_fs.run(["/" + SCRIPT_PATH + "configure"])

                root_fs.commit_changes()

        except Exception as e:
            for link in created_symlinks:
                if link.exists() and link.is_symlink():
                    link.unlink()

            raise RuntimeError(f"Error running postinst script: {e}")


    def _extract_pkg(self, zip_name, target_dir):

        zip_path = os.path.join(target_dir, zip_name)
        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
            zip_ref.extractall(target_dir)

        # Look for the .deb inside
        deb_file = next((f for f in os.listdir(target_dir) if f.endswith('.deb')), None)
        if not deb_file:
            raise Exception("No .deb found in zip")

        deb_full_path = os.path.join(target_dir, deb_file)

        # We extract to a contents subdir
        content_dir = os.path.join(target_dir, "contents")
        os.makedirs(content_dir)

        # dpkg-deb -x extracts the data.tar (files destined for /usr, /bin, etc.)
        subprocess.run(["dpkg-deb", "-x", deb_full_path, content_dir], check=True)

        # Extract .deb control scripts (preinst, postinst, etc)
        control_dir = os.path.join(target_dir, "control")
        os.makedirs(control_dir)
        subprocess.run(["dpkg-deb", "-e", deb_full_path, control_dir], check=True)

        # Cleanup transport files
        os.remove(zip_path)
        os.remove(deb_full_path)

    def get_recipe(self, hash_path: str) -> Optional[Dict]:
        """Reads the recipe.json for a specific package hash."""
        target_dir = self.full_path(hash_path)
        recipe_path = os.path.join(target_dir, RECIPE) # recipe.json
        
        if not os.path.exists(recipe_path):
            return None
            
        try:
            with open(recipe_path, "r") as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"[Store] Failed to read recipe at {recipe_path}: {e}")
            return None
        
        