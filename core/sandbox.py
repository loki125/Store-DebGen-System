import shutil
import subprocess

from config import *

class SandBox:
    def __init__(self, mounts : List[str], view : View):
        self.view : View = view  # view has merged, lower, upper, work
        self.mounts = mounts

    def __enter__(self):
        """Allows 'with SandBox(target) as sb:' syntax for auto-cleanup."""
        for d in self.view.view_list:
            os.makedirs(d, exists_ok=True)

        lower_str = ":".join(self.mounts)
        cmd = [
            "mount", "-t", "overlay", "overlay",
            "-o", f"lowerdir={lower_str},upperdir={self.view.upper},workdir={self.view.work}",
            self.view.merged
        ]
        try:
            subprocess.check_call(cmd, check=True, capture_output=True, text=True)
        except subprocess.CalledProcessError as e:
            raise RuntimeError(f"Failed to mount overlay: {e}")
        
        #mount system directories
        for sys_dir in SYSTEM_DIRS:
            target = self.make_merge_dir(sys_dir)

            cmd = ["mount", "--bind", sys_dir, target]
            try:
                subprocess.check_call(cmd, check=True, capture_output=True, text=True)
            except subprocess.CalledProcessError as e:
                raise RuntimeError(f"Failed to mount system directory {sys_dir}: {e}")
        
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Automatically cleans up even if an error occurs."""
        for sys_dir in SYSTEM_DIRS:
            target = self.make_merge_dir(sys_dir)

            cmd = ["umount", "-l", target]
            subprocess.run(cmd, check=False, capture_output=True, text=True)

        subprocess.run(["umount", "-l", self.view.merged], check=False, capture_output=True, text=True)
        shutil.rmtree(self.view.work, ignore_errors=True)

    def make_merge_dir(self, sys_dir):
        target = os.path.join(self.view.merged, sys_dir.lstrip("/"))
        os.makedirs(target, exist_ok=True)

        return target
    
    def commit_script(self):
        """Communicates script results back to the Health Checker."""
        for root, dirs, files in os.walk(self.view.upper):
            for name in files:
                src_file = os.path.join(root, name)
                rel_path = os.path.relpath(src_file, self.view.upper)
                dst_file = os.path.join(self.view.isolated_path, rel_path)

                # ensure the dir exists in the isolated path
                os.makedirs(
                    os.path.dirname(dst_file),
                    exist_ok=True
                )
                
                # commit generated files from scripts to the isolated path
                shutil.move(src_file, dst_file)
        

