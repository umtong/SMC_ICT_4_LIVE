"""NautilusTrader strategy for the EasyChart v3 multi-scale scenarios."""
from __future__ import annotations

from dataclasses import asdict
from typing import Any

from nautilus_trader.config import StrategyConfig
from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.model.identifiers import ClientOrderId, InstrumentId
from nautilus_trader.trading.strategy import Strategy

from domain import Candle
from easychart_mtf_scenario import MTFTradePlan, MultiScaleScenarioBundle
from strategy_orders import EasyChartOrderMixin


class EasyChartMTFConfig(StrategyConfig, frozen=True):
    instrument_ids: tuple[InstrumentId, ...]
    higher_bar_types: tuple[BarType, ...]
    decision_bar_types: tuple[BarType, ...]
    trigger_bar_types: tuple[BarType, ...]
    execution_bar_types: tuple[BarType, ...]
    risk_fraction: float = 0.03
    min_gross_rr: float = 1.0
    estimated_entry_fee_rate: float = 0.00075
    estimated_stop_fee_rate: float = 0.00075
    estimated_funding_rate: float = 0.00010
    estimated_entry_slippage_ticks: int = 2
    estimated_stop_slippage_ticks: int = 2
    trading_start_ns: int = 0


class EasyChartMTFStrategy(EasyChartOrderMixin, Strategy):
    """One continuous four-symbol account with causal, deterministic routing."""

    HIGHER_MINUTES = 60
    DECISION_MINUTES = 15
    TRIGGER_MINUTES = 5
    EXECUTION_MINUTES = 1
    NS_PER_MINUTE = 60_000_000_000

    def __init__(self, config: EasyChartMTFConfig) -> None:
        super().__init__(config)
        count = len(config.instrument_ids)
        lengths = (
            len(config.higher_bar_types),
            len(config.decision_bar_types),
            len(config.trigger_bar_types),
            len(config.execution_bar_types),
        )
        if any(length != count for length in lengths):
            raise ValueError("instrument IDs and all bar-type tuples must have equal length")
        if config.estimated_entry_slippage_ticks < 0 or config.estimated_stop_slippage_ticks < 0:
            raise ValueError("estimated slippage ticks cannot be negative")

        self.instruments: dict[InstrumentId, Any] = {}
        self.scenario_engines: dict[InstrumentId, MultiScaleScenarioBundle] = {}
        self.route_by_key: dict[str, tuple[InstrumentId, int]] = {}
        for instrument_id, higher, decision, trigger, execution in zip(
            config.instrument_ids,
            config.higher_bar_types,
            config.decision_bar_types,
            config.trigger_bar_types,
            config.execution_bar_types,
            strict=True,
        ):
            self.route_by_key[higher.id_spec_key()] = (instrument_id, self.HIGHER_MINUTES)
            self.route_by_key[decision.id_spec_key()] = (instrument_id, self.DECISION_MINUTES)
            self.route_by_key[trigger.id_spec_key()] = (instrument_id, self.TRIGGER_MINUTES)
            self.route_by_key[execution.id_spec_key()] = (instrument_id, self.EXECUTION_MINUTES)

        self.active_plan: MTFTradePlan | None = None
        self.active_instrument_id: InstrumentId | None = None
        self.active_entry_id: ClientOrderId | None = None
        self.entry_cancel_requested = False
        self.emergency_exit_requested = False
        self.bar_bucket_ts: int | None = None
        self.bar_bucket: list[tuple[InstrumentId, int, Bar]] = []
        self.bar_bucket_seen: set[tuple[InstrumentId, int]] = set()
        self.event_log: list[dict[str, Any]] = []
        self.plan_log: dict[str, MTFTradePlan] = {}

    def _record(self, kind: str, **values: Any) -> None:
        self.event_log.append({"kind": kind, "ts_ns": self.clock.timestamp_ns(), **values})

    def _portfolio_flat(self) -> bool:
        return all(self.portfolio.is_flat(item) for item in self.config.instrument_ids)

    @staticmethod
    def _candle(bar: Bar) -> Candle:
        return Candle(
            ts_close_ns=bar.ts_event,
            open=float(bar.open),
            high=float(bar.high),
            low=float(bar.low),
            close=float(bar.close),
            volume=float(bar.volume),
        )

    @staticmethod
    def _plan_event_values(plan: MTFTradePlan) -> dict[str, Any]:
        values = asdict(plan)
        values["side"] = plan.side.name
        values["higher_zone_kind"] = plan.higher_zone_kind.value
        values["lower_zone_kind"] = plan.lower_zone_kind.value
        values["target_zone_kind"] = plan.target_zone_kind.value
        values["context_kind_diversity"] = len({plan.higher_zone_kind, plan.lower_zone_kind})
        return values

    @classmethod
    def expected_composite_count(cls, ts_event: int, symbol_count: int) -> int:
        """Return 1m plus coincident 5m/15m/60m bars expected at a UTC close."""
        minute = ts_event // cls.NS_PER_MINUTE
        per_symbol = 1
        if minute % cls.TRIGGER_MINUTES == 0:
            per_symbol += 1
        if minute % cls.DECISION_MINUTES == 0:
            per_symbol += 1
        if minute % cls.HIGHER_MINUTES == 0:
            per_symbol += 1
        return per_symbol * symbol_count

    def on_start(self) -> None:
        for instrument_id, higher, decision, trigger, execution in zip(
            self.config.instrument_ids,
            self.config.higher_bar_types,
            self.config.decision_bar_types,
            self.config.trigger_bar_types,
            self.config.execution_bar_types,
            strict=True,
        ):
            instrument = self.cache.instrument(instrument_id)
            if instrument is None:
                self.log.error(f"Could not find instrument for {instrument_id}")
                self.stop()
                return
            self.instruments[instrument_id] = instrument
            self.scenario_engines[instrument_id] = MultiScaleScenarioBundle(
                symbol=instrument.raw_symbol.value,
                tick_size=float(instrument.price_increment),
                minimum_gross_rr=self.config.min_gross_rr,
            )
            self.subscribe_bars(execution)
            self.subscribe_bars(trigger)
            self.subscribe_bars(decision)
            self.subscribe_bars(higher)

    def on_bar(self, bar: Bar) -> None:
        route = self.route_by_key.get(bar.bar_type.id_spec_key())
        if route is None:
            return
        instrument_id, timeframe = route
        if self.bar_bucket_ts is None:
            self.bar_bucket_ts = bar.ts_event
        elif bar.ts_event != self.bar_bucket_ts:
            self._record(
                "incomplete_bucket_timestamp_change",
                bucket_ts=self.bar_bucket_ts,
                next_ts=bar.ts_event,
                received=len(self.bar_bucket),
                expected=self.expected_composite_count(
                    self.bar_bucket_ts,
                    len(self.config.instrument_ids),
                ),
            )
            self._flush_bar_bucket()
            self.bar_bucket_ts = bar.ts_event

        identity = (instrument_id, timeframe)
        if identity in self.bar_bucket_seen:
            raise RuntimeError(f"duplicate composite bar in bucket: {identity} @ {bar.ts_event}")
        self.bar_bucket_seen.add(identity)
        self.bar_bucket.append((instrument_id, timeframe, bar))
        expected = self.expected_composite_count(bar.ts_event, len(self.config.instrument_ids))
        if len(self.bar_bucket) == expected:
            self._flush_bar_bucket()
        elif len(self.bar_bucket) > expected:
            raise RuntimeError(
                f"too many composite bars at {bar.ts_event}: {len(self.bar_bucket)} > {expected}",
            )

    def _flush_bar_bucket(self) -> None:
        if self.bar_bucket_ts is None:
            return
        plans: list[tuple[InstrumentId, MTFTradePlan]] = []
        for instrument_id, timeframe, bar in sorted(
            self.bar_bucket,
            key=lambda item: (-item[1], str(item[0])),
        ):
            engine = self.scenario_engines[instrument_id]
            emitted = engine.on_bar(timeframe, self._candle(bar))
            for transition in engine.drain_trace():
                if transition.get("event_time_ns", 0) >= self.config.trading_start_ns:
                    self._record(
                        "scenario_transition",
                        instrument_id=str(instrument_id),
                        timeframe_minutes=timeframe,
                        **transition,
                    )
            if bar.ts_event < self.config.trading_start_ns:
                continue
            for plan in emitted:
                self.plan_log[plan.plan_id] = plan
                plans.append((instrument_id, plan))
                self._record("plan", **self._plan_event_values(plan))

        ranked = sorted(
            plans,
            key=lambda item: (
                item[1].interaction_time_ns,
                -item[1].higher_timeframe_minutes,
                -len({item[1].higher_zone_kind, item[1].lower_zone_kind}),
                item[1].setup_observed_time_ns,
                item[1].symbol,
                item[1].plan_id,
            ),
        )
        if ranked:
            if self.active_plan is not None or not self._portfolio_flat():
                for rank, (instrument_id, plan) in enumerate(ranked, start=1):
                    self._record(
                        "plan_skipped_global_slot",
                        plan_id=plan.plan_id,
                        instrument_id=str(instrument_id),
                        arbitration_rank=rank,
                        active_plan_id=None if self.active_plan is None else self.active_plan.plan_id,
                        portfolio_flat=self._portfolio_flat(),
                    )
            else:
                selected_index: int | None = None
                for index, (instrument_id, plan) in enumerate(ranked):
                    if self._submit_plan(instrument_id, plan):
                        selected_index = index
                        self._record(
                            "arbitration_selected",
                            plan_id=plan.plan_id,
                            instrument_id=str(instrument_id),
                            arbitration_rank=index + 1,
                            candidates=len(ranked),
                        )
                        break
                if selected_index is not None:
                    selected_plan_id = ranked[selected_index][1].plan_id
                    for rank, (instrument_id, plan) in enumerate(ranked, start=1):
                        if rank - 1 <= selected_index:
                            continue
                        self._record(
                            "plan_skipped_arbitration",
                            plan_id=plan.plan_id,
                            instrument_id=str(instrument_id),
                            arbitration_rank=rank,
                            selected_plan_id=selected_plan_id,
                        )
        self.bar_bucket.clear()
        self.bar_bucket_seen.clear()
        self.bar_bucket_ts = None

    def on_stop(self) -> None:
        if self.bar_bucket:
            self._record(
                "signal_bucket_discarded_at_stop",
                bucket_ts=self.bar_bucket_ts,
                bars=len(self.bar_bucket),
            )
        self.bar_bucket.clear()
        self.bar_bucket_seen.clear()
        self.bar_bucket_ts = None
        for instrument_id, higher, decision, trigger, execution in zip(
            self.config.instrument_ids,
            self.config.higher_bar_types,
            self.config.decision_bar_types,
            self.config.trigger_bar_types,
            self.config.execution_bar_types,
            strict=True,
        ):
            self.cancel_all_orders(instrument_id)
            if not self.portfolio.is_flat(instrument_id):
                self.close_all_positions(instrument_id)
            self.unsubscribe_bars(higher)
            self.unsubscribe_bars(decision)
            self.unsubscribe_bars(trigger)
            self.unsubscribe_bars(execution)
