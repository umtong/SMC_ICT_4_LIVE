"""One NautilusTrader strategy routing the current EasyChart scenario families.

The strategy does not average standalone backtests. Every enabled family submits
into one four-symbol account, one global pending-entry/position slot and one
fixed 3% loss budget. Arbitration is deterministic and not a learned score.
"""
from __future__ import annotations

from dataclasses import asdict
from enum import Enum
from typing import Any

from nautilus_trader.config import StrategyConfig
from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.model.identifiers import ClientOrderId, InstrumentId
from nautilus_trader.trading.strategy import Strategy

from domain import Candle, Side
from easychart_mtf_scenario import MTFOverlapScenarioEngine
from mtf_zone_touch_scenario import MTFZoneFirstTouchScenarioEngine
from strategy_orders import EasyChartOrderMixin
from trendline_retest_scenario import TrendlineFirstRetestScenarioEngine


class EasyChartIntegratedConfig(StrategyConfig, frozen=True):
    instrument_ids: tuple[InstrumentId, ...]
    higher_bar_types: tuple[BarType, ...]
    decision_bar_types: tuple[BarType, ...]
    trigger_bar_types: tuple[BarType, ...]
    execution_bar_types: tuple[BarType, ...]
    risk_fraction: float = 0.03
    min_gross_rr: float = 1.0
    estimated_entry_fee_rate: float = 0.00075
    estimated_target_fee_rate: float = 0.00075
    estimated_stop_fee_rate: float = 0.00075
    estimated_funding_rate: float = 0.00010
    estimated_entry_slippage_ticks: int = 2
    estimated_stop_slippage_ticks: int = 2
    trading_start_ns: int = 0
    # Only source-case-derived families are enabled in the primary diagnostic.
    # The older zone-sweep-first-OB hypothesis remains available for controlled
    # comparison but is not silently treated as a universal EasyChart rule.
    enable_mtf_sweep_family: bool = False
    enable_mtf_touch_family: bool = True
    enable_trendline_family: bool = True


