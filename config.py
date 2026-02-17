import os
from typing import List, Dict, Tuple, Set, Optional
import logging
from pathlib import Path

# Fetcher API
STORE_NODE = "http://192.168.50.4:11080"
ENDPOINTS = {
    "-d" : "download_pkg",
    "-i" : "pkgs_by_name",
    "-ih" : "pkgs_by_hash"
}

# Base Paths

"""
STRUCTURE:

/var/lib/isolated-manager        ← BASE_DIR
 ├── base/                       ← debootstrap rootfs
 ├── store/                      ← package file store
 ├── generations/                ← generation manifests + roots

/run/isolated-manager            ← runtime state
 ├── current → generation link

/mnt/isolated-manager/generation           ← generation mounts

"""
# GLOBAL VAR
MANAGER : str = "isolated-manager"
BASE_DIR = Path(os.getenv("IM_BASE", f"/var/lib/{MANAGER}"))

MANIFEST : str =  "manifest.json"
RECIPE : str = "recipe.json"


# PATHS
BASE_ROOTFS = BASE_DIR / "base"
STORE_ROOT = BASE_DIR / os.getenv("IM_STORE", "store")
GEN_ROOT =  BASE_DIR / os.getenv("IM_GEN", "generations")
GEN_MOUNT_BASE = Path(f"/mnt/{MANAGER}/generations")
CURRENT_SYSTEM_LINK = Path(f"/run/{MANAGER}/current")
CURRENT_MANIFEST_LINK = GEN_ROOT / "current.json"



# Sandbox / OverlayFS Constants
SYSTEM_DIRS = ["/proc", "/sys", "/dev", "/dev/pts"]
TEMP_OVERLAY_DIR = "/tmp/sandbox_"
POLICY_PATH = "usr/sbin/policy-rc.d"
OVERLAYFS_ENV = {"PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"}
POLICY_BLOCKER_SCRIPT = "#!/bin/sh\nexit 101\n" # 101 means 'action not allowed'

SCRIPT_PATH = "var/lib/dpkg/info/postinst"

# Health Check Settings
# Paths that, if conflicted, will cause the transaction to fail immediately
CRITICAL_PATHS = [
    "/etc/passwd",
    "/etc/shadow",
    "/etc/fstab",
    "/etc/network/interfaces",
    "/boot"
]
