#!/usr/bin/env python3
"""Patch the frozen Candidate 11 runner with v63 flow continuation."""
from __future__ import annotations

import argparse
from pathlib import Path

from c10_v27_patch import patch as patch_v27


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one marker, found {count}")
    return text.replace(old, new, 1)


def patch(path: Path) -> None:
    patch_v27(path)
    text = path.read_text(encoding="utf-8")
    text = replace_once(
        text,
        "from c10_v27_overlay import (\n",
        "from c10_v63_overlay import (\n"
        "    require_flow_event_leader,\n",
        "v63 execution overlay import",
    )
    text = replace_once(
        text,
        "from session_engine import RegionalHandoffAuctionEngine\n",
        "from c10_v63_flow_continuation import (\n"
        "    FlowShockContinuationEngine as RegionalHandoffAuctionEngine,\n"
        ")\n",
        "v63 logic-engine import",
    )
    text = replace_once(
        text,
        '''                plan.details["market_leadership"] = leadership.to_dict()
                if not leadership.approved:
''',
        '''                plan.details["market_leadership"] = leadership.to_dict()
                flow_leader = require_flow_event_leader(plan)
                plan.details["flow_event_leader_router"] = flow_leader.details
                if not flow_leader.approved:
                    self.logic[symbol].mark_rejected(
                        plan,
                        ts_ns,
                        flow_leader.reason,
                        flow_leader.details,
                    )
                    self._capture_events(symbol)
                    self.rejections.append({
                        "type": "FLOW_EVENT_LEADER_REJECTED",
                        "observed_ts_ns": plan.observed_ts_ns,
                        "scenario_id": plan.scenario_id,
                        "symbol": symbol,
                        "reason": flow_leader.reason,
                        "event_direction_rank": (
                            flow_leader.event_direction_rank
                        ),
                        "net_structural_r": str(plan.net_r),
                    })
                    continue
                if not leadership.approved:
''',
        "v63 event-leader router",
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
