"""Native NautilusTrader execution of parent-frozen CIRB five-second plans."""
from __future__ import annotations

from collections import defaultdict
from decimal import Decimal
from typing import Any, Mapping, Sequence

import pandas as pd

from cirb_execution_resolution import ChildPlan
from lrb_types import BarObservation, PrimitiveSnapshot, ScenarioStep
from nautilus_runner import (
    NautilusRunResult,
    build_btcusdt_perpetual,
    frame_to_nautilus_bars,
    frame_to_observations,
)
from nautilus_strategy import make_strategy_class

NS_PER_MINUTE = 60_000_000_000


class _NoopScenarioEngine:
    def abort_active(self, snapshot: PrimitiveSnapshot, reason: str) -> ScenarioStep:
        del snapshot, reason
        return ScenarioStep()


def _snapshot(observation: BarObservation, index: int, atr: float) -> PrimitiveSnapshot:
    span = max(observation.high - observation.low, 0.0)
    body = abs(observation.close - observation.open)
    upper_wick = max(observation.high - max(observation.open, observation.close), 0.0)
    lower_wick = max(min(observation.open, observation.close) - observation.low, 0.0)
    close_location = (
        (observation.close - observation.low) / span if span > 0.0 else 0.5
    )
    return PrimitiveSnapshot(
        index=index,
        observation=observation,
        ready=True,
        atr=max(atr, 1e-12),
        rel_volume=0.0,
        flow_ratio=observation.flow_ratio,
        body_atr=body / atr if atr > 0.0 else 0.0,
        range_atr=span / atr if atr > 0.0 else 0.0,
        upper_wick_fraction=upper_wick / span if span > 0.0 else 0.0,
        lower_wick_fraction=lower_wick / span if span > 0.0 else 0.0,
        close_location=close_location,
        upper_fast=None,
        lower_fast=None,
        upper_slow=None,
        lower_slow=None,
        slow_mid=None,
        range_position=None,
        upper_pool_touches=0,
        lower_pool_touches=0,
    )


