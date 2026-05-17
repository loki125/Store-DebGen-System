from dataclasses import dataclass, field, asdict
from enum import StrEnum
from typing import List, Dict, Optional
import json
import os
from datetime import datetime

from config import *


@dataclass(frozen=True)
class APIEndpoints:
    RECIPE_PKG: str = "/recipe_pkg"
    PKGS_BY_NAME: str = "/pkgs_by_name"
    PKGS_BY_NAME_VERSION: str = "/pkgs_by_name_version"
    PKG_BY_HASH: str = "/pkg_by_hash"
    DOWNLOAD_PKG: str = "/download_pkg"


class APIParams(StrEnum):
    STORE_PATH = "Store_Path"
    PACKAGE = "Package"
    VERSION = "Version"
    SHA256 = "SHA256"

@dataclass
class HealthInfo:
    status: str = "pending"
    logs: str = ""

@dataclass
class Layer:
    h: str  # Hash Path
    p: int  # Global Priority
    def __hash__(self):
        return hash(self.h)

@dataclass
class GenManifest:
    timestamp_id: Optional[str] = None
    prev_id: Optional[int] = None
    active_layers: List[Layer] = field(default_factory=list)
    relations: Dict[str, Dict[str, int]] = field(default_factory=dict)
    active: bool = False
    health: HealthInfo = field(default_factory=HealthInfo)

    def __post_init__(self):
        if self.timestamp_id is None:
            self.timestamp_id = datetime.now().strftime("%m.%d.%Y:%H:%M:%S")

    def to_json(self):
        return json.dumps(asdict(self), indent=4)

    @classmethod
    def from_dict(cls, data: dict):
        data = data.copy()
        health = HealthInfo(**data.pop("health"))
        layers = [Layer(**l) for l in data.pop("active_layers")]

        return cls(
            active_layers=layers,
            health=health,
            **data
        )

@dataclass
class WrapperConfig:
    upper_path: str
    work_path: str
    bin_src: str
    lower_dirs: str 
    store_root:  str
    shared_path: str = field(init=False)

    def __post_init__(self):
        self.shared_path = str(SHARED_RUN)

    def to_dict(self):
        return asdict(self)


@dataclass
class TransactionPaths:
    """Holds the specific paths for a single installation transaction."""
    stage: Path
    forest: Path
    upper: Path
    work: Path
    merged: Path
    download: Path

def env_injection_list(pkg_name: str) -> Dict[str, str]:
        env = os.environ.copy()

        env.update({
            "DEBIAN_FRONTEND": "noninteractive",
            "DEBCONF_NONINTERACTIVE_SEEN": "true",
            "RUNLEVEL": "1",
            "FAKE_CHROOT": "1",
            "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
            "LD_LIBRARY_PATH": "/usr/local/lib:/usr/lib:/lib:/usr/lib/x86_64-linux-gnu:/lib/x86_64-linux-gnu",
            "TERM": "linux",
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            
            "DPKG_MAINTSCRIPT_PACKAGE": pkg_name,
            "DPKG_MAINTSCRIPT_ARCH": DEFAULT_ARCH, 
            "DPKG_MAINTSCRIPT_NAME": POSTINST
        })

        env.pop("DEBCONF_USE_CDEBCONF", None)
        env.pop("DEBIAN_HAS_FRONTEND", None)

        return env

