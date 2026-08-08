#!/usr/bin/env python3
"""Patch v47 with the v49 cross-market transfer-state router."""
from __future__ import annotations

import argparse
from pathlib import Path

from c10_v47_patch import patch as patch_v47


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one marker, found {count}")
    return text.replace(old, new, 1)


def patch(path: Path) -> None:
    patch_v47(path)
    text = path.read_text(encoding="utf-8")
    text = replace_once(
        text,
        "from c10_v47_overlay import (\n",
        "from c10_v49_overlay import (\n"
        "    classify_transfer_state,\n",
        "v49 overlay import",
    )

    text = replace_once(
        text,
        "                candidate = Candidate(\n",
        '''                transfer_state = classify_transfer_state(
                    plan,
                    minimum_confirmation_impulse=float(
                        getattr(
                            self.leadership,
                            "original",
                            self.leadership,
                        ).minimum_follower_confirmation_impulse
                    ),
                )
                plan.details["event_transfer_state_router"] = (
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
                        "type": "EVENT_TRANSFER_STATE_REJECTED",
                        "observed_ts_ns": plan.observed_ts_ns,
                        "scenario_id": plan.scenario_id,
                        "symbol": symbol,
                        "direction": plan.direction.value,
                        "reason": transfer_state.reason,
                        "transfer_state": transfer_state.state,
                        "event_direction_rank": (
                            leadership.event_direction_rank
                        ),
                        "candidate_event_move": (
                            leadership.candidate_event_move
                        ),
                        "peer_event_median": leadership.peer_event_median,
                        "confirmation_impulse": (
                            leadership.confirmation_impulse
                        ),
                        "candidate_trailing_directional_trend_score": (
                            leadership.directional_trend_scores.get(symbol)
                        ),
                        "net_structural_r": str(plan.net_r),
                    })
                    continue
                candidate = Candidate(
''',
        "v49 transfer-state router",
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