def run_cirb_five_second_nautilus_backtest(
    frame: pd.DataFrame,
    plans: Sequence[ChildPlan],
    *,
    config: Mapping[str, Any],
    logic_params: Mapping[str, Any],
    generation_diagnostics: Mapping[str, Any],
) -> NautilusRunResult:
    """Replay only five-second bars; every order/fill/account action is native."""
    from nautilus_trader.backtest.engine import BacktestEngine
    from nautilus_trader.backtest.models import FillModel
    from nautilus_trader.config import BacktestEngineConfig, LoggingConfig
    from nautilus_trader.model.data import BarType
    from nautilus_trader.model.enums import AccountType, OmsType, OtoTriggerMode
    from nautilus_trader.model.identifiers import Venue
    from nautilus_trader.model.objects import Currency, Money

    effective_fee = Decimal(str(config["effective_fee_rate_per_fill"]))
    instrument = build_btcusdt_perpetual(effective_fee)
    bar_type = BarType.from_str("BTCUSDT-PERP.BINANCE-5-SECOND-LAST-EXTERNAL")
    bars = frame_to_nautilus_bars(frame, bar_type=bar_type)
    observations = frame_to_observations(frame)
    if len(bars) != len(observations):
        raise RuntimeError(
            f"five-second bar conversion mismatch: bars={len(bars)}, observations={len(observations)}"
        )

    by_timestamp: dict[int, list[ChildPlan]] = defaultdict(list)
    by_signal: dict[str, ChildPlan] = {}
    for plan in plans:
        by_timestamp[int(plan.signal.observed_ts_ns)].append(plan)
        by_signal[plan.signal.scenario_id] = plan
    for values in by_timestamp.values():
        values.sort(key=lambda item: item.signal.scenario_id)

    BaseConfig, BaseStrategy = make_strategy_class()

    class CIRBFiveSecondStrategy(BaseStrategy):
        def __init__(self, strategy_config: Any) -> None:
            super().__init__(
                strategy_config,
                observations=observations,
                logic_params=logic_params,
            )
            self._scenario_engine = _NoopScenarioEngine()
            self._execution_index = -1
            self._plans_by_timestamp = dict(by_timestamp)
            self._plans_by_signal = dict(by_signal)
            self._max_holding_ns = int(logic_params["max_holding_bars"]) * NS_PER_MINUTE
            self.diagnostics["cirb_execution_resolution"] = {
                **dict(generation_diagnostics),
                "execution_bar_interval": "5s",
                "parent_event_source": "authoritative one-minute CIRB Nautilus event ledger",
                "orders_fills_positions_nav": "NautilusTrader BacktestEngine only",
                "scheduled_context_invalidations": sum(
                    plan.invalidation_ts_ns is not None for plan in plans
                ),
                "signal_collisions": 0,
                "forced_context_exits": 0,
                "geometry_rescued_submissions": 0,
            }

        def _resolution_diagnostics(self) -> dict[str, Any]:
            return self.diagnostics["cirb_execution_resolution"]

        def _force_exit(self, reason: str) -> None:
            trade = self._active_trade
            if trade is None or self._exit_inflight:
                return
            trade["forced_exit_reason"] = reason
            self._exit_inflight = True
            self.cancel_all_orders(self.config.instrument_id)
            self.close_all_positions(self.config.instrument_id)

        def on_bar(self, bar: Any) -> None:
            self.diagnostics["bars_seen"] += 1
            self._execution_index += 1
            ts_ns = int(bar.ts_event)
            observation = self._observations.get(ts_ns)
            if observation is None:
                raise RuntimeError(
                    f"missing five-second side-channel observation for ts_event={ts_ns}"
                )
            candidates = self._plans_by_timestamp.get(ts_ns, [])
            atr = candidates[0].signal.atr if candidates else (
                float(self._active_trade.get("atr_at_signal", 1.0))
                if self._active_trade is not None
                else 1.0
            )
            snapshot = _snapshot(observation, self._execution_index, float(atr))
            self._last_snapshot = snapshot
            self._bar_index = self._execution_index
            self._sample_equity(ts_ns)

            if ts_ns >= int(self.config.final_ts_ns):
                if self._entry_inflight and self.portfolio.is_flat(self.config.instrument_id):
                    self.cancel_all_orders(self.config.instrument_id)
                if not self.portfolio.is_flat(self.config.instrument_id):
                    self._force_exit("BOUNDARY_EXIT")
                return

            if self._active_trade is not None:
                active = self._plans_by_signal.get(str(self._active_trade["scenario_id"]))
                if (
                    active is not None
                    and active.invalidation_ts_ns is not None
                    and ts_ns >= int(active.invalidation_ts_ns)
                ):
                    diagnostics = self._resolution_diagnostics()
                    diagnostics["forced_context_exits"] = int(
                        diagnostics.get("forced_context_exits", 0)
                    ) + 1
                    self._force_exit(
                        f"CAUSAL_CONTEXT_INVALIDATION_{active.invalidation_reason}"
                    )
                    return
                opened_ts = self._active_trade.get("opened_ts_ns")
                if (
                    opened_ts is not None
                    and ts_ns - int(opened_ts) >= self._max_holding_ns
                ):
                    self._force_exit("TIMEOUT")
                    return

            if not self.portfolio.is_flat(self.config.instrument_id):
                return
            if self._entry_inflight or self._exit_inflight:
                return
            if not candidates:
                return

            chosen = candidates[0]
            if len(candidates) > 1:
                diagnostics = self._resolution_diagnostics()
                diagnostics["signal_collisions"] = int(
                    diagnostics.get("signal_collisions", 0)
                ) + len(candidates) - 1
            self.diagnostics["signals_armed"] += 1
            self._record_external_transition(
                scenario_id=chosen.signal.scenario_id,
                previous_state="IDLE",
                next_state="ENTRY_ARMED",
                reason="PARENT_FROZEN_CIRB_FIVE_SECOND_RESPONSE_ENTRY_ARMED",
                ts_ns=ts_ns,
                reference_price=chosen.signal.reference_entry,
                details={
                    "parent_scenario_id": chosen.parent_scenario_id,
                    "family": chosen.signal.family,
                    "response_delay_seconds": chosen.response_delay_seconds,
                    "entry_price_improvement_bps": chosen.entry_price_improvement_bps,
                    "baseline_rr_eroded": chosen.baseline_rr_eroded,
                },
            )
            submitted_before = int(self.diagnostics["entries_submitted"])
            self._attempt_entry(chosen.signal, snapshot)
            if (
                chosen.baseline_rr_eroded
                and int(self.diagnostics["entries_submitted"]) > submitted_before
            ):
                diagnostics = self._resolution_diagnostics()
                diagnostics["geometry_rescued_submissions"] = int(
                    diagnostics.get("geometry_rescued_submissions", 0)
                ) + 1
            for rejected in candidates[1:]:
                self._abstain_signal(
                    rejected.signal,
                    snapshot,
                    "GLOBAL_SLOT_COLLISION_AT_SAME_FIVE_SECOND_CLOSE",
                    {"selected_scenario_id": chosen.signal.scenario_id},
                )

    strategy = CIRBFiveSecondStrategy(
        BaseConfig(
            instrument_id=instrument.id,
            bar_type=bar_type,
            starting_balance=Decimal(str(config["starting_balance_usdt"])),
            risk_fraction=Decimal(str(config["risk_fraction"])),
            effective_fee_rate=effective_fee,
            max_holding_bars=int(logic_params["max_holding_bars"]) * 12,
            final_ts_ns=int(frame.index[-1].value),
            min_net_rr_after_delay=Decimal(
                str(logic_params["minimum_net_rr_after_entry_delay"])
            ),
            max_entry_drift_atr=Decimal(str(logic_params["max_entry_drift_atr"])),
            one_tick_slippage_per_fill=bool(config["one_tick_slippage_per_fill"]),
        )
    )

    engine = BacktestEngine(
        config=BacktestEngineConfig(logging=LoggingConfig(log_level="ERROR"))
    )
    venue = Venue("BINANCE")
    usdt = Currency.from_str("USDT")
    try:
        fill_model = FillModel(
            prob_fill_on_limit=float(config["prob_fill_on_limit_touch"]),
            prob_slippage=1.0 if config["one_tick_slippage_per_fill"] else 0.0,
            random_seed=int(config["fill_model_seed"]),
        )
        engine.add_venue(
            venue=venue,
            oms_type=OmsType.NETTING,
            account_type=AccountType.MARGIN,
            starting_balances=[Money(float(config["starting_balance_usdt"]), usdt)],
            base_currency=usdt,
            default_leverage=Decimal(str(config["venue_default_leverage"])),
            fill_model=fill_model,
            support_contingent_orders=True,
            oto_trigger_mode=OtoTriggerMode.PARTIAL,
            use_reduce_only=True,
            bar_execution=True,
            bar_adaptive_high_low_ordering=True,
        )
        engine.add_instrument(instrument)
        engine.add_data(bars)
        engine.add_strategy(strategy)
        engine.run()
        return NautilusRunResult(
            strategy=strategy,
            fills=engine.trader.generate_order_fills_report(),
            positions=engine.trader.generate_positions_report(),
            account=engine.trader.generate_account_report(venue),
        )
    finally:
        engine.dispose()
