#!/usr/bin/env python3
"""Patch frozen Candidate 11 with v38 one-minute micro-pivot protection."""
from __future__ import annotations

import argparse
from pathlib import Path

from c10_v37_patch import patch as patch_v37


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one marker, found {count}")
    return text.replace(old, new, 1)


def patch(path: Path) -> None:
    patch_v37(path)
    text = path.read_text(encoding="utf-8")

    text = replace_once(
        text,
        "from c10_v37_overlay import (\n",
        "from c10_v38_overlay import (\n"
        "    micro_pivot_protection_enabled,\n"
        "    micro_pivot_reference_contract,\n",
        "v38 overlay import",
    )
    text = replace_once(
        text,
        "from c10_v37_state import ConfirmedInternalPivotProtectionEngine as RegionalHandoffAuctionEngine\n",
        "from c10_v38_state import ConfirmedMicroPivotProtectionEngine as RegionalHandoffAuctionEngine\n",
        "v38 state-engine import",
    )

    text = replace_once(
        text,
        '''                "internal_pivot_protection_enabled": (
                    internal_pivot_protection_enabled()
                ),
                "internal_pivot_protection_armed": False,
''',
        '''                "internal_pivot_protection_enabled": (
                    internal_pivot_protection_enabled()
                ),
                "micro_pivot_protection_enabled": (
                    micro_pivot_protection_enabled()
                ),
                "internal_pivot_protection_armed": False,
                "pivot_timeframe": None,
                "pivot_reference_contract": None,
                "pivot_reference_level": None,
''',
        "v38 cost-record fields",
    )
    text = replace_once(
        text,
        '''                not internal_pivot_protection_enabled()
                or self.internal_pivot_protection_armed
''',
        '''                not (
                    internal_pivot_protection_enabled()
                    or micro_pivot_protection_enabled()
                )
                or self.internal_pivot_protection_armed
''',
        "v38 protection gate",
    )

    text = replace_once(
        text,
        '''            logic = self.logic[symbol]
            instrument = instruments[symbol]
            decision = first_favorable_internal_pivot(
                direction=self.active_plan.direction.value,
                internal_highs=logic.internal_highs,
                internal_lows=logic.internal_lows,
                entry_fill_ts_ns=entry_fill_ts_ns,
                observed_ts_ns=observation.ts_ns,
                original_stop=float(self.active_plan.stop_price),
                reference_extreme=float(
                    self.active_plan.details["ce_rejection_primary"][
                        "retest_extreme"
                    ],
                ),
''',
        '''            logic = self.logic[symbol]
            instrument = instruments[symbol]
            use_micro = micro_pivot_protection_enabled()
            reference_contract = (
                micro_pivot_reference_contract()
                if use_micro
                else "CE_RETEST_EXTREME"
            )
            reference_level = (
                float(self.active_plan.expected_entry)
                if reference_contract == "EXPECTED_ENTRY"
                else float(
                    self.active_plan.details["ce_rejection_primary"][
                        "retest_extreme"
                    ],
                )
            )
            decision = first_favorable_internal_pivot(
                direction=self.active_plan.direction.value,
                internal_highs=(
                    logic.micro_highs if use_micro else logic.internal_highs
                ),
                internal_lows=(
                    logic.micro_lows if use_micro else logic.internal_lows
                ),
                entry_fill_ts_ns=entry_fill_ts_ns,
                observed_ts_ns=observation.ts_ns,
                original_stop=float(self.active_plan.stop_price),
                reference_extreme=reference_level,
''',
        "v38 pivot-source selection",
    )

    text = replace_once(
        text,
        '''            self.active_cost_record["internal_pivot_reference_extreme"] = (
                decision.reference_extreme
            )
            self.active_cost_record["internal_pivot_protective_stop"] = (
                decision.protective_stop
            )
''',
        '''            self.active_cost_record["internal_pivot_reference_extreme"] = (
                decision.reference_extreme
            )
            self.active_cost_record["internal_pivot_protective_stop"] = (
                decision.protective_stop
            )
            self.active_cost_record["pivot_timeframe"] = (
                "ONE_MINUTE" if use_micro else "FIVE_MINUTE"
            )
            self.active_cost_record["pivot_reference_contract"] = (
                reference_contract
            )
            self.active_cost_record["pivot_reference_level"] = reference_level
''',
        "v38 cost-record attribution",
    )

    text = replace_once(
        text,
        '''                    tags=["V37_CONFIRMED_INTERNAL_PIVOT_STOP"],
''',
        '''                    tags=[
                        "V38_CONFIRMED_MICRO_PIVOT_STOP"
                        if use_micro
                        else "V37_CONFIRMED_INTERNAL_PIVOT_STOP"
                    ],
''',
        "v38 replacement stop tag",
    )
    text = replace_once(
        text,
        '''                    tags=["V37_SOURCE_EQUILIBRIUM_PRIMARY_TARGET"],
''',
        '''                    tags=[
                        "V38_SOURCE_EQUILIBRIUM_PRIMARY_TARGET"
                        if use_micro
                        else "V37_SOURCE_EQUILIBRIUM_PRIMARY_TARGET"
                    ],
''',
        "v38 replacement target tag",
    )

    text = replace_once(
        text,
        '''            logic.mark_internal_pivot_protected(
                observed_ts_ns=decision.pivot_known_ts_ns,
''',
        '''            mark_protected = (
                logic.mark_micro_pivot_protected
                if use_micro
                else logic.mark_internal_pivot_protected
            )
            mark_protected(
                observed_ts_ns=decision.pivot_known_ts_ns,
''',
        "v38 state transition method",
    )
    text = replace_once(
        text,
        '''                "type": "CONFIRMED_INTERNAL_PIVOT_PROTECTION_ARMED",
''',
        '''                "type": (
                    "CONFIRMED_MICRO_PIVOT_PROTECTION_ARMED"
                    if use_micro
                    else "CONFIRMED_INTERNAL_PIVOT_PROTECTION_ARMED"
                ),
''',
        "v38 lifecycle type",
    )
    text = replace_once(
        text,
        '''                "direction": decision.direction,
                "pivot_event_ts_ns": decision.pivot_event_ts_ns,
''',
        '''                "direction": decision.direction,
                "pivot_timeframe": (
                    "ONE_MINUTE" if use_micro else "FIVE_MINUTE"
                ),
                "pivot_reference_contract": reference_contract,
                "pivot_reference_level": reference_level,
                "pivot_event_ts_ns": decision.pivot_event_ts_ns,
''',
        "v38 lifecycle attribution",
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
