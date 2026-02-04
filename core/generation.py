import os

from health import HealthChecker


class View:
    def __init__(self, gen_path):
        self.gen_path = gen_path
        self.work = os.path.join(self.gen_path, "work")
        self.upper = os.path.join(self.gen_path, "delta")
        self.merged = os.path.join(self.gen_path, "merged")
        self.lower = os.path.join(self.gen_path, "root")

    def view_list(self):
        return [self.work, self.upper, self.merged, self.lower]


class GenerationBuilder:
    def __init__(self, generation_id, store_root="/opt/my-store"):
        self.gen_path = f"/var/lib/generations/{generation_id}"
        self.views = View(self.gen_path)
        self.store_root = store_root

        self.health = HealthChecker()

    def _gen_setup(self):
        """
        setting up the directory structure
        """
        if os.path.exists(self.gen_path):
            raise FileExistsError("Generation already exists")

        os.makedirs(self.gen_path)
        for view in self.views.view_list():
            os.makedirs(os.path.join(self.gen_path, view))


    def build_symlink_forest(self, package_paths):
        """
        package_paths: List of relative paths in store e.g. ['nginx-v1', 'redis-v2']
        """
        self._gen_setup()

        #Iterate through packages and link them into 'root'
        for pkg in package_paths:
            pkg_full_path = os.path.join(self.store_root, pkg, "contents")
            if os.path.exists(pkg_full_path):
                self._link_tree(pkg_full_path, self.views.lower)
            else:
                print(f"Error: {pkg} content not found in store.")


    def _link_tree(self, src_root : str, dst_root : str):
        """
        Recursively symlink files from src to dst.
        If dst is a directory, merge.
        """

        conflicts = []

        for root, dirs, files in os.walk(src_root):
            rel_path = os.path.relpath(root, src_root)
            dst_dir = os.path.join(dst_root, rel_path)
            os.makedirs(dst_dir, exist_ok=True)

            for file in files:
                src_file = os.path.join(root, file)
                dst_file = os.path.join(dst_dir, file)

                if os.path.lexists(dst_file):
                    # if it's the exact same store path no conflict
                    if os.path.islink(dst_file) and os.readlink(dst_file) == src_file:
                        continue

                    # It's a real conflict. Log it for the Health Checker.
                    conflicts.append({
                        "path": dst_file,
                        "old_source": os.readlink(dst_file) if os.path.islink(dst_file) else "real_file",
                        "new_source": src_file
                    })

                    #Priority (Overwrite)
                    os.remove(dst_file)

                os.symlink(src_file, dst_file)

        # Save the conflict log into the generation folder
        if conflicts:
            self.health.add_conflicts(conflicts)