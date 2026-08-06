"""Authoritative NautilusTrader execution adapter for causal event-bar plans.

Signal detection and target routing remain candidate logic. Order submission,
bracket management, commissions, margin, positions, account equity, and reports
are owned exclusively by NautilusTrader. This module contains no fill, PnL, NAV,
or stop/target simulator.

The adapter intentionally delays each completed-event signal until the next
completed event before submitting a market bracket. The pinned NautilusTrader
bar execution model then owns the fill and all contingent-order processing.
"""

from __future__ import annotations

from bisect import bisect_right
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from decimal import Decimal
from importlib.metadata import version as package_version
import json
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import pandas as pd

from core import Side
from impact_regime_probe import EventFeature, ScenarioPlan


@dataclass(frozen=True, slots=True)
class NautilusExecutionConfig:
    starting_nav: float
    risk_fraction: float
    all_in_cost_bps_per_side: float
    minimum_net_reward_risk: float
    venue_max_leverage: float
    minimum_price_risk_fraction: float
    price_precision: int
    quantity_precision: int
    price_increment: float
    quantity_increment: float

    def __post_init__(self) -> None:
        if self.starting_nav <= 0.0:
            raise ValueError("starting_nav must be positive")
        if not 0.0 < self.risk_fraction <= 0.03:
            raise ValueError("risk_fraction must be in (0, 0.03]")
        if self.all_in_cost_bps_per_side < 0.0:
            raise ValueError("all_in_cost_bps_per_side cannot be negative")
        if self.minimum_net_reward_risk <= 1.0:
            raise ValueError("minimum_net_reward_risk must exceed one")
        if self.venue_max_leverage <= 0.0:
            raise ValueError("venue_max_leverage must be positive")
        if not 0.0 < self.minimum_price_risk_fraction < 1.0:
            raise ValueError("minimum_price_risk_fraction must be in (0, 1)")
        if self.price_precision < 0 or self.quantity_precision < 0:
            raise ValueError("precisions cannot be negative")
        if self.price_increment <= 0.0 or self.quantity_increment <= 0.0:
            raise ValueError("increments must be positive")

    @classmethod
    def from_mapping(cls, values: Mapping[str, Any]) -> "NautilusExecutionConfig":
        known = {field.name for field in cls.__dataclass_fields__.values()}
        unknown = sorted(set(values) - known)
        if unknown:
            raise ValueError(f"unknown Nautilus execution config keys: {unknown}")
        return cls(**dict(values))


@dataclass(slots=True)
class NautilusRunEvidence:
    metrics: dict[str, Any]
    daily_nav: list[dict[str, Any]]
    submissions: list[dict[str, Any]]
    rejections: list[dict[str, Any]]
    execution_events: list[dict[str, Any]]
    fills: pd.DataFrame
    positions: pd.DataFrame
    account: pd.DataFrame


class GlobalEntryGate:
    """One pending entry or open position across all participating strategies."""

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
    enum_value = getattr(value, "value", None)
    if isinstance(enum_value, (str, int, float, bool)):
        return enum_value
    return str(value)


