#!/usr/bin/env python3
"""Route first-objective protection by the causal meaning of each scenario.

V9 treated every first objective as a waypoint and followed completed structure.
V10 treated every first objective as an immediate invalidation boundary with the
existing 0.05 ATR buffer.  Both uniform policies were logically incomplete:

- VALUE_EDGE_CONTINUATION begins inside the directional outer third of the prior
  dealing range.  Once price accepts beyond the prior external boundary, that
  boundary is the causal invalidation level for the continuation.
- Reclaim and acceptance scenarios use their first objective as an intermediate
  draw on liquidity.  Crossing it does not invalidate the scenario on a normal
  retest; protection should follow completed structure instead.

V11 therefore changes only the state-to-protection mapping.  VALUE_EDGE uses the
already frozen 0.05 ATR objective buffer; all other first-objective scenarios use
the already frozen 20 completed minutes plus 0.05 ATR structure trail.  Detector,
entry, initial stop, target, risk, fees, funding, execution, positions and NAV are
unchanged.  The patch is idempotent and contains no fill or PnL simulation.
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
        '''            "structural_objective_buffer_activations": 0,
            "entries_submitted": 0,
''',
        '''            "structural_objective_buffer_activations": 0,
            "waypoint_structure_trail_activations": 0,
            "entries_submitted": 0,
''',
        "scenario protection counter",
    )

    old_block = '''        structural_trigger = signal_structural_protection_trigger(active.signal)
        if (
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
    new_block = '''        structural_trigger = signal_structural_protection_trigger(active.signal)
        scenario_kind = str(active.signal.get("scenario_kind", ""))
        boundary_invalidation = scenario_kind == "VALUE_EDGE_CONTINUATION"
        if (
            structural_trigger is not None
            and not active.structural_protection_active
            and active.direction * (executable - structural_trigger) > 0.0
        ):
            active.structural_protection_active = True
            self.counters["structural_protection_activations"] += 1
            if boundary_invalidation:
                buffer = self.config.continuation_trail_buffer_atr * active.atr
                buffered_stop = structural_trigger - active.direction * buffer
                if active.direction * (executable - buffered_stop) <= 0.0:
                    raise RuntimeError("buffered structural stop is not behind executable price")
                active.stop = (
                    max(active.stop, buffered_stop)
                    if active.direction > 0
                    else min(active.stop, buffered_stop)
                )
                self.counters["structural_objective_buffer_activations"] += 1
                self._emit(
                    scenario_id=active.signal["scenario_id"],
                    event_type="VALUE_EDGE_BOUNDARY_PROTECTION_ACTIVATED",
                    event_time_ns=timestamp_ns,
                    observed_time_ns=timestamp_ns,
                    previous_state=f"{active.kind}_ACTIVE",
                    next_state=f"{active.kind}_STRUCTURALLY_PROTECTED",
                    reason_code="PRIOR_RANGE_EXTERNAL_BECAME_CAUSAL_INVALIDATION",
                    reference_price=active.stop,
                    details={
                        "scenario_kind": scenario_kind,
                        "structural_trigger": structural_trigger,
                        "structural_buffer": buffer,
                        "buffered_stop": buffered_stop,
                        "after_cost_break_even": active.break_even_price,
                        "mfe_net_r": net_r,
                    },
                )
            else:
                self.counters["waypoint_structure_trail_activations"] += 1
                self._emit(
                    scenario_id=active.signal["scenario_id"],
                    event_type="INTERMEDIATE_LIQUIDITY_WAYPOINT_TRAIL_ARMED",
                    event_time_ns=timestamp_ns,
                    observed_time_ns=timestamp_ns,
                    previous_state=f"{active.kind}_ACTIVE",
                    next_state=f"{active.kind}_STRUCTURALLY_PROTECTED",
                    reason_code="FIRST_OBJECTIVE_IS_WAYPOINT_NOT_EXACT_INVALIDATION",
                    reference_price=structural_trigger,
                    details={
                        "scenario_kind": scenario_kind,
                        "structural_trigger": structural_trigger,
                        "existing_stop": active.stop,
                        "after_cost_break_even": active.break_even_price,
                        "mfe_net_r": net_r,
                    },
                )

        if active.structural_protection_active and not boundary_invalidation:
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
                        event_type="INTERMEDIATE_WAYPOINT_TRAIL_UPDATED",
                        event_time_ns=timestamp_ns,
                        observed_time_ns=timestamp_ns,
                        previous_state=f"{active.kind}_STRUCTURALLY_PROTECTED",
                        next_state=f"{active.kind}_STRUCTURALLY_PROTECTED",
                        reason_code="COMPLETED_TWENTY_MINUTE_STRUCTURE_ADVANCED_AFTER_WAYPOINT",
                        reference_price=active.stop,
                        details={
                            "scenario_kind": scenario_kind,
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
        old_block,
        new_block,
        "uniform objective buffer to scenario-aware protection",
    )

    source = replace_once(
        source,
        '''    def _structural_stop(self, active: ActiveLeg) -> float | None:
''',
        '''    def _structural_protection_stop(self, active: ActiveLeg) -> float | None:
        """Return a stop behind frozen completed structure after a waypoint."""
        if len(self._completed_minutes) < self.config.continuation_trail_minutes:
            return None
        recent = list(self._completed_minutes)[-self.config.continuation_trail_minutes :]
        if active.direction > 0:
            return min(item[1] for item in recent) - self.config.continuation_trail_buffer_atr * active.atr
        return max(item[2] for item in recent) + self.config.continuation_trail_buffer_atr * active.atr

    def _structural_stop(self, active: ActiveLeg) -> float | None:
''',
        "restore waypoint structure method",
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
