#!/usr/bin/env python3
"""Patch v46 with the v47 event-direction leadership router."""
from __future__ import annotations

import argparse
from pathlib import Path

from c10_v46_patch import patch as patch_v46


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one marker, found {count}")
    return text.replace(old, new, 1)


def patch(path: Path) -> None:
    patch_v46(path)
    text = path.read_text(encoding="utf-8")
    text = replace_once(
        text,
        "from c10_v46_overlay import (\n",
        "from c10_v47_overlay import (\n"
        "    require_event_direction_leader,\n",
        "v47 overlay import",
    )

    text = replace_once(
        text,
        "                candidate = Candidate(\n",
        '''                event_router = require_event_direction_leader(plan)
                plan.details["event_direction_leader_router"] = (
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
                        "type": "EVENT_DIRECTION_LEADER_REJECTED",
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
        "v47 event-direction router",
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
