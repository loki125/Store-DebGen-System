import os
from typing import List, Dict, Tuple, Set, Optional
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

/run/isolated-manager            ← runtime state
 ├── current → generation link


"""
# GLOBAL VAR
MANAGER : str = "isolated-manager"
BASE_DIR = Path(os.getenv("IM_BASE", f"/var/lib/{MANAGER}"))
ACTIVE_LINK = Path(os.getenv("IM_ACTIVE_LINK", f"/var/{MANAGER}/active"))

# DATA VAR
BASE_ROOTFS_TARBALL = Path(os.getenv("IM_BASE_ROOTFS", "data/base.tar.gz"))
PACKAGE_WRAPPER_PATH = Path(os.getenv("IM_PKG_WRAPPER", "data/wrapper.sh"))

# STATIC VAR
PROFILE_SCRIPT = "/etc/profile.d/ddls_env.sh" # Add the active generation to the global system PATH and LD_LIBRARY_PATH
EXPORTS = (
    'export PATH="/var/store/active/bin:$PATH"\n'
    'export LD_LIBRARY_PATH="/var/store/active/lib:$LD_LIBRARY_PATH"\n'
)

# STATIC FILENAMES
MANIFEST : str =  "manifest.json"
RECIPE : str = "recipe.json"
CURRENT : str = "current.json"

# PATHS
BASE_ROOTFS = BASE_DIR / "base"
STORE_ROOT = BASE_DIR / os.getenv("IM_STORE", "store")
GEN_ROOT =  BASE_DIR / os.getenv("IM_GEN", "generations")
CURRENT_MANIFEST_LINK = GEN_ROOT / CURRENT


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


