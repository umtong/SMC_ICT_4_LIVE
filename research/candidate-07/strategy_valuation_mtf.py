"""NautilusTrader strategy for five-minute selection and one-minute valuation entry."""
from __future__ import annotations

from decimal import Decimal
import json
from typing import Any

from nautilus_trader.config import StrategyConfig
from nautilus_trader.model.data import Bar, BarType, CustomData, DataType
from nautilus_trader.model.identifiers import InstrumentId

from flow_data import AggressorFlow, FLOW_CLIENT_ID
from index_reference import INDEX_REFERENCE_CLIENT_ID, IndexPriceReference
from model_valuation_mtf import (
    MTFValuationDislocationRouter,
    MTFValuationLogicConfig,
    ValuationMinuteBar,
    ValuationSignalBar,
)
from positioning_data import POSITIONING_CLIENT_ID, PositioningSnapshot
from strategy import Candidate07Strategy as ExecutionStrategy


NS_PER_MINUTE = 60_000_000_000


class Candidate07MTFValuationStrategyConfig(StrategyConfig, frozen=True):
    instrument_id: InstrumentId
    bar_type: BarType
    trade_start_ns: int
    trade_end_ns: int
    initial_nav: Decimal
    risk_fraction: Decimal
    risk_funding_reserve_bps: Decimal
    max_hold_minutes: int
    logic_json: str
    positioning_logic_json: str


