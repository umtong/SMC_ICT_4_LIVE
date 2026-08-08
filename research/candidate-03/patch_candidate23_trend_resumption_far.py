#!/usr/bin/env python3
"""Add a distinct trend-resumption role for ordinary completed FAR plans.

The preserved policy treats every ordinary failed-auction reclaim as a
countertrend inventory transfer. Consequently, a complete FAR whose proposed
direction is already supported by both the candidate's trailing auction and
the market-wide median is rejected as SEMANTIC_FAR_NOT_COUNTERTREND.

That conflates two different SMC/ICT scenarios:

* countertrend failed auction: a liquidity raid exhausts the controlling move;
* trend-resumption failed auction: a pullback raids liquidity against the
  controlling move, reclaims, and resumes with synchronized markets.

Candidate 23 adds the second categorical role without changing any numeric
threshold. Trend resumption requires unanimous event-direction peers, both
local and market trailing auctions aligned with the plan, top-half event price
discovery, and the already-existing efficiency and standardized-displacement
minimums. Mixed trailing auctions and dominant-quorum-only events remain
rejected. Entry, target, costs, exact 3% NAV risk and one global slot are
unchanged.
"""
from __future__ import annotations

import argparse
from pathlib import Path

OLD = '''        if candidate_trend >= 0.0 or market_trend >= 0.0:\n            return _with(decision, False, "SEMANTIC_FAR_NOT_COUNTERTREND")\n'''

NEW = '''        if candidate_trend > 0.0 and market_trend > 0.0:\n            if not all_peers_aligned:\n                return _with(\n                    decision,\n                    False,\n                    "SEMANTIC_FAR_TREND_RESUMPTION_REQUIRES_UNANIMOUS_PEERS",\n                )\n            top_half = max(1, symbol_count // 2)\n            if event_rank > top_half:\n                return _with(\n                    decision,\n                    False,\n                    "SEMANTIC_FAR_TREND_RESUMPTION_EVENT_NOT_TOP_HALF",\n                )\n            if (\n                decision.event_path_efficiency is None\n                or decision.event_path_efficiency < minimum_event_efficiency\n            ):\n                return _with(\n                    decision,\n                    False,\n                    "SEMANTIC_FAR_TREND_RESUMPTION_INEFFICIENT_PATH",\n                )\n            if (\n                decision.event_standardized_displacement is None\n                or decision.event_standardized_displacement < minimum_event_displacement\n            ):\n                return _with(\n                    decision,\n                    False,\n                    "SEMANTIC_FAR_TREND_RESUMPTION_INSUFFICIENT_DISPLACEMENT",\n                )\n            return _with(\n                decision,\n                True,\n                "SEMANTIC_FAR_TREND_RESUMPTION_SYNCHRONIZED",\n            )\n        if candidate_trend >= 0.0 or market_trend >= 0.0:\n            return _with(decision, False, "SEMANTIC_FAR_MIXED_TRAILING_AUCTION")\n'''


def apply(path: Path) -> bool:
    source = path.read_text(encoding="utf-8")
    if "SEMANTIC_FAR_TREND_RESUMPTION_SYNCHRONIZED" in source:
        return False
    if source.count(OLD) != 1:
        raise RuntimeError(f"expected one FAR role anchor, found {source.count(OLD)}")
    path.write_text(source.replace(OLD, NEW, 1), encoding="utf-8")
    return True


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("semantic_market", type=Path)
    args = parser.parse_args()
    print(f"candidate23 trend-resumption FAR patch applied={apply(args.semantic_market)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
