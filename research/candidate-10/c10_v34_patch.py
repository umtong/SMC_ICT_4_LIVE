#!/usr/bin/env python3
"""Patch frozen Candidate 11 with v34 source-boundary retest scenarios."""
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
        "from c10_v34_overlay import (\n"
        "    normalize_kline_open_time,\n"
        "    reframe_source_retest,\n",
        "v34 overlay import",
    )
    text = replace_once(
        text,
        """        frame, flow_repairs = repair_kline_flow_frame(frame, filename)
        frames.append(frame)
""",
        """        frame, flow_repairs = repair_kline_flow_frame(frame, filename)
        frame = normalize_kline_open_time(frame, filename)
        frames.append(frame)
""",
        "v34 timestamp normalization",
    )
    text = replace_once(
        text,
        '''                if ts_ns < self.config.evaluation_start_ns:
                    self.logic[symbol].mark_rejected(plan, ts_ns, "OUTSIDE_EVALUATION_WINDOW")
                    self._capture_events(symbol)
                    continue
                leadership = self.leadership.decide(
''',
        '''                if ts_ns < self.config.evaluation_start_ns:
                    self.logic[symbol].mark_rejected(plan, ts_ns, "OUTSIDE_EVALUATION_WINDOW")
                    self._capture_events(symbol)
                    continue
                if plan.scenario.value != "FAR":
                    self.logic[symbol].mark_rejected(
                        plan,
                        ts_ns,
                        "V34_SOURCE_RETEST_FAR_ONLY",
                    )
                    self._capture_events(symbol)
                    continue
                leadership = self.leadership.decide(
''',
        "v34 FAR-only split",
    )
    text = replace_once(
        text,
        '''                candidate = Candidate(
                    symbol=symbol,
''',
        '''                source_retest = reframe_source_retest(
                    plan,
                    self.logic[symbol],
                )
                if not source_retest.approved:
                    self.logic[symbol].mark_rejected(
                        plan,
                        ts_ns,
                        source_retest.reason,
                        source_retest.details,
                    )
                    self._capture_events(symbol)
                    self.rejections.append({
                        "type": "SOURCE_RETEST_REJECTED",
                        "observed_ts_ns": plan.observed_ts_ns,
                        "scenario_id": plan.scenario_id,
                        "symbol": symbol,
                        "reason": source_retest.reason,
                        "details": source_retest.details,
                        "net_structural_r": str(plan.net_r),
                    })
                    continue
                plan = source_retest.plan
                candidate = Candidate(
                    symbol=symbol,
''',
        "v34 plan reframe",
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
