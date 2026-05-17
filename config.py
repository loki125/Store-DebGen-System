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

# Bootstrapper Constants
EXIT_SCRIPT_TEMPLATE : str = "#!/bin/sh\nexit {exit_code}\n"
TAR_PART_SUFFIX : str = ".part_*"
DEV_NULL_REL_PATH : str = "dev/null"
DEV_NULL_STR : str = "mknod -m 666 {path} c 1 3"
SHIM_PERM_MODE : int = 0o755
SHIM_EXIT_CODE_OK : int = 0
SHIM_EXIT_CODE_POLICY : int = 101
DPKG_QUERY_W_FLAG : str = "-W"
DPKG_QUERY_STATUS_FORMAT : str = "--showformat=${Status}"
DPKG_STATUS_INSTALLED_OK : str = "install ok installed"

# Fetcher Configuration
FETCH_TIMEOUT : int = 10
ENCODING_UTF8 : str = "utf-8"
DOWNLOAD_CHUNK_SIZE : int = 8192
DEFAULT_PKG_ZIP : str = "pkg.zip"

ENDPOINT_RECIPE : str = "/recipe_pkg"
ENDPOINT_PKGS_NAME : str = "/pkgs_by_name"
ENDPOINT_PKGS_VER : str = "/pkgs_by_name_version"
ENDPOINT_PKG_HASH : str = "/pkg_by_hash"
ENDPOINT_DOWNLOAD : str = "/download_pkg"

# Dictionary / JSON Keys
KEY_STORE_PATH : str = "Store_path"
KEY_PACKAGE : str = "Package"
KEY_VERSION : str = "Version"
KEY_SHA256 : str = "SHA256"

# --- Store Recipe/Metadata Keys ---
KEY_STORE_PATH_CAP : str = "Store_Path"
KEY_RECIPE_VERSION : str = "version"
KEY_PKG_NAME : str = "package_name"
KEY_STATUS : str = "status"
KEY_MOUNT_INSTRUCTIONS : str = "mount_instructions"
KEY_REQUIRED_MOUNTS : str = "required_mounts"
KEY_SYSTEM_MOUNTS : str = "system_mounts"
KEY_PROVIDER_MAP : str = "provider_map"
KEY_SYMLINK_FOREST : str = "symlink_forest"
KEY_NAME : str = "name"
KEY_ARCH : str = "arch"
KEY_STATUS_BLOCK : str = "status_block"
KEY_FILES : str = "files"

HDR_CONTENT_DISPOSITION : str = "Content-Disposition"
FILENAME_KEEP_CHARS : tuple = ('.', '_', '-')

# Generation Constants
GEN_INIT_STATUS : str = "healthy"
GEN_INIT_LOGS : str = "Initial System Creation"
GLOB_ALL : str = "*"
PKILL_CMD : str = "pkill"
PGREP_CMD : str = "pgrep"
SIGTERM_FLAG : str = "-TERM"
SIGKILL_FLAG : str = "-KILL"
PROC_MATCH_FLAG : str = "-f"
SHUTDOWN_WAIT_TIME : int = 2
SERVICE_IGNORE_FILES : List[str] = ["README", "skeleton", "functions"]

class SystemPackageNotFoundError(FileNotFoundError):
    sys_rel_path : str
    def __init__(self, message: str, sys_rel_path: str):
        self.sys_rel_path = sys_rel_path
        super().__init__(message)


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
DEFAULT_ARCH = "amd64" 

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
OVLFS_JUNK_STR = ".wh."
ADD_INDICATOR = '+'
RM_INDICATOR = '-'
INDICATOR_SIZE = 1
HASH_LENGTH = 64

# System Binaries
DPKG_CMD = "dpkg"
DPKG_QUERY_CMD = "dpkg-query"
DPKG_DEB_CMD = "dpkg-deb"

# Store Sandboxing & Operations Commands
CMD_UMOUNT = "umount"
CMD_MOUNT = "mount"
CMD_FUSE_OVERLAYFS = "fuse-overlayfs"
CMD_CHROOT = "chroot"        
CMD_CP = "cp"
CMD_STAT_F = "stat -f"
CMD_DMESG_TAIL = "dmesg | tail -n 30"

