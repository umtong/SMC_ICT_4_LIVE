"""NautilusTrader strategy integration for positioning-aware candidate-07."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
import json
from typing import Any

from nautilus_trader.config import StrategyConfig
from nautilus_trader.model.data import Bar, BarType, CustomData, DataType
from nautilus_trader.model.identifiers import InstrumentId

from flow_data import AggressorFlow, FLOW_CLIENT_ID
from model import TradePlan
from model_positioning import PositioningLogicConfig, PositioningSignalBar
from model_positioning_gap_safe import GapSafePositioningAuctionRouter
from positioning_data import POSITIONING_CLIENT_ID, PositioningSnapshot
from strategy import Candidate07Strategy as ExecutionStrategy


NS_PER_MINUTE = 60_000_000_000


@dataclass(frozen=True, slots=True)
class _MinuteAuctionBar:
    ts_event_ns: int
    open: float
    high: float
    low: float
    close: float
    volume: float
    taker_buy_volume: float


class Candidate07PositioningStrategyConfig(StrategyConfig, frozen=True):
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


class Candidate07PositioningStrategy(ExecutionStrategy):
    """Route external-liquidity contacts by completed inventory changes."""

    def __init__(self, config: Candidate07PositioningStrategyConfig):
        super().__init__(config)
        self.logic = PositioningLogicConfig.from_mapping(
            json.loads(config.positioning_logic_json)
        )
        self.router = GapSafePositioningAuctionRouter(self.logic)
        self._bucket: list[_MinuteAuctionBar] = []
        self._signal_index = 0
        self._flow_by_ts: dict[int, AggressorFlow] = {}
        self._positioning_by_ts: dict[int, PositioningSnapshot] = {}

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

    def on_data(self, data: Any) -> None:
        payload = data.data if isinstance(data, CustomData) else data
        if isinstance(payload, AggressorFlow) and payload.instrument_id == self.config.instrument_id:
            self._flow_by_ts[int(payload.ts_event)] = payload
        elif (
            isinstance(payload, PositioningSnapshot)
            and payload.instrument_id == self.config.instrument_id
        ):
            self._positioning_by_ts[int(payload.ts_event)] = payload

    def on_bar(self, bar: Bar) -> None:
        now = int(bar.ts_event)
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

        flow = self._flow_by_ts.pop(now - 1, None)
        if flow is None:
            for transition in self.router.invalidate_data_gap(
                index=self._signal_index,
                event_time_ns=now,
                reference_price=bar.close.as_double(),
                reason_code="AGGRESSOR_FLOW_DATA_GAP",
            ):
                self._append_transition(transition)
            self._diagnostics.append(
                {"reason": "AGGRESSOR_FLOW_MISSING", "bar_ts_event_ns": now}
            )
            self._bucket.clear()
            raise RuntimeError(f"verified aggressor flow missing at {now - 1}")

        self._bucket.append(
            _MinuteAuctionBar(
                ts_event_ns=now,
                open=bar.open.as_double(),
                high=bar.high.as_double(),
                low=bar.low.as_double(),
                close=bar.close.as_double(),
                volume=float(flow.total_volume),
                taker_buy_volume=float(flow.taker_buy_volume),
            )
        )
        if len(self._bucket) < self.logic.signal_minutes:
            return
        if len(self._bucket) > self.logic.signal_minutes:
            raise RuntimeError("positioning aggregation bucket overflow")

        positioning = self._positioning_by_ts.pop(now - 1, None)
        if positioning is None:
            for transition in self.router.invalidate_data_gap(
                index=self._signal_index,
                event_time_ns=now,
                reference_price=self._bucket[-1].close,
                reason_code="POSITIONING_DATA_GAP",
            ):
                self._append_transition(transition)
            self._diagnostics.append(
                {
                    "reason": "POSITIONING_SNAPSHOT_MISSING",
                    "signal_ts_event_ns": now,
                    "expected_snapshot_ts_event_ns": now - 1,
                    "bucket_minutes": self.logic.signal_minutes,
                    "signal_interval_skipped": True,
                    "forward_fill_used": False,
                    "interpolation_used": False,
                }
            )
            self._signal_index += 1
            self._bucket.clear()
            return

        aggregated = PositioningSignalBar(
            ts_event_ns=now,
            open=self._bucket[0].open,
            high=max(item.high for item in self._bucket),
            low=min(item.low for item in self._bucket),
            close=self._bucket[-1].close,
            volume=sum(item.volume for item in self._bucket),
            taker_buy_volume=sum(item.taker_buy_volume for item in self._bucket),
            open_interest=positioning.open_interest,
            open_interest_value=positioning.open_interest_value,
            top_trader_account_ratio=positioning.top_trader_account_ratio,
            top_trader_position_ratio=positioning.top_trader_position_ratio,
            global_long_short_ratio=positioning.global_long_short_ratio,
            taker_long_short_ratio=positioning.taker_long_short_ratio,
        )
        self._bucket.clear()
        eligible = (
            self.config.trade_start_ns <= aggregated.ts_event_ns < self.config.trade_end_ns
            and self.portfolio.is_flat(self.config.instrument_id)
            and self._pending_plan is None
            and self._active_plan is None
        )
        observation = self.router.observe(
            aggregated,
            self._signal_index,
            eligible=eligible,
        )
        self._signal_index += 1
        for transition in observation.transitions:
            self._append_transition(transition)
        if observation.diagnostics.get("reason") not in {"WARMUP", "INELIGIBLE"} or observation.transitions:
            self._diagnostics.append(dict(observation.diagnostics))
        if observation.plan is not None:
            self._pending_plan = observation.plan
            self._pending_created_ns = aggregated.ts_event_ns

        stale_before = now - 2 * self.logic.signal_minutes * NS_PER_MINUTE
        self._flow_by_ts = {
            timestamp: item
            for timestamp, item in self._flow_by_ts.items()
            if timestamp >= stale_before
        }
        self._positioning_by_ts = {
            timestamp: item
            for timestamp, item in self._positioning_by_ts.items()
            if timestamp >= stale_before
        }

    def _submit_pending(self, bar: Bar) -> None:
        plan: TradePlan | None = self._pending_plan
        if plan is None:
            return
        # The model fixes stop and target at ENTRY_READY. The inherited
        # Nautilus execution path rejects a delayed market entry if the actual
        # reward-to-risk has eroded, then performs current-NAV 3% sizing,
        # bracket creation, fees, slippage and funding reserve accounting.
        super()._submit_pending(bar)


__all__ = [
    "Candidate07PositioningStrategy",
    "Candidate07PositioningStrategyConfig",
]
