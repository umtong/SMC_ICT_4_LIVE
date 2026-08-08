#!/usr/bin/env python3
"""Patch v49 with the v50 internal dealing-range engine."""
from __future__ import annotations

import argparse
from pathlib import Path

from c10_v49_patch import patch as patch_v49


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one marker, found {count}")
    return text.replace(old, new, 1)


def patch(path: Path) -> None:
    patch_v49(path)
    text = path.read_text(encoding="utf-8")
    text = replace_once(
        text,
        "from c10_v40_state import SourceEquilibriumFailedAuctionEngine as RegionalHandoffAuctionEngine\n",
        "from c10_v50_state import InternalDealingRangeFailedAuctionEngine as RegionalHandoffAuctionEngine\n",
        "v50 state-engine import",
    )
    path.write_text(text, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("path", type=Path)
    args = parser.parse_args()
    patch(args.path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
