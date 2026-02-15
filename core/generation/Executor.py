import os
import subprocess

from config import *
from Builder import GenManifest

class GenerationExecutor:
    def __init__(self):
        pass

    def apply(self, new_manifest: GenManifest, current_manifest: GenManifest) -> bool:
        """
        The Master Workflow: Orchestrates the move from one generation to the next.
        """
        print(f"\n=== STARTING TRANSITION: Gen {current_manifest.id} -> Gen {new_manifest.id} ===")
        
        # 1. Mount New (Invisible to user)
        try:
            new_path = self._mount_new_gen(new_manifest)
        except Exception as e:
            print(f"[FATAL] Failed to mount new generation: {e}")
            return False

        # 2. Verify Health
        if not self._verify_health(new_path):
            print("[FATAL] New generation is broken. Aborting.")
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
            print(f"[FATAL] Switch failed! Error: {e}")
            return False

        # 6. Activate New (Startup)
        self._activate_new_services(to_add, new_path)

        # 7. Cleanup Old
        self._cleanup_old_gen(current_manifest)
        return True
    
    @staticmethod
    def _mount_new_gen(new_manifest: GenManifest) -> str:
        mount_point = os.path.join(GEN_MOUNT_BASE, str(new_manifest.id))
        os.makedirs(mount_point, exist_ok=True)

        # Predictable Sort: Priority DESC, then Hash ASC
        sorted_layers = sorted(new_manifest.active_layers, key=lambda x: (-x.p, x.h))
        lower_dirs = ":".join([layer.h for layer in sorted_layers])

        print(f"[*] Step 1: Mounting Gen {new_manifest.id} at {mount_point}")
        cmd = [
            "mount", "-t", "overlay", "overlay",
            "-o", f"lowerdir={lower_dirs}",
            mount_point
        ]
        subprocess.run(cmd, check=True)
        return mount_point

    @staticmethod
    def _verify_health(mount_point: str) -> bool:
        print(f"[*] Step 2: Verifying health of {mount_point}")
        for f in ["/bin/sh", "/usr/bin/env"]:
            if not os.path.exists(mount_point + f):
                print(f"[!] Critical file missing: {f}")
                return False
        return True
    
    @staticmethod
    def _calculate_diff(old_manifest: GenManifest, new_manifest: GenManifest) -> Tuple[Set[str], Set[str]]:
        print("[*] Step 3: Calculating differences...")
        old_pkgs = set(r.split("=")[0] for r in old_manifest.roots)
        new_pkgs = set(r.split("=")[0] for r in new_manifest.roots)

        return old_pkgs - new_pkgs, new_pkgs - old_pkgs
    
    @staticmethod
    def _shutdown_old_services(pkgs_to_remove: Set[str], old_mount_point: str):
        print("[*] Step 4: Shutting down old services...")
        for pkg in pkgs_to_remove:
            # Stop live service
            subprocess.run(["systemctl", "stop", pkg], check=False, capture_output=True)
            
            # Run prerm script in the OLD context
            script_path = os.path.join(old_mount_point, f"var/lib/dpkg/info/{pkg}.prerm")
            if os.path.exists(script_path):
                print(f"    - Running prerm for {pkg}")
                subprocess.run(["chroot", old_mount_point, f"/var/lib/dpkg/info/{pkg}.prerm"], check=False)

    @staticmethod
    def _atomic_switch(new_mount_point: str):
        print("[*] Step 5: Flipping the switch...")
        tmp_link = CURRENT_SYSTEM_LINK + ".tmp"
        if os.path.exists(tmp_link):
            os.remove(tmp_link)
            
        os.symlink(new_mount_point, tmp_link)
        os.rename(tmp_link, CURRENT_SYSTEM_LINK) 
        print("    - SYSTEM POINTER UPDATED.")

    @staticmethod
    def _activate_new_services(pkgs_to_add: Set[str], new_mount_point: str):
        print("[*] Step 6: Activating new services...")
        # Reload systemd once for all new units
        subprocess.run(["systemctl", "daemon-reload"], check=False)
        
        for pkg in pkgs_to_add:
            unit_file = os.path.join(new_mount_point, f"usr/lib/systemd/system/{pkg}.service")
            if os.path.exists(unit_file):
                print(f"    - Starting: {pkg}")
                subprocess.run(["systemctl", "start", pkg], check=False)

    @staticmethod
    def _cleanup_old_gen(old_manifest: GenManifest):
        if not old_manifest: return
        old_path = os.path.join(GEN_MOUNT_BASE, str(old_manifest.id))
        print(f"[*] Step 7: Lazy unmounting {old_path}")
        subprocess.run(["umount", "-l", old_path], check=False)