#!/usr/bin/env python3
"""Patch frozen Candidate 11 with v41 source-entry timing attribution."""
from __future__ import annotations

import argparse
from pathlib import Path

from c10_v40_patch import patch as patch_v40


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one marker, found {count}")
    return text.replace(old, new, 1)


def patch(path: Path) -> None:
    patch_v40(path)
    text = path.read_text(encoding="utf-8")
    text = replace_once(
        text,
        "from c10_v40_overlay import (\n",
        "from c10_v41_overlay import (\n"
        "    reframe_first_displacement_entry,\n",
        "v41 overlay import",
    )

    text = replace_once(
        text,
        '''                plan = self.logic[symbol].on_bar(observation)
                self._capture_events(symbol)
                self._maybe_transfer_internal_pivot(symbol, observation)
                if plan is None:
''',
        '''                plan = self.logic[symbol].on_bar(observation)
                self._capture_events(symbol)
                if plan is not None:
                    timing = reframe_first_displacement_entry(
                        plan,
                        self.logic[symbol],
                    )
                    if not timing.approved:
                        self.logic[symbol].mark_rejected(
                            plan,
                            ts_ns,
                            timing.reason,
                            timing.details,
                        )
                        self._capture_events(symbol)
                        self.rejections.append({
                            "type": "SOURCE_ENTRY_TIMING_REJECTED",
                            "observed_ts_ns": plan.observed_ts_ns,
                            "scenario_id": plan.scenario_id,
                            "symbol": symbol,
                            "reason": timing.reason,
                            "details": timing.details,
                            "net_structural_r": str(plan.net_r),
                        })
                        plan = None
                    else:
                        plan = timing.plan
                self._maybe_transfer_internal_pivot(symbol, observation)
                if plan is None:
''',
        "v41 source-entry reframe",
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
