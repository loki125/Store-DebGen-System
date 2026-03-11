#!/bin/sh
# Generation Wrapper for @@PKG_NAME@@
# Target Binary: @@BIN_NAME@@

exec unshare --mount --pid --fork --map-root-user sh -c '
    forest="$1"
    shift
    app="$1"
    shift
    
    # 1. Prepare the Forest Root
    mount --bind "$forest" "$forest"
    
    # 2. Mount System Virtual Filesystems
    mount -t proc proc "$forest/proc"
    mount --rbind /dev "$forest/dev"
    mount --rbind /sys "$forest/sys"

    # 3. Enter the jail and execute the app
    # We use exec so the app takes over the PID
    exec chroot "$forest" "$app" "$@"
' -- "@@FOREST_PATH@@" "@@BIN_SRC@@" "$@"