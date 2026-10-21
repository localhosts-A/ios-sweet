#!/system/bin/sh
# Run as root on stock Dali Android; survives the ADB cable being removed.
# Read existing kernel interfaces only. Never changes USB role, regulators,
# register values, partition contents or mount state. Debugfs must be mounted.
# Usage: sh dali-usb-host-capture.sh /data/local/tmp/dali-usb-host-capture
set -eu

[ "$(id -u)" = 0 ] || { echo 'root required' >&2; exit 1; }
[ "$(getprop ro.product.device)" = dali ] || { echo 'Dali only' >&2; exit 1; }
out=${1:-/data/local/tmp/dali-usb-host-capture}
case "$out" in /data/local/tmp/dali-usb-host-capture*) ;; *) exit 2 ;; esac
[ ! -e "$out" ] || { echo 'capture directory already exists' >&2; exit 1; }
mkdir -m 700 "$out"

read_file() {
    [ -r "$1" ] || return 0
    printf '\nFILE %s\n' "$1"
    # A slow debugfs handler must not hold the capture indefinitely.
    timeout 5 cat "$1" 2>&1 || true
}

snapshot() {
    destination=$out/$1.txt
    {
        date -u
        read_file /proc/uptime
        for f in /sys/class/usb_role/*/role \
                 /sys/class/typec/port0/data_role \
                 /sys/class/typec/port0/power_role \
                 /sys/class/typec/port0/port_type \
                 /sys/class/typec/port0/orientation \
                 /sys/kernel/debug/usb/16701000.usb0/regs/reg-ippc; do
            read_file "$f"
        done
        printf '\nCLOCKS\n'
        timeout 5 cat /sys/kernel/debug/clk/clk_summary 2>/dev/null |
            grep -E 'usb|xhci|167[037]|clk26m|frmcnt' || true
        printf '\nREGULATORS\n'
        timeout 5 cat /sys/kernel/debug/regulator/regulator_summary 2>/dev/null |
            grep -E 'usb|otg|6379|boost' || true
        printf '\nPHY PROVIDERS\n'
        ls -l /sys/class/phy 2>/dev/null || true
        printf '\nCHARGER USB STATUS\n'
        for name in typec_mode typec_cc_orientation voltage_now reverse_quick_charge; do
            read_file "/sys/class/power_supply/usb/$name"
        done
        # These are named read-only xHCI register views from the Linux driver.
        for controller in /sys/kernel/debug/usb/xhci/*; do
            [ -d "$controller" ] || continue
            for name in reg-cap reg-op reg-runtime; do
                read_file "$controller/$name"
            done
            printf '\nXHCI FILES %s\n' "$controller"
            find "$controller" -maxdepth 2 -type f 2>/dev/null || true
        done
        printf '\nUSB DEVICES\n'
        for dev in /sys/bus/usb/devices/*; do
            [ -f "$dev/idVendor" ] || continue
            printf '\nDEVICE %s\n' "$dev"
            for name in idVendor idProduct manufacturer product bDeviceClass speed; do
                read_file "$dev/$name"
            done
        done
        printf '\nUSB BLOCK DEVICES\n'
        for dev in /sys/class/block/sd*; do
            [ -e "$dev" ] || continue
            path=$(readlink -f "$dev")
            case "$path" in */usb*/*) ;; *) continue ;; esac
            printf '\nBLOCK %s\n' "$path"
            for name in size ro start partition device/vendor device/model; do
                read_file "$dev/$name"
            done
        done
    } > "$destination" 2>&1
}

snapshot device-before
echo waiting-host > "$out/state"
# 10 minute bounded attachment window. No background permanent service.
count=0
while [ "$count" -lt 600 ]; do
    role=$(cat /sys/class/usb_role/*/role 2>/dev/null || true)
    case "$role" in
        *host*)
            snapshot host-attached
            sleep 5
            snapshot host-after-5s
            sleep 10
            snapshot host-after-15s
            echo complete > "$out/state"
            exit 0
            ;;
    esac
    sleep 1
    count=$((count + 1))
done
echo attachment-timeout > "$out/state"
