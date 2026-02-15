import os
from typing import List, Dict, Tuple, Set
from pathlib import Path

# Fetcher API
STORE_NODE = "http://192.168.50.4:11080"
ENDPOINTS = {
    "-d" : "download_pkg",
    "-i" : "pkgs_by_name",
    "-ih" : "pkgs_by_hash"
}

# Base Paths
BASE_DIR = Path("/var/lib/isolated-manager")
STORE_ROOT = "/opt/my-store"
GEN_ROOT = "/var/lib/generations"
GEN_MOUNT_BASE = "/mnt/generations"
CURRENT_SYSTEM_LINK = "/system/current"
CURRENT_MANIFEST_LINK = os.path.join(GEN_ROOT, "current.json")
MANIFEST =  "manifest.json"
RECIPE = "recipe.json"


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

# Create directories if they don't exist
for path in [STORE_ROOT, GEN_ROOT]:
    os.makedirs(path, exist_ok=True)
