#!/usr/bin/env python3
"""Patch v47 with the v62 isolated extreme-transfer router."""
from __future__ import annotations

import argparse
from pathlib import Path

from c10_long_interval_event_identity_patch import patch as patch_event_identity
from c10_v47_patch import patch as patch_v47


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one marker, found {count}")
    return text.replace(old, new, 1)


def patch(path: Path) -> None:
    patch_event_identity(path.with_name("logic.py"))
    patch_v47(path)
    text = path.read_text(encoding="utf-8")
    text = replace_once(
        text,
        "from c10_v47_overlay import (\n",
        "from c10_v62_overlay import (\n"
        "    classify_isolated_extreme_transfer,\n",
        "v62 overlay import",
    )
    text = replace_once(
        text,
        "                candidate = Candidate(\n",
        '''                transfer_state = classify_isolated_extreme_transfer(
                    plan,
                )
                plan.details["isolated_extreme_transfer_router"] = (
                    transfer_state.details
                )
                if not transfer_state.approved:
                    self.logic[symbol].mark_rejected(
                        plan,
                        ts_ns,
                        transfer_state.reason,
                        transfer_state.details,
                    )
                    self._capture_events(symbol)
                    self.rejections.append({
                        "type": "ISOLATED_EXTREME_TRANSFER_REJECTED",
                        "observed_ts_ns": plan.observed_ts_ns,
                        "scenario_id": plan.scenario_id,
                        "symbol": symbol,
                        "direction": plan.direction.value,
                        "reason": transfer_state.reason,
                        "state": transfer_state.state,
                        "trailing_direction_rank": (
                            transfer_state.trailing_direction_rank
                        ),
                        "event_direction_rank": (
                            transfer_state.event_direction_rank
                        ),
                        "market_count": transfer_state.market_count,
                        "peer_event_median": (
                            transfer_state.peer_event_median
                        ),
                        "net_structural_r": str(plan.net_r),
                    })
                    continue
                candidate = Candidate(
''',
        "v62 isolated extreme-transfer router",
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
