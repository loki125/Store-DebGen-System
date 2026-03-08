import subprocess
import logging
import shutil
import os
from pathlib import Path
import tarfile

from config import *

class Bootstrapper:
    def __init__(self, target_path: Path = BASE_ROOTFS, rootfs_tarball: Path = BASE_ROOTFS_TARBALL):
        self.target_path = target_path
        self.rootfs_tarball = rootfs_tarball
        self.exit_script = "#!/bin/sh\nexit {exit_code}\n"

        self.logger = logging.getLogger(self.__class__.__name__)

    def deploy(self):
        if self.target_path.exists():
            shutil.rmtree(self.target_path)
        self.target_path.mkdir(parents=True)
        
        with tarfile.open(self.rootfs_tarball, "r:gz") as tar:
            tar.extractall(path=self.target_path)

        self.logger.info(f"Base rootfs deployed to {self.target_path}, preparing environment...")
        self.patch_environment()

    def patch_environment(self):
        """Apply runtime shims and ensure /dev/null exists."""
        # Ensure /dev/null
        null_device = self.target_path / "dev/null"
        if not null_device.exists():
            subprocess.run(["sudo", "mknod", "-m", "666", str(null_device), "c", "1", "3"], check=True)

        # Apply shims (systemctl, etc.)
        for shim_name in ["/usr/sbin/invoke-rc.d", "/usr/sbin/update-rc.d", "/usr/bin/systemctl"]:
            shim_path = self.target_path / shim_name.lstrip("/")
            shim_path.parent.mkdir(parents=True, exist_ok=True)
            with open(shim_path, "w") as f:
                f.write(self.exit_script.format(exit_code=0))
            shim_path.chmod(0o755)

        # Apply policy-rc.d
        policy_path = self.target_path / "usr/sbin/policy-rc.d"
        with open(policy_path, "w") as f:
            f.write(self.exit_script.format(exit_code=101))
        policy_path.chmod(0o755)

        self.logger.info("Environment patch applied successfully.")