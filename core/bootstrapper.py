import subprocess
import logging
import shutil
import posixpath
from pathlib import Path
import tarfile
from typing import Union, List

from config import *

class Bootstrapper:
    def __init__(self, target_path: Path = BASE_ROOTFS, rootfs_tarball: Path = BASE_ROOTFS_TARBALL):
        self.target_path = target_path
        self.rootfs_tarball = rootfs_tarball
        self.exit_script = "#!/bin/sh\nexit {exit_code}\n"

        self.logger = logging.getLogger(self.__class__.__name__)

    def _stitch_tarball(self) -> None:
        """Finds split parts and stitches them back into a single tarball."""
        parts = sorted(self.rootfs_tarball.parent.glob(f"{self.rootfs_tarball.name}.part_*"))
        
        if not parts:
            raise FileNotFoundError(f"Cannot find {self.rootfs_tarball} or any of its split parts!")

        self.logger.info(f"Stitching {len(parts)} parts to reconstruct {self.rootfs_tarball.name}...")
        
        with open(self.rootfs_tarball, 'wb') as outfile:
            for part in parts:
                self.logger.debug(f"Appending {part.name}...")
                with open(part, 'rb') as infile:
                    shutil.copyfileobj(infile, outfile)
                    
        self.logger.info("Tarball successfully reconstructed.")

    def deploy(self) -> None:
        if not self.rootfs_tarball.exists():
            self._stitch_tarball()

        if self.target_path.exists():
            shutil.rmtree(self.target_path)
        self.target_path.mkdir(parents=True)
        
        self.logger.info(f"Extracting {self.rootfs_tarball.name}...")
        with tarfile.open(self.rootfs_tarball, "r:gz") as tar:
            tar.extractall(path=self.target_path)

        self.logger.info(f"Base rootfs deployed to {self.target_path}, preparing environment...")
        self.patch_environment()

    def patch_environment(self) -> None:
        """Apply runtime shims and ensure /dev/null exists."""
        
        null_device = self.target_path / "dev/null"
        if not null_device.exists():
            self.logger.debug("Creating /dev/null node...")
            subprocess.run(["mknod", "-m", "666", str(null_device), "c", "1", "3"], check=True)

        for shim_name in SHIM_PATHS:
            shim_path = self.target_path / shim_name.lstrip("/")
            shim_path.parent.mkdir(parents=True, exist_ok=True)
            with open(shim_path, "w") as f:
                f.write(self.exit_script.format(exit_code=0))
            shim_path.chmod(0o755)

        policy_path = self.target_path / POLICY_RC_D_PATH.lstrip("/")
        policy_path.parent.mkdir(parents=True, exist_ok=True)
        with open(policy_path, "w") as f:
            f.write(self.exit_script.format(exit_code=101))
        policy_path.chmod(0o755)

        self.logger.info("Environment patch applied successfully.")

    def is_system_pkg(self, pkg_name: str) -> bool:
        """
        Gets a package name and asks if ANY version of it exists 
        and is currently installed on the target system (chroot).
        """
        cmd = ["chroot", str(self.target_path), DPKG_QUERY_CMD, "-W", "--showformat=${Status}", pkg_name]
        res = subprocess.run(cmd, capture_output=True, text=True)

        if res.returncode != 0:
            return False

        return "install ok installed" in res.stdout