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
            **data  # now includes timestamp_id safely
        )

@dataclass
class WrapperConfig:
    store_path: str
    store_path_work: str 
    bin_src: str
    lower_dirs: str 
    shared_path: str = field(init=False)

    def __post_init__(self):
        # Cast to str just in case SHARED_RUN is a Path object
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


