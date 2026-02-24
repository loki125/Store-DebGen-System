import shutil
import os
import subprocess

from config import *
from .utils import View

class SandBox:
    def __init__(self, mounts: List[str], view : View):
        self.view = view  # Object containing: merged, upper, work, isolated_path
        self.mounts = mounts
        self._mounted = []

    def __enter__(self):
        try:
            self.view.ensure_dirs()
            merged = Path(self.view.merged)

            # 1. Setup OverlayFS
            # Order: [Dependencies] : [Global Root]
            all_layers = [str(p) for p in self.mounts] + [str(BASE_ROOTFS)]
            lower_str = ":".join(all_layers)
            overlay_opts = f"lowerdir={lower_str},upperdir={self.view.upper},workdir={self.view.work}"
            
            self._mount("overlay", merged, m_type="overlay", options=overlay_opts)

            # 2. Mount Virtual Filesystems (Kernel Interfaces)
            # We mount these into the 'merged' view
            
            # /proc
            self._mount("proc", merged / "proc", m_type="proc")
            
            # /sys
            self._mount("sysfs", merged / "sys", m_type="sysfs")

            # 3. Setup Isolated /dev
            # We use a tmpfs so the sandbox has its own empty /dev 
            # instead of seeing the host's hardware devices.
            self._mount("tmpfs", merged / "dev", m_type="tmpfs", options="mode=755")

            # 4. Bind-mount ONLY safe character devices from the host
            for dev in self.SAFE_DEVICES:
                host_dev = Path("/dev") / dev
                sandbox_dev = merged / "dev" / dev
                if host_dev.exists():
                    sandbox_dev.touch() # Create mount point
                    self._mount(host_dev, sandbox_dev, options="bind")

            # 5. Setup /dev/pts (Terminal support)
            (merged / "dev/pts").mkdir(exist_ok=True)
            self._mount("devpts", merged / "dev/pts", m_type="devpts")

            return self

        except Exception as e:
            self.__exit__(None, None, None)
            raise RuntimeError(f"Sandbox setup failed: {e}")

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Clean up mounts in reverse order and remove temp workdirs."""
        
        # 1. Unmount everything in reverse order
        # Using lazy unmount (-l) to ensure it detaches even if a process is hanging
        for target in reversed(self._mounted):
            subprocess.run(["umount", "-l", str(target)], check=False)
        
        self._mounted.clear()

        # 2. Cleanup OverlayFS work directory
        if os.path.exists(self.view.work):
            shutil.rmtree(self.view.work, ignore_errors=True)

    def _mount(self, source, target, m_type=None, options=None):
        """Helper to run mount command and track it for cleanup."""
        cmd = ["mount"]
        if m_type:
            cmd += ["-t", m_type]
        if options:
            cmd += ["-o", options]
        cmd += [str(source), str(target)]

        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(f"Mount failed: {cmd}\nError: {result.stderr}")
        
        # Track the target for reverse unmounting
        self._mounted.append(target)
    
    def run_chroot(self, cmd_list):
        """Helper to run a command inside the prepared sandbox."""
        full_cmd = ["chroot", str(self.view.merged)] + cmd_list
        return subprocess.run(full_cmd, capture_output=True, text=True)

    def run(self, package_name: str, args: List[str] = ["configure"], env: Dict = OVERLAYFS_ENV):
        """Runs the package's postinst script INSIDE the sandbox."""
        
        # 1. Construct the dynamic path to the script
        # Reminder: the script was extracted into the 'upper' layer 
        # during the 'unpack' phase before entering the sandbox.
        script_path = f"/var/lib/dpkg/info/{package_name}.postinst"
        
        # 2. Check if the script exists before running
        # (Some packages don't have a postinst)
        check_cmd = ["chroot", self.view.merged, "test", "-f", script_path]
        if subprocess.run(check_cmd).returncode != 0:
            print(f"[*] No postinst script found for {package_name}, skipping.")
            return

        # 3. Execute the script
        real_cmd = ["chroot", self.view.merged, script_path] + args
        print(f"[*] Executing: {' '.join(real_cmd)}")
        
        return subprocess.run(real_cmd, env=env, check=True)

    def commit_changes(self):
        """Merges generated files from 'upper' into the permanent store."""
        upper_path = Path(self.view.upper)
        isolated_path = Path(self.view.isolated_path)

        if not upper_path.exists():
            return

        # Surgical cleanup: Remove policy-rc.d from upper if the package copied it
        # so we don't accidentally save our dummy shim into the package's permanent data.
        policy_shim = upper_path / "usr/sbin/policy-rc.d"
        if policy_shim.exists():
            policy_shim.unlink()

        # Merge the tree using copytree (dirs_exist_ok=True prevents the overwrite bug)
        # This safely layers the new .pyc files or configs into the existing unpacked data.
        shutil.copytree(upper_path, isolated_path, dirs_exist_ok=True)

        # After successfully copying, empty the upper directory so it's clean
        shutil.rmtree(upper_path)
        upper_path.mkdir()