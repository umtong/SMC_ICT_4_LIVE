#!/usr/bin/env python3
"""Change only V7's structural-protection stop anchor.

V7 correctly identified causal first objectives but moved the stop directly to
an after-cost break-even level. When that level had not yet traded, the stop
could be placed through the current executable price; when the structural
objective was materially more favorable than break-even, subsequent retracement
returned avoidable profit.

V8 anchors the first protection stop at the reached structural level itself.
If price subsequently reaches the conservative after-cost break-even level, the
stop ratchets further only when that is more favorable. No detector, signal,
entry, initial stop, full target, risk budget, fee, funding, order, fill,
position, or NAV rule is changed. The patch is idempotent.
"""
from __future__ import annotations

import argparse
from pathlib import Path


def replace_once(source: str, old: str, new: str, label: str) -> str:
    if new in source:
        return source
    count = source.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one source block, found {count}")
    return source.replace(old, new, 1)


def apply_patch(path: Path) -> bool:
    original = path.read_text(encoding="utf-8")
    source = original

    source = replace_once(
        source,
        '''            "structural_protection_activations": 0,
            "entries_submitted": 0,
''',
        '''            "structural_protection_activations": 0,
            "structural_break_even_ratchets": 0,
            "entries_submitted": 0,
''',
        "structural protection counters",
    )

    old_activation = '''        if (
            structural_trigger is not None
            and not active.structural_protection_active
            and active.direction * (executable - structural_trigger) >= 0.0
        ):
            active.structural_protection_active = True
            active.stop = (
                max(active.stop, active.break_even_price)
                if active.direction > 0
                else min(active.stop, active.break_even_price)
            )
            self.counters["structural_protection_activations"] += 1
            self._emit(
                scenario_id=active.signal["scenario_id"],
                event_type="STRUCTURAL_PROTECTION_ACTIVATED",
                event_time_ns=timestamp_ns,
                observed_time_ns=timestamp_ns,
                previous_state=f"{active.kind}_ACTIVE",
                next_state=f"{active.kind}_STRUCTURALLY_PROTECTED",
                reason_code="FIRST_CAUSAL_LIQUIDITY_OBJECTIVE_REACHED",
                reference_price=active.stop,
                details={
                    "structural_trigger": structural_trigger,
                    "after_cost_break_even": active.break_even_price,
                    "mfe_net_r": net_r,
                },
            )

        if active.kind == "CONTINUATION" and not active.protection_active and net_r >= self.config.continuation_protection_activate_r:
'''
    new_activation = '''        if (
            structural_trigger is not None
            and not active.structural_protection_active
            and active.direction * (executable - structural_trigger) > 0.0
        ):
            active.structural_protection_active = True
            active.stop = (
                max(active.stop, structural_trigger)
                if active.direction > 0
                else min(active.stop, structural_trigger)
            )
            self.counters["structural_protection_activations"] += 1
            self._emit(
                scenario_id=active.signal["scenario_id"],
                event_type="STRUCTURAL_PROTECTION_ACTIVATED",
                event_time_ns=timestamp_ns,
                observed_time_ns=timestamp_ns,
                previous_state=f"{active.kind}_ACTIVE",
                next_state=f"{active.kind}_STRUCTURALLY_PROTECTED",
                reason_code="FIRST_CAUSAL_LIQUIDITY_OBJECTIVE_BECAME_INVALIDATION",
                reference_price=active.stop,
                details={
                    "structural_trigger": structural_trigger,
                    "after_cost_break_even": active.break_even_price,
                    "mfe_net_r": net_r,
                },
            )

        if (
            active.structural_protection_active
            and active.direction * (executable - active.break_even_price) > 0.0
        ):
            ratcheted = (
                max(active.stop, active.break_even_price)
                if active.direction > 0
                else min(active.stop, active.break_even_price)
            )
            if ratcheted != active.stop:
                active.stop = ratcheted
                self.counters["structural_break_even_ratchets"] += 1
                self._emit(
                    scenario_id=active.signal["scenario_id"],
                    event_type="STRUCTURAL_PROTECTION_RATCHETED_TO_AFTER_COST_BREAK_EVEN",
                    event_time_ns=timestamp_ns,
                    observed_time_ns=timestamp_ns,
                    previous_state=f"{active.kind}_STRUCTURALLY_PROTECTED",
                    next_state=f"{active.kind}_STRUCTURALLY_PROTECTED",
                    reason_code="AFTER_COST_BREAK_EVEN_TRADED_AFTER_FIRST_OBJECTIVE",
                    reference_price=active.stop,
                    details={
                        "structural_trigger": structural_trigger,
                        "after_cost_break_even": active.break_even_price,
                        "mfe_net_r": net_r,
                    },
                )

        if active.kind == "CONTINUATION" and not active.protection_active and net_r >= self.config.continuation_protection_activate_r:
'''
    source = replace_once(
        source,
        old_activation,
        new_activation,
        "structural protection anchor",
    )

    source = replace_once(
        source,
        '''            "structural_protection_active": active.structural_protection_active,
            "protection_active": active.protection_active,
''',
        '''            "structural_protection_active": active.structural_protection_active,
            "structural_protection_stop": active.stop if active.structural_protection_active else None,
            "protection_active": active.protection_active,
''',
        "structural stop diagnostics",
    )

    if source == original:
        return False
    path.write_text(source, encoding="utf-8")
    return True


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "path",
        type=Path,
        nargs="?",
        default=Path(__file__).with_name("nt_lvcfr_strategy.py"),
    )
    args = parser.parse_args()
    changed = apply_patch(args.path.resolve())
    print({"path": str(args.path.resolve()), "changed": changed})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
