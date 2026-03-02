from dataclasses import dataclass, field, asdict
from typing import List, Dict, Optional
import json
import os

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