# Command Arguments
ARG_RECURSIVE = "-R"
ARG_LAZY = "-l"
ARG_OPTIONS = "-o"
ARG_BIND = "--bind"
ARG_RO = "ro"
ARG_AUTO_DECONFIGURE = "--auto-deconfigure"
ARG_INSTALL = "-i"
ARG_ARCHIVE = "-a"
ARG_CONFIGURE = "configure"
ARG_EXTRACT = "-x"
ARG_CONTROL_EXTRACT = "-e"

# Bootstrapper environment patching
SHIM_PATHS =[
    "/usr/sbin/invoke-rc.d", 
    "/usr/sbin/update-rc.d", 
    "/usr/bin/systemctl"
]
POLICY_RC_D_PATH = "/usr/sbin/policy-rc.d"

# Internal Rootfs Paths
TMP_DIR_REL = "tmp"
DIR_VAR = "var"
DIR_LIB = "lib"
DIR_DPKG = "dpkg"
DIR_INFO = "info"
DIR_DOWNLOADS = "downloads"

# Paths inside the chroot environment
POSTINST = "postinst"
DPKG_POSTINST_PATH = f"var/lib/dpkg/info/{POSTINST}"
DPKG_INFO_PATH = "var/lib/dpkg/info"
USR_BIN_PATH = "usr/bin"
LDCONFIG_PATH = "/sbin/ldconfig"
PROC_MOUNTS_PATH = "/proc/mounts"

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
SLOT_COUNT = 1000  
STATUS_SIZE = 1
STATUS_EMPTY = 0
STATUS_OCCUPIED = 1
STATUS_DELETED = 2

KEY_SIZE = 127
VALUE_SIZE = 256 # hash(64) + name(64) + version (20) + buffer(106)

SLOT_SIZE = STATUS_SIZE + KEY_SIZE + VALUE_SIZE  # 384 bytes
KEY_STR = "{name}={version}"
BYTE_ZERO = b"\x00"

# STATIC FILENAMES
MANIFEST : str =  "manifest.json"
RECIPE : str = "recipe.json"
CURRENT : str = "current.json"
PKG_MAP = "packages.dat"
ROOT = "root"
FILE_STATUS = "status"
LOCK_FILE_NAME = ".update.lock"

# Extensions and Globs
EXT_SO = ".so"
EXT_SO_PREFIX = ".so."
EXT_TMP = ".tmp"        
EXT_LIST = ".list"
EXT_OPQ = ".opq"
GLOB_DEB = "*.deb"

# PATHS
BASE_ROOTFS = BASE_DIR / "base"
WRAPPER_DIR = BASE_DIR / "wrappers"
STORE_ROOT = BASE_DIR / os.getenv("IM_STORE", "store")
STORE_TMP_ROOT = STORE_ROOT / ".tmp"
GEN_DIR =  BASE_DIR / os.getenv("IM_GEN", "generations")
SHARED_RUN = BASE_DIR / "shared_run" 
CURRENT_LINK = BASE_DIR / CURRENT
PKG_MAP_PATH = BASE_DIR / PKG_MAP

# Wrapper Inner Paths
WRAPPER_FOREST = ".forest"
WRAPPER_WORK = ".work"
WRAPPER_UPPER = ".upper"

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
LIB_PATHS = []
DEV_PATHS = [
    ["usr/lib", "lib"],                   # Defualt headers 
    ["usr/include", "headers"],           # C/C++ Headers
    ["usr/lib/pkgconfig", "metadata"],    # Compilation metadata
    ["usr/share/pkgconfig", "metadata"],  # Compilation metadata
    ["usr/lib/cmake", "metadata"],        # CMake discovery
    ["usr/share/java", "java"],           # Java JARs
    ["usr/lib/python3", "python"],        # Python modules
    ["usr/share", "data"]                 # Icons, translations, shared data
]
LIB64_PATHS = ["usr/lib64", "lib64"]


# Sandbox / OverlayFS Constants
TRANS_ROOT = BASE_DIR / "transient" 

PREFIX_STAGE = "stage_"
PREFIX_FOREST = "forest_"
PREFIX_UPPER = "upper_"
PREFIX_WORK = "work_"
PREFIX_MERGED = "merged_"

LINK_TYPE_STORE = "link-to-store"
LINK_TYPE_CROSS = "cross-package-symlink"
LINK_TYPE_EXEC = "exec-copy"

API_MOUNT_POINTS = ["proc", "sys", "dev"]

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