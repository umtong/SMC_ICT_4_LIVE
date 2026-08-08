#!/usr/bin/env python3
"""Patch v56 funded runner with v57 earliest solvable funding checkpoint."""
from __future__ import annotations

import argparse
from pathlib import Path

from c10_v56_patch import patch as patch_v56


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one marker, found {count}")
    return text.replace(old, new, 1)


def patch(path: Path) -> None:
    patch_v56(path)
    text = path.read_text(encoding="utf-8")
    text = replace_once(
        text,
        "from c10_v56_overlay import (\n",
        "from c10_v57_overlay import (\n"
        "    select_funded_checkpoint,\n",
        "v57 overlay import",
    )

    text = replace_once(
        text,
        "            self.external_runner_funded = False\n"
        "            self.active_source_equilibrium_checkpoint: float | None = None\n",
        "            self.external_runner_funded = False\n"
        "            self.active_source_equilibrium_checkpoint: float | None = None\n"
        "            self.active_funding_checkpoint: float | None = None\n"
        "            self.active_funding_checkpoint_source: str | None = None\n",
        "v57 lifecycle state",
    )
    text = replace_once(
        text,
        '''            self.active_source_equilibrium_checkpoint = (
                None if checkpoint_raw is None else float(checkpoint_raw)
            )
            self.internal_pivot_protection_armed = False
''',
        '''            self.active_source_equilibrium_checkpoint = (
                None if checkpoint_raw is None else float(checkpoint_raw)
            )
            funding_raw = plan.details.get(
                "funding_checkpoint",
                self.active_source_equilibrium_checkpoint,
            )
            self.active_funding_checkpoint = (
                None if funding_raw is None else float(funding_raw)
            )
            funding_source_raw = plan.details.get(
                "funding_checkpoint_source",
                "SOURCE_EQUILIBRIUM",
            )
            self.active_funding_checkpoint_source = str(funding_source_raw)
            self.internal_pivot_protection_armed = False
''',
        "v57 submitted funding state",
    )
    text = replace_once(
        text,
        '''                "source_equilibrium_checkpoint": (
                    self.active_source_equilibrium_checkpoint
                ),
                "external_runner_funded": False,
''',
        '''                "source_equilibrium_checkpoint": (
                    self.active_source_equilibrium_checkpoint
                ),
                "funding_checkpoint": self.active_funding_checkpoint,
                "funding_checkpoint_source": (
                    self.active_funding_checkpoint_source
                ),
                "external_runner_funded": False,
''',
        "v57 cost-record funding fields",
    )

    preview = '''                funded_checkpoint = select_funded_checkpoint(
                    plan,
                    self.logic[symbol],
                    preview_solution,
                    instrument,
                    maker_fee=Decimal(
                        str(execution_config["effective_maker_rate"])
                    ),
                    taker_fee=Decimal(
                        str(execution_config["effective_taker_rate"])
                    ),
                )
                if not funded_checkpoint.approved:
                    self.logic[symbol].mark_rejected(
                        plan,
                        ts_ns,
                        funded_checkpoint.reason,
                        funded_checkpoint.details,
                    )
                    self._capture_events(symbol)
                    self.rejections.append({
                        "type": "FUNDED_CHECKPOINT_REJECTED",
                        "observed_ts_ns": plan.observed_ts_ns,
                        "scenario_id": plan.scenario_id,
                        "symbol": symbol,
                        "direction": plan.direction.value,
                        "reason": funded_checkpoint.reason,
                        "details": funded_checkpoint.details,
                    })
                    continue
                plan = funded_checkpoint.plan
                candidate = Candidate(
'''
    text = replace_once(
        text,
        "                candidate = Candidate(\n",
        preview,
        "v57 preview checkpoint selection",
    )

    submission = '''            submission_checkpoint = select_funded_checkpoint(
                plan,
                self.logic[symbol],
                submission_solution,
                instrument,
                maker_fee=Decimal(
                    str(execution_config["effective_maker_rate"])
                ),
                taker_fee=Decimal(
                    str(execution_config["effective_taker_rate"])
                ),
            )
            if not submission_checkpoint.approved:
                self.logic[symbol].mark_rejected(
                    plan,
                    self.last_ts_ns,
                    submission_checkpoint.reason,
                    submission_checkpoint.details,
                )
                self._capture_events(symbol)
                self.rejections.append({
                    "type": "FUNDED_CHECKPOINT_REJECTED_AT_SUBMISSION",
                    "observed_ts_ns": plan.observed_ts_ns,
                    "scenario_id": plan.scenario_id,
                    "symbol": symbol,
                    "direction": plan.direction.value,
                    "reason": submission_checkpoint.reason,
                    "details": submission_checkpoint.details,
                })
                return
            plan = submission_checkpoint.plan

            side = OrderSide.BUY if plan.direction == Direction.LONG else OrderSide.SELL
'''
    text = replace_once(
        text,
        "            side = OrderSide.BUY if plan.direction == Direction.LONG else OrderSide.SELL\n",
        submission,
        "v57 submission checkpoint selection",
    )

    old_guard = "                or self.active_source_equilibrium_checkpoint is None\n"
    count = text.count(old_guard)
    if count != 2:
        raise RuntimeError(
            f"v57 funding guard: expected two markers, found {count}"
        )
    text = text.replace(
        old_guard,
        "                or self.active_funding_checkpoint is None\n",
    )
    old_level = "            midpoint = float(self.active_source_equilibrium_checkpoint)\n"
    count = text.count(old_level)
    if count != 2:
        raise RuntimeError(
            f"v57 funding level: expected two markers, found {count}"
        )
    text = text.replace(
        old_level,
        "            midpoint = float(self.active_funding_checkpoint)\n",
    )

    text = text.replace(
        '"PENDING_ENTRY_CANCELED_AFTER_EQUILIBRIUM_DELIVERY"',
        '"PENDING_ENTRY_CANCELED_AFTER_FUNDING_CHECKPOINT_DELIVERY"',
    )
    text = text.replace(
        '"V52_FUNDED_SOURCE_EQUILIBRIUM_REDUCTION"',
        '"V57_FUNDED_DELIVERY_CHECKPOINT_REDUCTION"',
    )
    text = text.replace(
        '"V52_FUNDED_EQUILIBRIUM_PARTIAL"',
        '"V57_FUNDED_DELIVERY_CHECKPOINT_PARTIAL"',
    )
    text = text.replace(
        '"FUNDED_SOURCE_EQUILIBRIUM_EXTERNAL_RUNNER_SUBMITTED"',
        '"FUNDED_DELIVERY_CHECKPOINT_EXTERNAL_RUNNER_SUBMITTED"',
    )
    text = text.replace(
        '"source_equilibrium_checkpoint": midpoint,\n',
        '"funding_checkpoint": midpoint,\n'
        '                "funding_checkpoint_source": (\n'
        '                    self.active_funding_checkpoint_source\n'
        '                ),\n',
    )

    reset_old = '''                    self.external_runner_funded = False
                    self.active_source_equilibrium_checkpoint = None
                    self.internal_pivot_protection_armed = False
'''
    reset_new = '''                    self.external_runner_funded = False
                    self.active_source_equilibrium_checkpoint = None
                    self.active_funding_checkpoint = None
                    self.active_funding_checkpoint_source = None
                    self.internal_pivot_protection_armed = False
'''
    count = text.count(reset_old)
    if count != 2:
        raise RuntimeError(
            f"v57 terminal reset: expected two markers, found {count}"
        )
    text = text.replace(reset_old, reset_new)
    path.write_text(text, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("path", type=Path)
    args = parser.parse_args()
    patch(args.path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
