#!/usr/bin/env python3
"""Apply v64 execution fail-close contracts and replace its detector with v65."""
from __future__ import annotations

import argparse
from pathlib import Path

from c10_v64_patch import patch as patch_v64


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one marker, found {count}")
    return text.replace(old, new, 1)


def patch(path: Path) -> None:
    patch_v64(path)
    text = path.read_text(encoding="utf-8")
    text = replace_once(
        text,
        "from c10_v64_intraday_delivery import (\n"
        "    IntradayDeliveryContinuationEngine as RegionalHandoffAuctionEngine,\n"
        ")\n",
        "from c10_v65_breakout_resolution import (\n"
        "    BreakoutResolutionAuctionEngine as RegionalHandoffAuctionEngine,\n"
        ")\n",
        "v65 breakout-resolution engine",
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
