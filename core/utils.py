from dataclasses import dataclass, field, asdict
from typing import List, Dict, Optional
import json
import os

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
    gen_number: int
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

    def view_list(self):
        return [self.work, self.upper, self.merged, self.lower]
    

#healther helpers

@dataclass
class Conflict:
    path : str
    old_source : str = field(init=False)
    new_source : str

    def __post_init__(self):
        self.old_source = os.readlink(self.path) if os.path.islink(self.path) else "real_file"

@dataclass
class Result:
    pkg : str
    exit_code : int
    output : str