def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(
            _json_safe(dict(payload)),
            indent=2,
            sort_keys=True,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _write_jsonl(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as stream:
        for row in rows:
            stream.write(
                json.dumps(_json_safe(dict(row)), sort_keys=True, ensure_ascii=False),
            )
            stream.write("\n")
    temporary.replace(path)


def _utc_date(ts_ns: int) -> str:
    # Never round a nanosecond timestamp through binary floating point.  Values
    # such as evaluation_end_ns - 1 can otherwise round to the next UTC day.
    return pd.Timestamp(int(ts_ns), unit="ns", tz="UTC").date().isoformat()


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
            extracted = raw.astype(str).str.extract(
                r"([-+]?\d+(?:\.\d+)?)",
                expand=False,
            )
            return pd.to_numeric(extracted, errors="coerce")
    return None


def _liquidation_marker_rows(*frames: pd.DataFrame) -> int:
    total = 0
    for frame in frames:
        if frame.empty:
            continue
        row_text = frame.apply(
            lambda row: " ".join(str(value) for value in row.tolist()),
            axis=1,
        ).str.upper()
        total += int(row_text.str.contains("LIQUIDAT", regex=False).sum())
    return total


def _build_metrics(
    *,
    label: str,
    evaluation_start: datetime,
    evaluation_end: datetime,
    execution: NautilusExecutionConfig,
    strategy: Any,
    fills: pd.DataFrame,
    positions: pd.DataFrame,
) -> dict[str, Any]:
    start_nav = float(strategy.start_nav or execution.starting_nav)
    final_nav = float(strategy.final_nav or start_nav)
    calendar_days = max(
        (evaluation_end - evaluation_start).total_seconds() / 86_400.0,
        1.0 / 1440.0,
    )
    total_return = final_nav / start_nav - 1.0
    geometric_daily = (
        (final_nav / start_nav) ** (1.0 / calendar_days) - 1.0
        if final_nav > 0.0
        else -1.0
    )

    day_returns: list[float] = []
    prior = start_nav
    for row in strategy.daily_nav:
        value = float(row["nav"])
        if prior > 0.0:
            day_returns.append(value / prior - 1.0)
        prior = value

    realized = _numeric_series(positions, ("realized", "pnl"))
    if realized is None:
        realized = _numeric_series(positions, ("pnl",))
    win_rate: float | None = None
    profit_factor: float | None = None
    if realized is not None:
        clean = realized.dropna()
        if len(clean):
            win_rate = float((clean > 0.0).mean())
            gross_profit = float(clean[clean > 0.0].sum())
            gross_loss = abs(float(clean[clean < 0.0].sum()))
            profit_factor = gross_profit / gross_loss if gross_loss > 0.0 else None

    leverage = [
        float(row["effective_leverage_at_submission"])
        for row in strategy.submissions
    ]
    net_rr = [
        float(row["net_reward_risk_at_submission"])
        for row in strategy.submissions
    ]
    rejection_counts: dict[str, int] = {}
    for row in strategy.rejections:
        reason = str(row["reason"])
        rejection_counts[reason] = rejection_counts.get(reason, 0) + 1

    return {
        "label": label,
        "evaluation_start_utc": evaluation_start.isoformat(),
        "evaluation_end_utc": evaluation_end.isoformat(),
        "calendar_days": calendar_days,
        "start_nav": start_nav,
        "final_nav": final_nav,
        "total_return": total_return,
        "geometric_mean_daily_return": geometric_daily,
        "target_geometric_mean_daily_return": 0.01,
        "target_met": geometric_daily >= 0.01,
        "max_drawdown": float(strategy.max_drawdown),
        "daily_returns": day_returns,
        "orders_filled": int(len(fills.index)),
        "closed_positions": int(len(positions.index)),
        "submissions": len(strategy.submissions),
        "trades_per_calendar_day": len(positions.index) / calendar_days,
        "win_rate": win_rate,
        "profit_factor": profit_factor,
        "max_effective_leverage_at_submission": max(leverage, default=0.0),
        "median_effective_leverage_at_submission": (
            float(pd.Series(leverage).median()) if leverage else 0.0
        ),
        "minimum_net_reward_risk_at_submission": min(net_rr, default=None),
        "risk_fraction": execution.risk_fraction,
        "all_in_cost_bps_per_side": execution.all_in_cost_bps_per_side,
        "rejection_counts": rejection_counts,
        "one_global_entry_gate_violations": strategy.gate_violations,
        "protective_order_failures": strategy.protective_order_failures,
        "maximum_hold_exits": strategy.maximum_hold_exits,
        "ended_flat": bool(strategy.ended_flat),
        "minimum_equity_to_maintenance_margin": (
            strategy.minimum_equity_to_maintenance_margin
        ),
        "liquidation_marker_rows": _liquidation_marker_rows(fills, positions),
        "execution_engine": "NautilusTrader",
        "nautilus_trader_version": package_version("nautilus_trader"),
        "custom_fill_simulator": False,
        "custom_pnl_or_nav_ledger": False,
        "market_data_for_execution": (
            "official Binance Vision USD-M one-minute external bars"
        ),
        "entry_semantics": (
            "equal-notional signal mapped to the first strictly later "
            "completed one-minute bar; market bracket submitted there; "
            "NautilusTrader owns fills and contingent orders"
        ),
        "bar_adaptive_high_low_ordering": True,
    }


def run_nautilus_plan_backtest(
    *,
    label: str,
    features: Sequence[EventFeature],
    execution_frame: pd.DataFrame,
    plans: Sequence[ScenarioPlan],
    evaluation_start: datetime,
    evaluation_end: datetime,
    execution: NautilusExecutionConfig,
    maximum_hold_ns: int,
    output_dir: Path,
) -> NautilusRunEvidence:
    """Execute routed plans through the pinned NautilusTrader engine only."""

    if evaluation_end <= evaluation_start:
        raise ValueError("evaluation_end must be after evaluation_start")
    if maximum_hold_ns <= 0:
        raise ValueError("maximum_hold_ns must be positive")
    if not features:
        raise ValueError("features cannot be empty")
    required_execution_columns = {
        "close_dt", "open", "high", "low", "close", "base_volume"
    }
    missing_execution_columns = sorted(
        required_execution_columns - set(execution_frame.columns)
    )
    if missing_execution_columns:
        raise ValueError(
            f"execution_frame missing columns: {missing_execution_columns}"
        )
    if execution_frame.empty:
        raise ValueError("execution_frame cannot be empty")

    from nautilus_trader.backtest.engine import BacktestEngine
    from nautilus_trader.config import (
        BacktestEngineConfig,
        LoggingConfig,
        StrategyConfig,
    )
    from nautilus_trader.model.data import Bar, BarType
    from nautilus_trader.model.enums import (
        AccountType,
        OmsType,
        OrderSide,
        TimeInForce,
    )
    from nautilus_trader.model.events import PositionClosed, PositionOpened
    from nautilus_trader.model.identifiers import InstrumentId, Symbol, Venue
    from nautilus_trader.model.instruments import CryptoPerpetual
    from nautilus_trader.model.objects import Currency, Money, Price, Quantity
    from nautilus_trader.trading.strategy import Strategy

    start_ns = int(pd.Timestamp(evaluation_start).as_unit("ns").value)
    end_ns = int(pd.Timestamp(evaluation_end).as_unit("ns").value)
    ordered_execution = (
        execution_frame.copy()
        .sort_values("close_dt", kind="stable")
        .drop_duplicates("close_dt", keep="last")
        .reset_index(drop=True)
    )
    execution_bar_times = [
        int(pd.Timestamp(value).as_unit("ns").value)
        for value in ordered_execution["close_dt"]
    ]
    evaluation_bar_times = [
        value for value in execution_bar_times if start_ns <= value < end_ns
    ]
    if not evaluation_bar_times:
        raise ValueError("no completed one-minute execution bars in interval")
    force_exit_ts_ns = max(evaluation_bar_times)

    plans_by_signal_time: dict[int, list[ScenarioPlan]] = {}
    for plan in sorted(
        plans,
        key=lambda item: (item.signal_time_ns, item.scenario_id),
    ):
        if not start_ns <= plan.signal_time_ns < end_ns:
            continue
        activation_index = bisect_right(
            execution_bar_times,
            int(plan.signal_time_ns),
        )
        if activation_index >= len(execution_bar_times):
            continue
        activation_time_ns = execution_bar_times[activation_index]
        if not start_ns <= activation_time_ns < end_ns:
            continue
        plans_by_signal_time.setdefault(activation_time_ns, []).append(plan)

    class PlanStrategyConfig(StrategyConfig, frozen=True):
        instrument_id: InstrumentId
        bar_type: BarType
        risk_fraction: Decimal
        cost_fraction_per_side: Decimal
        minimum_net_reward_risk: Decimal
        minimum_price_risk_fraction: Decimal
        evaluation_start_ns: int
        evaluation_end_ns: int
        force_exit_ts_ns: int
        maximum_hold_ns: int

    class PlanStrategy(Strategy):
        def __init__(
            self,
            config: PlanStrategyConfig,
            *,
            schedule: Mapping[int, Sequence[ScenarioPlan]],
            gate: GlobalEntryGate,
            instrument: CryptoPerpetual,
        ) -> None:
            super().__init__(config)
            self.schedule = schedule
            self.gate = gate
            self.instrument = instrument
            self.pending: list[ScenarioPlan] = []
            self.active_owner: str | None = None
            self.position_opened_ns: int | None = None
            self.submissions: list[dict[str, Any]] = []
            self.rejections: list[dict[str, Any]] = []
            self.execution_events: list[dict[str, Any]] = []
            self.daily_nav: list[dict[str, Any]] = []
            self.start_nav: float | None = None
            self.final_nav: float | None = None
            self._current_day: str | None = None
            self._last_day_equity: float | None = None
            self._high_water: float | None = None
            self._last_ts_ns = 0
            self.max_drawdown = 0.0
            self.gate_violations = 0
            self.protective_order_failures = 0
            self.maximum_hold_exits = 0
            self.minimum_equity_to_maintenance_margin: float | None = None
            self.ended_flat = True

        def on_start(self) -> None:
            self.subscribe_bars(self.config.bar_type)

        def _record(self, event_type: str, ts_ns: int, **details: Any) -> None:
            self.execution_events.append(
                {
                    "event_type": event_type,
                    "observed_time_ns": ts_ns,
                    "details": _json_safe(details),
                },
            )

        def _reject(
            self,
            plan: ScenarioPlan,
            *,
            ts_ns: int,
            reason: str,
            **details: Any,
        ) -> None:
            row = {
                "scenario_id": plan.scenario_id,
                "signal_time_ns": plan.signal_time_ns,
                "entry_evaluation_time_ns": ts_ns,
                "side": plan.side.value,
                "response": plan.response,
                "reason": reason,
                **details,
            }
            self.rejections.append(row)
            self._record("PLAN_REJECTED", ts_ns, **row)

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
                self.daily_nav.append(
                    {"date": self._current_day, "nav": self._last_day_equity},
                )
                self._current_day = day
            self._last_day_equity = equity
            self.final_nav = equity
            if self._high_water is None or equity > self._high_water:
                self._high_water = equity
            if self._high_water > 0.0:
                self.max_drawdown = min(
                    self.max_drawdown,
                    equity / self._high_water - 1.0,
                )
            margins = (
                self.portfolio.margins_maint(self.config.instrument_id.venue)
                or {}
            )
            try:
                maintenance = _money_from_equity_map(
                    margins,
                    Currency.from_str("USDT"),
                )
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

        def _release_gate(self) -> None:
            if self.active_owner is not None:
                self.gate.release(self.active_owner)
                self.active_owner = None

        def _flatten(self, *, ts_ns: int, reason: str) -> None:
            self.pending = []
            if self.portfolio.is_flat(self.config.instrument_id):
                self._release_gate()
                return
            self.cancel_all_orders(self.config.instrument_id)
            self.close_all_positions(
                self.config.instrument_id,
                reduce_only=True,
            )
            self._record(reason, ts_ns)

        def _time_exit_if_needed(self, ts_ns: int) -> None:
            if self.position_opened_ns is None:
                return
            if ts_ns - self.position_opened_ns < self.config.maximum_hold_ns:
                return
            self.maximum_hold_exits += 1
            self._flatten(ts_ns=ts_ns, reason="MAXIMUM_HOLD_EXIT_SUBMITTED")
            self.position_opened_ns = None

        def _viable(
            self,
            plan: ScenarioPlan,
            *,
            entry: float,
            ts_ns: int,
        ) -> tuple[float, float, float, float, float] | None:
            stop = _as_float(self.instrument.make_price(plan.stop_price))
            target = _as_float(self.instrument.make_price(plan.target_price))
            hold_ok = (
                entry >= plan.confirmation_hold_price
                if plan.side is Side.LONG
                else entry <= plan.confirmation_hold_price
            )
            if not hold_ok:
                self._reject(
                    plan,
                    ts_ns=ts_ns,
                    reason="FAILED_CONFIRMATION_HOLD",
                    entry=entry,
                    stop=stop,
                    target=target,
                )
                return None
            geometry_ok = (
                stop < entry < target
                if plan.side is Side.LONG
                else target < entry < stop
            )
            if not geometry_ok:
                self._reject(
                    plan,
                    ts_ns=ts_ns,
                    reason="INVALID_DELAYED_GEOMETRY",
                    entry=entry,
                    stop=stop,
                    target=target,
                )
                return None

            cost = float(self.config.cost_fraction_per_side)
            price_risk = abs(entry - stop)
            planned_loss = price_risk + entry * cost + stop * cost
            planned_gain = abs(target - entry) - entry * cost - target * cost
            price_fraction = (
                price_risk / planned_loss if planned_loss > 0.0 else 0.0
            )
            net_rr = planned_gain / planned_loss if planned_loss > 0.0 else -1.0
            if price_fraction < float(self.config.minimum_price_risk_fraction):
                self._reject(
                    plan,
                    ts_ns=ts_ns,
                    reason="COST_DOMINATED",
                    entry=entry,
                    stop=stop,
                    target=target,
                    price_risk_fraction=price_fraction,
                    net_reward_risk=net_rr,
                )
                return None
            if (
                planned_gain <= 0.0
                or net_rr < float(self.config.minimum_net_reward_risk)
            ):
                self._reject(
                    plan,
                    ts_ns=ts_ns,
                    reason="INSUFFICIENT_NET_REWARD_RISK",
                    entry=entry,
                    stop=stop,
                    target=target,
                    price_risk_fraction=price_fraction,
                    net_reward_risk=net_rr,
                )
                return None
            return stop, target, planned_loss, price_fraction, net_rr

        def _submit_pending(self, bar: Bar) -> None:
            pending = self.pending
            self.pending = []
            if not pending:
                return
            ts_ns = int(bar.ts_event)
            if not self.portfolio.is_flat(self.config.instrument_id):
                for plan in pending:
                    self._reject(
                        plan,
                        ts_ns=ts_ns,
                        reason="GLOBAL_POSITION_OCCUPIED",
                    )
                return
            if self.gate.owner is not None:
                self.gate_violations += 1
                for plan in pending:
                    self._reject(
                        plan,
                        ts_ns=ts_ns,
                        reason="GLOBAL_ENTRY_GATE_OCCUPIED",
                    )
                return

            entry = _as_float(bar.close)
            viable: list[
                tuple[float, ScenarioPlan, float, float, float, float]
            ] = []
            for plan in pending:
                geometry = self._viable(plan, entry=entry, ts_ns=ts_ns)
                if geometry is None:
                    continue
                stop, target, planned_loss, price_fraction, net_rr = geometry
                viable.append(
                    (
                        net_rr,
                        plan,
                        stop,
                        target,
                        planned_loss,
                        price_fraction,
                    ),
                )
            if not viable:
                return

            ordered = sorted(
                viable,
                key=lambda row: (-row[0], row[1].scenario_id),
            )
            (
                net_rr,
                plan,
                stop,
                target,
                planned_loss,
                price_fraction,
            ) = ordered[0]
            for _, competing, *_ in ordered[1:]:
                self._reject(
                    competing,
                    ts_ns=ts_ns,
                    reason="LOWER_NET_RR_COMPETING_PLAN",
                )

            if not self.gate.acquire(plan.scenario_id):
                self.gate_violations += 1
                self._reject(
                    plan,
                    ts_ns=ts_ns,
                    reason="GLOBAL_ENTRY_GATE_ACQUIRE_FAILED",
                )
                return

            equity = self._equity()
            risk_budget = equity * float(self.config.risk_fraction)
            raw_quantity = risk_budget / planned_loss
            quantity = self.instrument.make_qty(raw_quantity, round_down=True)
            quantity_value = _as_float(quantity)
            if quantity_value <= 0.0:
                self.gate.release(plan.scenario_id)
                self._reject(
                    plan,
                    ts_ns=ts_ns,
                    reason="ZERO_QUANTITY_AFTER_PRECISION",
                )
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
                "response": plan.response,
                "submission_time_ns": ts_ns,
                "entry_reference": entry,
                "rounded_stop_price": stop,
                "rounded_target_price": target,
                "quantity": quantity_value,
                "engine_equity_at_submission": equity,
                "risk_budget": risk_budget,
                "planned_loss_per_unit_after_cost": planned_loss,
                "price_risk_fraction": price_fraction,
                "net_reward_risk_at_submission": net_rr,
                "effective_leverage_at_submission": effective_leverage,
                "one_completed_event_delay": True,
            }
            self.submissions.append(submission)
            self._record("BRACKET_SUBMITTED", ts_ns, **submission)

        def on_bar(self, bar: Bar) -> None:
            ts_ns = int(bar.ts_event)
            self._last_ts_ns = ts_ns
            self._mark_nav(ts_ns)

            if ts_ns >= self.config.force_exit_ts_ns:
                self._flatten(
                    ts_ns=ts_ns,
                    reason="EVALUATION_END_EXIT_SUBMITTED",
                )
                return

            self._time_exit_if_needed(ts_ns)

            new_plans = list(self.schedule.get(ts_ns, ()))
            if new_plans:
                if (
                    self.portfolio.is_flat(self.config.instrument_id)
                    and self.gate.owner is None
                ):
                    self.pending = new_plans
                else:
                    for plan in new_plans:
                        self._reject(
                            plan,
                            ts_ns=ts_ns,
                            reason="SIGNAL_WHILE_GLOBAL_POSITION_OCCUPIED",
                        )
            # The activation bar is strictly later than the signal timestamp.
            # It is now complete, so submission here is causal and avoids an
            # unintended second full minute of latency.
            self._submit_pending(bar)

        def on_position_opened(self, event: PositionOpened) -> None:
            self.position_opened_ns = int(event.ts_event)
            self._record(
                "POSITION_OPENED",
                int(event.ts_event),
                event=str(event),
            )

        def on_position_closed(self, event: PositionClosed) -> None:
            self.position_opened_ns = None
            self.cancel_all_orders(self.config.instrument_id)
            self._record(
                "POSITION_CLOSED",
                int(event.ts_event),
                event=str(event),
            )
            self._release_gate()
            self._mark_nav(int(event.ts_event))

        def _handle_order_failure(self, event_type: str, event: Any) -> None:
            ts_ns = int(event.ts_event)
            self._record(event_type, ts_ns, event=str(event))
            if self.portfolio.is_flat(self.config.instrument_id):
                self._release_gate()
                return
            self.protective_order_failures += 1
            self._flatten(
                ts_ns=ts_ns,
                reason="PROTECTIVE_ORDER_FAILURE_EXIT_SUBMITTED",
            )

        def on_order_denied(self, event: Any) -> None:
            self._handle_order_failure("ORDER_DENIED", event)

        def on_order_rejected(self, event: Any) -> None:
            self._handle_order_failure("ORDER_REJECTED", event)

        def on_stop(self) -> None:
            self.pending = []
            if not self.portfolio.is_flat(self.config.instrument_id):
                self.cancel_all_orders(self.config.instrument_id)
                self.close_all_positions(
                    self.config.instrument_id,
                    reduce_only=True,
                )
            self._release_gate()

        def finalize(self) -> None:
            if self._last_ts_ns >= self.config.evaluation_start_ns:
                self._mark_nav(self._last_ts_ns)
            if self._current_day is not None and self._last_day_equity is not None:
                if (
                    not self.daily_nav
                    or self.daily_nav[-1]["date"] != self._current_day
                ):
                    self.daily_nav.append(
                        {
                            "date": self._current_day,
                            "nav": self._last_day_equity,
                        },
                    )
            self.ended_flat = self.portfolio.is_flat(
                self.config.instrument_id,
            )

    venue = Venue("BINANCE")
    usdt = Currency.from_str("USDT")
    btc = Currency.from_str("BTC")
    cost_fraction = Decimal(
        str(execution.all_in_cost_bps_per_side / 10_000.0),
    )
    instrument = CryptoPerpetual(
        instrument_id=InstrumentId(Symbol("BTCUSDT-PERP"), venue),
        raw_symbol=Symbol("BTCUSDT"),
        base_currency=btc,
        quote_currency=usdt,
        settlement_currency=usdt,
        is_inverse=False,
        price_precision=execution.price_precision,
        size_precision=execution.quantity_precision,
        price_increment=Price.from_str(
            f"{execution.price_increment:.{execution.price_precision}f}",
        ),
        size_increment=Quantity.from_str(
            f"{execution.quantity_increment:.{execution.quantity_precision}f}",
        ),
        ts_event=0,
        ts_init=0,
        min_quantity=Quantity.from_str(
            f"{execution.quantity_increment:.{execution.quantity_precision}f}",
        ),
        min_notional=Money(10.0, usdt),
        max_price=Price.from_str(
            f"{10_000_000:.{execution.price_precision}f}",
        ),
        min_price=Price.from_str(
            f"{execution.price_increment:.{execution.price_precision}f}",
        ),
        margin_init=Decimal(1) / Decimal(str(execution.venue_max_leverage)),
        margin_maint=(
            Decimal(1) / Decimal(str(execution.venue_max_leverage))
        )
        / Decimal(2),
        maker_fee=cost_fraction,
        taker_fee=cost_fraction,
        info={
            "execution_engine": "NautilusTrader",
            "all_in_cost_bps_per_side": execution.all_in_cost_bps_per_side,
            "cost_model": (
                "fee/slippage/impact/funding stress represented as commission"
            ),
        },
    )
    # NautilusTrader's OHLC matching path requires a time-based BarType.
    # Equal-notional bars are therefore never registered as execution bars.
    # Official Binance Vision one-minute bars carry all matching events.
    bar_type = BarType.from_str(
        "BTCUSDT-PERP.BINANCE-1-MINUTE-LAST-EXTERNAL",
    )

    bars: list[Bar] = []
    for row in ordered_execution.itertuples(index=False):
        ts_event = int(pd.Timestamp(row.close_dt).as_unit("ns").value)
        bars.append(
            Bar(
                bar_type=bar_type,
                open=Price(float(row.open), instrument.price_precision),
                high=Price(float(row.high), instrument.price_precision),
                low=Price(float(row.low), instrument.price_precision),
                close=Price(float(row.close), instrument.price_precision),
                volume=Quantity(
                    float(row.base_volume),
                    instrument.size_precision,
                ),
                ts_event=ts_event,
                ts_init=ts_event,
            ),
        )

    engine = BacktestEngine(
        config=BacktestEngineConfig(
            logging=LoggingConfig(log_level="ERROR"),
        ),
    )
    gate = GlobalEntryGate()
    strategy = PlanStrategy(
        PlanStrategyConfig(
            instrument_id=instrument.id,
            bar_type=bar_type,
            risk_fraction=Decimal(str(execution.risk_fraction)),
            cost_fraction_per_side=cost_fraction,
            minimum_net_reward_risk=Decimal(
                str(execution.minimum_net_reward_risk),
            ),
            minimum_price_risk_fraction=Decimal(
                str(execution.minimum_price_risk_fraction),
            ),
            evaluation_start_ns=start_ns,
            evaluation_end_ns=end_ns,
            force_exit_ts_ns=force_exit_ts_ns,
            maximum_hold_ns=maximum_hold_ns,
        ),
        schedule=plans_by_signal_time,
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
        )
        engine.add_instrument(instrument)
        engine.add_data(bars)
        engine.add_strategy(strategy)
        engine.run()
        strategy.finalize()

        fills = engine.trader.generate_order_fills_report()
        positions = engine.trader.generate_positions_report()
        account = engine.trader.generate_account_report(venue)
        metrics = _build_metrics(
            label=label,
            evaluation_start=evaluation_start,
            evaluation_end=evaluation_end,
            execution=execution,
            strategy=strategy,
            fills=fills,
            positions=positions,
        )

        output_dir.mkdir(parents=True, exist_ok=True)
        fills.to_csv(output_dir / "orders.csv", index=False)
        positions.to_csv(output_dir / "positions.csv", index=False)
        account.to_csv(output_dir / "account.csv", index=False)
        pd.DataFrame(strategy.submissions).to_csv(
            output_dir / "trade_plans.csv",
            index=False,
        )
        pd.DataFrame(strategy.rejections).to_csv(
            output_dir / "rejections.csv",
            index=False,
        )
        pd.DataFrame(strategy.daily_nav).to_csv(
            output_dir / "daily_nav.csv",
            index=False,
        )
        _write_jsonl(
            output_dir / "execution_events.jsonl",
            strategy.execution_events,
        )
        _atomic_json(output_dir / "nautilus_metrics.json", metrics)
        _atomic_json(
            output_dir / "execution_contract.json",
            {
                "authoritative_performance_engine": "NautilusTrader",
                "nautilus_trader_version": package_version(
                    "nautilus_trader",
                ),
                "custom_fill_simulator": False,
                "custom_pnl_or_nav_ledger": False,
                "signal_generation_is_candidate_logic": True,
                "order_matching_commission_margin_positions_accounting": (
                    "NautilusTrader"
                ),
                "entry_delay": (
                    "submission on first strictly later completed one-minute "
                    "execution bar"
                ),
                "execution_market_data": (
                    "official Binance Vision USD-M one-minute klines"
                ),
                "risk_budget": (
                    "current NautilusTrader portfolio equity * fixed 3%"
                ),
                "one_global_position": True,
                "bar_adaptive_high_low_ordering": True,
            },
        )
        return NautilusRunEvidence(
            metrics=metrics,
            daily_nav=list(strategy.daily_nav),
            submissions=list(strategy.submissions),
            rejections=list(strategy.rejections),
            execution_events=list(strategy.execution_events),
            fills=fills,
            positions=positions,
            account=account,
        )
    finally:
        engine.dispose()
