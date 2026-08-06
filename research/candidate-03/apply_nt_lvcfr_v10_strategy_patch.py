#!/usr/bin/env python3
"""Replace V9's uniform 20-minute trail with a causal objective buffer.

V9 confirmed that the first causal objective should arm protection rather than
act as an exact executable stop.  Its uniform 20-minute completed-structure
trail, however, was too loose for several state types and returned valid
objective profit before the existing full target was reached.

V10 changes one variable only: once the first causal objective is crossed, the
stop is placed behind that objective by the already frozen 0.05 ATR structural
buffer.  This is the same volatility buffer already used by the continuation
trail; no detector threshold, entry, initial stop, target, risk, fee, funding,
order, fill, position, or NAV rule changes.  The after-cost break-even ratchet
and the existing 2R continuation protection remain unchanged.

The patch is idempotent and contains no execution or accounting simulation.
"""
from __future__ import annotations

import argparse
from pathlib import Path


def replace_once(source: str, old: str, new: str, label: str) -> str:
    if new:
        if new in source:
            return source
    elif old not in source:
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
        '''            "structural_trail_updates": 0,
            "entries_submitted": 0,
''',
        '''            "structural_trail_updates": 0,
            "structural_objective_buffer_activations": 0,
            "entries_submitted": 0,
''',
        "objective buffer counter",
    )

    old_block = '''        if (
            structural_trigger is not None
            and not active.structural_protection_active
            and active.direction * (executable - structural_trigger) > 0.0
        ):
            active.structural_protection_active = True
            self.counters["structural_protection_activations"] += 1
            self._emit(
                scenario_id=active.signal["scenario_id"],
                event_type="STRUCTURAL_TRAIL_ARMED",
                event_time_ns=timestamp_ns,
                observed_time_ns=timestamp_ns,
                previous_state=f"{active.kind}_ACTIVE",
                next_state=f"{active.kind}_STRUCTURALLY_PROTECTED",
                reason_code="FIRST_CAUSAL_LIQUIDITY_OBJECTIVE_ARMED_COMPLETED_STRUCTURE_TRAIL",
                reference_price=structural_trigger,
                details={
                    "structural_trigger": structural_trigger,
                    "existing_stop": active.stop,
                    "after_cost_break_even": active.break_even_price,
                    "mfe_net_r": net_r,
                },
            )

        if active.structural_protection_active:
            structural_stop = self._structural_protection_stop(active)
            if (
                structural_stop is not None
                and active.direction * (executable - structural_stop) > 0.0
            ):
                updated = (
                    max(active.stop, structural_stop)
                    if active.direction > 0
                    else min(active.stop, structural_stop)
                )
                if updated != active.stop:
                    active.stop = updated
                    self.counters["structural_trail_updates"] += 1
                    self._emit(
                        scenario_id=active.signal["scenario_id"],
                        event_type="STRUCTURAL_TRAIL_UPDATED",
                        event_time_ns=timestamp_ns,
                        observed_time_ns=timestamp_ns,
                        previous_state=f"{active.kind}_STRUCTURALLY_PROTECTED",
                        next_state=f"{active.kind}_STRUCTURALLY_PROTECTED",
                        reason_code="COMPLETED_TWENTY_MINUTE_STRUCTURE_ADVANCED",
                        reference_price=active.stop,
                        details={
                            "structural_trigger": structural_trigger,
                            "structural_stop": structural_stop,
                            "mfe_net_r": net_r,
                        },
                    )

        if (
            active.structural_protection_active
            and active.direction * (executable - active.break_even_price) > 0.0
        ):
'''
    new_block = '''        if (
            structural_trigger is not None
            and not active.structural_protection_active
            and active.direction * (executable - structural_trigger) > 0.0
        ):
            buffer = self.config.continuation_trail_buffer_atr * active.atr
            buffered_stop = structural_trigger - active.direction * buffer
            if active.direction * (executable - buffered_stop) <= 0.0:
                raise RuntimeError("buffered structural stop is not behind executable price")
            active.structural_protection_active = True
            active.stop = (
                max(active.stop, buffered_stop)
                if active.direction > 0
                else min(active.stop, buffered_stop)
            )
            self.counters["structural_protection_activations"] += 1
            self.counters["structural_objective_buffer_activations"] += 1
            self._emit(
                scenario_id=active.signal["scenario_id"],
                event_type="STRUCTURAL_OBJECTIVE_BUFFER_ACTIVATED",
                event_time_ns=timestamp_ns,
                observed_time_ns=timestamp_ns,
                previous_state=f"{active.kind}_ACTIVE",
                next_state=f"{active.kind}_STRUCTURALLY_PROTECTED",
                reason_code="FIRST_CAUSAL_LIQUIDITY_OBJECTIVE_HELD_WITH_FROZEN_ATR_BUFFER",
                reference_price=active.stop,
                details={
                    "structural_trigger": structural_trigger,
                    "structural_buffer": buffer,
                    "buffered_stop": buffered_stop,
                    "after_cost_break_even": active.break_even_price,
                    "mfe_net_r": net_r,
                },
            )

        if (
            active.structural_protection_active
            and active.direction * (executable - active.break_even_price) > 0.0
        ):
'''
    source = replace_once(
        source,
        old_block,
        new_block,
        "completed structure to objective buffer",
    )

    source = replace_once(
        source,
        '''    def _structural_protection_stop(self, active: ActiveLeg) -> float | None:
        """Return a stop behind frozen completed structure, without R anchoring."""
        if len(self._completed_minutes) < self.config.continuation_trail_minutes:
            return None
        recent = list(self._completed_minutes)[-self.config.continuation_trail_minutes :]
        if active.direction > 0:
            return min(item[1] for item in recent) - self.config.continuation_trail_buffer_atr * active.atr
        return max(item[2] for item in recent) + self.config.continuation_trail_buffer_atr * active.atr

''',
        '',
        "remove superseded V9 structural trail method",
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
