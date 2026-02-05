import subprocess

from generation import GenerationBuilder, View
from config import *

class SandBox:
    def __init__(self, target : GenerationBuilder):
        self.target = target
        self.view : View = target.views  # target.view has merged, lower, upper, work
        self.mounts = []

    def __enter__(self):
        """Allows 'with SandBox(target) as sb:' syntax for auto-cleanup."""
        self.mount_overlay()
        self.mount_system_dirs()
        self.setup_debian_policy()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Automatically cleans up even if an error occurs."""
        self.cleanup()

    def mount_overlay(self):
        opts = f"lowerdir={self.view.lower},upperdir={self.view.upper},workdir={self.view.work}"

        cmd = ["mount", "-t", "overlay", "overlay", "-o", opts, self.view.merged]
        subprocess.run(cmd, check=True)
        self.mounts.append(self.view.merged)

    def mount_system_dirs(self):
        for d in SYSTEM_DIRS:
            target_path = os.path.join(self.view.merged, d.lstrip("/"))
            os.makedirs(target_path, exist_ok=True)
            subprocess.run(["mount", "--bind", d, target_path], check=True)
            self.mounts.append(target_path)

    def setup_debian_policy(self):
        """
            Prevents postinst scripts from failing on systemd calls.
        """
        policy_path = os.path.join(self.view.merged, POLICY_PATH)
        os.makedirs(os.path.dirname(policy_path), exist_ok=True)

        with open(policy_path, "w") as f:
            f.write(POLICY_BLOCKER_SCRIPT)
        os.chmod(policy_path, 0o755) # 0o755 -> rwxr-xr-x

    def run_command(self, command_list):
        # We must provide a PATH or basic tools like 'ls' or 'cp' won't be found
        full_cmd = ["chroot", self.view.merged] + command_list
        return subprocess.run(full_cmd, env=OVERLAYFS_ENV)

    def cleanup(self):
        """Unmounts everything in reverse order."""
        for m_path in reversed(self.mounts):
            # Use -l (lazy) to ensure it unmounts even if files are open
            subprocess.run(["umount", "-l", m_path], check=False)
        self.mounts = []
        print("Sandbox cleaned up.")