class EasyChartIntegratedStrategy(EasyChartOrderMixin, Strategy):
    """Current unified EasyChart policy under the project execution contract."""

    HIGHER_MINUTES = 60
    DECISION_MINUTES = 15
    TRIGGER_MINUTES = 5
    NS_PER_MINUTE = 60_000_000_000

    def __init__(self, config: EasyChartIntegratedConfig) -> None:
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
        self.mtf_sweep_engines: dict[InstrumentId, MTFOverlapScenarioEngine] = {}
        self.mtf_touch_engines: dict[InstrumentId, MTFZoneFirstTouchScenarioEngine] = {}
        self.trendline_engines: dict[
            tuple[InstrumentId, int],
            TrendlineFirstRetestScenarioEngine,
        ] = {}
        self.route_by_key: dict[str, tuple[InstrumentId, int]] = {}
        self.execution_route: dict[str, InstrumentId] = {}
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
            self.execution_route[execution.id_spec_key()] = instrument_id

        self.active_plan: Any | None = None
        self.active_instrument_id: InstrumentId | None = None
        self.active_entry_id: ClientOrderId | None = None
        self.entry_cancel_requested = False
        self.emergency_exit_requested = False

        self.bar_bucket_ts: int | None = None
        self.bar_bucket: list[tuple[InstrumentId, int, Bar]] = []
        self.bar_bucket_seen: set[tuple[InstrumentId, int]] = set()
        self.event_log: list[dict[str, Any]] = []
        self.plan_log: dict[str, Any] = {}

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
    def _plain(value: Any) -> Any:
        if isinstance(value, Enum):
            return value.value
        if isinstance(value, dict):
            return {str(key): EasyChartIntegratedStrategy._plain(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [EasyChartIntegratedStrategy._plain(item) for item in value]
        return value

    @classmethod
    def _plan_event_values(cls, plan: Any) -> dict[str, Any]:
        values = cls._plain(asdict(plan))
        values["side"] = plan.side.name
        values["family"] = str(getattr(plan, "family", type(plan).__name__))
        values["entry_order_kind"] = cls.entry_order_kind(plan)
        return values

    @staticmethod
    def entry_order_kind(plan: Any) -> str:
        return str(getattr(plan, "entry_order_kind", "MARKET")).upper()

    @classmethod
    def arbitration_key(cls, instrument_id: InstrumentId, plan: Any) -> tuple[Any, ...]:
        # A fully confirmed market opportunity is perishable now, whereas a
        # limit plan waits for a future return. This is execution-state routing,
        # not a setup-quality score. Remaining ties are stable identifiers only.
        kind_rank = 0 if cls.entry_order_kind(plan) == "MARKET" else 1
        return (
            int(plan.observed_time_ns),
            kind_rank,
            str(instrument_id),
            str(getattr(plan, "family", "")),
            str(plan.plan_id),
        )

    @classmethod
    def expected_composite_count(cls, ts_event: int, symbol_count: int) -> int:
        minute = ts_event // cls.NS_PER_MINUTE
        count = symbol_count
        if minute % cls.DECISION_MINUTES == 0:
            count += symbol_count
        if minute % cls.HIGHER_MINUTES == 0:
            count += symbol_count
        return count

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
            symbol = instrument.raw_symbol.value
            tick = float(instrument.price_increment)
            if self.config.enable_mtf_sweep_family:
                self.mtf_sweep_engines[instrument_id] = MTFOverlapScenarioEngine(
                    symbol=symbol,
                    tick_size=tick,
                    minimum_gross_rr=self.config.min_gross_rr,
                )
            if self.config.enable_mtf_touch_family:
                self.mtf_touch_engines[instrument_id] = MTFZoneFirstTouchScenarioEngine(
                    symbol=symbol,
                    tick_size=tick,
                    minimum_gross_rr=self.config.min_gross_rr,
                )
            if self.config.enable_trendline_family:
                for timeframe in (self.TRIGGER_MINUTES, self.DECISION_MINUTES):
                    self.trendline_engines[(instrument_id, timeframe)] = (
                        TrendlineFirstRetestScenarioEngine(
                            symbol=symbol,
                            timeframe_minutes=timeframe,
                            tick_size=tick,
                            minimum_gross_rr=self.config.min_gross_rr,
                        )
                    )
            self.subscribe_bars(execution)
            self.subscribe_bars(trigger)
            self.subscribe_bars(decision)
            self.subscribe_bars(higher)

    def _manage_pending_limit(self, instrument_id: InstrumentId, bar: Bar) -> None:
        if (
            self.active_plan is None
            or self.active_instrument_id != instrument_id
            or self.entry_order_kind(self.active_plan) != "LIMIT"
            or self.entry_cancel_requested
            or not self.portfolio.is_flat(instrument_id)
        ):
            return
        high = float(bar.high)
        low = float(bar.low)
        side = self.active_plan.side
        target_spent = high >= self.active_plan.target if side is Side.LONG else low <= self.active_plan.target
        invalidated = low <= self.active_plan.stop if side is Side.LONG else high >= self.active_plan.stop
        if not target_spent and not invalidated:
            return
        self.entry_cancel_requested = True
        reason = "TARGET_SPENT_BEFORE_FILL" if target_spent else "INVALIDATED_BEFORE_FILL"
        self._record(
            "pending_limit_cancel_requested",
            plan_id=self.active_plan.plan_id,
            instrument_id=str(instrument_id),
            reason=reason,
            bar_high=high,
            bar_low=low,
        )
        self.cancel_all_orders(instrument_id)

    def on_bar(self, bar: Bar) -> None:
        key = bar.bar_type.id_spec_key()
        execution_instrument = self.execution_route.get(key)
        if execution_instrument is not None:
            self._manage_pending_limit(execution_instrument, bar)
            if self.bar_bucket_ts is not None and bar.ts_event > self.bar_bucket_ts:
                self._record(
                    "incomplete_bucket_fallback",
                    bucket_ts=self.bar_bucket_ts,
                    received=len(self.bar_bucket),
                    expected=self.expected_composite_count(
                        self.bar_bucket_ts,
                        len(self.config.instrument_ids),
                    ),
                )
                self._flush_bar_bucket()
            return

        route = self.route_by_key.get(key)
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
            raise RuntimeError(f"too many composite bars at {bar.ts_event}")

    def _plans_from_bar(self, instrument_id: InstrumentId, timeframe: int, candle: Candle) -> list[Any]:
        plans: list[Any] = []
        sweep_engine = self.mtf_sweep_engines.get(instrument_id)
        if sweep_engine is not None:
            plans.extend(sweep_engine.on_bar(timeframe, candle))

        touch_engine = self.mtf_touch_engines.get(instrument_id)
        if touch_engine is not None and timeframe in (
            self.HIGHER_MINUTES,
            self.DECISION_MINUTES,
        ):
            plans.extend(touch_engine.on_bar(timeframe, candle))

        trendline_engine = self.trendline_engines.get((instrument_id, timeframe))
        if trendline_engine is not None:
            plans.extend(trendline_engine.on_bar(candle))
        return plans

    def _flush_bar_bucket(self) -> None:
        if self.bar_bucket_ts is None:
            return
        candidates: list[tuple[InstrumentId, Any]] = []
        for instrument_id, timeframe, bar in sorted(
            self.bar_bucket,
            key=lambda item: (-item[1], str(item[0])),
        ):
            emitted = self._plans_from_bar(instrument_id, timeframe, self._candle(bar))
            if bar.ts_event < self.config.trading_start_ns:
                continue
            for plan in emitted:
                self.plan_log[plan.plan_id] = plan
                candidates.append((instrument_id, plan))
                self._record("plan", **self._plan_event_values(plan))

        ranked = sorted(candidates, key=lambda item: self.arbitration_key(item[0], item[1]))
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
                            basis="market_before_limit_then_stable_identifiers",
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

    def diagnostics(self) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for instrument_id in self.config.instrument_ids:
            symbol = self.instruments[instrument_id].raw_symbol.value
            values: dict[str, Any] = {}
            sweep = self.mtf_sweep_engines.get(instrument_id)
            if sweep is not None:
                values["mtf_sweep_first_ob"] = sweep.diagnostics
            touch = self.mtf_touch_engines.get(instrument_id)
            if touch is not None:
                values["mtf_ob_first_touch"] = touch.diagnostics
            for timeframe in (self.TRIGGER_MINUTES, self.DECISION_MINUTES):
                line = self.trendline_engines.get((instrument_id, timeframe))
                if line is not None:
                    values[f"trendline_first_retest_ob_{timeframe}m"] = {
                        "scenario": line.diagnostics,
                        "lines": len(line.line_tracker.lines),
                        "setups": len(line.setups),
                        "plans": len(line.plans),
                    }
            result[symbol] = values
        return result

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
