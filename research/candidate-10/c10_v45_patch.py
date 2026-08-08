#!/usr/bin/env python3
"""Patch v44 target hierarchy with the v45 entry-leg invalidation."""
from __future__ import annotations

import argparse
from pathlib import Path

from c10_v44_patch import patch as patch_v44


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one marker, found {count}")
    return text.replace(old, new, 1)


def patch(path: Path) -> None:
    patch_v44(path)
    text = path.read_text(encoding="utf-8")
    text = replace_once(
        text,
        "from c10_v44_overlay import (\n",
        "from c10_v45_overlay import (\n"
        "    reframe_entry_leg_invalidation,\n",
        "v45 overlay import",
    )

    text = replace_once(
        text,
        '''                    else:
                        plan = timing.plan
                if plan is not None:
                    target = reframe_primary_target(
''',
        '''                    else:
                        plan = timing.plan
                if plan is not None:
                    invalidation = reframe_entry_leg_invalidation(
                        plan,
                        self.logic[symbol],
                    )
                    if not invalidation.approved:
                        self.logic[symbol].mark_rejected(
                            plan,
                            ts_ns,
                            invalidation.reason,
                            invalidation.details,
                        )
                        self._capture_events(symbol)
                        self.rejections.append({
                            "type": "SOURCE_ENTRY_LEG_INVALIDATION_REJECTED",
                            "observed_ts_ns": plan.observed_ts_ns,
                            "scenario_id": plan.scenario_id,
                            "symbol": symbol,
                            "reason": invalidation.reason,
                            "details": invalidation.details,
                            "net_structural_r": str(plan.net_r),
                        })
                        plan = None
                    else:
                        plan = invalidation.plan
                if plan is not None:
                    target = reframe_primary_target(
''',
        "v45 invalidation before target hierarchy",
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
