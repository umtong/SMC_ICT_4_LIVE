#!/usr/bin/env python3
"""Reclassify moderate countertrend FAR as an unresolved auction.

The frozen 112-day development replay attributed 15 ordinary FAR fills to
SEMANTIC_FAR_MODERATE_COUNTERTREND_UNANIMOUS. They produced 5 wins, 10 losses
and approximately -9,998 USDT after native fills and fees. The state is
structurally ambiguous: all peers reclaim over the short event interval, yet
the candidate and market-wide trailing auctions remain adverse. A short-horizon
unanimous bounce is therefore not sufficient evidence that inventory transfer
has completed.

Candidate 22 does not add a fitted threshold and does not reverse those plans.
It changes this categorical state from immediate approval to
SEMANTIC_FAR_MODERATE_COUNTERTREND_CONTESTED. The complete local FAR plan is
rejected before order submission and enters the existing semantic-rejection
watch. Only a later two-close acceptance beyond the swept boundary may create a
continuation candidate, which still must pass Candidate 21's cost and
cross-market gates. The stronger dominant-peer-quorum FAR route and completed
I7 session substitutions remain unchanged.
"""
from __future__ import annotations

import argparse
from pathlib import Path

REASONS_OLD = '''SEMANTIC_REJECTED_FAR_REASONS = frozenset(\n    {\n        "SEMANTIC_FAR_REQUIRES_DOMINANT_PEER_RECLAIM",\n        "SEMANTIC_FAR_UNRESOLVED_ADVERSE_AUCTION",\n    }\n)\n'''
REASONS_NEW = '''SEMANTIC_REJECTED_FAR_REASONS = frozenset(\n    {\n        "SEMANTIC_FAR_REQUIRES_DOMINANT_PEER_RECLAIM",\n        "SEMANTIC_FAR_UNRESOLVED_ADVERSE_AUCTION",\n        "SEMANTIC_FAR_MODERATE_COUNTERTREND_CONTESTED",\n    }\n)\n'''

SEMANTIC_OLD = '''        if all_peers_aligned:\n            return _with(decision, True, "SEMANTIC_FAR_MODERATE_COUNTERTREND_UNANIMOUS")\n'''
SEMANTIC_NEW = '''        if all_peers_aligned:\n            return _with(\n                decision,\n                False,\n                "SEMANTIC_FAR_MODERATE_COUNTERTREND_CONTESTED",\n            )\n'''


def replace_once(path: Path, old: str, new: str, label: str) -> None:
    source = path.read_text(encoding="utf-8")
    if new in source:
        return
    if source.count(old) != 1:
        raise RuntimeError(f"expected one {label} anchor, found {source.count(old)}")
    path.write_text(source.replace(old, new, 1), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("candidate_state", type=Path)
    parser.add_argument("semantic_market", type=Path)
    args = parser.parse_args()
    replace_once(args.candidate_state, REASONS_OLD, REASONS_NEW, "eligible-reason set")
    replace_once(args.semantic_market, SEMANTIC_OLD, SEMANTIC_NEW, "moderate FAR decision")
    print("candidate22 contested-countertrend patch applied")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
