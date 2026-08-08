"""Execution-cost correction for bar-only market entries used by Session Raid Reversal V2.

The pinned NautilusTrader replay has two deterministic adverse entry components when a market order
is submitted from a completed bar without native quotes: one tick for crossing the synthetic bar
side and one tick from ``OneTickSlippageFillModel``.  V1 reserved only the latter.  This module adds
the missing crossing tick before quantity sizing, while leaving scenario logic, stops, targets,
fees, funding, leverage and the current-shared-NAV three-percent loss budget unchanged.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import replace
from typing import Any, Mapping

from aggtrade_acceptance_risk_v2 import RiskCompleteAggTradeAcceptanceStrategy
from quote_resiliency_signals import QuoteResiliencySignalBundle


BAR_MARKET_ENTRY_RESERVE_TICKS = 2.0
EXECUTION_RISK_REVISION = "BAR_MARKET_TWO_TICK_ENTRY_RESERVE_V1"


def apply_bar_market_entry_cost_contract(
    geometry: Mapping[str, float | int],
    *,
    tick: float,
) -> dict[str, float | int] | None:
    """Add only the missing deterministic bar-crossing tick to an existing cost geometry."""

    if tick <= 0.0:
        raise ValueError("tick must be positive")
    result: dict[str, float | int] = dict(geometry)
    existing_entry_reserve = float(result.get("entry_slippage_reserve_per_unit", tick))
    required_entry_reserve = max(existing_entry_reserve, BAR_MARKET_ENTRY_RESERVE_TICKS * tick)
    incremental = required_entry_reserve - existing_entry_reserve
    loss = float(result["expected_loss_per_unit"]) + incremental
    gain = float(result["expected_gain_per_unit"]) - incremental
    if loss <= 0.0 or gain <= 0.0:
        return None
    result.update(
        {
            "expected_loss_per_unit": loss,
            "expected_gain_per_unit": gain,
            "net_reward_risk": gain / loss,
            "entry_slippage_reserve_per_unit": required_entry_reserve,
            "bar_market_crossing_reserve_per_unit": tick,
            "fill_model_slippage_reserve_per_unit": tick,
            "bar_market_entry_reserve_ticks": BAR_MARKET_ENTRY_RESERVE_TICKS,
            "execution_risk_revision": EXECUTION_RISK_REVISION,
        }
    )
    return result


def reprice_signal_bundle_for_bar_market(
    bundle: QuoteResiliencySignalBundle,
    *,
    tick: float,
    minimum_net_reward_risk: float,
) -> QuoteResiliencySignalBundle:
    """Make signal diagnostics and final strategy sizing use the same entry-cost contract."""

    diagnostics: Counter[str] = Counter(bundle.diagnostics)
    rejected = list(bundle.rejected_scenarios)
    grouped: dict[int, list[Any]] = {}
    accepted = 0
    for timestamp, signals in bundle.signals_by_time_ns.items():
        for signal in signals:
            geometry = apply_bar_market_entry_cost_contract(
                {
                    "expected_loss_per_unit": float(signal.expected_loss_per_unit),
                    "expected_gain_per_unit": float(signal.expected_gain_per_unit),
                    "net_reward_risk": float(signal.net_reward_risk),
                    "entry_slippage_reserve_per_unit": tick,
                },
                tick=tick,
            )
            if geometry is None or float(geometry["net_reward_risk"]) < minimum_net_reward_risk:
                diagnostics["V2_INSUFFICIENT_COST_AFTER_BAR_MARKET_ENTRY"] += 1
                rejected.append(
                    {
                        "scenario_id": signal.scenario_id,
                        "scenario_family": signal.scenario_family,
                        "reason": "V2_INSUFFICIENT_COST_AFTER_BAR_MARKET_ENTRY",
                        "signal_time_ns": int(signal.signal_time_ns),
                        "details": {
                            "minimum_net_reward_risk": minimum_net_reward_risk,
                            "adjusted_net_reward_risk": (
                                None if geometry is None else float(geometry["net_reward_risk"])
                            ),
                        },
                    }
                )
                continue
            details = dict(signal.details)
            details.update(
                {
                    "execution_risk_revision": EXECUTION_RISK_REVISION,
                    "bar_market_entry_reserve_ticks": BAR_MARKET_ENTRY_RESERVE_TICKS,
                }
            )
            adjusted = replace(
                signal,
                expected_loss_per_unit=float(geometry["expected_loss_per_unit"]),
                expected_gain_per_unit=float(geometry["expected_gain_per_unit"]),
                net_reward_risk=float(geometry["net_reward_risk"]),
                details=details,
            )
            grouped.setdefault(int(timestamp), []).append(adjusted)
            accepted += 1

    diagnostics["V2_BAR_MARKET_COST_CONTRACT_PASS"] = accepted
    diagnostics["SIGNAL"] = accepted
    diagnostics["SIGNAL_TIMES"] = len(grouped)
    return QuoteResiliencySignalBundle(
        signals_by_time_ns={
            timestamp: tuple(
                sorted(
                    signals,
                    key=lambda item: (item.net_reward_risk, item.scenario_id),
                    reverse=True,
                )
            )
            for timestamp, signals in sorted(grouped.items())
        },
        diagnostics=dict(sorted(diagnostics.items())),
        rejected_scenarios=tuple(rejected),
    )


class BarMarketRiskCompleteStrategy(RiskCompleteAggTradeAcceptanceStrategy):
    """Size with the full deterministic bar-market entry cost, then verify the actual fill."""

    def _rounded_geometry(
        self,
        signal: Any,
        funding_state: dict[str, float | int],
    ) -> dict[str, float | int] | None:
        geometry = super()._rounded_geometry(signal, funding_state)
        if geometry is None:
            return None
        instrument = self.instruments.get(signal.instrument_id)
        if instrument is None:
            raise RuntimeError(f"signal instrument unavailable: {signal.instrument_id}")
        tick = float(instrument.price_increment.as_double())
        return apply_bar_market_entry_cost_contract(geometry, tick=tick)

    def _submit_signal(
        self,
        signal: Any,
        geometry: dict[str, float | int],
        ts_event_ns: int,
    ) -> None:
        before = len(self.trade_intents)
        super()._submit_signal(signal, geometry, ts_event_ns)
        if len(self.trade_intents) <= before:
            return
        intent = self.trade_intents[-1]
        intent["execution_risk_revision"] = EXECUTION_RISK_REVISION
        intent["bar_market_entry_reserve_ticks"] = BAR_MARKET_ENTRY_RESERVE_TICKS
        intent["bar_market_crossing_reserve_per_unit"] = geometry[
            "bar_market_crossing_reserve_per_unit"
        ]
        intent["fill_model_slippage_reserve_per_unit"] = geometry[
            "fill_model_slippage_reserve_per_unit"
        ]
        intent["entry_slippage_reserve_per_unit"] = geometry[
            "entry_slippage_reserve_per_unit"
        ]

    def on_order_filled(self, event: Any) -> None:
        entry_order_id = self.active_entry_order_id
        super().on_order_filled(event)
        if str(event.client_order_id) != entry_order_id or not self.trade_intents:
            return
        instrument = (
            self.instruments.get(str(self.active_instrument_id))
            if self.active_instrument_id is not None
            else None
        )
        if instrument is None:
            return
        tick = float(instrument.price_increment.as_double())
        intent = self.trade_intents[-1]
        intent["execution_risk_revision"] = EXECUTION_RISK_REVISION
        intent["bar_market_entry_reserve_ticks"] = BAR_MARKET_ENTRY_RESERVE_TICKS
        intent["bar_market_crossing_reserve_per_unit"] = tick
        intent["fill_model_slippage_reserve_per_unit"] = tick
        intent["entry_slippage_reserve_per_unit"] = BAR_MARKET_ENTRY_RESERVE_TICKS * tick


__all__ = [
    "BAR_MARKET_ENTRY_RESERVE_TICKS",
    "EXECUTION_RISK_REVISION",
    "BarMarketRiskCompleteStrategy",
    "apply_bar_market_entry_cost_contract",
    "reprice_signal_bundle_for_bar_market",
]
