#!/usr/bin/env python3
"""Four-market continuous-account evaluation of frozen Candidate 12 I19.

This runner reuses the synchronized-batch and one-global-slot architecture
already exercised in Candidate 14, while the alpha policy remains the exact
Candidate 12 I19 CausalLiquidityAuctionEngine on each allowed market. There is
one NautilusTrader margin account, one strategy, one current-NAV risk sizer and
at most one pending entry or open position across BTC/ETH/SOL/XRP.

At a timestamp where several frozen I19 engines emit simultaneously, candidates
are ranked deterministically by higher costed structural R, then lower loss as
a fraction of entry, then symbol/scenario ID. No asset-specific alpha parameter
or post-outcome symbol score is introduced.
"""
from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import json
from pathlib import Path
import sys
from typing import Any

import pandas as pd

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bar_adapter import build_bars
from data_loader import load_binance_bars
from logic import (
    BarObs,
    CausalLiquidityAuctionEngine,
    Direction,
    EntryOrder,
    LogicConfig,
    RiskSizer,
    TradePlan,
)
from metrics import decimal_value

from smc_ict_4.event_log import EventLogError, write_events
from smc_ict_4.manifest import create_run_manifest, write_json_atomic

UTC = timezone.utc
SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT")


@dataclass(frozen=True, slots=True)
class RankedPlan:
    symbol: str
    plan: TradePlan

    @property
    def loss_fraction(self) -> Decimal:
        return Decimal(str(self.plan.loss_per_unit)) / Decimal(str(self.plan.expected_entry))

    def rank(self) -> tuple[Decimal, Decimal, str, str]:
        return (
            -Decimal(str(self.plan.net_r)),
            self.loss_fraction,
            self.symbol,
            self.plan.scenario_id,
        )


def _closed_pnls(positions: pd.DataFrame) -> list[Decimal]:
    if positions.empty:
        return []
    column = next((name for name in ("realized_pnl", "pnl") if name in positions.columns), None)
    if column is None:
        return []
    result: list[Decimal] = []
    for value in positions[column].tolist():
        text = str(value).strip()
        if text and text.lower() != "nan":
            result.append(Decimal(text.split()[0]))
    return result


def _overlap_count(positions: pd.DataFrame) -> int:
    if positions.empty or not {"ts_opened", "ts_closed"}.issubset(positions.columns):
        return 0
    intervals: list[tuple[pd.Timestamp, pd.Timestamp]] = []
    for opened, closed in zip(positions["ts_opened"], positions["ts_closed"], strict=True):
        if pd.isna(opened) or pd.isna(closed):
            continue
        intervals.append((pd.Timestamp(opened), pd.Timestamp(closed)))
    intervals.sort(key=lambda item: item[0])
    return sum(intervals[index][0] < intervals[index - 1][1] for index in range(1, len(intervals)))


