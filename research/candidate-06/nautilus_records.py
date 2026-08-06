"""Causal event logging and direct portfolio NAV sampling for candidate-06."""

from __future__ import annotations

from typing import Any, Mapping

from logic import PrimitiveSnapshot, ScenarioSignal, ScenarioTransition
from nautilus_strategy_common import money_to_float as _money_to_float
from smc_ict_4.contracts import ResearchEvent


class NautilusRecordMixin:
    """Persist explainable state chains and NAV without parallel accounting."""

    def _abstain_signal(
        self,
        signal: ScenarioSignal,
        snapshot: PrimitiveSnapshot,
        reason: str,
        details: Mapping[str, Any],
    ) -> None:
        bucket = self.diagnostics["entry_abstentions"]
        bucket[reason] = int(bucket.get(reason, 0)) + 1
        self._record_external_transition(
            scenario_id=signal.scenario_id,
            previous_state="ENTRY_ARMED",
            next_state="RESET",
            reason=reason,
            ts_ns=snapshot.observation.ts_ns,
            reference_price=snapshot.observation.close,
            details=dict(details),
        )

    def _record_transitions(self, transitions: tuple[ScenarioTransition, ...], ts_ns: int) -> None:
        for transition in transitions:
            self._record_external_transition(
                scenario_id=transition.scenario_id,
                previous_state=transition.previous_state,
                next_state=transition.next_state,
                reason=transition.reason_code,
                ts_ns=ts_ns,
                reference_price=transition.reference_price,
                details=transition.details,
                event_type=transition.event_type,
            )

    def _record_external_transition(
        self,
        *,
        scenario_id: str,
        previous_state: str,
        next_state: str,
        reason: str,
        ts_ns: int,
        reference_price: float | None,
        details: Mapping[str, Any],
        event_type: str = "SCENARIO_TRANSITION",
    ) -> None:
        expected = self._scenario_states.get(scenario_id, "IDLE")
        if previous_state != expected:
            raise RuntimeError(
                f"scenario state chain mismatch for {scenario_id}: expected {expected}, got {previous_state}",
            )
        event = ResearchEvent(
            scenario_id=scenario_id,
            instrument_id=str(self.config.instrument_id),
            event_type=event_type,
            event_time_ns=ts_ns,
            observed_time_ns=ts_ns,
            previous_state=previous_state,
            next_state=next_state,
            reason_code=reason,
            reference_price=None if reference_price is None else f"{reference_price:.8f}",
            details=dict(details),
        )
        self.events.append(event)
        self._scenario_states[scenario_id] = next_state

    def _current_equity(self) -> float:
        try:
            values = self.portfolio.equity(self.config.instrument_id.venue)
            if isinstance(values, dict):
                money = values.get(self._usdt)
                if money is None:
                    for currency, candidate in values.items():
                        if str(currency) == "USDT":
                            money = candidate
                            break
                if money is not None:
                    return _money_to_float(money)
            elif values is not None and hasattr(values, "as_decimal"):
                return _money_to_float(values)
        except Exception as exc:
            self.errors.append(f"portfolio equity query failed: {type(exc).__name__}: {exc}")
        self.diagnostics["equity_query_fallbacks"] += 1
        return float(self.config.starting_balance) + sum(
            float(trade["realized_pnl_after_cost"]) for trade in self.closed_trades
        )

    def _sample_equity(self, ts_ns: int) -> None:
        value = self._current_equity()
        if self.equity_samples and self.equity_samples[-1]["ts_ns"] == ts_ns:
            self.equity_samples[-1]["nav"] = value
        else:
            self.equity_samples.append({"ts_ns": ts_ns, "nav": value})

