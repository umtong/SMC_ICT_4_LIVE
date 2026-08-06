"""NautilusTrader strategy adapter for the causal LRAE state machine."""

from __future__ import annotations

import csv
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

from nautilus_trader.config import StrategyConfig
from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.model.enums import OrderSide, TimeInForce
from nautilus_trader.model.events import PositionClosed, PositionOpened
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.objects import Currency
from nautilus_trader.trading.strategy import Strategy

from lrae import (
    BarSnapshot,
    LiquidityReactionEngine,
    TradePlan,
    Transition,
    risk_quantity,
)
from smc_ict_4.contracts import ResearchEvent


def _as_float(value: Any) -> float:
    if value is None:
        return 0.0
    method = getattr(value, "as_double", None)
    if callable(method):
        return float(method())
    text = str(value).strip().split()[0]
    return float(text)


def _as_int_ns(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        raw = getattr(value, "as_u64", None)
        if callable(raw):
            return int(raw())
        return int(str(value))


class LRAEStrategyConfig(StrategyConfig, frozen=True):
    instrument_id: InstrumentId
    bar_type: BarType
    feature_path: str
    research_config_path: str
    variant: str
    final_ts_ns: int


class LRAEStrategy(Strategy):
    """One-position strategy.  Pattern detection and scenario logic remain separate."""

    def __init__(self, config: LRAEStrategyConfig) -> None:
        super().__init__(config=config)
        self._research_config = json.loads(Path(config.research_config_path).read_text(encoding="utf-8"))
        self._features = self._load_features(Path(config.feature_path))
        self._logic = LiquidityReactionEngine(self._research_config, variant=config.variant)
        self._usdt = Currency.from_str("USDT")
        self.events: list[ResearchEvent] = []
        self.closed_trades: list[dict[str, Any]] = []
        self.rejections: list[dict[str, Any]] = []
        self.equity_curve: list[dict[str, Any]] = []
        self.daily_equity: dict[str, float] = {}
        self.plan_counts: dict[str, int] = {
            "emitted": 0,
            "submitted": 0,
            "skipped_busy": 0,
            "skipped_size": 0,
            "skipped_final_bar": 0,
        }
        self.active_plan: TradePlan | None = None
        self.active_state = "IDLE"
        self.bars_in_position = 0
        self.last_bar_ts_ns = 0
        self._last_day: str | None = None
        self._last_equity = float(self._research_config["initial_nav"])

    @staticmethod
    def _load_features(path: Path) -> dict[int, BarSnapshot]:
        result: dict[int, BarSnapshot] = {}
        with path.open("r", encoding="utf-8", newline="") as stream:
            reader = csv.DictReader(stream)
            required = {
                "close_time_ns",
                "open",
                "high",
                "low",
                "close",
                "volume",
                "taker_buy_volume",
                "trades",
            }
            missing = required.difference(reader.fieldnames or [])
            if missing:
                raise ValueError(f"feature CSV missing columns: {sorted(missing)}")
            for row in reader:
                snapshot = BarSnapshot(
                    ts_ns=int(row["close_time_ns"]),
                    open=float(row["open"]),
                    high=float(row["high"]),
                    low=float(row["low"]),
                    close=float(row["close"]),
                    volume=float(row["volume"]),
                    taker_buy_volume=float(row["taker_buy_volume"]),
                    trades=int(row["trades"]),
                )
                if snapshot.ts_ns in result:
                    raise ValueError(f"duplicate feature timestamp: {snapshot.ts_ns}")
                result[snapshot.ts_ns] = snapshot
        if not result:
            raise ValueError(f"feature CSV is empty: {path}")
        return result

    def on_start(self) -> None:
        self.subscribe_bars(self.config.bar_type)

    def on_bar(self, bar: Bar) -> None:
        ts_ns = _as_int_ns(bar.ts_init)
        self.last_bar_ts_ns = ts_ns

        # The complete one-minute bar is only visible now.  Feature lookup uses the
        # same close timestamp; there is no next-bar or future-confirmed information.
        snapshot = self._features.get(ts_ns)
        if snapshot is None:
            raise RuntimeError(f"no causal feature row for bar ts_init={ts_ns}")

        self._sample_equity(ts_ns)

        if self.portfolio.is_flat(self.config.instrument_id):
            self.bars_in_position = 0
        else:
            self.bars_in_position += 1
            if self.bars_in_position >= int(self._research_config["max_hold_bars"]):
                self._time_exit(ts_ns)

        if ts_ns >= int(self.config.final_ts_ns):
            if not self.portfolio.is_flat(self.config.instrument_id):
                self._flatten("FINAL_BAR_FLATTEN", ts_ns)
            self.plan_counts["skipped_final_bar"] += 1
            return

        transitions, plan = self._logic.observe(snapshot)
        for transition in transitions:
            self._record_transition(transition)

        if plan is None:
            return
        self.plan_counts["emitted"] += 1

        if not self.portfolio.is_flat(self.config.instrument_id) or self.active_plan is not None:
            self.plan_counts["skipped_busy"] += 1
            return

        self._submit_plan(plan)

    def on_position_opened(self, event: PositionOpened) -> None:
        self.bars_in_position = 0

    def on_position_closed(self, event: PositionClosed) -> None:
        realized_pnl = _as_float(event.realized_pnl) if event.realized_pnl is not None else 0.0
        avg_close = float(event.avg_px_close) if event.avg_px_close is not None else None
        scenario_id = self.active_plan.scenario_id if self.active_plan is not None else "unmapped"
        scenario_type = self.active_plan.scenario_type if self.active_plan is not None else "unmapped"
        direction = self.active_plan.direction if self.active_plan is not None else "unknown"
        self.closed_trades.append(
            {
                "scenario_id": scenario_id,
                "scenario_type": scenario_type,
                "direction": direction,
                "avg_px_open": float(event.avg_px_open),
                "avg_px_close": avg_close,
                "realized_return": float(event.realized_return),
                "realized_pnl": realized_pnl,
                "ts_opened": _as_int_ns(event.ts_opened),
                "ts_closed": _as_int_ns(event.ts_closed) if event.ts_closed is not None else _as_int_ns(event.ts_event),
                "duration_ns": _as_int_ns(event.duration),
            }
        )

        if self.active_plan is not None:
            self.events.append(
                ResearchEvent(
                    scenario_id=self.active_plan.scenario_id,
                    instrument_id=str(self.config.instrument_id),
                    event_type="POSITION_CLOSED",
                    event_time_ns=_as_int_ns(event.ts_event),
                    observed_time_ns=_as_int_ns(event.ts_init),
                    previous_state=self.active_state,
                    next_state="CLOSED",
                    reason_code="NAUTILUS_POSITION_CLOSED",
                    reference_price=str(avg_close) if avg_close is not None else None,
                    details={
                        "realized_pnl": realized_pnl,
                        "realized_return": float(event.realized_return),
                        "scenario_type": self.active_plan.scenario_type,
                    },
                )
            )

        self.active_plan = None
        self.active_state = "IDLE"
        self.bars_in_position = 0
        self._sample_equity(_as_int_ns(event.ts_init))

    def on_order_rejected(self, event: Any) -> None:
        self._record_rejection(event, "ORDER_REJECTED")

    def on_order_denied(self, event: Any) -> None:
        self._record_rejection(event, "ORDER_DENIED")

    def on_stop(self) -> None:
        self._sample_equity(self.last_bar_ts_ns or int(self.config.final_ts_ns), force_daily=True)

    def _submit_plan(self, plan: TradePlan) -> None:
        instrument = self.cache.instrument(self.config.instrument_id)
        if instrument is None:
            raise RuntimeError(f"instrument not in cache: {self.config.instrument_id}")

        nav = self._current_equity()
        qty = risk_quantity(
            nav=nav,
            risk_fraction=float(self._research_config["risk_fraction"]),
            entry=plan.entry,
            stop=plan.stop,
            fee_rate_per_side=float(self._research_config["effective_fee_rate_per_side"]),
            size_increment=float(self._research_config["size_increment"]),
        )
        if qty < float(self._research_config["size_increment"]):
            self.plan_counts["skipped_size"] += 1
            return

        order_side = OrderSide.BUY if plan.direction == "long" else OrderSide.SELL
        order_list = self.order_factory.bracket(
            instrument_id=self.config.instrument_id,
            order_side=order_side,
            quantity=instrument.make_qty(qty),
            time_in_force=TimeInForce.GTC,
            tp_price=instrument.make_price(plan.target),
            sl_trigger_price=instrument.make_price(plan.stop),
        )
        self.submit_order_list(order_list)

        self.active_plan = plan
        self.active_state = "ENTRY_SUBMITTED"
        self.plan_counts["submitted"] += 1
        self.events.append(
            ResearchEvent(
                scenario_id=plan.scenario_id,
                instrument_id=str(self.config.instrument_id),
                event_type="ENTRY_SUBMITTED",
                event_time_ns=plan.signal_ts_ns,
                observed_time_ns=plan.signal_ts_ns,
                previous_state=plan.source_state,
                next_state="ENTRY_SUBMITTED",
                reason_code="COST_AWARE_3PCT_RISK_BRACKET",
                reference_price=str(plan.entry),
                details={
                    "direction": plan.direction,
                    "scenario_type": plan.scenario_type,
                    "quantity": qty,
                    "nav": nav,
                    "risk_fraction": float(self._research_config["risk_fraction"]),
                    "entry": plan.entry,
                    "stop": plan.stop,
                    "target": plan.target,
                    "planned_loss_per_unit": plan.planned_loss_per_unit,
                    "expected_net_rr": plan.expected_net_rr,
                },
            )
        )

    def _time_exit(self, ts_ns: int) -> None:
        if self.active_plan is not None:
            self.events.append(
                ResearchEvent(
                    scenario_id=self.active_plan.scenario_id,
                    instrument_id=str(self.config.instrument_id),
                    event_type="TIME_INVALIDATION",
                    event_time_ns=ts_ns,
                    observed_time_ns=ts_ns,
                    previous_state=self.active_state,
                    next_state="TIME_EXIT_SUBMITTED",
                    reason_code="SCENARIO_INFORMATION_DECAY",
                    reference_price=None,
                    details={"bars_in_position": self.bars_in_position},
                )
            )
            self.active_state = "TIME_EXIT_SUBMITTED"
        self._flatten("TIME_EXIT", ts_ns)

    def _flatten(self, reason: str, ts_ns: int) -> None:
        try:
            self.cancel_all_orders(self.config.instrument_id)
        finally:
            if not self.portfolio.is_flat(self.config.instrument_id):
                self.close_all_positions(self.config.instrument_id)
        self.log.info(f"{reason} at {ts_ns}")

    def _record_rejection(self, event: Any, reason: str) -> None:
        ts_event = _as_int_ns(getattr(event, "ts_event", self.last_bar_ts_ns))
        ts_init = _as_int_ns(getattr(event, "ts_init", ts_event))
        record = {
            "reason": reason,
            "event": str(event),
            "ts_event": ts_event,
            "ts_init": ts_init,
        }
        self.rejections.append(record)
        if self.active_plan is not None and self.portfolio.is_flat(self.config.instrument_id):
            self.events.append(
                ResearchEvent(
                    scenario_id=self.active_plan.scenario_id,
                    instrument_id=str(self.config.instrument_id),
                    event_type=reason,
                    event_time_ns=ts_event,
                    observed_time_ns=ts_init,
                    previous_state=self.active_state,
                    next_state="REJECTED",
                    reason_code=reason,
                    reference_price=None,
                    details=record,
                )
            )
            self.active_plan = None
            self.active_state = "IDLE"

    def _record_transition(self, transition: Transition) -> None:
        self.events.append(
            ResearchEvent(
                scenario_id=transition.scenario_id,
                instrument_id=str(self.config.instrument_id),
                event_type="SCENARIO_STATE_TRANSITION",
                event_time_ns=transition.event_time_ns,
                observed_time_ns=transition.observed_time_ns,
                previous_state=transition.previous_state,
                next_state=transition.next_state,
                reason_code=transition.reason_code,
                reference_price=str(transition.reference_price),
                details=dict(transition.details),
            )
        )

    def _current_equity(self) -> float:
        venue = self.config.instrument_id.venue

        # Prefer NautilusTrader's mark-to-market equity.  Fallbacks support the
        # pinned version's account API without creating parallel accounting.
        try:
            values = self.portfolio.equity(venue)
            if values:
                money = values.get(self._usdt)
                if money is None and len(values) == 1:
                    money = next(iter(values.values()))
                if money is not None:
                    value = _as_float(money)
                    if value > 0.0:
                        self._last_equity = value
                        return value
        except (AttributeError, TypeError, RuntimeError):
            pass

        try:
            account = self.portfolio.account(venue)
        except (AttributeError, TypeError):
            account = self.cache.account_for_venue(venue)
        if account is not None:
            money = account.balance_total(self._usdt)
            if money is not None:
                value = _as_float(money)
                # Add engine-computed unrealized PnL for the margin account if
                # the older API did not expose equity().
                try:
                    unrealized = self.portfolio.unrealized_pnl(
                        self.config.instrument_id,
                        target_currency=self._usdt,
                    )
                except (AttributeError, TypeError):
                    unrealized = self.portfolio.unrealized_pnl(self.config.instrument_id)
                if unrealized is not None:
                    value += _as_float(unrealized)
                if value > 0.0:
                    self._last_equity = value
                    return value

        return self._last_equity

    def _sample_equity(self, ts_ns: int, *, force_daily: bool = False) -> None:
        if ts_ns <= 0:
            return
        equity = self._current_equity()
        date = datetime.fromtimestamp(ts_ns / 1_000_000_000, tz=timezone.utc).date().isoformat()
        self.equity_curve.append({"ts_ns": ts_ns, "equity": equity})
        self.daily_equity[date] = equity
        self._last_day = date
        if force_daily:
            self.daily_equity[date] = equity

    def evidence(self) -> dict[str, Any]:
        return {
            "variant": self.config.variant,
            "plan_counts": dict(self.plan_counts),
            "closed_trades": list(self.closed_trades),
            "rejections": list(self.rejections),
            "daily_equity": dict(sorted(self.daily_equity.items())),
            "equity_curve": list(self.equity_curve),
            "final_equity": self._current_equity(),
            "events": len(self.events),
        }
