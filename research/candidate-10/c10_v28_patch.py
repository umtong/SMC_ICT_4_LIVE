#!/usr/bin/env python3
"""Patch frozen Candidate 11 with v27 costs and the v28 resolution gate."""
from __future__ import annotations

import argparse
from pathlib import Path

from c10_v27_patch import patch as patch_v27


def patch(path: Path) -> None:
    patch_v27(path)
    text = path.read_text(encoding="utf-8")
    old = "from c10_v27_overlay import (\n"
    new = "from c10_v28_overlay import (\n"
    if text.count(old) != 1:
        raise RuntimeError("v28 overlay import marker is not unique")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("path", type=Path)
    args = parser.parse_args()
    patch(args.path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
