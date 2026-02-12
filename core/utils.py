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
    relations: Dict[str, Dict[str, int]] # {hash: {dep_hash: weight}}
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