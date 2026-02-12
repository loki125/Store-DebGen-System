import os
import shutil
import subprocess

from config import *

class SandBox:
    def __init__(self, mounts: List[str], view):
        self.view = view  # Object containing: merged, upper, work, isolated_path
        self.mounts = mounts

    def __enter__(self):
        """Sets up the sandbox: dirs, overlay, system mounts, and policy blocker."""
        
        # Create Directories 
        for d in [self.view.merged, self.view.upper, self.view.work]:
            os.makedirs(d, exist_ok=True)

        # Mount OverlayFS 
        lower_str = ":".join(self.mounts)
        cmd = [
            "mount", "-t", "overlay", "overlay",
            "-o", f"lowerdir={lower_str},upperdir={self.view.upper},workdir={self.view.work}",
            self.view.merged
        ]
        
        try:
            # check_call doesn't support capture_output, changed to run
            subprocess.check_call(cmd, check=True)
        except subprocess.CalledProcessError as e:
            raise RuntimeError(f"Failed to mount overlay: {e.stderr}")
        
        # Mount System Directories
        for sys_dir in SYSTEM_DIRS: 
            target = self.make_merge_dir(sys_dir)
            
            cmd = ["mount", "--bind", sys_dir, target]
            try:
                subprocess.check_call(cmd, check=True)
            except subprocess.CalledProcessError as e:
                # Cleanup if one fails? For now, just raise
                raise RuntimeError(f"Failed to mount {sys_dir}: {e.stderr}")

        # prevent services from starting. 
        self._create_policy_blocker()
        
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Clean up mounts and temp dirs."""
        
        for sys_dir in reversed(SYSTEM_DIRS):
            target = os.path.join(self.view.merged, sys_dir.lstrip("/"))
            # Lazy unmount (-l) is safer for scripts that leave hanging processes
            subprocess.run(["umount", "-l", target], check=False)

        subprocess.run(["umount", "-l", self.view.merged], check=False)

        # 3. Delete work dir
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
        
        print(f"[*] Sandbox Exec: {' '.join(command)}")
        return subprocess.run(real_cmd, env=env, check=True)

    def commit_changes(self):
        """Moves generated files from 'upper' to the permanent store."""
        print("[*] Committing changes...")
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