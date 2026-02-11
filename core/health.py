from dataclasses import dataclass, field

from config import *

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

class HealthChecker:
    def __init__(self):
        self.conflicts : List[Conflict] = []
        self.script_results = []
        self.warnings = []

    def add_conflicts(self, conflicts_list):
        """Called by GenerationBuilder during the forest creation."""
        self.conflicts.extend(conflicts_list)

    def record_script_result(self, package_name, return_code, stdout=""):
        """Called by Sandbox after running a postinst script."""
        self.script_results.append(Result(package_name, return_code, stdout))

    @staticmethod
    def check_fs_integrity(root_view) -> List:
        """Scans the Symlink Forest for broken links."""
        broken_links = []
        for root, dirs, files in os.walk(root_view):
            for name in files + dirs:
                root_path = Path(root) / name
                if root_path.is_symlink() and not root_path.exists():
                    broken_links.append(str(root_path))
        return broken_links

    def is_healthy(self, root_view):
        """The final decision maker."""
        # check - script failure
        for result in self.script_results:
            if result.exit_code != 0:
                raise Exception(f"Health Check Failed: Script for {result.pkg} exited with {result.exit_code}")

        # check - broken symlinks in the forest
        broken = self.check_fs_integrity(root_view)
        if broken:
            raise Exception(f"Health Check Failed: Found {len(broken)} broken symlinks.\n{broken}")

        # Conflict Analysis
        for conflict in self.conflicts:
            if any(conflict.path.endswith(cp) for cp in CRITICAL_PATHS):
                raise Exception(f"Health Check Failed: Critical system file conflict at {conflict.path}")

        print("Health Check Passed: Generation is stable.")
