from typing import Dict
import os
import zipfile
import subprocess
import shutil

from .fetcher import Fetcher

class Store:
    def __init__(self, fetcher : Fetcher, root="/opt/my-store"):
        self.root = root
        os.makedirs(self.root, exist_ok=True)

        self.fetcher = fetcher

    def full_path(self, path):
        return os.path.join(self.root, path)


    def update(self, pkg : Dict) -> str:
        store_path = pkg["Store_Path"]
        if os.path.exists(store_path):
            return "pkg already exists"

        target_dir = self.full_path(store_path)
        os.makedirs(target_dir, exist_ok=False)

        zip_path = f"{target_dir}.zip"

        if not self.fetcher.download_file(zip_path):
            return "download failed"

        try:
            os.makedirs(target_dir, exist_ok=True)

            # Extract the ZIP
            with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                zip_ref.extractall(target_dir)

            # Look for the .deb inside
            deb_file = next((f for f in os.listdir(target_dir) if f.endswith('.deb')), None)
            if not deb_file:
                return "No .deb found in zip"

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

            return "success"

        except Exception as e:
            # Atomic failure: if anything goes wrong, wipe the dir
            if os.path.exists(target_dir):
                shutil.rmtree(target_dir)
            return f"failed during extraction: {str(e)}"
        
        