#!/usr/bin/env python3
"""Run one fixed private-ichi reconstruction for full-vs-512 history identity."""
from __future__ import annotations

import argparse
from datetime import date
import importlib.util
import json
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
SOURCE = HERE / "ichi5m_private_recon_v1_campaign.py"
SPEC = importlib.util.spec_from_file_location("candidate57_private_ichi_identity_base", SOURCE)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"cannot import {SOURCE}")
BASE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = BASE
SPEC.loader.exec_module(BASE)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--label", required=True, choices=("full", "finite"))
    args = parser.parse_args()
    root = ROOT / ".work" / "candidate-57-private-ichi-fast-v2-identity"
    BASE.WORK = root / args.label / "work"
    BASE.ARTIFACTS = root / args.label / "artifacts"
    BASE.EVIDENCE = root / args.label / "evidence"
    BASE.CACHE = ROOT / ".cache" / "candidate-57-private-ichi-fast-v2-identity"
    for path in (BASE.WORK, BASE.ARTIFACTS, BASE.EVIDENCE, BASE.CACHE):
        path.mkdir(parents=True, exist_ok=True)
    stage = BASE.Stage("identity", date(2026, 2, 15), date(2026, 2, 18))
    row = BASE.run_case(stage, "structural_anchor_long")
    target = root / f"{args.label}.json"
    target.write_text(
        json.dumps(row, indent=2, sort_keys=True, allow_nan=False, default=str)
        + "\n",
        encoding="utf-8",
    )
    return 0 if row.get("produced") and BASE.account_ok(row) else 2


if __name__ == "__main__":
    raise SystemExit(main())
