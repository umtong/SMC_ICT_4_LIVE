#!/usr/bin/env python3
"""Patch frozen Candidate 11 with v31 efficiency and lower layers."""
from __future__ import annotations

import argparse
from pathlib import Path

from c10_v30_patch import patch as patch_v30


def patch(path: Path) -> None:
    patch_v30(path)
    text = path.read_text(encoding="utf-8")
    old_import = "from c10_v30_overlay import (\n"
    new_import = "from c10_v31_overlay import (\n    certify_sweep_efficiency,\n"
    if text.count(old_import) != 1:
        raise RuntimeError("v31 overlay import marker is not unique")
    text = text.replace(old_import, new_import, 1)
    old_decision = "                leadership = certify_plan(plan, leadership)\n"
    new_decision = (
        "                leadership = certify_plan(plan, leadership)\n"
        "                leadership = certify_sweep_efficiency(\n"
        "                    plan,\n"
        "                    leadership,\n"
        "                    self.logic[symbol],\n"
        "                )\n"
    )
    if text.count(old_decision) != 1:
        raise RuntimeError("v31 efficiency certificate marker is not unique")
    path.write_text(text.replace(old_decision, new_decision, 1), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("path", type=Path)
    args = parser.parse_args()
    patch(args.path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
