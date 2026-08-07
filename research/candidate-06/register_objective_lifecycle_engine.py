#!/usr/bin/env python3
"""Idempotently register UOAM and its causal pending/position invalidation."""

from __future__ import annotations

from pathlib import Path


SELECTOR_ANCHOR = '''    if name == "SEQUENTIAL_IMPACT_PERSISTENCE_RELAY":
        from sequential_impact_persistence_engine import SequentialImpactPersistenceRelayEngine

        return SequentialImpactPersistenceRelayEngine(logic_params)
'''
SELECTOR_PATCH = SELECTOR_ANCHOR + '''    if name == "UNRESOLVED_OBJECTIVE_LIFECYCLE":
        from objective_lifecycle_engine import UnresolvedObjectiveLifecycleEngine

        return UnresolvedObjectiveLifecycleEngine(logic_params)
'''

IMPORT_ANCHOR = '''from nautilus_execution import NautilusExecutionMixin
'''
IMPORT_PATCH = '''from causal_context_control import first_matching_reason, signal_exit_contract
from nautilus_execution import NautilusExecutionMixin
'''

OBSERVE_ANCHOR = '''        def _observe_scenario_without_new_entry(self, snapshot: PrimitiveSnapshot, ts_ns: int) -> None:
            """Advance clocks and episode state while the global trade slot is occupied."""
            step = self._scenario_engine.observe(snapshot, allow_new=False)
            self._record_transitions(step.transitions, ts_ns)
'''
OBSERVE_PATCH = '''        def _apply_causal_context_control(
            self,
            transitions: tuple[Any, ...],
            snapshot: PrimitiveSnapshot,
        ) -> None:
            pending = self._pending_signal
            if pending is not None:
                codes, _ = signal_exit_contract(pending)
                reason = first_matching_reason(transitions, codes)
                if reason is not None:
                    self._pending_signal = None
                    self._pending_created_index = None
                    self._abstain_signal(
                        pending,
                        snapshot,
                        "CAUSAL_CONTEXT_INVALIDATED_BEFORE_ENTRY",
                        {"invalidation_reason": reason},
                    )

            trade = self._active_trade
            if trade is None or self._exit_inflight:
                return
            codes = tuple(trade.get("causal_exit_reason_codes", ()))
            if not bool(trade.get("causal_exit_open_position", False)):
                return
            reason = first_matching_reason(transitions, codes)
            if reason is None:
                return
            trade["forced_exit_reason"] = f"CAUSAL_CONTEXT_INVALIDATION_{reason}"
            trade["causal_context_invalidation_reason"] = reason
            self._exit_inflight = True
            self.cancel_all_orders(self.config.instrument_id)
            self.close_all_positions(self.config.instrument_id)

        def _observe_scenario_without_new_entry(self, snapshot: PrimitiveSnapshot, ts_ns: int) -> None:
            """Advance clocks and apply only scenario-declared invalidations."""
            step = self._scenario_engine.observe(snapshot, allow_new=False)
            self._record_transitions(step.transitions, ts_ns)
            self._apply_causal_context_control(step.transitions, snapshot)
'''

PENDING_ANCHOR = '''            if self._pending_signal is not None:
                self._observe_scenario_without_new_entry(snapshot, ts_ns)
                if self._pending_created_index is None or snapshot.index <= self._pending_created_index:
                    return
                signal = self._pending_signal
'''
PENDING_PATCH = '''            if self._pending_signal is not None:
                self._observe_scenario_without_new_entry(snapshot, ts_ns)
                if self._pending_signal is None:
                    return
                if self._pending_created_index is None or snapshot.index <= self._pending_created_index:
                    return
                signal = self._pending_signal
'''

ACTIVE_ANCHOR = '''                "failed_acceptance_trap": trap_armed,
                "favorable_drift_guard_enabled": enforce_drift_guard,
'''
ACTIVE_PATCH = '''                "failed_acceptance_trap": trap_armed,
                "favorable_drift_guard_enabled": enforce_drift_guard,
                "causal_exit_reason_codes": tuple(
                    str(value)
                    for value in signal.details.get("causal_exit_reason_codes", ())
                ),
                "causal_exit_open_position": bool(
                    signal.details.get("causal_exit_open_position", False)
                ),
'''


def _replace_once(text: str, anchor: str, patch: str, label: str) -> str:
    if patch in text:
        return text
    if anchor not in text:
        raise RuntimeError(f"{label} anchor changed; refusing ambiguous UOAM registration")
    return text.replace(anchor, patch, 1)


def main() -> int:
    candidate = Path(__file__).resolve().parent
    strategy_path = candidate / "nautilus_strategy.py"
    execution_path = candidate / "nautilus_execution.py"

    strategy = strategy_path.read_text(encoding="utf-8")
    strategy = _replace_once(strategy, IMPORT_ANCHOR, IMPORT_PATCH, "context-control import")
    strategy = _replace_once(strategy, SELECTOR_ANCHOR, SELECTOR_PATCH, "engine selector")
    strategy = _replace_once(strategy, OBSERVE_ANCHOR, OBSERVE_PATCH, "scenario observer")
    strategy = _replace_once(strategy, PENDING_ANCHOR, PENDING_PATCH, "pending invalidation")
    strategy_path.write_text(strategy, encoding="utf-8")

    execution = execution_path.read_text(encoding="utf-8")
    execution = _replace_once(execution, ACTIVE_ANCHOR, ACTIVE_PATCH, "active trade contract")
    execution_path.write_text(execution, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
