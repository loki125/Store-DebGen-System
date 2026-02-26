# Store-DebGen-System

## Phase 1: Preparation (The Build/Store)

    [V] 1. Recursive Dependency Mapping

        What it fools: The Linker’s Dependency Resolver. It ensures that when the linker asks "Where is libA.so?", the file actually exists in the provided environment.

    [V] 2. Script & Metadata Extraction

        What it fools: The System State. It ensures users/groups/folders the app expects are created before the binary even tries to find them.

    [V] 3. Symlink Forest Generation (Internal to Store)

        What it fools: Hardcoded Relative Paths. By making links relative to the store-hash, you ensure the package is "Relocatable."

    [ ] 4. ELF Interpreter Path Verification

        What it fools: The Kernel Binary Loader.

        Detail: Every binary has a path like /lib64/ld-linux-x86-64.so.2. If your fake FHS doesn't have this exact file at that exact path, the binary won't even start.

## Phase 2: The "Sandboxing" (Mounting & Isolation)

    [ ] 5. Basic FHS "Lower" Mount (The Skeleton)

        What it fools: The Linker’s Default Search Path. It provides /lib, /usr/lib, etc., so the linker doesn't panic when looking for standard C libraries.

    [ ] 6. Virtual Filesystem Projection (/proc, /dev, /sys)

        What it fools: Low-level System Calls.

        Detail: Many modern libraries (like Graphics or Networking) fail if they can't read /proc/self. You must bind-mount these from the host into your fake root.

    [ ] 7. OverlayFS Merging (Upper/Lower)

        What it fools: The Filesystem Persistence. It allows you to "install" things (Upper) onto a "read-only" store (Lower) to see the final result without modifying the original package.

    [ ] 8. ldconfig Cache Generation

        What it fools: The Linker’s Search Cache (/etc/ld.so.cache).

        Detail: The linker doesn't actually look at your folders first; it looks at a binary file called the cache. You must run ldconfig -r [chroot_path] to update this cache inside your jail.

## Phase 3: Activation (The Environment)

    [ ] 9. Generation Symlink Switching (The "Nix" Switch)

        What it fools: The User's PATH. By pointing /current-system to a specific store hash, you "activate" a specific version of your entire OS world.

    [ ] 10. Namespace Unsharing (CLONE_NEWNS)

        What it fools: The Kernel's Mount Table.

        Detail: If you use chroot, the whole system sees the mount. If you use Mount Namespaces, Package A and Package B can both have a /usr/lib that points to different things at the same time on the same CPU.

    [ ] 11. Environment Variable Injection (LD_LIBRARY_PATH)

        What it fools: The Linker’s Search Priority. As a "last resort," this ensures your isolated store-paths are checked before the system’s global paths.