class Candidate07MTFValuationStrategy(ExecutionStrategy):
    """Use completed five-minute OI events and subsequent one-minute execution."""

    def __init__(self, config: Candidate07MTFValuationStrategyConfig):
        super().__init__(config)
        self.logic = MTFValuationLogicConfig.from_mapping(
            json.loads(config.positioning_logic_json)
        )
        self.router = MTFValuationDislocationRouter(self.logic)
        self._minute_bucket: list[ValuationMinuteBar] = []
        self._minute_index = 0
        self._signal_index = 0
        self._flow_by_ts: dict[int, AggressorFlow] = {}
        self._positioning_by_ts: dict[int, PositioningSnapshot] = {}
        self._index_by_ts: dict[int, IndexPriceReference] = {}

    def on_start(self) -> None:
        super().on_start()
        self.subscribe_data(
            DataType(AggressorFlow),
            client_id=FLOW_CLIENT_ID,
            instrument_id=self.config.instrument_id,
        )
        self.subscribe_data(
            DataType(PositioningSnapshot),
            client_id=POSITIONING_CLIENT_ID,
            instrument_id=self.config.instrument_id,
        )
        self.subscribe_data(
            DataType(IndexPriceReference),
            client_id=INDEX_REFERENCE_CLIENT_ID,
            instrument_id=self.config.instrument_id,
        )

    def on_data(self, data: Any) -> None:
        payload = data.data if isinstance(data, CustomData) else data
        if isinstance(payload, AggressorFlow) and payload.instrument_id == self.config.instrument_id:
            self._flow_by_ts[int(payload.ts_event)] = payload
        elif (
            isinstance(payload, PositioningSnapshot)
            and payload.instrument_id == self.config.instrument_id
        ):
            self._positioning_by_ts[int(payload.ts_event)] = payload
        elif (
            isinstance(payload, IndexPriceReference)
            and payload.instrument_id == self.config.instrument_id
        ):
            self._index_by_ts[int(payload.ts_event)] = payload

    def on_bar(self, bar: Bar) -> None:
        now = int(bar.ts_event)
        current_minute_index = self._minute_index
        self._record_nav(now)
        in_window = self.config.trade_start_ns <= now < self.config.trade_end_ns
        flat = self.portfolio.is_flat(self.config.instrument_id)

        if not flat and self._position_open_ns is not None:
            held = now - self._position_open_ns
            if held >= self.config.max_hold_minutes * NS_PER_MINUTE and not self._exit_pending:
                self._exit_pending = True
                self.cancel_all_orders(self.config.instrument_id)
                self.close_all_positions(self.config.instrument_id)

        if now >= self.config.trade_end_ns - NS_PER_MINUTE and not flat and not self._exit_pending:
            self._exit_pending = True
            self.cancel_all_orders(self.config.instrument_id)
            self.close_all_positions(self.config.instrument_id)

        if self._pending_plan is not None and now > self._pending_plan.observed_time_ns:
            if in_window and flat and self._active_plan is None:
                self._submit_pending(bar)
            else:
                self._invalidate_pending("ENTRY_WINDOW_OR_SLOT_LOST", now)

        source_ts = now - 1
        flow = self._flow_by_ts.pop(source_ts, None)
        index = self._index_by_ts.pop(source_ts, None)
        if flow is None or index is None:
            reason = (
                "AGGRESSOR_FLOW_DATA_GAP"
                if flow is None
                else "INDEX_PRICE_DATA_GAP"
            )
            for transition in self.router.invalidate_data_gap(
                minute_index=current_minute_index,
                event_time_ns=now,
                reference_price=bar.close.as_double(),
                reason_code=reason,
            ):
                self._append_transition(transition)
            self._diagnostics.append(
                {
                    "reason": reason,
                    "bar_ts_event_ns": now,
                    "source_ts_event_ns": source_ts,
                    "flow_present": flow is not None,
                    "index_present": index is not None,
                }
            )
            self._minute_bucket.clear()
            raise RuntimeError(f"verified completed data missing at {source_ts}: {reason}")

        minute = ValuationMinuteBar(
            ts_event_ns=now,
            open=bar.open.as_double(),
            high=bar.high.as_double(),
            low=bar.low.as_double(),
            close=bar.close.as_double(),
            volume=float(flow.total_volume),
            taker_buy_volume=float(flow.taker_buy_volume),
            index_open=index.open,
            index_high=index.high,
            index_low=index.low,
            index_close=index.close,
        )
        minute_eligible = (
            in_window
            and self.portfolio.is_flat(self.config.instrument_id)
            and self._pending_plan is None
            and self._active_plan is None
        )
        minute_observation = self.router.observe_minute(
            minute,
            current_minute_index,
            eligible=minute_eligible,
        )
        self._record_observation(minute_observation)
        if minute_observation.plan is not None:
            self._pending_plan = minute_observation.plan
            self._pending_created_ns = minute.ts_event_ns

        self._minute_bucket.append(minute)
        if len(self._minute_bucket) > self.logic.signal_minutes:
            raise RuntimeError("MTF valuation aggregation bucket overflow")

        if len(self._minute_bucket) == self.logic.signal_minutes:
            positioning = self._positioning_by_ts.pop(source_ts, None)
            if positioning is None:
                for transition in self.router.invalidate_data_gap(
                    minute_index=current_minute_index,
                    event_time_ns=now,
                    reference_price=minute.close,
                    reason_code="POSITIONING_DATA_GAP",
                ):
                    self._append_transition(transition)
                self._diagnostics.append(
                    {
                        "reason": "POSITIONING_SNAPSHOT_MISSING",
                        "signal_ts_event_ns": now,
                        "expected_snapshot_ts_event_ns": source_ts,
                        "signal_interval_skipped": True,
                        "forward_fill_used": False,
                        "interpolation_used": False,
                    }
                )
                self._signal_index += 1
                self._minute_bucket.clear()
            else:
                signal = ValuationSignalBar(
                    ts_event_ns=now,
                    open=self._minute_bucket[0].open,
                    high=max(item.high for item in self._minute_bucket),
                    low=min(item.low for item in self._minute_bucket),
                    close=self._minute_bucket[-1].close,
                    volume=sum(item.volume for item in self._minute_bucket),
                    taker_buy_volume=sum(
                        item.taker_buy_volume for item in self._minute_bucket
                    ),
                    index_close=self._minute_bucket[-1].index_close,
                    open_interest=positioning.open_interest,
                    open_interest_value=positioning.open_interest_value,
                    top_trader_account_ratio=positioning.top_trader_account_ratio,
                    top_trader_position_ratio=positioning.top_trader_position_ratio,
                    global_long_short_ratio=positioning.global_long_short_ratio,
                    taker_long_short_ratio=positioning.taker_long_short_ratio,
                )
                signal_eligible = (
                    in_window
                    and self.portfolio.is_flat(self.config.instrument_id)
                    and self._pending_plan is None
                    and self._active_plan is None
                )
                signal_observation = self.router.observe_signal(
                    signal,
                    self._signal_index,
                    current_minute_index,
                    eligible=signal_eligible,
                )
                self._record_observation(signal_observation)
                if signal_observation.plan is not None:
                    self._pending_plan = signal_observation.plan
                    self._pending_created_ns = signal.ts_event_ns
                self._signal_index += 1
                self._minute_bucket.clear()

        self._minute_index += 1
        stale_before = now - 2 * self.logic.signal_minutes * NS_PER_MINUTE
        self._flow_by_ts = {
            timestamp: item
            for timestamp, item in self._flow_by_ts.items()
            if timestamp >= stale_before
        }
        self._index_by_ts = {
            timestamp: item
            for timestamp, item in self._index_by_ts.items()
            if timestamp >= stale_before
        }
        self._positioning_by_ts = {
            timestamp: item
            for timestamp, item in self._positioning_by_ts.items()
            if timestamp >= stale_before
        }

    def _record_observation(self, observation) -> None:
        for transition in observation.transitions:
            self._append_transition(transition)
        diagnostics = dict(observation.diagnostics)
        reason = diagnostics.get("reason")
        if (
            observation.transitions
            or observation.plan is not None
            or diagnostics.get("active_scenario_id") is not None
            or diagnostics.get("geometry_reason") is not None
            or reason not in {None, "SIGNAL_WARMUP", "MINUTE_WARMUP", "SIGNAL_INELIGIBLE", "EXECUTION_INELIGIBLE"}
        ):
            self._diagnostics.append(diagnostics)


__all__ = [
    "Candidate07MTFValuationStrategy",
    "Candidate07MTFValuationStrategyConfig",
]
