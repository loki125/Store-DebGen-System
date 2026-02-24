from dataclasses import dataclass, field, asdict
from typing import List, Dict, Optional
import json
import os
import shutil
import subprocess
from pathlib import Path

from config import *

@dataclass
class HealthInfo:
    status: str = "pending"
    logs: str = ""

@dataclass
class Layer:
    h: str  # Hash Path
    p: int  # Global Priority

@dataclass
class GenManifest:
    id: int # Timestamp
    prev_id: Optional[int]
    roots: List[str] # ["name=version"]
    active_layers: List[Layer]
    relations: Dict[str, Dict[str, int]] #{hash_path : {hash_path : isolated_priority number} }
    active: bool = False
    health: HealthInfo = field(default_factory=HealthInfo)

    def to_json(self):
        return json.dumps(asdict(self), indent=4)

    @classmethod
    def from_dict(cls, data: dict):
        # Convert nested dicts back into Dataclasses
        data = data.copy()
        health = HealthInfo(**data.pop("health"))
        layers = [Layer(**l) for l in data.pop("active_layers")]
        return cls(active_layers=layers, health=health, **data)
    


#view for overlayfs
class View:
    def __init__(self, isolated_path):
        self.isolated_path = isolated_path
        self.work = os.path.join(self.isolated_path, "work")
        self.upper = os.path.join(self.isolated_path, "delta")
        self.merged = os.path.join(self.isolated_path, "merged")
        self.lower = os.path.join(self.isolated_path, "root")
        
    def ensure_dirs(self):
        for p in [self.work, self.upper, self.merged, self.lower]:
            os.makedirs(p, exist_ok=True)

#healther helpers

@dataclass
class Conflict:
    path : str
    old_source : str = field(init=False)
    new_source : str

    def __post_init__(self):
        try:
            if os.path.islink(self.path):
                self.old_source = os.readlink(self.path)
            else:
                self.old_source = "real_file"
        except OSError:
            self.old_source = "unknown"

@dataclass
class Result:
    pkg : str
    exit_code : int
    output : str


# bootstrap init
def bootstrap_base_rootfs(target_path: Path):
    """
    Creates a minimal filesystem shim for dpkg maintainer scripts.
    Requires root privileges to create /dev/null via mknod.
    """
    logger = logging.getLogger("BOOTSTRAP")
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

    logger.info(f"Bootstrapping minimal rootfs at: {target_path}")

    # 1. Essential Directory Structure
    dirs = [
        "bin", "sbin", "lib", "lib64", "usr/bin", "usr/sbin", 
        "usr/share/python3", "var/lib/dpkg/info", "var/lib/dpkg/updates",
        "dev", "etc", "tmp", "proc", "sys"
    ]
    for d in dirs:
        (target_path / d).mkdir(parents=True, exist_ok=True)

    # 2. Helper to resolve and copy shared libraries (the 'ldd' logic)
    def copy_with_libs(source_bin):
        source_path = Path(source_bin)
        if not source_path.exists():
            return
        
        # Copy the binary itself
        dest = target_path / source_path.relative_to("/")
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_path, dest)

        # Use ldd to find libraries
        try:
            ldd_output = subprocess.check_output(["ldd", str(source_path)], text=True)
            for line in ldd_output.splitlines():
                if "=>" in line:  # Standard lib: libname => /path/to/lib (0x...)
                    parts = line.split("=>")
                    lib_path = parts[1].split("(")[0].strip()
                    if os.path.exists(lib_path):
                        rel_lib = Path(lib_path).relative_to("/")
                        dest_lib = target_path / rel_lib
                        dest_lib.parent.mkdir(parents=True, exist_ok=True)
                        if not dest_lib.exists():
                            shutil.copy2(lib_path, dest_lib)
                elif "/" in line: # Direct path (like the loader /lib64/ld-linux...)
                    lib_path = line.strip().split(" ")[0]
                    if os.path.exists(lib_path):
                        rel_lib = Path(lib_path).relative_to("/")
                        dest_lib = target_path / rel_lib
                        dest_lib.parent.mkdir(parents=True, exist_ok=True)
                        if not dest_lib.exists():
                            shutil.copy2(lib_path, dest_lib)
        except subprocess.CalledProcessError:
            pass # Not a dynamic executable

    # 3. Copy Core Binaries
    # We use dash (standard sh) and busybox (all other tools)
    copy_with_libs("/bin/dash")
    if os.path.exists("/bin/busybox"):
        copy_with_libs("/bin/busybox")
    
    # Ensure /bin/sh exists as a link to dash
    if not (target_path / "bin/sh").exists():
        os.symlink("/bin/dash", target_path / "bin/sh")

    # 4. Create BusyBox Symlinks
    # These are the tools maintainer scripts call most often
    tools = ["cp", "mv", "rm", "ln", "sed", "grep", "awk", "mkdir", "cat", 
             "chmod", "chown", "dirname", "basename", "which", "id"]
    for tool in tools:
        tool_path = target_path / "bin" / tool
        if not tool_path.exists():
            os.symlink("/bin/busybox", tool_path)

    # 5. Create Device Nodes (Crucial)
    # Most scripts fail if they can't redirect to /dev/null
    null_device = target_path / "dev/null"
    if not null_device.exists():
        # mknod requires root
        subprocess.run(["sudo", "mknod", "-m", "666", str(null_device), "c", "1", "3"], check=True)

    # 6. Dpkg Identity Shims
    # Create the status file (empty is fine for a start)
    (target_path / "var/lib/dpkg/status").touch()
    (target_path / "var/lib/dpkg/available").touch()

    # 7. Python-Specific Shim (for FastAPI and similar)
    with open(target_path / "usr/share/python3/debian_defaults", "w") as f:
        f.write("[DEFAULT]\ndefault-version = python3.10\nsupported-versions = python3.10\n")

    # 8. System Management Shims (The "Lies")
    # We create dummy scripts that always return success (exit 0)
    # This prevents errors when the package tries to restart a service
    shims = [
        "/usr/sbin/ldconfig", 
        "/usr/sbin/invoke-rc.d", 
        "/usr/sbin/update-rc.d", 
        "/usr/bin/systemctl"
    ]
    for shim in shims:
        shim_path = target_path / shim.lstrip("/")
        shim_path.parent.mkdir(parents=True, exist_ok=True)
        with open(shim_path, "w") as f:
            f.write("#!/bin/sh\nexit 0\n")
        shim_path.chmod(0o755)

    # policy-rc.d is special: 101 means "Action not allowed" (standard for containers)
    policy_path = target_path / "usr/sbin/policy-rc.d"
    with open(policy_path, "w") as f:
        f.write("#!/bin/sh\nexit 101\n")
    policy_path.chmod(0o755)

    logger.info("Bootstrap complete.")