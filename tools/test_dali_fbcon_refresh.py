#!/usr/bin/env python3
"""Guard the Dali fbcon-to-DSI refresh integration in XNU patch 0084."""

from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]
PATCH = ROOT / "xnu/patches/0084-sweet-dali-fbcon-refresh.patch"
BASE_PATCH = ROOT / "xnu/patches/0083-sweet-dali-command-refresh.patch"


def patch_text() -> str:
    return (PATCH if PATCH.exists() else BASE_PATCH).read_text()


def file_diff(text: str, path: str) -> str:
    for block in text.split("diff --git ")[1:]:
        if f"b/{path}\n" in block:
            return block
    return ""


class DaliFbconRefreshPatchTest(unittest.TestCase):
    def test_newline_requests_dsi_refresh_after_fbcon_row_update(self):
        block = file_diff(patch_text(), "pexpert/arm/pe_serial.c")
        self.assertRegex(
            block,
            r"fbcon_newline\(void\)[\s\S]{0,300}\+\s*sweet_early_refresh\(\);",
        )

    def test_refresh_api_remains_usable_after_early_console_shutdown(self):
        block = file_diff(patch_text(), "osfmk/arm64/sweet_early.c")
        self.assertRegex(block, r"\+\s*if \(!early_display_ready \|\| early_flushing\)")
        self.assertRegex(block, r"\+sweet_early_refresh\(void\)")
        self.assertRegex(block, r"\+\s*dali_display_flush\(\);")


if __name__ == "__main__":
    unittest.main()
