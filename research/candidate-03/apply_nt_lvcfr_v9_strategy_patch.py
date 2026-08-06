#!/usr/bin/env python3
"""Replace V8's exact-level stop with the frozen completed-structure trail.

V8 proved that causal first objectives contain information, but using the exact
objective price as an immediately executable stop treated ordinary retests as
scenario failure. V9 changes one variable only: after a first objective is
crossed, protection is armed and the stop advances behind the most recent 20
fully completed minutes plus the already frozen 0.05 ATR buffer. The stop is
updated only when it is both more favorable than the existing stop and behind
the current executable price. The existing after-cost break-even ratchet and
2R continuation protection remain unchanged.

This patch contains no fill, fee, position, PnL, or NAV simulation. It is
idempotent and is verified inside the pinned NautilusTrader 1.230.0 image before
being committed.
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
        '''            "structural_break_even_ratchets": 0,
            "entries_submitted": 0,
''',
        '''            "structural_break_even_ratchets": 0,
            "structural_trail_updates": 0,
            "entries_submitted": 0,
''',
        "structural trail counter",
    )

    old_activation = '''        if (
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
'''
    new_activation = '''        if (
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
    source = replace_once(
        source,
        old_activation,
        new_activation,
        "structural protection mechanism",
    )

    method_marker = '''    def _structural_stop(self, active: ActiveLeg) -> float | None:
'''
    method = '''    def _structural_protection_stop(self, active: ActiveLeg) -> float | None:
        """Return a stop behind frozen completed structure, without R anchoring."""
        if len(self._completed_minutes) < self.config.continuation_trail_minutes:
            return None
        recent = list(self._completed_minutes)[-self.config.continuation_trail_minutes :]
        if active.direction > 0:
            return min(item[1] for item in recent) - self.config.continuation_trail_buffer_atr * active.atr
        return max(item[2] for item in recent) + self.config.continuation_trail_buffer_atr * active.atr

'''
    if method not in source:
        if method_marker not in source:
            raise RuntimeError("structural stop method marker missing")
        source = source.replace(method_marker, method + method_marker, 1)

    source = replace_once(
        source,
        '''            "structural_protection_stop": active.stop if active.structural_protection_active else None,
            "protection_active": active.protection_active,
''',
        '''            "structural_protection_stop": active.stop if active.structural_protection_active else None,
            "structural_trail_updates": self.counters["structural_trail_updates"],
            "protection_active": active.protection_active,
''',
        "structural trail diagnostics",
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
