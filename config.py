import os
from typing import List, Dict, Tuple, Set, Optional, Any
import logging
from enum import Enum
from pathlib import Path

# Fetcher API
STORE_NODE : str = "http://192.168.50.4:11080"
class ENDPOINTS(Enum):
    DOWNLOAD = "download_pkg"
    PKG_INFO  = "pkgs_by_name"
    PKG_VER_INFO = "pkgs_by_name_version"
    HASH_INFO = "pkgs_by_hash"


# Base Paths

"""
STRUCTURE:

/var/lib/isolated-manager        ← BASE_DIR
 ├── base/                       ← debootstrap rootfs
 ├── store/                      ← package file store
 ├── generations/                ← generation manifests + roots

/var/isolated-manager/active         ← runtime state
 ├── current → generation link


"""
# GLOBAL VAR
MANAGER : str = "isolated-manager"
BASE_DIR = Path(os.getenv("IM_BASE", f"/var/lib/{MANAGER}"))
ACTIVE_LINK = Path(os.getenv("IM_ACTIVE_LINK", f"/var/{MANAGER}/active"))
PKG_MANAGER_LINK = "/usr/bin/ddls"

# DATA VAR
BASE_ROOTFS_TARBALL = Path(os.getenv("IM_BASE_ROOTFS", "data/base.tar.gz"))
PACKAGE_WRAPPER_PATH = Path(os.getenv("IM_PKG_WRAPPER", "data/wrapper.sh"))

# STATIC VAR
PROFILE_SCRIPT_PATH = "/etc/profile.d/ddls_env.sh" # Add the active generation to the global system PATH and LD_LIBRARY_PATH
EXPORTS = (
    f'export PATH="/var/{MANAGER}/active/bin:$PATH"\n'
    f'export LD_LIBRARY_PATH="/var/{MANAGER}/active/lib:$LD_LIBRARY_PATH"\n'
    f'export LD_LIBRARY_PATH="/var/{MANAGER}/active/lib64:$LD_LIBRARY_PATH"\n'
)
ADD_INDICATOR = '+'
RM_INDICATOR = '-'
INDICATOR_SIZE = 1
HASH_LENGTH = 64

# System Binaries
DPKG_CMD = "dpkg"
DPKG_QUERY_CMD = "dpkg-query"
DPKG_DEB_CMD = "dpkg-deb"

# Bootstrapper environment patching
SHIM_PATHS =[
    "/usr/sbin/invoke-rc.d", 
    "/usr/sbin/update-rc.d", 
    "/usr/bin/systemctl"
]
POLICY_RC_D_PATH = "/usr/sbin/policy-rc.d"

# Internal Rootfs Paths
TMP_DIR_REL = "tmp"

# Paths inside the chroot environment
DPKG_POSTINST_PATH = "var/lib/dpkg/info/postinst"
DPKG_INFO_PATH = "var/lib/dpkg/info"
USR_BIN_PATH = "usr/bin"
LDCONFIG_PATH = "/sbin/ldconfig"

# Environment Modification Paths
PROFILE_D_DIR = Path("/etc/profile.d")
PROFILE_SCRIPT_PATH = PROFILE_D_DIR / "ddls.sh"
SYS_PROFILE_PATH = Path("/etc/profile")

# Strings expected in the profile FHS setup
ACTIVE_BIN_EXPORT_STR = "/var/store/active/bin"
ETC_PROFILE_COMMENT = "# DDLS Package Manager Environment\n"

# Daemons
INIT_D_REL_PATH = Path("etc/init.d")

# PACKAGE MAP VAR
SLOT_COUNT = 1000  # How many packages expected

STATUS_SIZE = 1
STATUS_EMPTY = 0
STATUS_OCCUPIED = 1
STATUS_DELETED = 2

KEY_SIZE = 127
VALUE_SIZE = 256 # hash(64) + name(64) + version (20) + buffer(106)

SLOT_SIZE = STATUS_SIZE + KEY_SIZE + VALUE_SIZE  # 384 bytes
KEY_STR = "{name}={version}"

# STATIC FILENAMES
MANIFEST : str =  "manifest.json"
RECIPE : str = "recipe.json"
CURRENT : str = "current.json"
PKG_MAP = "packages.dat"
ROOT = "root"

# PATHS
BASE_ROOTFS = BASE_DIR / "base"
WRAPPER_DIR = BASE_DIR / "wrappers"
STORE_ROOT = BASE_DIR / os.getenv("IM_STORE", "store")
STORE_TMP_ROOT = STORE_ROOT / ".tmp"
GEN_DIR =  BASE_DIR / os.getenv("IM_GEN", "generations")
SHARED_RUN = BASE_DIR / "shared_run" 
CURRENT_LINK = BASE_DIR / CURRENT
PKG_MAP_PATH = BASE_DIR / PKG_MAP

# generation paths
class GenPath:
    @staticmethod
    def base(gen_id: int | str) -> Path:
        return GEN_DIR / str(gen_id)

    @staticmethod
    def root(gen_id: int | str) -> Path:
        return GenPath.base(gen_id) / ROOT

    @staticmethod
    def root_bin(gen_id: int | str) -> Path:
        return GenPath.base(gen_id) / ROOT / "bin"

    @staticmethod
    def root_lib(gen_id: int | str) -> Path:
        return GenPath.base(gen_id) / ROOT / "lib"

    @staticmethod
    def root_lib64(gen_id: int | str) -> Path:
        return GenPath.base(gen_id) / ROOT / "lib64"

    @staticmethod
    def manifest(gen_id: int | str) -> Path:
        return GenPath.base(gen_id) / MANIFEST
    
BIN_PATHS = ["usr/bin", "bin", "usr/sbin", "sbin"]
LIB_PATHS = ["usr/lib", "lib"]
LIB64_PATHS = ["usr/lib64", "lib64"]


# Sandbox / OverlayFS Constants
TRANS_ROOT = BASE_DIR / "transient" # Transient area for OverlayFS mechanics
DEVICE_NODES = {
    "null":   (1, 3),
    "zero":   (1, 5),
    "full":   (1, 7),
    "random": (1, 8),
    "urandom":(1, 9)
}

OVERLAYFS_ENV = {
    "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
    "DEBIAN_FRONTEND": "noninteractive", # Prevents scripts from hanging
    "LC_ALL": "C.UTF-8"                   # Prevents encoding errors in scripts
}

# Health Check Settings
# Paths that, if conflicted, will cause the transaction to fail immediately
CRITICAL_PATHS = [
    "/etc/passwd",
    "/etc/shadow",
    "/etc/fstab",
    "/etc/network/interfaces",
    "/boot"
]


