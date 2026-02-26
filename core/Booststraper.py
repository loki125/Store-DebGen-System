import subprocess
import logging
import shutil
from config import *

        
logger = logging.getLogger("BOOTSTRAP")
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

class Bootstraper:
    def __init__(self, target_path : Path):
        self.target_path = target_path
        self.null_device = self.target_path / "dev/null"
        self.exit_script = "#!/bin/sh\nexit {exist_code}\n"

        self.dirs = [
            "bin", "sbin", "lib", "lib64", "usr/bin", "usr/sbin", 
            "usr/share/python3", "var/lib/dpkg/info", "var/lib/dpkg/updates",
            "dev", "etc", "tmp", "proc", "sys"
        ]
        self.tools = ["cp", "mv", "rm", "ln", "sed", "grep", "awk", "mkdir", "cat", 
                "chmod", "chown", "dirname", "basename", "which", "id"]
        
        self.shims = [
            "/usr/sbin/invoke-rc.d", 
            "/usr/sbin/update-rc.d", 
            "/usr/bin/systemctl"
        ]

    def setup_dir(self):
        # Essential Directory Structure
        for d in self.dirs:
            (self.target_path / d).mkdir(parents=True, exist_ok=True)

        # providing a fake ect environment with a root user"
        with open(self.target_path / "etc/passwd", "w") as f:
            f.write("root:x:0:0:root:/root:/bin/sh\n")
            f.write("nobody:x:65534:65534:nobody:/nonexistent:/usr/sbin/nologin\n")

        with open(self.target_path / "etc/group", "w") as f:
            f.write("root:x:0:\n")
            f.write("nogroup:x:65534:\n")

    def run(self):
        """
        Creates a minimal filesystem shim for dpkg maintainer scripts.
        Requires root privileges to create /dev/null via mknod.
        """
        logger.info(f"Bootstrapping minimal rootfs at: {self.target_path}")
        self.setup_dir()

        self.copy_with_libs("/bin/dash")
        self.copy_with_libs("/sbin/ldconfig")

        if os.path.exists("/bin/busybox"):
            self.copy_with_libs("/bin/busybox")
        
        # Ensure /bin/sh exists as a link to dash
        if not (self.target_path / "bin/sh").exists():
            os.symlink("/bin/dash", self.target_path / "bin/sh")

        for tool in self.tools:
            tool_path = self.target_path / "bin" / tool
            if not tool_path.exists():
                os.symlink("/bin/busybox", tool_path)


        if not self.null_device.exists():
            # mknod requires root
            subprocess.run(["sudo", "mknod", "-m", "666", str(self.null_device), "c", "1", "3"], check=True)

        (self.target_path / "var/lib/dpkg/status").touch()
        (self.target_path / "var/lib/dpkg/available").touch()

        with open(self.target_path / "usr/share/python3/debian_defaults", "w") as f:
            f.write("[DEFAULT]\ndefault-version = python3.10\nsupported-versions = python3.10\n")

        for shim in self.shims:
            shim_path = self.target_path / shim.lstrip("/")
            shim_path.parent.mkdir(parents=True, exist_ok=True)
            with open(shim_path, "w") as f:
                f.write(self.exit_script.format(exist_code=0))
            shim_path.chmod(0o755)

        # policy-rc.d is special: 101 means "Action not allowed" (standard for containers)
        policy_path = self.target_path / "usr/sbin/policy-rc.d"
        with open(policy_path, "w") as f:
            f.write(self.exit_script.format(exist_code=101))
        policy_path.chmod(0o755)

        logger.info("Bootstrap complete.")

    def _smart_copy(self, host_path: Path):
        """
        Helper that replicates a file OR a symlink chain into the jail.
        """
        # Calculate where it goes in the local path
        dest_path = self.localize_path(str(host_path))
        
        # Avoid infinite loops or redundant work
        if dest_path.exists():
            return

        if host_path.is_symlink():
            link_target = os.readlink(host_path)
            
            os.symlink(link_target, dest_path)
            
            # We resolve the target's path relative to the host's filesystem
            target_on_host = (host_path.parent / link_target).resolve()
            self._smart_copy(target_on_host)
        else:
            # It's a real file, just copy it
            shutil.copy2(host_path, dest_path)

    def copy_with_libs(self, source_bin):
        source_path = Path(source_bin)
        if not source_path.exists():
            return
        
        # Copy the binary itself 
        self._smart_copy(source_path)

        # Use ldd to find libraries
        try:
            ldd_output = subprocess.check_output(["ldd", str(source_path)], text=True)
            for line in ldd_output.splitlines():
                lib_path = None
                
                if "=>" in line:  # Standard lib: libname => /path/to/lib
                    parts = line.split("=>")
                    if len(parts) > 1:
                        lib_path = parts[1].split("(")[0].strip()
                elif "/" in line: # Direct path (like the loader /lib64/ld-linux...)
                    lib_path = line.strip().split(" ")[0]

                # If we found a path and it's valid, use smart_copy
                if lib_path and os.path.exists(lib_path):
                    self._smart_copy(Path(lib_path))

        except subprocess.CalledProcessError:
            pass


    def localize_path(self, lib_path : str) -> Path:
        rel_lib = Path(lib_path).relative_to("/")
        dest_lib = self.target_path / rel_lib
        dest_lib.parent.mkdir(parents=True, exist_ok=True)

        return dest_lib
    