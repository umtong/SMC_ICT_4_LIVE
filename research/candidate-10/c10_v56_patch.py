#!/usr/bin/env python3
"""Patch v52 funded runner with v56 pre-event reversal ownership."""
from __future__ import annotations

import argparse
from pathlib import Path

from c10_v52_patch import patch as patch_v52


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one marker, found {count}")
    return text.replace(old, new, 1)


def patch(path: Path) -> None:
    patch_v52(path)
    text = path.read_text(encoding="utf-8")
    text = replace_once(
        text,
        "from c10_v52_overlay import (\n",
        "from c10_v56_overlay import (\n"
        "    classify_reversal_ownership,\n",
        "v56 overlay import",
    )

    ownership = '''                reversal_ownership = classify_reversal_ownership(
                    plan
                )
                plan.details["pre_event_reversal_ownership"] = (
                    reversal_ownership.details
                )
                if not reversal_ownership.approved:
                    self.logic[symbol].mark_rejected(
                        plan,
                        ts_ns,
                        reversal_ownership.reason,
                        reversal_ownership.details,
                    )
                    self._capture_events(symbol)
                    self.rejections.append({
                        "type": "REVERSAL_OWNERSHIP_REJECTED",
                        "observed_ts_ns": plan.observed_ts_ns,
                        "scenario_id": plan.scenario_id,
                        "symbol": symbol,
                        "direction": plan.direction.value,
                        "reason": reversal_ownership.reason,
                        "details": reversal_ownership.details,
                    })
                    continue
                runner = reframe_external_runner(
'''
    text = replace_once(
        text,
        "                runner = reframe_external_runner(\n",
        ownership,
        "v56 reversal ownership",
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
