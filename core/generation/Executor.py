import subprocess

from config import *
from .Builder import GenManifest

# Setup logger
logger = logging.getLogger("Executor")
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

class GenerationExecutor:
    def __init__(self, new_manifest: GenManifest, current_manifest: GenManifest) -> bool:
        """
        The Master Workflow: Orchestrates the move from one generation to the next.
        """
        logger.info(f"=== STARTING TRANSITION: Gen {current_manifest.id} -> Gen {new_manifest.id} ===")
        
        # 1. Mount New (Invisible to user)
        try:
            new_path = self._mount_new_gen(new_manifest)
        except Exception as e:
            logger.error(f"Failed to mount new generation: {e}")
            return False

        # 2. Verify Health
        if not self._verify_health(new_path):
            logger.error("New generation is broken. Aborting.")
            # Cleanup the failed mount
            subprocess.run(["umount", "-l", new_path])
            return False

        # 3. Calculate Diff
        to_remove, to_add = self._calculate_diff(current_manifest, new_manifest)
        current_path = os.path.join(GEN_MOUNT_BASE, str(current_manifest.id))

        # 4. Shutdown Old (Pre-Flight)
        self._shutdown_old_services(to_remove, current_path)

        # 5. The Atomic Switch
        try:
            self._atomic_switch(new_path)
        except OSError as e:
            logger.error(f"Atomic switch failed! Error: {e}")
            return False

        # 6. Activate New (Startup)
        self._activate_new_services(to_add, new_path)

        # 7. Cleanup Old
        self._cleanup_old_gen(current_manifest)
        
        logger.info("=== TRANSITION COMPLETE SUCCESSFULLY ===")
        return True
    
    @staticmethod
    def _mount_new_gen(new_manifest: GenManifest) -> str:
        mount_point = os.path.join(GEN_MOUNT_BASE, str(new_manifest.id))
        os.makedirs(mount_point, exist_ok=True)

        # Predictable Sort: Priority DESC, then Hash ASC for deterministic mounting
        sorted_layers = sorted(new_manifest.active_layers, key=lambda x: (-x.p, x.h))
        lower_dirs = ":".join([layer.h for layer in sorted_layers])

        logger.info(f"Mounting Gen {new_manifest.id} at {mount_point}")
        
        # overlayfs mount command
        cmd = [
            "mount", "-t", "overlay", "overlay",
            "-o", f"lowerdir={lower_dirs}",
            mount_point
        ]
        subprocess.run(cmd, check=True)
        return mount_point

    @staticmethod
    def _verify_health(mount_point: str) -> bool:
        logger.info(f"Verifying health of {mount_point}")
        
        # Standard essential binaries for a sane Linux environment
        essential_binaries = ["/bin/sh", "/usr/bin/env"]
        
        for f in essential_binaries:
            if not os.path.exists(mount_point + f):
                logger.warning(f"Critical file missing in new mount: {f}")
                return False
        return True
    
    @staticmethod
    def _calculate_diff(old_manifest: GenManifest, new_manifest: GenManifest) -> Tuple[Set[str], Set[str]]:
        logger.info("Calculating differences...")
        
        # Get names from 'name=version' format
        old_pkgs = set(r.split("=")[0] for r in old_manifest.roots)
        new_pkgs = set(r.split("=")[0] for r in new_manifest.roots)

        return old_pkgs - new_pkgs, new_pkgs - old_pkgs
    
    @staticmethod
    def _shutdown_old_services(pkgs_to_remove: Set[str], old_mount_point: str):
        logger.info("Shutting down old services...")
        
        for pkg in pkgs_to_remove:
            # 1. Stop live systemd service
            logger.info(f"  - Stopping service: {pkg}")
            subprocess.run(["systemctl", "stop", pkg], check=False, capture_output=True)
            
            # 2. Run prerm script in the context of the OLD mount
            prerm_rel_path = f"var/lib/dpkg/info/{pkg}.prerm"
            script_path = os.path.join(old_mount_point, prerm_rel_path)
            
            if os.path.exists(script_path):
                logger.info(f"  - Executing prerm for {pkg}")
                # We chroot so the script sees the environment it was built for
                subprocess.run(["chroot", old_mount_point, f"/{prerm_rel_path}"], check=False)

    @staticmethod
    def _atomic_switch(new_mount_point: str):
        logger.info("Flipping the global symlink...")
        
        # Atomic swap using rename (standard Linux behavior)
        tmp_link = f"{CURRENT_SYSTEM_LINK}.tmp"
        
        if os.path.exists(tmp_link):
            os.remove(tmp_link)
            
        os.symlink(new_mount_point, tmp_link)
        os.rename(tmp_link, CURRENT_SYSTEM_LINK) 
        logger.info(f"System pointer successfully moved to {new_mount_point}")

    @staticmethod
    def _activate_new_services(pkgs_to_add: Set[str], new_mount_point: str):
        logger.info("Activating new services...")
        
        # Tell systemd to look for new unit files in the updated /system/current
        subprocess.run(["systemctl", "daemon-reload"], check=False)
        
        for pkg in pkgs_to_add:
            # Check for systemd service file existence in new mount
            service_rel_path = f"usr/lib/systemd/system/{pkg}.service"
            unit_file = os.path.join(new_mount_point, service_rel_path)
            
            if os.path.exists(unit_file):
                logger.info(f"  - Starting new service: {pkg}")
                subprocess.run(["systemctl", "start", pkg], check=False)

    @staticmethod
    def _cleanup_old_gen(old_manifest: GenManifest):
        if not old_manifest:
            return
            
        old_path = os.path.join(GEN_MOUNT_BASE, str(old_manifest.id))
        logger.info(f"Lazy unmounting old generation at {old_path}")
        
        # -l (lazy) unmounts immediately from the tree, cleans up when files close
        subprocess.run(["umount", "-l", old_path], check=False)