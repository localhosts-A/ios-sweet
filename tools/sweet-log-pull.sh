#!/bin/sh
# Read the SWEET console log (kernel patch 0047) from recovery with adb root.
#   tools/sweet-log-pull.sh [-s SERIAL] [-p unixnu_logs|userdata] OUT.txt
# `unixnu_logs` reads from the start of its dedicated raw partition. `userdata`
# reads the legacy log area from the end of that partition.
set -eu

serial=
partition=unixnu_logs
while [ "$#" -gt 0 ]; do
	case "$1" in
	-h|--help)
		echo "Usage: $(basename "$0") [-s SERIAL] [-p unixnu_logs|userdata] OUT.txt"
		echo "Pull the 4 MiB kernel log ring via recovery ADB (default partition: unixnu_logs)."
		exit 0
		;;
	-s)
		[ "$#" -ge 2 ] || { echo "Error: -s requires a device serial" >&2; exit 2; }
		serial=$2
		shift 2
		;;
	-p|--partition)
		[ "$#" -ge 2 ] || { echo "Error: -p requires a partition name" >&2; exit 2; }
		partition=$2
		shift 2
		;;
	--)
		shift
		break
		;;
	-*)
		echo "Error: unknown option $1" >&2
		exit 2
		;;
	*)
		break
		;;
	esac
done

out=${1:?usage: sweet-log-pull.sh [-s SERIAL] [-p PARTITION] OUT.txt}
[ "$#" -eq 1 ] || { echo "Error: unexpected extra arguments" >&2; exit 2; }
case "$partition" in
	unixnu_logs|userdata) ;;
	*) echo "Error: supported partitions are unixnu_logs and userdata" >&2; exit 2 ;;
esac
command -v adb >/dev/null 2>&1 || { echo "Error: adb not found in PATH" >&2; exit 1; }

adb_cmd() {
	if [ -n "$serial" ]; then
		adb -s "$serial" "$@"
	else
		adb "$@"
	fi
}

remote="for d in /dev/block/by-name/$partition /dev/block/bootdevice/by-name/$partition; do [ -e \"\$d\" ] && readlink -f \"\$d\" && break; done"
dev=$(adb_cmd shell "$remote" 2>/dev/null | tr -d '\r')
[ -n "$dev" ] || { echo "$partition block device not found on connected device" >&2; exit 1; }
bytes=$(adb_cmd shell blockdev --getsize64 "$dev" | tr -d '\r')
blocks=$((bytes / 4096))
if [ "$partition" = "unixnu_logs" ]; then
	[ "$blocks" -ge 1025 ] || { echo "unixnu_logs is too small for the log area" >&2; exit 1; }
	skip=0
else
	[ "$blocks" -ge 1025 ] || { echo "userdata is too small for the legacy log area" >&2; exit 1; }
	skip=$((blocks - 1025))
fi
adb_cmd exec-out dd "if=$dev" bs=4096 "skip=$skip" count=1025 2>/dev/null > "$out.bin"
actual=$(stat -c %s "$out.bin")
[ "$actual" -eq $((1025 * 4096)) ] || { echo "short partition read: got $actual bytes" >&2; exit 1; }
echo "$partition ($dev): $bytes bytes, read blocks $skip..$((skip + 1024)) ($actual bytes)" >&2
python3 "$(dirname "$0")/sweet-log.py" "$out.bin" "$out"
