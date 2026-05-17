import subprocess
import logging
import shutil
from pathlib import Path
import tarfile

from config import *

class Bootstrapper:
    """
    @brief Orchestrates the extraction and environment patching of the base system rootfs.
    """
    def __init__(self, target_path: Path = BASE_ROOTFS, rootfs_tarball: Path = BASE_ROOTFS_TARBALL):
        """
        @brief Initializes the bootstrapper with target paths and tarball location.
        @param target_path Path where the rootfs will be deployed.
        @param rootfs_tarball Path to the source rootfs compressed archive.
        """
        self.target_path = target_path
        self.rootfs_tarball = rootfs_tarball
        self.exit_script = EXIT_SCRIPT_TEMPLATE

        self.logger = logging.getLogger(self.__class__.__name__)

    def _stitch_tarball(self) -> None:
        """
        @brief Reconstructs a split tarball archive from its parts found in the source directory.
        @throws FileNotFoundError if no parts or archive are found.
        """
        parts = sorted(self.rootfs_tarball.parent.glob(f"{self.rootfs_tarball.name}{TAR_PART_SUFFIX}"))
        
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
        """
        @brief Deploys the rootfs to the target path and applies necessary patches.
        @throws FileNotFoundError if source archive cannot be prepared.
        """
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
        """
        @brief Applies runtime shims, policy scripts, and creates essential device nodes.
        @throws subprocess.CalledProcessError if mknod command fails.
        """
        null_device = self.target_path / DEV_NULL_REL_PATH
        if not null_device.exists():
            self.logger.debug("Creating /dev/null node...")
            subprocess.run(DEV_NULL_STR.format(path=str(null_device)), shell=True, check=True)
            
        for shim_name in SHIM_PATHS:
            shim_path = self.target_path / shim_name.lstrip("/")
            shim_path.parent.mkdir(parents=True, exist_ok=True)
            with open(shim_path, "w") as f:
                f.write(self.exit_script.format(exit_code=SHIM_EXIT_CODE_OK))
            shim_path.chmod(SHIM_PERM_MODE)

        policy_path = self.target_path / POLICY_RC_D_PATH.lstrip("/")
        policy_path.parent.mkdir(parents=True, exist_ok=True)
        with open(policy_path, "w") as f:
            f.write(self.exit_script.format(exit_code=SHIM_EXIT_CODE_POLICY))
        policy_path.chmod(SHIM_PERM_MODE)

        self.logger.info("Environment patch applied successfully.")

    def is_system_pkg(self, pkg_name: str) -> bool:
        """
        @brief Checks if a package is installed within the base rootfs using dpkg-query.
        @param pkg_name Name of the package to query.
        @return True if the package status is 'install ok installed', False otherwise.
        """
        cmd = [CMD_CHROOT, str(self.target_path), DPKG_QUERY_CMD, DPKG_QUERY_W_FLAG, DPKG_QUERY_STATUS_FORMAT, pkg_name]
        res = subprocess.run(cmd, capture_output=True, text=True)

        if res.returncode != 0:
            return False

        return DPKG_STATUS_INSTALLED_OK in res.stdout