#!/bin/sh

# Grab info on the host before entering the namespace
REAL_USER=$(id -un)
REAL_UID=$(id -u)
REAL_HOME="$HOME"
ORIG_PWD="$PWD"

exec unshare --mount --pid --fork --map-root-user sh -c '
    store_dir="$1"
    app="$2"
    u_name="$3"
    u_home="$4"
    orig_pwd="$5"
    u_uid="$6"
    shift 6
    
    forest="/tmp/forest_$$"
    mkdir -p "$forest"
    
    # Mount overlayfs (upperdir gets the writes, lowerdir is read-only packages)
    mount -t overlay overlay -o lowerdir="{lower_dirs}",upperdir="$store_dir",workdir="{store_path_work}" "$forest"
        
    # Mount System Virtual Filesystems
    mount -t proc proc "$forest/proc"
    mount --rbind /dev "$forest/dev"
    mount --rbind /sys "$forest/sys"
    
    # Mount the Standard Bridges
    mount --rbind /home "$forest/home"
    mount --rbind /tmp "$forest/tmp"
    mount --bind {shared_path} "$forest/run"

    # Mount the actual user home directory
    if [ -n "$u_home" ] && [ -d "$u_home" ]; then
        mkdir -p "$forest$u_home"
        mount --rbind "$u_home" "$forest$u_home"
    fi

    # Allow apps to see USBs and secondary Hard Drives
    for dir in /mnt /media /srv /opt; do
        if [ -d "$dir" ]; then
            mkdir -p "$forest$dir"
            mount --rbind "$dir" "$forest$dir"
        fi
    done

    # Provide access to the real host root filesystem
    mkdir -p "$forest/run/host"
    mount --rbind / "$forest/run/host"

    # Graphics and Audio sockets (Wayland, PulseAudio, Pipewire)
    if [ -d "/run/user/$u_uid" ]; then
        mkdir -p "$forest/run/user/$u_uid"
        mount --rbind "/run/user/$u_uid" "$forest/run/user/$u_uid"
    fi

    # DBus socket
    if [ -d "/var/run/dbus" ]; then
        mkdir -p "$forest/var/run/dbus"
        mount --bind /var/run/dbus "$forest/var/run/dbus"
    fi
    
    # DNS Bridge
    if [ -f /etc/resolv.conf ]; then
        mkdir -p "$forest/etc"
        touch "$forest/etc/resolv.conf"
        mount --bind /etc/resolv.conf "$forest/etc/resolv.conf"
    fi

    # Identity & Environment Setup
    export USER="$u_name"
    export HOME="$u_home"
    export LD_LIBRARY_PATH="/usr/lib/x86_64-linux-gnu:/lib/x86_64-linux-gnu:/usr/lib:/lib"
    export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"

    # Enter the chroot and execute the application
    exec chroot "$forest" /bin/sh -c "
        cd \"\$1\" 2>/dev/null || cd \"\$HOME\" 2>/dev/null || cd /
        
        # Ensure the app path is absolute
        case \"\$2\" in
            /*) app=\"\$2\" ;;
            *)  app=\"/\$2\" ;;
        esac
        shift 2
        
        exec \"\$app\" \"\$@\"
    " -- "$orig_pwd" "$app" "$@"

' -- "{store_path}" "{bin_src}" "$REAL_USER" "$REAL_HOME" "$ORIG_PWD" "$REAL_UID" "$@"