def _calculate_metrics(
    *,
    config: dict[str, Any],
    week_id: str,
    starting_nav: Decimal,
    final_nav: Decimal,
    positions: pd.DataFrame,
    plans: list[dict[str, Any]],
    logics: dict[str, CausalLiquidityAuctionEngine],
    errors: list[dict[str, Any]],
    lifecycle: list[dict[str, Any]],
    event_logs_valid: bool,
) -> dict[str, Any]:
    pnls = _closed_pnls(positions)
    wins = [pnl for pnl in pnls if pnl > 0]
    losses = [pnl for pnl in pnls if pnl < 0]
    win_rate = len(wins) / len(pnls) if pnls else 0.0
    if wins and losses:
        payoff_ratio: float | None = float(
            (sum(wins) / Decimal(len(wins)))
            / abs(sum(losses) / Decimal(len(losses)))
        )
    elif wins:
        payoff_ratio = None
    else:
        payoff_ratio = 0.0
    evaluation_days = (
        date.fromisoformat(config["selection"]["weeks"][week_id]["end_exclusive"])
        - date.fromisoformat(config["selection"]["weeks"][week_id]["start"])
    ).days
    daily_growth = (
        float((final_nav / starting_nav) ** (Decimal(1) / Decimal(evaluation_days)) - Decimal(1))
        if final_nav > 0
        else -1.0
    )
    equity = starting_nav
    peak = starting_nav
    max_drawdown = Decimal("0")
    for pnl in pnls:
        equity += pnl
        peak = max(peak, equity)
        if peak > 0:
            max_drawdown = max(max_drawdown, (peak - equity) / peak)
    overlap_count = _overlap_count(positions)
    liquidation = any("LIQUIDAT" in json.dumps(item, default=str).upper() for item in lifecycle)
    skips: Counter[str] = Counter()
    detected = 0
    for logic in logics.values():
        skips.update(logic.skips)
        detected += len(logic.events)
    gate = config["gates"]["project_target"]
    payoff_pass = (
        bool(wins) and not losses
        if payoff_ratio is None
        else payoff_ratio >= float(gate["min_payoff_ratio"])
    )
    project_pass = (
        len(pnls) >= int(gate["min_closed_trades_per_week"])
        and win_rate >= float(gate["min_win_rate"])
        and payoff_pass
        and daily_growth >= float(gate["min_daily_geometric_growth"])
        and float(max_drawdown) <= float(gate["max_closed_trade_drawdown"])
        and final_nav > 0
        and not errors
        and not liquidation
        and overlap_count == 0
        and event_logs_valid
    )
    return {
        "candidate": "candidate-12-i19-four-market-global-slot-portfolio",
        "alpha_source": config["candidate"],
        "evidence_class": "NAUTILUS_CONTINUOUS_ACCOUNT_NAV",
        "week_id": week_id,
        "starting_nav": str(starting_nav),
        "final_nav": str(final_nav),
        "net_return": float(final_nav / starting_nav - 1),
        "daily_geometric_growth": daily_growth,
        "evaluation_calendar_days": evaluation_days,
        "closed_trades": len(pnls),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": win_rate,
        "payoff_ratio": payoff_ratio,
        "all_closed_trades_won": bool(wins) and not losses,
        "closed_trade_max_drawdown": float(max_drawdown),
        "submitted_plans": len(plans),
        "symbol_counts": {
            symbol: sum(plan["symbol"] == symbol for plan in plans)
            for symbol in SYMBOLS
        },
        "scenario_counts": {
            scenario: sum(plan["scenario"] == scenario for plan in plans)
            for scenario in sorted({str(plan["scenario"]) for plan in plans})
        },
        "detected_events": detected,
        "skip_reasons": dict(skips),
        "engine_errors": errors,
        "liquidation_detected": liquidation,
        "global_slot_overlap_count": overlap_count,
        "event_logs_valid": event_logs_valid,
        "risk_budget_passed": all(
            Decimal(str(plan["expected_total_loss"]))
            <= Decimal(str(plan["planned_loss_budget"]))
            for plan in plans
        ),
        "global_slot_passed": overlap_count == 0,
        "project_target_gate_passed": project_pass,
        "success_claim": False,
    }


