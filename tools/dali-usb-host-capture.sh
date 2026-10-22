#!/system/bin/sh
# Run as root on stock Dali Android; survives the ADB cable being removed.
# Read existing kernel interfaces and use MT6379's standard IIO one-shot
# conversions for USB-DP/USB-DM and IBUS. Never changes USB role, regulator
# policy, partition contents or mount state. Debugfs must be mounted.
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

# regmap's fixed-width text views are seekable. Read only these documented
# USB configuration/trim addresses, not a full PMIC dump or interrupt status.
read_usb_analog() {
    printf '\nTARGETED MT6379 USB CONFIGURATION (READ ONLY)\n'
    map=/sys/kernel/debug/regmap/0-0e/registers
    [ -r "$map" ] || return 0
    identity=$(timeout 3 dd if="$map" bs=8 count=1 2>/dev/null || true)
    case "$identity" in '000: 74') ;; *)
        printf 'regmap layout/chip mismatch: %s\n' "$identity"; return 0 ;; esac
    printf '%s\n' "$identity"
    # d00..d35: repeater analog controls; d88..d8b: normal-mode controls.
    timeout 3 dd if="$map" bs=8 skip=3328 count=54 2>/dev/null || true
    timeout 3 dd if="$map" bs=8 skip=3464 count=4 2>/dev/null || true
    for register in 3473 3474 3476; do
        timeout 3 dd if="$map" bs=8 skip="$register" count=1 2>/dev/null || true
    done
    # Read-only efuse shadows and the native probe's interrupt-mask register.
    for range in '14 1' '803 5' '897 2'; do
        set -- $range
        timeout 3 dd if="$map" bs=8 skip="$1" count="$2" 2>/dev/null || true
    done
    printf '\nTARGETED MT6373 VUSB CONFIGURATION (READ ONLY)\n'
    map=/sys/kernel/debug/regmap/0-05/registers
    [ -r "$map" ] || return 0
    control=$(timeout 3 dd if="$map" bs=9 skip=7061 count=1 2>/dev/null || true)
    case "$control" in '1b95: '*) printf '%s\n' "$control" ;; *)
        printf 'regmap layout mismatch: %s\n' "$control"; return 0 ;; esac
    timeout 3 dd if="$map" bs=9 skip=7692 count=2 2>/dev/null || true
}

read_tcpc_status() {
    printf '\nTARGETED MT6379 TCPC STATUS (READ ONLY)\n'
    map=/sys/kernel/debug/regmap/0-0e/registers
    [ -r "$map" ] || return 0
    identity=$(timeout 3 dd if="$map" bs=8 count=1 2>/dev/null || true)
    case "$identity" in '000: 74') ;; *)
        printf 'regmap layout/chip mismatch: %s\n' "$identity"; return 0 ;; esac
    printf '%s\n' "$identity"
    # TCPCI ROLE_CTRL, CC_STATUS, FAULT_STATUS and POWER_STATUS on SID14.
    for register in 1045 1050 1053 1054; do
        timeout 3 dd if="$map" bs=8 skip="$register" count=1 2>/dev/null || true
    done
}

read_usb_adc() {
    printf '\nMT6379 USB ANALOG ADC (IIO ONE-SHOT; IBUS DIRECTION NOT ASSUMED)\n'
    found=0
    for device in /sys/bus/iio/devices/iio:device*; do
        [ -r "$device/name" ] || continue
        name=$(timeout 2 cat "$device/name" 2>/dev/null || true)
        case "$name" in *6379*|*6373*) ;; *) continue ;; esac
        found=1
        read_file "$device/name"
        for input in "$device"/in_voltage2_* "$device"/in_voltage_usb-dp_* \
                    "$device"/in_voltage7_* "$device"/in_voltage_usb-dm_* \
                    "$device"/in_current5_* "$device"/in_current_ibus_*; do
            read_file "$input"
        done
    done
    [ "$found" -eq 1 ] || printf 'MT6379 IIO device not exposed\n'
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
        read_usb_analog
        read_tcpc_status
        read_usb_adc
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

final_summary() {
    printf 'DALI NATIVE USB CAPTURE FINAL: state=%s\n' \
        "$(cat "$out/state" 2>/dev/null || echo unknown)"
    for name in host-attached host-after-5s host-after-15s device-before; do
        file=$out/$name.txt
        [ -r "$file" ] || continue
        printf '\n--- %s ---\n' "$name"
        grep -E '^(FILE |DEVICE |BLOCK |TARGETED |MT6379 |[0-9a-f]{3,4}:)|role$|orientation$|speed$|size$|in_voltage|in_current|SSUSB_IP_|SSUSB_OTG_|DALI NATIVE' \
            "$file" || true
    done
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
            final_summary > "$out/FINAL.txt"
            cat "$out/FINAL.txt"
            exit 0
            ;;
    esac
    sleep 1
    count=$((count + 1))
done
echo attachment-timeout > "$out/state"
final_summary > "$out/FINAL.txt"
cat "$out/FINAL.txt"
