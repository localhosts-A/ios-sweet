#!/usr/bin/env python3
"""Package a Sweet payload as the final LZ4 CPIO archive in Dali init_boot."""
import argparse
import os
import subprocess
import tempfile


PARTITION_SIZE = 8 * 1024 * 1024
FINGERPRINT = (
    "alps/hal_mgvi_64_64only_armv82/mgvi_64_64only_armv82:15/"
    "AP3A.240905.015.A2/OS4.0.0.5.XONCN:user/release-keys"
)


def newc_entry(name: bytes, data: bytes, mode: int) -> bytes:
    fields = (1, mode, 0, 0, 1, 0, len(data), 0, 0, 0, 0, len(name) + 1, 0)
    header = b"070701" + b"".join(f"{value:08x}".encode("ascii") for value in fields)
    record = header + name + b"\0"
    record += b"\0" * (-len(record) % 4)
    record += data
    record += b"\0" * (-len(data) % 4)
    return record


def make_archive(payload: bytes) -> bytes:
    return newc_entry(b"sweet.payload", payload, 0o100644) + newc_entry(
        b"TRAILER!!!", b"", 0
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("payload")
    parser.add_argument("output")
    parser.add_argument("--partition-size", type=int, default=PARTITION_SIZE)
    args = parser.parse_args()

    with open(args.payload, "rb") as stream:
        payload = stream.read()
    if len(payload) < 0x2A8 or payload[:8] != b"SWEETXNU":
        parser.error("payload is not a Sweet XNU payload")

    os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix="sweet-initboot-", dir=os.path.dirname(os.path.abspath(args.output))
    ) as tmp:
        cpio_path = os.path.join(tmp, "sweet.cpio")
        lz4_path = os.path.join(tmp, "ramdisk.lz4")
        with open(cpio_path, "wb") as stream:
            stream.write(make_archive(payload))

        subprocess.run(
            [
                "lz4", "-q", "-f", "-12", "-B4", "-BI", "--content-size",
                "--no-frame-crc", cpio_path, lz4_path,
            ],
            check=True,
        )
        with open(lz4_path, "rb") as stream:
            compressed = stream.read()

        temp_image = os.path.join(tmp, "init_boot.img")
        subprocess.run(
            [
                "mkbootimg", "--header_version", "4", "--ramdisk", lz4_path,
                "--pagesize", "4096", "--cmdline", "console=null", "-o", temp_image,
            ],
            check=True,
        )
        image_size = os.path.getsize(temp_image)
        if image_size > args.partition_size:
            parser.error(
                f"init_boot image is {image_size} bytes, larger than the "
                f"{args.partition_size}-byte partition"
            )

        subprocess.run(
            [
                "avbtool", "add_hash_footer", "--image", temp_image,
                "--partition_name", "init_boot", "--partition_size", str(args.partition_size),
                "--algorithm", "NONE", "--salt", "00000000000000000000000000000000",
                "--prop", "com.android.build.init_boot.os_version:15",
                "--prop", f"com.android.build.init_boot.fingerprint:{FINGERPRINT}",
                "--prop", "com.android.build.init_boot.security_patch:2026-09-01",
            ],
            check=True,
        )
        os.replace(temp_image, args.output)
        print(f"payload: {len(payload)} bytes")
        print(f"init_boot ramdisk: {len(compressed)} bytes (LZ4 frame, 64 KiB independent blocks)")
        print(f"init_boot image: {args.partition_size} bytes including AVB footer")
        print(f"wrote {args.output}")


if __name__ == "__main__":
    main()
