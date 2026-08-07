#!/usr/bin/env python3
"""Patch frozen Candidate 11 with the v36 CE-rejection state machine."""
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
        "from c10_v36_overlay import (\n"
        "    normalize_kline_open_time,\n",
        "v36 overlay import",
    )
    text = replace_once(
        text,
        "from session_engine import RegionalHandoffAuctionEngine\n",
        "from c10_v36_state import ConsequentEncroachmentRejectionEngine as RegionalHandoffAuctionEngine\n",
        "v36 state-engine import",
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
        "v36 timestamp normalization",
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
                        "V36_CE_REJECTION_FAR_ONLY",
                    )
                    self._capture_events(symbol)
                    continue
                leadership = self.leadership.decide(
''',
        "v36 FAR-only split",
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
