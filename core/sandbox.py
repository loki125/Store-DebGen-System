import shutil
import os
import subprocess

from config import *
from utils import View

class SandBox:
    def __init__(self, mounts: List[str], view : View):
        self.view = view  # Object containing: merged, upper, work, isolated_path
        self.mounts = mounts
        self._mounted = []

    def __enter__(self):
        """Sets up the sandbox: dirs, overlay, system mounts, and policy blocker."""
        try:
            # Create Directories 
            self.view.ensure_dirs()

            # Mount OverlayFS 
            for p in self.mounts:
                if not os.path.isdir(p):
                    raise RuntimeError(f"Lower dir missing: {p}")
        
            lower_str = ":".join(self.mounts)
            cmd = [
                "mount", "-t", "overlay", "overlay",
                "-o", f"lowerdir={lower_str},upperdir={self.view.upper},workdir={self.view.work}",
                self.view.merged
            ]
            
            result = subprocess.run(cmd, capture_output=True, text=True)
            if result.returncode != 0:
                raise RuntimeError(f"Failed to mount overlay: {result.stderr}")
            self._mounted.append(self.view.merged)
            
            # Mount System Directories
            for sys_dir in SYSTEM_DIRS: 
                target = self.make_merge_dir(sys_dir)
                
                result = subprocess.run(
                    ["mount", "--bind", sys_dir, target],
                    capture_output=True,
                    text=True
                )
                
                if result.returncode != 0:
                    raise RuntimeError(f"Failed to mount {sys_dir}: {result.stderr}")

                self._mounted.append(target)

            # prevent services from starting. 
            self._create_policy_blocker()

            return self

        except Exception as e:
            for m in reversed(self._mounted):
                subprocess.run(["umount", "-l", m], check=False)
                
            raise RuntimeError(f"Sandbox setup failed: {e}")
        

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Clean up mounts and temp dirs."""
        
        for sys_dir in reversed(SYSTEM_DIRS):
            target = os.path.join(self.view.merged, sys_dir.lstrip("/"))
            # Lazy unmount (-l) is safer for scripts that leave hanging processes
            subprocess.run(["umount", "-l", target], check=False)

        subprocess.run(["umount", "-l", self.view.merged], check=False)

        # Delete work dir
        shutil.rmtree(self.view.work, ignore_errors=True)

    def _create_policy_blocker(self):
        """Creates the 'policy-rc.d' script to stop daemons from starting."""
        # We write to UPPER so it overlays on top of the package's /usr/sbin
        policy_path = os.path.join(self.view.upper, POLICY_PATH)
        
        os.makedirs(os.path.dirname(policy_path), exist_ok=True)
        
        with open(policy_path, "w") as f:
            f.write(POLICY_BLOCKER_SCRIPT) # 101 = Action Forbidden by Policy
            
        # Make it executable rwxr-xr-x
        os.chmod(policy_path, 0o755)

    def make_merge_dir(self, sys_dir):
        """Helper to create paths inside the merged view."""
        target = os.path.join(self.view.merged, sys_dir.lstrip("/"))
        os.makedirs(target, exist_ok=True)
        return target
    
    def run(self, command: List[str], env : Dict = OVERLAYFS_ENV):
        """Runs a command INSIDE the sandbox using chroot."""
        
        # Chroot requires the full command to be wrapped
        real_cmd = ["chroot", self.view.merged] + command
        
        return subprocess.run(real_cmd, env=env, check=True)

    def commit_changes(self):
        """Moves generated files from 'upper' to the permanent store."""
        for root, dirs, files in os.walk(self.view.upper):
            for name in files:
                src_file = os.path.join(root, name)
                
                # Check if this is the policy blocker (we don't want to save that!)
                if "policy-rc.d" in src_file:
                    continue

                rel_path = os.path.relpath(src_file, self.view.upper)
                dst_file = os.path.join(self.view.isolated_path, rel_path)

                os.makedirs(os.path.dirname(dst_file), exist_ok=True)
                shutil.move(src_file, dst_file)