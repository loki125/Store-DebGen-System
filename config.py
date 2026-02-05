import os
from pathlib import Path

# Fetcher API
STORE_NODE = ""
ENDPOINTS = {
    "-d" : "download_pkg",
    "-i" : "packages",
}

# Base Paths
BASE_DIR = Path("/var/lib/isolated-manager")
STORE_ROOT = "/opt/my-store"
GEN_ROOT = "/var/lib/generations"
CURRENT_SYSTEM_LINK = "/system/current"

# Sandbox / OverlayFS Constants
SYSTEM_DIRS = ["/proc", "/sys", "/dev", "/dev/pts"]
POLICY_PATH = "usr/sbin/policy-rc.d"
OVERLAYFS_ENV = {"PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"}
POLICY_BLOCKER_SCRIPT = "#!/bin/sh\nexit 101\n" # 101 means 'action not allowed'

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