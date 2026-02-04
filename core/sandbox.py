import subprocess
import os

from generation import GenerationBuilder, View

class SandBox:
    def __init__(self, target : GenerationBuilder):
        self.target = target
        self.view : View = target.views

        # track mounts to clean them up later
        self.mounts = []

    def mount_overlay(self):
        """Mounts the OverlayFS layers into the merged directory."""
        opts = f"lowerdir={self.view.lower},upperdir={self.view.upper},workdir={self.view.work}"
        mrg = self.view.merged
        cmd = ["mount", "-t", "overlay", "overlay", "-o", opts, mrg]

        try:
            subprocess.run(cmd, check=True)
            self.mounts.append(mrg)
            print(f"Successfully mounted OverlayFS at {mrg}")
        except subprocess.CalledProcessError as e:
            print(f"Mount failed: {e}")
            raise

    def mount_system_dirs(self):
        """Bind mount essential system dirs so scripts don't crash."""
        system_dirs = ["/proc", "/sys", "/dev", "/dev/pts"]

        for d in system_dirs:
            target_path = os.path.join(self.view.merged, d.lstrip("/"))
            # Ensure the mount point exists inside the sandbox
            os.makedirs(target_path, exist_ok=True)

            cmd = ["mount", "--bind", d, target_path]
            subprocess.run(cmd, check=True)
            self.mounts.append(target_path)

    def run_command(self, command_list):
        """Runs a command inside the sandbox using chroot."""
        # Example: command_list = ["/bin/sh", "/var/lib/dpkg/info/package.postinst"]
        # Note: Paths in command_list must be relative to the sandbox root!

        full_cmd = ["chroot", self.view.merged] + command_list
        return subprocess.run(full_cmd)

    def cleanup(self):
        """Unmounts everything in reverse order."""
        for path in reversed(self.view.mounts):
            subprocess.run(["umount", "-l", path], check=True)
        self.view.mounts = []
        print("Sandbox cleaned up.")
