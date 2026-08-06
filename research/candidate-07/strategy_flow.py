from __future__ import annotations

from decimal import Decimal
import json
from typing import Any

from nautilus_trader.config import StrategyConfig
from nautilus_trader.model.data import Bar, BarType, DataType
from nautilus_trader.model.identifiers import InstrumentId

from flow_data import AggressorFlow
from model import Direction, TradePlan
from model_flow import CausalAggressorFlowRouter, FlowLogicConfig, FlowSignalBar
from strategy import Candidate07Strategy as ExecutionStrategy

NS_PER_MINUTE = 60_000_000_000


class Candidate07FlowStrategyConfig(StrategyConfig, frozen=True):
    instrument_id: InstrumentId
    bar_type: BarType
    trade_start_ns: int
    trade_end_ns: int
    initial_nav: Decimal
    risk_fraction: Decimal
    risk_funding_reserve_bps: Decimal
    max_hold_minutes: int
    logic_json: str
    flow_logic_json: str


class Candidate07FlowStrategy(ExecutionStrategy):
    def __init__(self, config: Candidate07FlowStrategyConfig):
        super().__init__(config)
        self.logic = FlowLogicConfig.from_mapping(json.loads(config.flow_logic_json))
        self.router = CausalAggressorFlowRouter(self.logic)
        self._bucket: list[FlowSignalBar] = []
        self._signal_index = 0
        self._flow_by_ts: dict[int, AggressorFlow] = {}

    def on_start(self) -> None:
        super().on_start()
        self.subscribe_data(
            DataType(AggressorFlow),
            instrument_id=self.config.instrument_id,
        )

    def on_data(self, data: Any) -> None:
        if isinstance(data, AggressorFlow) and data.instrument_id == self.config.instrument_id:
            self._flow_by_ts[int(data.ts_event)] = data

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
            self._diagnostics.append({"reason": "AGGRESSOR_FLOW_MISSING", "bar_ts_event_ns": now})
            return

        self._bucket.append(
            FlowSignalBar(
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
            raise RuntimeError("flow aggregation bucket overflow")

        aggregated = FlowSignalBar(
            ts_event_ns=self._bucket[-1].ts_event_ns,
            open=self._bucket[0].open,
            high=max(item.high for item in self._bucket),
            low=min(item.low for item in self._bucket),
            close=self._bucket[-1].close,
            volume=sum(item.volume for item in self._bucket),
            taker_buy_volume=sum(item.taker_buy_volume for item in self._bucket),
        )
        self._bucket.clear()
        eligible = (
            self.config.trade_start_ns <= aggregated.ts_event_ns < self.config.trade_end_ns
            and self.portfolio.is_flat(self.config.instrument_id)
            and self._pending_plan is None
            and self._active_plan is None
        )
        observation = self.router.observe(aggregated, self._signal_index, eligible=eligible)
        self._signal_index += 1
        for transition in observation.transitions:
            self._append_transition(transition)
        if observation.diagnostics.get("reason") not in {"WARMUP", "INELIGIBLE"} or observation.transitions:
            self._diagnostics.append(dict(observation.diagnostics))
        if observation.plan is not None:
            self._pending_plan = observation.plan
            self._pending_created_ns = aggregated.ts_event_ns

    def _submit_pending(self, bar: Bar) -> None:
        plan: TradePlan | None = self._pending_plan
        if plan is None:
            return
        raw_atr = plan.details.get("atr")
        if raw_atr is None:
            self._invalidate_pending("FLOW_PLAN_ATR_MISSING", int(bar.ts_event))
            return
        atr = Decimal(str(raw_atr))
        current = Decimal(str(bar.close.as_double()))
        stop = Decimal(str(plan.stop_price))
        risk = current - stop if plan.direction is Direction.LONG else stop - current
        if atr <= 0 or risk <= 0:
            self._invalidate_pending("FLOW_DELAYED_ENTRY_GEOMETRY_INVALID", int(bar.ts_event))
            return
        risk_atr = risk / atr
        if risk_atr < Decimal(str(self.logic.minimum_stop_atr)) or risk_atr > Decimal(str(self.logic.maximum_stop_atr)):
            self._invalidate_pending("FLOW_DELAYED_ENTRY_STOP_OUTSIDE_STATE", int(bar.ts_event))
            return
        super()._submit_pending(bar)


__all__ = ["Candidate07FlowStrategy", "Candidate07FlowStrategyConfig"]
