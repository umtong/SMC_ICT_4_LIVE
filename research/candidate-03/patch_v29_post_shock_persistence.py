#!/usr/bin/env python3
"""Remove only V29's post-shock leader-persistence requirement.

The initial ETH/SOL/XRP consensus shock remains mandatory. BTC must still be a
causal underreactor, begin the catch-up on the next completed block, and defend
a completed one-minute pullback with futures/spot flow. The ablation recognizes
that once information has diffused to BTC, the leaders need not continue moving
on every confirmation and retest bar.
"""
from __future__ import annotations

import argparse
from pathlib import Path

CONFIRM_OLD = '''            or direction * float(confirm.btcusdt_spot_flow) <= 0.0
            or len(aligned_leaders(confirm, direction)) < 2
        ):
'''
CONFIRM_NEW = '''            or direction * float(confirm.btcusdt_spot_flow) <= 0.0
        ):
'''
RETEST_OLD = '''            or direction * minute_flow(row, "BTCUSDT", "spot") <= 0.0
            or leader_minute_alignment(row, direction) < 2
        ):
'''
RETEST_NEW = '''            or direction * minute_flow(row, "BTCUSDT", "spot") <= 0.0
        ):
'''
POLICY_OLD = '''            "spot/futures flow confirmations; BTC underreaction; second-block "
            "catch-up; completed pullback defense; no evaluation outcomes"
'''
POLICY_NEW = '''            "spot/futures flow confirmations at event origin; BTC underreaction; "
            "second-block BTC catch-up; completed BTC pullback defense; leaders "
            "need not persist after information diffusion; no evaluation outcomes"
'''


def apply(path: Path) -> bool:
    source = path.read_text(encoding="utf-8")
    changed = False
    for old, new in (
        (CONFIRM_OLD, CONFIRM_NEW),
        (RETEST_OLD, RETEST_NEW),
        (POLICY_OLD, POLICY_NEW),
    ):
        if new in source:
            continue
        if old not in source:
            raise RuntimeError("V29 post-shock persistence block not found")
        source = source.replace(old, new, 1)
        changed = True
    if changed:
        path.write_text(source, encoding="utf-8")
    return changed


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "path",
        type=Path,
        nargs="?",
        default=Path("research/candidate-03/derive_nt_lvcfr_v29_signals.py"),
    )
    args = parser.parse_args()
    print(f"V29 post-shock persistence patch applied={apply(args.path)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