def run(config_path: Path, week_id: str, output_dir: Path) -> dict[str, Any]:
    from nautilus_trader.backtest.engine import BacktestEngine
    from nautilus_trader.backtest.models import FillModel
    from nautilus_trader.config import BacktestEngineConfig, LoggingConfig, StrategyConfig
    from nautilus_trader.model.currencies import USDT
    from nautilus_trader.model.data import Bar, BarType
    from nautilus_trader.model.enums import AccountType, OmsType, OrderSide, OrderType, TimeInForce
    from nautilus_trader.model.events import OrderEvent
    from nautilus_trader.model.identifiers import InstrumentId, Symbol, Venue
    from nautilus_trader.model.instruments import CryptoPerpetual
    from nautilus_trader.model.objects import Currency, Money, Price, Quantity
    from nautilus_trader.trading.strategy import Strategy

    config = json.loads(config_path.read_text(encoding="utf-8"))
    if week_id not in config["selection"]["weeks"]:
        raise ValueError(f"unknown frozen week {week_id!r}")
    selected = config["selection"]["weeks"][week_id]
    evaluation_start = date.fromisoformat(selected["start"])
    evaluation_end = date.fromisoformat(selected["end_exclusive"])
    warmup_start = evaluation_start - timedelta(days=int(config["selection"]["warmup_days"]))
    output_dir.mkdir(parents=True, exist_ok=True)

    frames: dict[str, pd.DataFrame] = {}
    manifest: list[dict[str, Any]] = []
    for symbol in SYMBOLS:
        frames[symbol], files = load_binance_bars(
            symbol,
            warmup_start,
            evaluation_end,
            output_dir / "data",
        )
        manifest.extend(files)
    data_manifest_path = output_dir / "data_manifest.json"
    write_json_atomic(
        data_manifest_path,
        {
            "schema": "candidate-12-i27-portfolio-source-manifest-v1",
            "dataset": "Binance USD-M one-minute daily klines",
            "symbols": list(SYMBOLS),
            "bar_visibility": "archive open_time plus one minute",
            "selection_seed": config["selection"]["seed"],
            "selection_rule": config["selection"]["selection_rule"],
            "warmup_start": warmup_start.isoformat(),
            "evaluation_start": evaluation_start.isoformat(),
            "evaluation_end_exclusive": evaluation_end.isoformat(),
            "files": manifest,
        },
    )

    account = config["account"]
    execution = config["execution"]
    venue = Venue("BINANCE")
    instruments: dict[str, Any] = {}
    bar_types: dict[str, Any] = {}
    all_bars: list[Any] = []
    flow: dict[tuple[str, int], tuple[float, float]] = {}
    logic_configs: dict[str, LogicConfig] = {}
    for symbol in SYMBOLS:
        spec = config["symbols"][symbol]
        base = Currency.from_str(spec["base_currency"], strict=False)
        if base is None:
            raise RuntimeError(f"could not resolve base currency for {symbol}")
        instrument_id = InstrumentId(symbol=Symbol(f"{symbol}-PERP"), venue=venue)
        instrument = CryptoPerpetual(
            instrument_id=instrument_id,
            raw_symbol=Symbol(symbol),
            base_currency=base,
            quote_currency=USDT,
            settlement_currency=USDT,
            is_inverse=False,
            price_precision=int(spec["price_precision"]),
            price_increment=Price.from_str(spec["price_increment"]),
            size_precision=int(spec["size_precision"]),
            size_increment=Quantity.from_str(spec["size_increment"]),
            max_quantity=Quantity.from_str("999999999"),
            min_quantity=Quantity.from_str(spec["min_quantity"]),
            max_notional=None,
            min_notional=Money(float(spec["min_notional"]), USDT),
            max_price=Price.from_str(
                format(10_000_000.0, f".{int(spec['price_precision'])}f")
            ),
            min_price=Price.from_str(
                format(float(spec["price_increment"]), f".{int(spec['price_precision'])}f")
            ),
            margin_init=Decimal(account["margin_init"]),
            margin_maint=Decimal(account["margin_maint"]),
            maker_fee=Decimal(execution["effective_maker_rate"]),
            taker_fee=Decimal(execution["effective_taker_rate"]),
            ts_event=0,
            ts_init=0,
        )
        bar_type = BarType.from_str(f"{symbol}-PERP.BINANCE-1-MINUTE-LAST-EXTERNAL")
        frame = frames[symbol]
        bars = build_bars(
            frame[["open", "high", "low", "close", "volume"]],
            bar_type,
            instrument,
        )
        all_bars.extend(bars)
        for ts, volume, taker_buy in zip(
            frame.index,
            frame["volume"],
            frame["taker_buy_volume"],
            strict=True,
        ):
            flow[(str(instrument_id), int(ts.value))] = (
                float(volume),
                float(taker_buy),
            )
        values = dict(config["logic"])
        values["price_increment"] = float(spec["price_increment"])
        values["risk_fraction"] = float(account["risk_fraction"])
        values["effective_maker_rate"] = float(execution["effective_maker_rate"])
        values["effective_taker_rate"] = float(execution["effective_taker_rate"])
        values["tick_slippage_units"] = float(execution["tick_slippage_units_in_risk_budget"])
        logic_configs[symbol] = LogicConfig(**values)
        instruments[symbol] = instrument
        bar_types[symbol] = bar_type
    all_bars.sort(key=lambda bar: (int(bar.ts_init), str(bar.bar_type)))

    starting_nav = Decimal(account["starting_nav"])
    evaluation_start_ns = int(pd.Timestamp(evaluation_start, tz="UTC").value)
    evaluation_end_ns = int(pd.Timestamp(evaluation_end, tz="UTC").value)

    class PortfolioConfig(StrategyConfig, frozen=True):
        instrument_ids: tuple[InstrumentId, ...]
        bar_types: tuple[BarType, ...]
        evaluation_start_ns: int
        evaluation_end_ns: int
        starting_nav: Decimal

    class PortfolioStrategy(Strategy):
        def __init__(self, strategy_config: PortfolioConfig) -> None:
            super().__init__(strategy_config)
            self.logic = {
                symbol: CausalLiquidityAuctionEngine(
                    logic_configs[symbol],
                    str(instruments[symbol].id),
                )
                for symbol in SYMBOLS
            }
            self.sizer = RiskSizer(float(account["risk_fraction"]))
            self.buffer_ts: int | None = None
            self.buffer: dict[str, BarObs] = {}
            self.active_plan: TradePlan | None = None
            self.active_symbol: str | None = None
            self.last_ts_ns = 0
            self.plans: list[dict[str, Any]] = []
            self.errors: list[dict[str, Any]] = []
            self.lifecycle: list[dict[str, Any]] = []
            self.rejections: list[dict[str, Any]] = []
            self.boundary_actions_started = False

        def on_start(self) -> None:
            for bar_type in self.config.bar_types:
                self.subscribe_bars(bar_type)

        @staticmethod
        def _symbol(bar: Bar) -> str:
            return str(bar.bar_type.instrument_id).split("-PERP", 1)[0]

        def _open_orders(self) -> int:
            return sum(
                int(
                    self.cache.orders_open_count(
                        instrument_id=instrument_id,
                        strategy_id=self.id,
                    )
                )
                for instrument_id in self.config.instrument_ids
            )

        def _all_flat(self) -> bool:
            return all(
                self.portfolio.is_flat(instrument_id)
                for instrument_id in self.config.instrument_ids
            )

        def _slot_free(self) -> bool:
            return self.active_plan is None and self._all_flat() and self._open_orders() == 0

        def _account_values(self) -> tuple[Decimal, Decimal]:
            account_state = self.cache.account_for_venue(venue)
            if account_state is None:
                return self.config.starting_nav, self.config.starting_nav
            total = decimal_value(account_state.balance_total(USDT))
            free = (
                decimal_value(account_state.balance_free(USDT), total)
                if hasattr(account_state, "balance_free")
                else total
            )
            return total, free

        def _record_order_event(self, event: OrderEvent, kind: str) -> None:
            self.lifecycle.append(
                {
                    "type": kind,
                    "ts_event": int(event.ts_event),
                    "client_order_id": str(event.client_order_id),
                    "event": str(event),
                }
            )

        def _terminal_if_flat(self, ts_ns: int, reason: str) -> None:
            if self.active_plan is None or self.active_symbol is None:
                return
            if self._all_flat() and self._open_orders() == 0:
                plan = self.active_plan
                symbol = self.active_symbol
                self.logic[symbol].mark_trade_terminal(
                    plan,
                    ts_ns,
                    reason,
                    {"lifecycle_events": len(self.lifecycle), "symbol": symbol},
                )
                if reason == "GTD_ENTRY_EXPIRED_UNFILLED":
                    self.logic[symbol].rearm_unfilled_plan(plan, ts_ns)
                self.active_plan = None
                self.active_symbol = None

        def _reject(self, candidate: RankedPlan, reason: str) -> None:
            self.logic[candidate.symbol].mark_plan_rejected(
                candidate.plan,
                self.last_ts_ns,
                reason,
                {"symbol": candidate.symbol},
            )
            self.rejections.append(
                {
                    "type": "GLOBAL_CANDIDATE_REJECTED",
                    "observed_ts_ns": candidate.plan.observed_ts_ns,
                    "scenario_id": candidate.plan.scenario_id,
                    "symbol": candidate.symbol,
                    "reason": reason,
                    "net_r": candidate.plan.net_r,
                }
            )

        def _submit(self, candidate: RankedPlan) -> None:
            plan = candidate.plan
            symbol = candidate.symbol
            if not self._slot_free():
                self._reject(candidate, "GLOBAL_SLOT_OCCUPIED")
                return
            instrument = instruments[symbol]
            nav, free = self._account_values()
            sizing = self.sizer.size(
                nav=nav,
                loss_per_unit=Decimal(str(plan.loss_per_unit)),
                entry_price=Decimal(str(plan.expected_entry)),
                quantity_increment=Decimal(str(instrument.size_increment)),
                min_quantity=Decimal(str(instrument.min_quantity)),
                min_notional=decimal_value(instrument.min_notional),
                margin_init=instrument.margin_init,
                free_balance=free,
            )
            if not sizing.feasible:
                self.logic[symbol].mark_plan_rejected(
                    plan,
                    self.last_ts_ns,
                    sizing.reason,
                    {
                        "planned_loss_budget": str(sizing.planned_loss_budget),
                        "expected_total_loss": str(sizing.expected_total_loss),
                        "required_margin": str(sizing.required_margin),
                        "free_balance": str(free),
                    },
                )
                return
            side = OrderSide.BUY if plan.direction is Direction.LONG else OrderSide.SELL
            common = dict(
                instrument_id=instrument.id,
                order_side=side,
                quantity=instrument.make_qty(sizing.quantity),
                tp_order_type=OrderType.LIMIT,
                tp_price=instrument.make_price(plan.target_price),
                tp_time_in_force=TimeInForce.GTC,
                tp_post_only=True,
                sl_order_type=OrderType.STOP_MARKET,
                sl_trigger_price=instrument.make_price(plan.stop_price),
                sl_time_in_force=TimeInForce.GTC,
            )
            try:
                if plan.entry_order is EntryOrder.MARKET:
                    order_list = self.order_factory.bracket(
                        entry_order_type=OrderType.MARKET,
                        **common,
                    )
                else:
                    if plan.expire_ts_ns is None:
                        raise ValueError("LIMIT_GTD plan is missing expire_ts_ns")
                    order_list = self.order_factory.bracket(
                        entry_order_type=OrderType.LIMIT,
                        entry_price=instrument.make_price(plan.expected_entry),
                        time_in_force=TimeInForce.GTD,
                        expire_time=(
                            datetime.fromtimestamp(
                                plan.expire_ts_ns / 1_000_000_000,
                                tz=UTC,
                            )
                            + timedelta(microseconds=1)
                        ),
                        entry_post_only=False,
                        **common,
                    )
                self.submit_order_list(order_list)
            except Exception as exc:
                record = {
                    "type": "ORDER_LIST_SUBMISSION_EXCEPTION",
                    "ts_ns": self.last_ts_ns,
                    "symbol": symbol,
                    "exception": type(exc).__name__,
                    "message": str(exc),
                }
                self.errors.append(record)
                self.logic[symbol].mark_plan_rejected(
                    plan,
                    self.last_ts_ns,
                    record["type"],
                    record,
                )
                return
            evidence = {
                "symbol": symbol,
                "scenario_id": plan.scenario_id,
                "scenario": plan.scenario.value,
                "direction": plan.direction.value,
                "observed_ts_ns": plan.observed_ts_ns,
                "entry_order_type": plan.entry_order.value,
                "expire_ts_ns": plan.expire_ts_ns,
                "entry": plan.expected_entry,
                "stop": plan.stop_price,
                "target": plan.target_price,
                "loss_per_unit": plan.loss_per_unit,
                "expected_profit_per_unit": plan.expected_profit_per_unit,
                "net_r": plan.net_r,
                "quantity": str(sizing.quantity),
                "nav_before": str(nav),
                "planned_loss_budget": str(sizing.planned_loss_budget),
                "expected_total_loss": str(sizing.expected_total_loss),
                "required_margin": str(sizing.required_margin),
                "details": plan.details,
            }
            self.plans.append(evidence)
            self.active_plan = plan
            self.active_symbol = symbol
            self.logic[symbol].mark_plan_submitted(
                plan,
                self.last_ts_ns,
                evidence,
            )

        def _process_batch(self, ts_ns: int) -> None:
            free_at_start = self._slot_free() and ts_ns >= self.config.evaluation_start_ns
            candidates: list[RankedPlan] = []
            for symbol in SYMBOLS:
                plan = self.logic[symbol].on_bar(
                    self.buffer[symbol],
                    allow_entry=free_at_start,
                )
                if plan is not None:
                    candidates.append(RankedPlan(symbol=symbol, plan=plan))
            if not candidates:
                return
            if not free_at_start:
                for candidate in candidates:
                    self._reject(candidate, "GLOBAL_SLOT_OCCUPIED")
                return
            ordered = sorted(candidates, key=lambda item: item.rank())
            for loser in ordered[1:]:
                self._reject(loser, "LOWER_GLOBAL_PRIORITY")
            self._submit(ordered[0])

        def on_bar(self, bar: Bar) -> None:
            self.last_ts_ns = int(bar.ts_event)
            self._terminal_if_flat(self.last_ts_ns, "NAUTILUS_FLAT_NO_WORKING_ORDERS")
            if self.last_ts_ns >= self.config.evaluation_end_ns:
                if self.buffer_ts is not None and len(self.buffer) == len(SYMBOLS):
                    self._process_batch(self.buffer_ts)
                    self.buffer.clear()
                    self.buffer_ts = None
                if not self.boundary_actions_started:
                    self.boundary_actions_started = True
                    for instrument_id in self.config.instrument_ids:
                        if self.cache.orders_open_count(
                            instrument_id=instrument_id,
                            strategy_id=self.id,
                        ):
                            self.cancel_all_orders(instrument_id)
                        if not self.portfolio.is_flat(instrument_id):
                            self.close_all_positions(instrument_id)
                return
            symbol = self._symbol(bar)
            key = (str(bar.bar_type.instrument_id), self.last_ts_ns)
            if key not in flow:
                self.errors.append(
                    {"type": "MISSING_FLOW", "symbol": symbol, "ts_ns": self.last_ts_ns}
                )
                return
            volume, taker_buy = flow[key]
            observation = BarObs(
                ts_ns=self.last_ts_ns,
                open=float(str(bar.open)),
                high=float(str(bar.high)),
                low=float(str(bar.low)),
                close=float(str(bar.close)),
                volume=volume,
                taker_buy_volume=taker_buy,
            )
            if self.buffer_ts is None:
                self.buffer_ts = self.last_ts_ns
            if self.last_ts_ns != self.buffer_ts:
                if len(self.buffer) != len(SYMBOLS):
                    self.errors.append(
                        {
                            "type": "INCOMPLETE_SYNCHRONIZED_BATCH",
                            "ts_ns": self.buffer_ts,
                            "symbols": sorted(self.buffer),
                        }
                    )
                else:
                    self._process_batch(self.buffer_ts)
                self.buffer.clear()
                self.buffer_ts = self.last_ts_ns
            self.buffer[symbol] = observation
            if len(self.buffer) == len(SYMBOLS):
                self._process_batch(self.buffer_ts)
                self.buffer.clear()
                self.buffer_ts = None

        def on_order_filled(self, event: OrderEvent) -> None:
            self._record_order_event(event, "ORDER_FILLED")

        def on_order_expired(self, event: OrderEvent) -> None:
            self._record_order_event(event, "ORDER_EXPIRED")
            self._terminal_if_flat(int(event.ts_event), "GTD_ENTRY_EXPIRED_UNFILLED")

        def on_order_canceled(self, event: OrderEvent) -> None:
            self._record_order_event(event, "ORDER_CANCELED")
            self._terminal_if_flat(int(event.ts_event), "ORDERS_CANCELED_FLAT")

        def on_order_denied(self, event: OrderEvent) -> None:
            self._record_order_event(event, "ORDER_DENIED")
            self.errors.append({"type": "ORDER_DENIED", "event": str(event)})

        def on_order_rejected(self, event: OrderEvent) -> None:
            self._record_order_event(event, "ORDER_REJECTED")
            self.errors.append({"type": "ORDER_REJECTED", "event": str(event)})

        def on_stop(self) -> None:
            for instrument_id in self.config.instrument_ids:
                if self.cache.orders_open_count(
                    instrument_id=instrument_id,
                    strategy_id=self.id,
                ):
                    self.cancel_all_orders(instrument_id)
                if not self.portfolio.is_flat(instrument_id):
                    self.close_all_positions(instrument_id)

    engine = BacktestEngine(
        config=BacktestEngineConfig(logging=LoggingConfig(log_level="ERROR"))
    )
    fill_model = FillModel(
        prob_fill_on_limit=float(execution["prob_fill_on_limit"]),
        prob_slippage=float(execution["prob_slippage"]),
        random_seed=int(execution["random_seed"]),
    )
    strategy = PortfolioStrategy(
        PortfolioConfig(
            instrument_ids=tuple(instruments[symbol].id for symbol in SYMBOLS),
            bar_types=tuple(bar_types[symbol] for symbol in SYMBOLS),
            evaluation_start_ns=evaluation_start_ns,
            evaluation_end_ns=evaluation_end_ns,
            starting_nav=starting_nav,
        )
    )
    try:
        engine.add_venue(
            venue=venue,
            oms_type=OmsType.NETTING,
            account_type=AccountType.MARGIN,
            starting_balances=[Money(starting_nav, USDT)],
            base_currency=USDT,
            default_leverage=Decimal(account["default_leverage"]),
            fill_model=fill_model,
            bar_adaptive_high_low_ordering=bool(
                execution["bar_adaptive_high_low_ordering"]
            ),
        )
        for symbol in SYMBOLS:
            engine.add_instrument(instruments[symbol])
        engine.add_data(all_bars)
        engine.add_strategy(strategy)
        engine.run()

        orders = engine.trader.generate_order_fills_report()
        positions = engine.trader.generate_positions_report()
        account_report = engine.trader.generate_account_report(venue)
        orders.to_csv(output_dir / "orders.csv", index=False)
        positions.to_csv(output_dir / "positions.csv", index=False)
        account_report.to_csv(output_dir / "account.csv", index=False)
        account_state = engine.cache.account_for_venue(venue)
        if account_state is None:
            raise RuntimeError("Nautilus margin account unavailable after portfolio run")
        final_nav = decimal_value(account_state.balance_total(USDT))
        event_logs_valid = True
        event_errors: dict[str, str] = {}
        for symbol, logic in strategy.logic.items():
            try:
                write_events(output_dir / f"scenario_events.{symbol}.jsonl", logic.events)
            except EventLogError as exc:
                event_logs_valid = False
                event_errors[symbol] = str(exc)
        metrics = _calculate_metrics(
            config=config,
            week_id=week_id,
            starting_nav=starting_nav,
            final_nav=final_nav,
            positions=positions,
            plans=strategy.plans,
            logics=strategy.logic,
            errors=strategy.errors,
            lifecycle=strategy.lifecycle,
            event_logs_valid=event_logs_valid,
        )
        metrics.update(
            {
                "evaluation_start": evaluation_start.isoformat(),
                "evaluation_end_exclusive": evaluation_end.isoformat(),
                "warmup_start": warmup_start.isoformat(),
                "bars": len(all_bars),
                "instruments": [str(instruments[symbol].id) for symbol in SYMBOLS],
                "event_log_errors": event_errors,
            }
        )
        write_json_atomic(output_dir / "metrics.json", metrics)
        write_json_atomic(output_dir / "submitted_plans.json", {"plans": strategy.plans})
        write_json_atomic(output_dir / "order_lifecycle.json", {"events": strategy.lifecycle})
        write_json_atomic(output_dir / "global_rejections.json", {"rejections": strategy.rejections})
        manifest_path = create_run_manifest(
            run_id=(
                f"candidate-12-i27-portfolio-{week_id.lower()}-"
                f"{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}"
            ),
            candidate=metrics["candidate"],
            config_path=config_path,
            data_manifest_path=data_manifest_path,
            extra={
                "week_id": week_id,
                "symbols": list(SYMBOLS),
                "evaluation_start": evaluation_start.isoformat(),
                "evaluation_end_exclusive": evaluation_end.isoformat(),
                "alpha_source": config["candidate"],
                "metrics_path": str(output_dir / "metrics.json"),
            },
        )
        write_json_atomic(output_dir / "run.json", manifest_path)
        return metrics
    finally:
        engine.dispose()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=ROOT / "config.json")
    parser.add_argument("--week", default="W1")
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "results" / "I27-PORTFOLIO-W1",
    )
    args = parser.parse_args()
    metrics = run(
        args.config.resolve(),
        args.week,
        args.output.resolve(),
    )
    print(json.dumps(metrics, indent=2, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
