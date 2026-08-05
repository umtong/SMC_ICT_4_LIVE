"""NautilusTrader execution adapter and evidence writer for candidate 01."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from decimal import Decimal
import json
from pathlib import Path
from typing import Any, Mapping

import pandas as pd

from core import AuctionBar, AuctionStateMachine, CandidateConfig, Side, TradePlan


@dataclass(frozen=True, slots=True)
class ExecutionConfig:
    starting_nav: float = 100_000.0
    risk_fraction: float = 0.01
    all_in_cost_bps_per_side: float = 7.0
    minimum_net_reward_risk: float = 1.35
    venue_max_leverage: float = 125.0
    price_precision: int = 1
    quantity_precision: int = 3
    price_increment: float = 0.1
    quantity_increment: float = 0.001

    def __post_init__(self) -> None:
        if self.starting_nav <= 0:
            raise ValueError("starting_nav must be positive")
        if not 0.0 < self.risk_fraction < 1.0:
            raise ValueError("risk_fraction must be between zero and one")
        if self.all_in_cost_bps_per_side < 0:
            raise ValueError("all_in_cost_bps_per_side cannot be negative")
        if self.minimum_net_reward_risk <= 1.0:
            raise ValueError("minimum_net_reward_risk must exceed one")
        if self.venue_max_leverage <= 0:
            raise ValueError("venue_max_leverage must be positive")

    @classmethod
    def from_mapping(cls, values: Mapping[str, Any]) -> "ExecutionConfig":
        known = {field.name for field in cls.__dataclass_fields__.values()}
        unknown = sorted(set(values) - known)
        if unknown:
            raise ValueError(f"unknown execution config keys: {unknown}")
        return cls(**dict(values))


class GlobalEntryGate:
    """One pending new entry or open position across all candidate instruments."""

    def __init__(self) -> None:
        self.owner: str | None = None

    def acquire(self, owner: str) -> bool:
        if self.owner is None:
            self.owner = owner
            return True
        return self.owner == owner

    def release(self, owner: str) -> None:
        if self.owner == owner:
            self.owner = None


@dataclass(slots=True)
class RunEvidence:
    metrics: dict[str, Any]
    daily_nav: list[dict[str, Any]]
    submissions: list[dict[str, Any]]
    execution_events: list[dict[str, Any]]
    scenario_events: list[dict[str, Any]]
    fills: pd.DataFrame
    positions: pd.DataFrame
    account: pd.DataFrame


def _as_float(value: Any) -> float:
    if value is None:
        raise ValueError("cannot convert None to float")
    for name in ("as_double", "as_f64"):
        method = getattr(value, name, None)
        if callable(method):
            return float(method())
    as_decimal = getattr(value, "as_decimal", None)
    if callable(as_decimal):
        return float(as_decimal())
    try:
        return float(value)
    except (TypeError, ValueError):
        text = str(value).strip()
        return float(text.split()[0].replace(",", ""))


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return str(value)


def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(_json_safe(dict(payload)), indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as stream:
        for row in rows:
            stream.write(json.dumps(_json_safe(row), sort_keys=True, ensure_ascii=False))
            stream.write("\n")
    temporary.replace(path)


def _utc_date(ts_ns: int) -> str:
    return datetime.fromtimestamp(ts_ns / 1_000_000_000, tz=timezone.utc).date().isoformat()


def _money_from_equity_map(equity: Mapping[Any, Any], currency: Any) -> float:
    if currency in equity:
        return _as_float(equity[currency])
    for key, value in equity.items():
        if str(key) == str(currency):
            return _as_float(value)
    raise RuntimeError(f"equity map has no {currency}: {equity}")


def _numeric_series(frame: pd.DataFrame, hints: tuple[str, ...]) -> pd.Series | None:
    for column in frame.columns:
        normalized = str(column).lower().replace(" ", "_")
        if all(hint in normalized for hint in hints):
            raw = frame[column]
            if pd.api.types.is_numeric_dtype(raw):
                return pd.to_numeric(raw, errors="coerce")
            extracted = raw.astype(str).str.extract(r"([-+]?\d+(?:\.\d+)?)", expand=False)
            return pd.to_numeric(extracted, errors="coerce")
    return None


def _liquidation_marker_rows(*frames: pd.DataFrame) -> int:
    """Count report rows explicitly marked as liquidation-related."""

    total = 0
    for frame in frames:
        if frame.empty:
            continue
        row_text = frame.astype(str).agg(" ".join, axis=1).str.upper()
        total += int(row_text.str.contains("LIQUIDAT", regex=False).sum())
    return total


def _build_metrics(
    *,
    label: str,
    start: datetime,
    end: datetime,
    execution: ExecutionConfig,
    strategy: Any,
    fills: pd.DataFrame,
    positions: pd.DataFrame,
) -> dict[str, Any]:
    daily_nav = strategy.daily_nav
    start_nav = float(strategy.start_nav or execution.starting_nav)
    final_nav = float(strategy.final_nav or start_nav)
    calendar_days = max((end - start).total_seconds() / 86_400.0, 1.0 / 1440.0)
    total_return = final_nav / start_nav - 1.0
    geo_daily = (final_nav / start_nav) ** (1.0 / calendar_days) - 1.0 if final_nav > 0 else -1.0

    day_returns: list[float] = []
    prior = start_nav
    for item in daily_nav:
        value = float(item["nav"])
        if prior > 0:
            day_returns.append(value / prior - 1.0)
        prior = value
    positive_days = sum(value > 1e-12 for value in day_returns)
    negative_days = sum(value < -1e-12 for value in day_returns)
    flat_days = max(int(round(calendar_days)) - positive_days - negative_days, 0)

    realized = _numeric_series(positions, ("realized", "pnl"))
    if realized is None:
        realized = _numeric_series(positions, ("pnl",))
    win_rate: float | None = None
    profit_factor: float | None = None
    if realized is not None and realized.notna().any():
        clean = realized.dropna()
        if len(clean):
            win_rate = float((clean > 0).mean())
            gross_profit = float(clean[clean > 0].sum())
            gross_loss = abs(float(clean[clean < 0].sum()))
            profit_factor = gross_profit / gross_loss if gross_loss > 0 else None

    response_counts: dict[str, int] = {}
    side_counts: dict[str, int] = {}
    for submission in strategy.submissions:
        response = str(submission.get("response"))
        side = str(submission.get("side"))
        response_counts[response] = response_counts.get(response, 0) + 1
        side_counts[side] = side_counts.get(side, 0) + 1

    leverages = [float(row["effective_leverage"]) for row in strategy.submissions]
    net_rrs = [float(row["net_reward_risk_at_submission"]) for row in strategy.submissions]
    liquidation_marker_rows = _liquidation_marker_rows(fills, positions)
    scenario_counts: dict[str, int] = {}
    for event in strategy.core.transitions:
        scenario_counts[event.event_type] = scenario_counts.get(event.event_type, 0) + 1

    return {
        "label": label,
        "evaluation_start_utc": start.isoformat(),
        "evaluation_end_utc": end.isoformat(),
        "calendar_days": calendar_days,
        "start_nav": start_nav,
        "final_nav": final_nav,
        "total_return": total_return,
        "geometric_mean_daily_return": geo_daily,
        "target_geometric_mean_daily_return": 0.01,
        "target_met": geo_daily >= 0.01,
        "max_drawdown": float(strategy.max_drawdown),
        "positive_days": positive_days,
        "negative_days": negative_days,
        "flat_days": flat_days,
        "daily_returns": day_returns,
        "orders_filled": int(len(fills.index)),
        "closed_positions": int(len(positions.index)),
        "submissions": len(strategy.submissions),
        "trades_per_calendar_day": len(strategy.submissions) / calendar_days,
        "win_rate": win_rate,
        "profit_factor": profit_factor,
        "response_counts": response_counts,
        "side_counts": side_counts,
        "scenario_event_counts": scenario_counts,
        "max_effective_leverage": max(leverages, default=0.0),
        "median_effective_leverage": float(pd.Series(leverages).median()) if leverages else 0.0,
        "minimum_net_reward_risk_at_submission": min(net_rrs, default=None),
        "risk_fraction": execution.risk_fraction,
        "all_in_cost_bps_per_side": execution.all_in_cost_bps_per_side,
        "one_global_entry_gate_violations": strategy.gate_violations,
        "ended_flat": bool(strategy.ended_flat),
        "rejected_after_one_bar_delay": strategy.delayed_plan_rejections,
        "max_hold_exits": strategy.max_hold_exits,
        "protective_order_failures": strategy.protective_order_failures,
        "minimum_equity_to_maintenance_margin": strategy.minimum_equity_to_maintenance_margin,
        "venue_liquidation_enabled": True,
        "venue_max_leverage": execution.venue_max_leverage,
        "liquidation_marker_rows": liquidation_marker_rows,
    }


def run_nautilus_backtest(
    *,
    label: str,
    frame: pd.DataFrame,
    evaluation_start: datetime,
    evaluation_end: datetime,
    candidate: CandidateConfig,
    execution: ExecutionConfig,
    output_dir: Path,
) -> RunEvidence:
    """Run the candidate through NautilusTrader's real order/accounting path."""

    from nautilus_trader.backtest.engine import BacktestEngine
    from nautilus_trader.config import BacktestEngineConfig, LoggingConfig, StrategyConfig
    from nautilus_trader.model.data import Bar, BarType
    from nautilus_trader.model.instruments import CryptoPerpetual
    from nautilus_trader.model.enums import AccountType, OmsType, OrderSide, TimeInForce
    from nautilus_trader.model.events import PositionClosed, PositionOpened
    from nautilus_trader.model.identifiers import InstrumentId, Symbol, Venue
    from nautilus_trader.model.objects import Currency, Money, Price, Quantity
    from nautilus_trader.persistence.wranglers import BarDataWrangler
    from nautilus_trader.trading.strategy import Strategy

    class CandidateStrategyConfig(StrategyConfig, frozen=True):
        instrument_id: InstrumentId
        bar_type: BarType
        risk_fraction: Decimal
        cost_fraction_per_side: Decimal
        minimum_net_reward_risk: Decimal
        evaluation_start_ns: int
        evaluation_end_ns: int
        max_hold_bars: int

    class CandidateStrategy(Strategy):
        def __init__(
            self,
            config: CandidateStrategyConfig,
            *,
            state_machine: AuctionStateMachine,
            flow_by_ts: dict[int, AuctionBar],
            gate: GlobalEntryGate,
            instrument: CryptoPerpetual,
        ) -> None:
            super().__init__(config)
            self.core = state_machine
            self.flow_by_ts = flow_by_ts
            self.gate = gate
            self.instrument = instrument
            self.pending_plan: TradePlan | None = None
            self.active_owner: str | None = None
            self.position_open_bar: int | None = None
            self.bar_count = 0
            self.submissions: list[dict[str, Any]] = []
            self.execution_events: list[dict[str, Any]] = []
            self.daily_nav: list[dict[str, Any]] = []
            self.start_nav: float | None = None
            self.final_nav: float | None = None
            self._current_day: str | None = None
            self._last_day_equity: float | None = None
            self._high_water: float | None = None
            self.max_drawdown = 0.0
            self.gate_violations = 0
            self.delayed_plan_rejections = 0
            self.max_hold_exits = 0
            self.protective_order_failures = 0
            self.minimum_equity_to_maintenance_margin: float | None = None
            self.ended_flat = True
            self._last_ts_ns = 0

        def on_start(self) -> None:
            self.subscribe_bars(self.config.bar_type)

        def _equity(self) -> float:
            equity = self.portfolio.equity(self.config.instrument_id.venue)
            return _money_from_equity_map(equity, Currency.from_str("USDT"))

        def _mark_nav(self, ts_ns: int) -> None:
            if ts_ns < self.config.evaluation_start_ns:
                return
            equity = self._equity()
            if self.start_nav is None:
                self.start_nav = equity
                self._high_water = equity
            day = _utc_date(ts_ns)
            if self._current_day is None:
                self._current_day = day
            elif day != self._current_day:
                assert self._last_day_equity is not None
                self.daily_nav.append({"date": self._current_day, "nav": self._last_day_equity})
                self._current_day = day
            self._last_day_equity = equity
            self.final_nav = equity
            if self._high_water is None or equity > self._high_water:
                self._high_water = equity
            if self._high_water > 0:
                drawdown = equity / self._high_water - 1.0
                self.max_drawdown = min(self.max_drawdown, drawdown)
            margins = self.portfolio.margins_maint(self.config.instrument_id.venue) or {}
            try:
                maintenance = _money_from_equity_map(margins, Currency.from_str("USDT"))
            except RuntimeError:
                maintenance = 0.0
            if maintenance > 0.0:
                ratio = equity / maintenance
                if self.minimum_equity_to_maintenance_margin is None:
                    self.minimum_equity_to_maintenance_margin = ratio
                else:
                    self.minimum_equity_to_maintenance_margin = min(
                        self.minimum_equity_to_maintenance_margin,
                        ratio,
                    )

        def finalize_nav(self) -> None:
            if self._last_ts_ns >= self.config.evaluation_start_ns:
                self._mark_nav(self._last_ts_ns)
            if self._current_day is not None and self._last_day_equity is not None:
                if not self.daily_nav or self.daily_nav[-1]["date"] != self._current_day:
                    self.daily_nav.append({"date": self._current_day, "nav": self._last_day_equity})
            self.ended_flat = self.portfolio.is_flat(self.config.instrument_id)

        def _record(self, event_type: str, ts_ns: int, **details: Any) -> None:
            self.execution_events.append(
                {
                    "event_type": event_type,
                    "observed_time_ns": ts_ns,
                    "details": _json_safe(details),
                },
            )

        def _release_gate(self) -> None:
            if self.active_owner is not None:
                self.gate.release(self.active_owner)
                self.active_owner = None

        def _submit_pending(self, bar: Bar) -> None:
            plan = self.pending_plan
            if plan is None:
                return
            self.pending_plan = None
            ts_ns = int(bar.ts_event)
            if (
                ts_ns < self.config.evaluation_start_ns
                or ts_ns >= self.config.evaluation_end_ns
                or plan.observed_time_ns < self.config.evaluation_start_ns
            ):
                self.delayed_plan_rejections += 1
                return
            if not self.portfolio.is_flat(self.config.instrument_id):
                self.delayed_plan_rejections += 1
                return
            if not self.gate.acquire(plan.scenario_id):
                self.gate_violations += 1
                self.delayed_plan_rejections += 1
                return

            entry = _as_float(bar.close)
            stop = plan.stop_price
            target = plan.target_price
            if plan.side is Side.LONG and not stop < entry < target:
                self.gate.release(plan.scenario_id)
                self.delayed_plan_rejections += 1
                return
            if plan.side is Side.SHORT and not target < entry < stop:
                self.gate.release(plan.scenario_id)
                self.delayed_plan_rejections += 1
                return

            equity = self._equity()
            cost = float(self.config.cost_fraction_per_side)
            planned_loss_per_unit = abs(entry - stop) + entry * cost + stop * cost
            planned_gain_per_unit = abs(target - entry) - entry * cost - target * cost
            if planned_loss_per_unit <= 0.0 or planned_gain_per_unit <= 0.0:
                self.gate.release(plan.scenario_id)
                self.delayed_plan_rejections += 1
                return
            net_rr = planned_gain_per_unit / planned_loss_per_unit
            if net_rr < float(self.config.minimum_net_reward_risk):
                self.gate.release(plan.scenario_id)
                self.delayed_plan_rejections += 1
                return

            risk_budget = equity * float(self.config.risk_fraction)
            raw_quantity = risk_budget / planned_loss_per_unit
            quantity = self.instrument.make_qty(raw_quantity, round_down=True)
            quantity_value = _as_float(quantity)
            if quantity_value <= 0.0:
                self.gate.release(plan.scenario_id)
                self.delayed_plan_rejections += 1
                return

            side = OrderSide.BUY if plan.side is Side.LONG else OrderSide.SELL
            order_list = self.order_factory.bracket(
                instrument_id=self.config.instrument_id,
                order_side=side,
                quantity=quantity,
                time_in_force=TimeInForce.GTC,
                tp_price=self.instrument.make_price(target),
                sl_trigger_price=self.instrument.make_price(stop),
            )
            self.active_owner = plan.scenario_id
            self.submit_order_list(order_list)
            effective_leverage = quantity_value * entry / equity
            submission = {
                **asdict(plan),
                "side": plan.side.value,
                "response": plan.response.value,
                "submission_time_ns": ts_ns,
                "entry_reference": entry,
                "quantity": quantity_value,
                "equity": equity,
                "risk_budget": risk_budget,
                "planned_loss_per_unit_after_cost": planned_loss_per_unit,
                "planned_gain_per_unit_after_cost": planned_gain_per_unit,
                "net_reward_risk_at_submission": net_rr,
                "effective_leverage": effective_leverage,
                "one_bar_execution_delay": True,
            }
            self.submissions.append(submission)
            self._record("BRACKET_SUBMITTED", ts_ns, **submission)

        def _time_exit_if_needed(self, bar: Bar) -> None:
            if self.position_open_bar is None:
                return
            if self.bar_count - self.position_open_bar < self.config.max_hold_bars:
                return
            if self.portfolio.is_flat(self.config.instrument_id):
                self.position_open_bar = None
                return
            self.cancel_all_orders(self.config.instrument_id)
            self.close_all_positions(self.config.instrument_id, reduce_only=True)
            self.max_hold_exits += 1
            self._record("MAX_HOLD_EXIT_SUBMITTED", int(bar.ts_event))
            self.position_open_bar = None

        def on_bar(self, bar: Bar) -> None:
            self.bar_count += 1
            ts_ns = int(bar.ts_event)
            self._last_ts_ns = ts_ns
            self._mark_nav(ts_ns)
            self._time_exit_if_needed(bar)
            self._submit_pending(bar)

            aux = self.flow_by_ts.get(ts_ns)
            if aux is None:
                raise RuntimeError(f"missing causal auxiliary kline data for {ts_ns}")
            plan = self.core.on_bar(aux)
            if plan is not None and ts_ns >= self.config.evaluation_start_ns:
                self.pending_plan = plan

            if ts_ns >= self.config.evaluation_end_ns - 60_000_000_000:
                self.pending_plan = None
                if not self.portfolio.is_flat(self.config.instrument_id):
                    self.cancel_all_orders(self.config.instrument_id)
                    self.close_all_positions(self.config.instrument_id, reduce_only=True)
                    self._record("EVALUATION_END_EXIT_SUBMITTED", ts_ns)

        def on_position_opened(self, event: PositionOpened) -> None:
            self.position_open_bar = self.bar_count
            self._record("POSITION_OPENED", int(event.ts_event), event=str(event))

        def on_position_closed(self, event: PositionClosed) -> None:
            self.position_open_bar = None
            self.cancel_all_orders(self.config.instrument_id)
            self._record("POSITION_CLOSED", int(event.ts_event), event=str(event))
            self._release_gate()
            self._mark_nav(int(event.ts_event))

        def _handle_order_failure(self, event_type: str, event: Any) -> None:
            ts_ns = int(event.ts_event)
            self._record(event_type, ts_ns, event=str(event))
            if self.portfolio.is_flat(self.config.instrument_id):
                self._release_gate()
                return
            # Never free the global gate while an unprotected position remains.
            # A denied/rejected child order is treated as a protective-order fault
            # and the position is flattened through the normal execution path.
            self.protective_order_failures += 1
            self.cancel_all_orders(self.config.instrument_id)
            self.close_all_positions(self.config.instrument_id, reduce_only=True)
            self._record("PROTECTIVE_ORDER_FAILURE_EXIT_SUBMITTED", ts_ns)

        def on_order_denied(self, event: Any) -> None:
            self._handle_order_failure("ORDER_DENIED", event)

        def on_order_rejected(self, event: Any) -> None:
            self._handle_order_failure("ORDER_REJECTED", event)

        def on_stop(self) -> None:
            self.pending_plan = None
            if not self.portfolio.is_flat(self.config.instrument_id):
                self.cancel_all_orders(self.config.instrument_id)
                self.close_all_positions(self.config.instrument_id, reduce_only=True)
            self._release_gate()

    venue = Venue("BINANCE")
    usdt = Currency.from_str("USDT")
    btc = Currency.from_str("BTC")
    cost_fraction = Decimal(str(execution.all_in_cost_bps_per_side / 10_000.0))
    instrument = CryptoPerpetual(
        instrument_id=InstrumentId(Symbol("BTCUSDT-PERP"), venue),
        raw_symbol=Symbol("BTCUSDT"),
        base_currency=btc,
        quote_currency=usdt,
        settlement_currency=usdt,
        is_inverse=False,
        price_precision=execution.price_precision,
        size_precision=execution.quantity_precision,
        price_increment=Price.from_str(f"{execution.price_increment:.{execution.price_precision}f}"),
        size_increment=Quantity.from_str(f"{execution.quantity_increment:.{execution.quantity_precision}f}"),
        ts_event=0,
        ts_init=0,
        min_quantity=Quantity.from_str(f"{execution.quantity_increment:.{execution.quantity_precision}f}"),
        min_notional=Money(10.0, usdt),
        max_price=Price.from_str(f"{10_000_000:.{execution.price_precision}f}"),
        min_price=Price.from_str(f"{execution.price_increment:.{execution.price_precision}f}"),
        margin_init=Decimal(1) / Decimal(str(execution.venue_max_leverage)),
        margin_maint=(Decimal(1) / Decimal(str(execution.venue_max_leverage))) / Decimal(2),
        maker_fee=cost_fraction,
        taker_fee=cost_fraction,
        info={
            "research_cost_model": "all-in fee/slippage/funding stress charged as commission",
            "all_in_cost_bps_per_side": execution.all_in_cost_bps_per_side,
        },
    )
    bar_type = BarType.from_str("BTCUSDT-PERP.BINANCE-1-MINUTE-LAST-EXTERNAL")

    # Build public model objects directly. pandas 3 copy-on-write exposes
    # read-only ``DataFrame.values`` while NautilusTrader v1.230.0's legacy
    # Cython wrangler requests a writable memoryview. Direct construction is
    # equivalent to that wrangler and keeps the pinned engine path deterministic.
    bars: list[Bar] = []
    append_bar = bars.append
    for row in frame.itertuples(index=False):
        ts_event = int(pd.Timestamp(row.close_dt).value)
        append_bar(
            Bar(
                bar_type=bar_type,
                open=Price(float(row.open), instrument.price_precision),
                high=Price(float(row.high), instrument.price_precision),
                low=Price(float(row.low), instrument.price_precision),
                close=Price(float(row.close), instrument.price_precision),
                volume=Quantity(float(row.base_volume), instrument.size_precision),
                ts_event=ts_event,
                ts_init=ts_event,
            )
        )
    aux_bars = {
        int(pd.Timestamp(row.close_dt).value): AuctionBar(
            ts_event_ns=int(pd.Timestamp(row.close_dt).value),
            open=float(row.open),
            high=float(row.high),
            low=float(row.low),
            close=float(row.close),
            base_volume=float(row.base_volume),
            quote_volume=float(row.quote_volume),
            taker_buy_quote_volume=float(row.taker_buy_quote_volume),
        )
        for row in frame.itertuples(index=False)
    }

    engine = BacktestEngine(
        config=BacktestEngineConfig(logging=LoggingConfig(log_level="ERROR")),
    )
    state_machine = AuctionStateMachine(candidate, instrument_id=str(instrument.id))
    gate = GlobalEntryGate()
    strategy = CandidateStrategy(
        CandidateStrategyConfig(
            instrument_id=instrument.id,
            bar_type=bar_type,
            risk_fraction=Decimal(str(execution.risk_fraction)),
            cost_fraction_per_side=cost_fraction,
            minimum_net_reward_risk=Decimal(str(execution.minimum_net_reward_risk)),
            evaluation_start_ns=int(pd.Timestamp(evaluation_start).value),
            evaluation_end_ns=int(pd.Timestamp(evaluation_end).value),
            max_hold_bars=candidate.max_hold_bars,
        ),
        state_machine=state_machine,
        flow_by_ts=aux_bars,
        gate=gate,
        instrument=instrument,
    )

    try:
        engine.add_venue(
            venue=venue,
            oms_type=OmsType.NETTING,
            account_type=AccountType.MARGIN,
            starting_balances=[Money(execution.starting_nav, usdt)],
            base_currency=usdt,
            default_leverage=Decimal(str(execution.venue_max_leverage)),
            reject_stop_orders=False,
            bar_adaptive_high_low_ordering=True,
            liquidation_enabled=True,
            liquidation_trigger_ratio=1.0,
            liquidation_cancel_open_orders=True,
        )
        engine.add_instrument(instrument)
        engine.add_data(bars)
        engine.add_strategy(strategy)
        engine.run()
        strategy.finalize_nav()

        fills = engine.trader.generate_order_fills_report()
        positions = engine.trader.generate_positions_report()
        account = engine.trader.generate_account_report(venue)
        metrics = _build_metrics(
            label=label,
            start=evaluation_start,
            end=evaluation_end,
            execution=execution,
            strategy=strategy,
            fills=fills,
            positions=positions,
        )

        output_dir.mkdir(parents=True, exist_ok=True)
        fills.to_csv(output_dir / "orders.csv", index=False)
        positions.to_csv(output_dir / "positions.csv", index=False)
        account.to_csv(output_dir / "account.csv", index=False)
        pd.DataFrame(strategy.submissions).to_csv(output_dir / "trade_plans.csv", index=False)
        pd.DataFrame(strategy.daily_nav).to_csv(output_dir / "daily_nav.csv", index=False)
        _atomic_json(output_dir / "metrics.json", metrics)
        _write_jsonl(
            output_dir / "scenario_events.jsonl",
            [event.to_dict() for event in state_machine.transitions],
        )
        _write_jsonl(output_dir / "execution_events.jsonl", strategy.execution_events)

        return RunEvidence(
            metrics=metrics,
            daily_nav=list(strategy.daily_nav),
            submissions=list(strategy.submissions),
            execution_events=list(strategy.execution_events),
            scenario_events=[event.to_dict() for event in state_machine.transitions],
            fills=fills,
            positions=positions,
            account=account,
        )
    finally:
        engine.dispose()
