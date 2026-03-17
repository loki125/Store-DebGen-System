import subprocess
import logging
import shutil
import os
from pathlib import Path
import tarfile
from typing import Union

from config import *

class Bootstrapper:
    def __init__(self, target_path: Path = BASE_ROOTFS, rootfs_tarball: Path = BASE_ROOTFS_TARBALL):
        self.target_path = target_path
        self.rootfs_tarball = rootfs_tarball
        self.exit_script = "#!/bin/sh\nexit {exit_code}\n"

        self.logger = logging.getLogger(self.__class__.__name__)

    def deploy(self) -> None:
        if self.target_path.exists():
            shutil.rmtree(self.target_path)
        self.target_path.mkdir(parents=True)
        
        with tarfile.open(self.rootfs_tarball, "r:gz") as tar:
            tar.extractall(path=self.target_path)

        self.logger.info(f"Base rootfs deployed to {self.target_path}, preparing environment...")
        self.patch_environment()

    def patch_environment(self) -> None:
        """Apply runtime shims and ensure /dev/null exists."""
        # Ensure /dev/null
        null_device = self.target_path / "dev/null"
        if not null_device.exists():
            subprocess.run(["mknod", "-m", "666", str(null_device), "c", "1", "3"], check=True)

        # Apply shims (systemctl, etc.)
        for shim_name in SHIM_PATHS:
            shim_path = self.target_path / shim_name.lstrip("/")
            shim_path.parent.mkdir(parents=True, exist_ok=True)
            with open(shim_path, "w") as f:
                f.write(self.exit_script.format(exit_code=0))
            shim_path.chmod(0o755)

        # Apply policy-rc.d
        policy_path = self.target_path / POLICY_RC_D_PATH.lstrip("/")
        policy_path.parent.mkdir(parents=True, exist_ok=True)
        with open(policy_path, "w") as f:
            f.write(self.exit_script.format(exit_code=101))
        policy_path.chmod(0o755)

        self.logger.info("Environment patch applied successfully.")

    def is_system_version_newer(self, new_version: str, pkg_name: str) -> bool:
        """
        new_version: The version string from your DB (e.g., "2.43-1")
        pkg_name: The package name (e.g., "libc6")
        """
        try:
            cmd_query =["chroot", str(self.target_path), DPKG_QUERY_CMD, "-W", "-f=${Version}", pkg_name]
            res = subprocess.run(cmd_query, capture_output=True, text=True, check=True)
            installed_version = res.stdout.strip()
        except subprocess.CalledProcessError:
            return False

        compare_cmd =[DPKG_CMD, "--compare-versions", new_version, ">>", installed_version]
        return subprocess.run(compare_cmd).returncode == 0
    
    def upgrade_system_lib(self, deb_path: Path) -> bool:
        deb_name = deb_path.name[:HASH_LENGTH]
        self.logger.info(f"Upgrading {deb_name} in {self.target_path}...")
        
        target_deb_in_root = self.target_path / TMP_DIR_REL / deb_path.name
        
        # Ensure /tmp exists inside the rootfs just in case
        target_deb_in_root.parent.mkdir(parents=True, exist_ok=True)
        
        try:
            shutil.copy2(deb_path, target_deb_in_root)
            
            subprocess.run(["chroot", str(self.target_path), DPKG_CMD, "-i", f"/{TMP_DIR_REL}/{deb_path.name}"],
                check=True,
                capture_output=True,
                text=True
            )
            return True
            
        except subprocess.CalledProcessError as e:
            self.logger.error(f"Failed to upgrade {deb_name}. STDERR: {e.stderr}")
            return False
            
        finally:
            if target_deb_in_root.exists():
                target_deb_in_root.unlink()

    def is_system_deb_newer(self, deb_path: Union[Path, str]) -> bool:
        """
        deb_path: Path or string pointing to the .deb file.
        Returns: True if the .deb is a system package AND its version is strictly greater.
        """
        deb_path_str = str(deb_path)

        try:
            cmd_info =[DPKG_DEB_CMD, "-W", "--showformat=${Package}\t${Version}", deb_path_str]
            res = subprocess.run(cmd_info, capture_output=True, text=True, check=True)
            
            output_line = res.stdout.strip().split('\n')[-1]
            pkg_name, new_version = output_line.split('\t')
            
        except (subprocess.CalledProcessError, ValueError) as e:
            self.logger.error(f"Could not read metadata from {deb_path_str}: {e}")
            return False

        try:
            cmd_query =["chroot", str(self.target_path), DPKG_QUERY_CMD, "-W", "-f=${Version}", pkg_name]
            res_query = subprocess.run(cmd_query, capture_output=True, text=True, check=True)
            installed_version = res_query.stdout.strip()
        except subprocess.CalledProcessError:
            return False

        compare_cmd = [DPKG_CMD, "--compare-versions", new_version, ">>", installed_version]
        return subprocess.run(compare_cmd).returncode == 0