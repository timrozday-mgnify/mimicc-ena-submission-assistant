#!/usr/bin/env python3
"""Patch DataHarmonizer toolbar setup to tolerate an absent current grid."""

from __future__ import annotations

import sys
from pathlib import Path


def main() -> int:
    if len(sys.argv) != 2:
        print("Usage: patch_dataharmonizer_toolbar.py <dataharmonizer_dir>", file=sys.stderr)
        return 2

    path = Path(sys.argv[1]) / "lib" / "Toolbar.js"
    text = path.read_text(encoding="utf-8")
    old = """    this.setupJumpToModal(dh);
    this.setupSectionMenu(dh);
    this.setupFillModal(dh);
    this.hideValidationResultButtons();
"""
    new = """    if (dh) {
      this.setupJumpToModal(dh);
      this.setupSectionMenu(dh);
      this.setupFillModal(dh);
    }
    this.hideValidationResultButtons();
"""
    if new in text:
        return 0
    if old not in text:
        print(f"Patch target not found in {path}", file=sys.stderr)
        return 1
    path.write_text(text.replace(old, new, 1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
