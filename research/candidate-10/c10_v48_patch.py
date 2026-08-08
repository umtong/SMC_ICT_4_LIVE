#!/usr/bin/env python3
"""Patch frozen Candidate 11 into the v48 AAC-only opportunity family."""
from __future__ import annotations

import argparse
from pathlib import Path

from c10_v29_patch import patch as patch_v29


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one marker, found {count}")
    return text.replace(old, new, 1)


def patch(path: Path) -> None:
    patch_v29(path)
    text = path.read_text(encoding="utf-8")
    text = replace_once(
        text,
        "from c10_v29_overlay import (\n",
        "from c10_v48_overlay import (\n"
        "    require_aac_event_direction_leader,\n",
        "v48 overlay import",
    )

    text = replace_once(
        text,
        "                candidate = Candidate(\n",
        '''                if plan.scenario.value != "AAC":
                    self.logic[symbol].mark_rejected(
                        plan,
                        ts_ns,
                        "V48_INDEPENDENT_AAC_FAMILY_ONLY",
                        {
                            "scenario": plan.scenario.value,
                            "reason": (
                                "v48 evaluates accepted-auction continuation "
                                "as an independent opportunity family"
                            ),
                        },
                    )
                    self._capture_events(symbol)
                    self.rejections.append({
                        "type": "V48_NON_AAC_PLAN_REJECTED",
                        "observed_ts_ns": plan.observed_ts_ns,
                        "scenario_id": plan.scenario_id,
                        "symbol": symbol,
                        "scenario": plan.scenario.value,
                        "direction": plan.direction.value,
                        "net_structural_r": str(plan.net_r),
                    })
                    continue
                event_router = require_aac_event_direction_leader(plan)
                plan.details["aac_event_direction_leader_router"] = (
                    event_router.details
                )
                if not event_router.approved:
                    self.logic[symbol].mark_rejected(
                        plan,
                        ts_ns,
                        event_router.reason,
                        event_router.details,
                    )
                    self._capture_events(symbol)
                    self.rejections.append({
                        "type": "AAC_EVENT_DIRECTION_LEADER_REJECTED",
                        "observed_ts_ns": plan.observed_ts_ns,
                        "scenario_id": plan.scenario_id,
                        "symbol": symbol,
                        "direction": plan.direction.value,
                        "reason": event_router.reason,
                        "event_direction_rank": (
                            event_router.event_direction_rank
                        ),
                        "quote_notional_leader": leadership.leader,
                        "candidate_event_move": (
                            leadership.candidate_event_move
                        ),
                        "peer_event_median": leadership.peer_event_median,
                        "net_structural_r": str(plan.net_r),
                    })
                    continue
                candidate = Candidate(
''',
        "v48 AAC family and event router",
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
