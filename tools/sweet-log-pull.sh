#!/bin/sh
# Read the SWEET console log (kernel patch 0047) from recovery with adb root.
#   sweet-log-pull.sh [-s SERIAL] [-p unixnu_logs|userdata] --slot 0|1|2 OUT.txt
#   sweet-log-pull.sh [-s SERIAL] --all [--expect-run-id ID] OUT_PREFIX
# `unixnu_logs` preserves its existing DALIUFSLOG1 data in the first 16 MiB.
# G4 slots begin at +16 MiB and are 1025 blocks each. Choose a slot explicitly
# or capture all; keep the .bin even when decoding fails.
# `userdata` reads the legacy log area from the end of that partition.
set -euC

serial=
partition=unixnu_logs
slot=0
slot_set=0
all=0
expected=
expected_set=0
while [ "$#" -gt 0 ]; do
	case "$1" in
	-h|--help)
		echo "Usage: $(basename "$0") [-s SERIAL] [-p unixnu_logs|userdata] --slot 0|1|2 OUT.txt"
		echo "       $(basename "$0") [-s SERIAL] --all [--expect-run-id ID] OUT_PREFIX"
		echo "Pull and inspect three bounded slots without replacing existing files."
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
	--slot)
		[ "$#" -ge 2 ] || { echo "Error: --slot requires 0, 1, or 2" >&2; exit 2; }
		case "$2" in 0|1|2) slot=$2 ;; *) echo "Error: invalid slot" >&2; exit 2 ;; esac
		slot_set=1
		shift 2
		;;
	--all)
		all=1
		shift
		;;
	--expect-run-id)
		[ "$#" -ge 2 ] || { echo "Error: missing expected run-id" >&2; exit 2; }
		expected=$2
		expected_set=1
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
if [ "$all" -eq 1 ]; then
	[ "$partition" = unixnu_logs ] && [ "$slot_set" -eq 0 ] || { echo "--all requires unixnu_logs without --slot" >&2; exit 2; }
elif [ "$slot_set" -eq 0 ]; then
	echo "specify --slot or --all; slot 0 may belong to an older boot" >&2; exit 2
fi
if [ "$expected_set" -eq 1 ]; then
	[ "${#expected}" -eq 32 ] || { echo "invalid expected run-id" >&2; exit 2; }
	case "$expected" in *[!0-9a-f]*) echo "invalid expected run-id" >&2; exit 2 ;; esac
fi
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
if [ "$all" -eq 1 ]; then
	[ "$blocks" -ge 7171 ] || { echo "unixnu_logs too small for three slots" >&2; exit 1; }
	for i in 0 1 2; do
		if [ -e "$out.slot$i.bin" ] || [ -L "$out.slot$i.bin" ]; then
			echo "refusing to replace $out.slot$i.bin" >&2; exit 1
		fi
	done
	for i in 0 1 2; do
		skip=$((4096 + i * 1025))
		adb_cmd exec-out dd "if=$dev" bs=4096 "skip=$skip" count=1025 status=none 2>/dev/null > "$out.slot$i.bin"
		actual=$(stat -c %s "$out.slot$i.bin")
		[ "$actual" -eq $((1025 * 4096)) ] || { echo "short slot $i read: $actual bytes; raw kept" >&2; exit 1; }
	done
	matches=0
	for i in 0 1 2; do
		if [ "$expected_set" -eq 1 ]; then
			info=$(python3 "$(dirname "$0")/sweet-log.py" --inspect --expect-run-id "$expected" "$out.slot$i.bin")
		else
			info=$(python3 "$(dirname "$0")/sweet-log.py" --inspect "$out.slot$i.bin")
		fi
		printf 'slot %s %s\n' "$i" "$info"
		case "$info" in *'"match": true'*) matches=$((matches + 1)) ;; esac
	done
	if [ "$expected_set" -eq 1 ]; then
		if [ "$matches" -gt 1 ]; then
			printf 'run-id-status ambiguous matches=%s; no slot selected\n' "$matches"
			exit 3
		elif [ "$matches" -eq 1 ]; then
			printf 'run-id-status image-id-only; boot unverified; payload unverified\n'
			exit 5
		else
			printf 'run-id-status no-header-match\n'
			exit 4
		fi
	fi
	exit 0
fi
if [ "$partition" = "unixnu_logs" ]; then
	skip=$((4096 + slot * 1025))
	[ "$blocks" -ge $((skip + 1025)) ] || { echo "unixnu_logs is too small for slot $slot" >&2; exit 1; }
else
	[ "$slot" -eq 0 ] || { echo "userdata supports only slot 0" >&2; exit 1; }
	[ "$blocks" -ge 1025 ] || { echo "userdata is too small for the legacy log area" >&2; exit 1; }
	skip=$((blocks - 1025))
fi
if [ -e "$out" ] || [ -L "$out" ] || [ -e "$out.bin" ] || [ -L "$out.bin" ]; then
	echo "refusing to replace prior output or raw capture" >&2; exit 1
fi
adb_cmd exec-out dd "if=$dev" bs=4096 "skip=$skip" count=1025 status=none 2>/dev/null > "$out.bin"
actual=$(stat -c %s "$out.bin")
[ "$actual" -eq $((1025 * 4096)) ] || { echo "short partition read: got $actual bytes" >&2; exit 1; }
echo "$partition ($dev): $bytes bytes, read blocks $skip..$((skip + 1024)) ($actual bytes)" >&2
if [ "$expected_set" -eq 1 ]; then
	python3 "$(dirname "$0")/sweet-log.py" --expect-run-id "$expected" "$out.bin" "$out"
	printf 'run-id-status image-id-only; boot unverified; payload unverified; other slots not checked\n'
	exit 5
else
	python3 "$(dirname "$0")/sweet-log.py" "$out.bin" "$out"
fi
