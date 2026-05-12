#!/bin/sh

# Host-side user/session information
REAL_USER=$(id -un)
REAL_UID=$(id -u)
REAL_HOME="$HOME"
ORIG_PWD="$PWD"

unshare --mount --pid --fork --map-root-user sh -c '

    set -eu

    app="$1"
    u_name="$2"
    u_home="$3"
    orig_pwd="$4"
    u_uid="$5"
    shift 5

    jail="/tmp/forest_$$"

    cleanup() {{
        # umount -l handles FUSE filesystems perfectly. 
        # When unmounted, the fuse-overlayfs daemon automatically dies.
        umount -l -R "$jail" 2>/dev/null || true
        rm -rf "$jail"
    }}

    trap cleanup EXIT

    mkdir -p "$jail"

    # Pre-create required mountpoints
    mkdir -p \
        "$jail/proc" \
        "$jail/dev" \
        "$jail/sys" \
        "$jail/home" \
        "$jail/tmp" \
        "$jail/run" \
        "$jail/etc" \
        "$jail/var/run"

    # OverlayFS root using fuse-overlayfs
    # Note: This runs synchronously. It will only return to the script
    # after the mount is fully established and daemonized.
    fuse-overlayfs \
        -o lowerdir="{lower_dirs}" \
        -o upperdir="{upper_path}" \
        -o workdir="{work_path}" \
        "$jail" 2>/dev/null

    if ! mountpoint -q "$jail"; then
        echo "ERROR: fuse-overlayfs failed to mount at $jail"
        exit 1
    fi

    # Core virtual filesystems
    mount -t proc proc "$jail/proc"
    mount --rbind /dev "$jail/dev"
    mount --rbind /sys "$jail/sys"

    # Store bridge
    mkdir -p "$jail{store_root}"

    mount --bind "{store_root}" "$jail{store_root}"

    # Remount readonly
    mount -o remount,ro,bind "$jail{store_root}"

    # Standard bridges
    mount --rbind /home "$jail/home"
    mount --rbind /tmp "$jail/tmp"

    mkdir -p "$jail/run"
    mount --bind "{shared_path}" "$jail/run"

    # User home bridge
    if [ -n "$u_home" ] && [ -d "$u_home" ]; then
        mkdir -p "$jail$u_home"
        mount --rbind "$u_home" "$jail$u_home"
    fi

    # External media bridges
    for dir in /mnt /media /srv /opt; do
        if [ -d "$dir" ] && [ "$dir" != "{store_root}" ]; then
            mkdir -p "$jail$dir"
            mount --rbind "$dir" "$jail$dir"
        fi
    done

    # Host filesystem access
    mkdir -p "$jail/run/host"
    mount --rbind / "$jail/run/host"

    # Runtime sockets (Wayland/Pipewire/Pulse/etc)
    if [ -d "/run/user/$u_uid" ]; then
        mkdir -p "$jail/run/user/$u_uid"
        mount --rbind "/run/user/$u_uid" "$jail/run/user/$u_uid"
    fi

    # DBus
    if [ -d "/var/run/dbus" ]; then
        mkdir -p "$jail/var/run/dbus"
        mount --bind /var/run/dbus "$jail/var/run/dbus"
    fi

    # DNS
    if [ -f /etc/resolv.conf ]; then
        mkdir -p "$jail/etc"
        touch "$jail/etc/resolv.conf"
        mount --bind /etc/resolv.conf "$jail/etc/resolv.conf"
    fi

    # Environment
    export USER="$u_name"
    export HOME="$u_home"
    export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"

    # Enter container
    exec chroot "$jail" /bin/sh -c "
        cd \"\$1\" 2>/dev/null || \
        cd \"\$HOME\" 2>/dev/null || \
        cd /

        case \"\$2\" in
            /*)
                app=\"\$2\"
                ;;
            *)
                app=\"/\$2\"
                ;;
        esac

        shift 2

        exec \"\$app\" \"\$@\"
    " -- "$orig_pwd" "$app" "$@"

' -- "{bin_src}" "$REAL_USER" "$REAL_HOME" "$ORIG_PWD" "$REAL_UID" "$@"

ret=$?

if [ "$ret" -ne 0 ]; then
    echo
    echo "========== KERNEL LOGS =========="
    dmesg | tail -n 20 || true
    echo "================================="
fi

exit "$ret"