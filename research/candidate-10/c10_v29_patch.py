#!/usr/bin/env python3
"""Patch frozen Candidate 11 with v29 draw certificate and v28/v27 layers."""
from __future__ import annotations

import argparse
from pathlib import Path

from c10_v28_patch import patch as patch_v28


def patch(path: Path) -> None:
    patch_v28(path)
    text = path.read_text(encoding="utf-8")
    old_import = "from c10_v28_overlay import (\n"
    new_import = "from c10_v29_overlay import (\n    certify_plan,\n"
    if text.count(old_import) != 1:
        raise RuntimeError("v29 overlay import marker is not unique")
    text = text.replace(old_import, new_import, 1)
    old_decision = '''                plan.details["market_leadership"] = leadership.to_dict()
                if not leadership.approved:
'''
    new_decision = '''                leadership = certify_plan(plan, leadership)
                plan.details["market_leadership"] = leadership.to_dict()
                if not leadership.approved:
'''
    if text.count(old_decision) != 1:
        raise RuntimeError("v29 plan certificate marker is not unique")
    text = text.replace(old_decision, new_decision, 1)
    path.write_text(text, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("path", type=Path)
    args = parser.parse_args()
    patch(args.path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
