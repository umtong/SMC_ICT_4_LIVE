#!/usr/bin/env python3
"""Patch v47 with the v60 cross-sectional extreme-state router."""
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
        "from c10_v60_overlay import (\n"
        "    classify_cross_sectional_extreme_state,\n",
        "v60 overlay import",
    )
    text = replace_once(
        text,
        "                candidate = Candidate(\n",
        '''                extreme_state = classify_cross_sectional_extreme_state(
                    plan,
                )
                plan.details["cross_sectional_extreme_state_router"] = (
                    extreme_state.details
                )
                if not extreme_state.approved:
                    self.logic[symbol].mark_rejected(
                        plan,
                        ts_ns,
                        extreme_state.reason,
                        extreme_state.details,
                    )
                    self._capture_events(symbol)
                    self.rejections.append({
                        "type": "CROSS_SECTIONAL_EXTREME_STATE_REJECTED",
                        "observed_ts_ns": plan.observed_ts_ns,
                        "scenario_id": plan.scenario_id,
                        "symbol": symbol,
                        "direction": plan.direction.value,
                        "reason": extreme_state.reason,
                        "state": extreme_state.state,
                        "trailing_direction_rank": (
                            extreme_state.trailing_direction_rank
                        ),
                        "event_direction_rank": (
                            extreme_state.event_direction_rank
                        ),
                        "market_count": extreme_state.market_count,
                        "net_structural_r": str(plan.net_r),
                    })
                    continue
                candidate = Candidate(
''',
        "v60 extreme-state router",
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
