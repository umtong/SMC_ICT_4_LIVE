#!/usr/bin/env python3
"""Idempotently register OLAR with the existing Nautilus strategy/execution path.

The transformation is deliberately narrow. It adds one named causal engine,
validates its one-bar-delayed signal immediately before order construction,
records context identifiers in the active trade, and consumes structural-exit
requests before the unchanged timeout fallback. It does not alter fees, fills,
risk sizing, bracket construction, or portfolio accounting.
"""

from __future__ import annotations

from pathlib import Path


SELECTOR_ANCHOR = '''    if name == "HIERARCHICAL_MULTI_LIQUIDITY":
        from hierarchical_multi_liquidity_engine import HierarchicalMultiLiquidityEngine

        return HierarchicalMultiLiquidityEngine(logic_params)
'''
SELECTOR_PATCH = SELECTOR_ANCHOR + '''    if name == "OBJECTIVE_LIFECYCLE_ACCEPTANCE_RELAY":
        from objective_lifecycle_engine import ObjectiveLifecycleAcceptanceRelayEngine

        return ObjectiveLifecycleAcceptanceRelayEngine(logic_params)
'''

ATTEMPT_ANCHOR = '''    def _attempt_entry(self, signal: ScenarioSignal, snapshot: PrimitiveSnapshot) -> None:
        assert self._instrument is not None
        original_signal = signal
'''
ATTEMPT_PATCH = '''    def _attempt_entry(self, signal: ScenarioSignal, snapshot: PrimitiveSnapshot) -> None:
        assert self._instrument is not None
        original_signal = signal
        pending_validator = getattr(self._scenario_engine, "validate_pending_signal", None)
        if callable(pending_validator):
            validation_reason = pending_validator(signal, snapshot)
            if validation_reason is not None:
                self._abstain_signal(
                    signal,
                    snapshot,
                    str(validation_reason),
                    {"causal_engine": type(self._scenario_engine).__name__},
                )
                return
'''

ACTIVE_TRADE_ANCHOR = '''                "favorable_drift_guard_enabled": enforce_drift_guard,
            }
'''
ACTIVE_TRADE_PATCH = '''                "favorable_drift_guard_enabled": enforce_drift_guard,
                "scenario_details": dict(signal.details),
                "bias_context_id": signal.details.get("bias_context_id"),
                "olar_leg_id": signal.details.get("olar_leg_id"),
            }
'''

MANAGE_ANCHOR = '''    def _manage_open_position(self, snapshot: PrimitiveSnapshot) -> None:
        trade = self._active_trade
        if trade is None or self._exit_inflight:
            return
        opened_index = trade.get("opened_bar_index")
'''
MANAGE_PATCH = '''    def _manage_open_position(self, snapshot: PrimitiveSnapshot) -> None:
        trade = self._active_trade
        if trade is None or self._exit_inflight:
            return
        structural_exit_provider = getattr(
            self._scenario_engine,
            "pop_position_exit_for",
            None,
        )
        if callable(structural_exit_provider):
            structural_exit = structural_exit_provider(
                context_id=trade.get("bias_context_id"),
                direction=str(trade.get("direction", "")),
            )
            if structural_exit is not None:
                trade["forced_exit_reason"] = str(structural_exit["reason"])
                trade["structural_exit_details"] = dict(structural_exit)
                self._exit_inflight = True
                self.cancel_all_orders(self.config.instrument_id)
                self.close_all_positions(self.config.instrument_id)
                return
        opened_index = trade.get("opened_bar_index")
'''

FINALIZE_ANCHOR = '''    def _finalize_at_boundary(self, snapshot: PrimitiveSnapshot) -> None:
        if self._pending_signal is not None:
'''
FINALIZE_PATCH = '''    def _finalize_at_boundary(self, snapshot: PrimitiveSnapshot) -> None:
        engine_diagnostics = getattr(self._scenario_engine, "diagnostics_snapshot", None)
        if callable(engine_diagnostics):
            self.diagnostics["scenario_engine"] = engine_diagnostics()
        if self._pending_signal is not None:
'''


def _replace_once(text: str, anchor: str, patch: str, marker: str, label: str) -> str:
    if marker in text:
        return text
    if anchor not in text:
        raise RuntimeError(f"{label} anchor changed; refusing ambiguous OLAR registration")
    return text.replace(anchor, patch, 1)


def register(candidate_dir: Path) -> tuple[Path, Path]:
    strategy_path = candidate_dir / "nautilus_strategy.py"
    execution_path = candidate_dir / "nautilus_execution.py"

    strategy = strategy_path.read_text(encoding="utf-8")
    strategy = _replace_once(
        strategy,
        SELECTOR_ANCHOR,
        SELECTOR_PATCH,
        'name == "OBJECTIVE_LIFECYCLE_ACCEPTANCE_RELAY"',
        "strategy selector",
    )
    strategy_path.write_text(strategy, encoding="utf-8")

    execution = execution_path.read_text(encoding="utf-8")
    execution = _replace_once(
        execution,
        ATTEMPT_ANCHOR,
        ATTEMPT_PATCH,
        'getattr(self._scenario_engine, "validate_pending_signal"',
        "pending-signal validator",
    )
    execution = _replace_once(
        execution,
        ACTIVE_TRADE_ANCHOR,
        ACTIVE_TRADE_PATCH,
        '"scenario_details": dict(signal.details)',
        "active-trade context",
    )
    execution = _replace_once(
        execution,
        MANAGE_ANCHOR,
        MANAGE_PATCH,
        '"pop_position_exit_for"',
        "structural exit",
    )
    execution = _replace_once(
        execution,
        FINALIZE_ANCHOR,
        FINALIZE_PATCH,
        'self.diagnostics["scenario_engine"]',
        "engine diagnostics snapshot",
    )
    execution_path.write_text(execution, encoding="utf-8")
    return strategy_path, execution_path


def main() -> int:
    register(Path(__file__).resolve().parent)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
