from dataclasses import dataclass, field, asdict
from typing import List, Dict, Optional
import json
import os
from datetime import datetime

from config import *

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
    timestamp_id: str = field(init=False) # Timestamp
    prev_id: Optional[int]
    pending_rootfs_upgrades: List[str] # [KEY_STR]
    active_layers: List[Layer]
    relations: Dict[str, Dict[str, int]] #{hash_path : {hash_path : isolated_priority number} }
    active: bool = False
    health: HealthInfo = field(default_factory=HealthInfo)

    def __post_init__(self):
        self.timestamp_id = datetime.now().strftime("%m.%d.%Y:%H:%M:%S")

    def to_json(self):
        return json.dumps(asdict(self), indent=4)

    @classmethod
    def from_dict(cls, data: dict):
        # Convert nested dicts back into Dataclasses
        data = data.copy()
        health = HealthInfo(**data.pop("health"))
        layers = [Layer(**l) for l in data.pop("active_layers")]
        return cls(active_layers=layers, health=health, **data)

@dataclass
class WrapperConfig:
    store_path : str
    bin_src : str
    shared_path : str = field(init=False)

    def __post_init__(self):
        self.shared_path = SHARED_RUN

    def to_dict(self):
        return {
            "store_path": self.store_path,
            "bin_src": self.bin_src,
            "shared_path": self.shared_path
        }


@dataclass
class TransactionPaths:
    """Holds the specific paths for a single installation transaction."""
    stage: Path
    forest: Path
    upper: Path
    work: Path
    merged: Path
    download: Path


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


