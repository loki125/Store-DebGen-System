#!/bin/sh

# 1. Grab info on the host before we enter the bubble
REAL_USER=$(id -un)
REAL_HOME="/home/$REAL_USER"

exec unshare --mount --pid --fork --map-root-user sh -c '
    forest="$1"
    app="$2"
    u_name="$3"
    u_home="$4"
    
    # Prepare the Forest Root
    mount --bind "$forest" "$forest"
    
    # Mount System Virtual Filesystems
    mount -t proc proc "$forest/proc"
    mount --rbind /dev "$forest/dev"
    mount --rbind /sys "$forest/sys"
    
    # Mount the Bridges
    mount --rbind /home "$forest/home"
    mount --rbind /tmp "$forest/tmp"
    mount --bind {shared_path} "$forest/run"
    
    # DNS Bridge
    if [ -f /etc/resolv.conf ]; then
        touch "$forest/etc/resolv.conf"
        mount --bind /etc/resolv.conf "$forest/etc/resolv.conf"
    fi

    # Identity & Environment Setup
    export USER="$u_name"
    export HOME="$u_home"
    export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"

    # Enter the jail and execute the app
    exec chroot "$forest" "$app" "$@"
' -- "{store_path}" "{bin_src}" "$REAL_USER" "$REAL_HOME" "$@"