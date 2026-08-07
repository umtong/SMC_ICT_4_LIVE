#!/usr/bin/env python3
"""Evaluate dynamic cross-market leader/follower convergence in NautilusTrader."""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from hashlib import sha256
import json
from pathlib import Path
import sys
from typing import Any

import pandas as pd

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bar_adapter import build_bars
from cross_market import CausalLeaderFollowerEngine, CrossMarketPlan, CrossObservation
from logic import RiskSizer
from run_leadership_scdam import load_symbol_bars

UTC = timezone.utc
SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT")
META = {
    "BTCUSDT": {"base": "BTC", "price_precision": 1, "price_increment": "0.1", "size_precision": 3, "size_increment": "0.001", "min_qty": "0.001"},
    "ETHUSDT": {"base": "ETH", "price_precision": 2, "price_increment": "0.01", "size_precision": 3, "size_increment": "0.001", "min_qty": "0.001"},
    "SOLUSDT": {"base": "SOL", "price_precision": 3, "price_increment": "0.001", "size_precision": 1, "size_increment": "0.1", "min_qty": "0.1"},
    "XRPUSDT": {"base": "XRP", "price_precision": 4, "price_increment": "0.0001", "size_precision": 0, "size_increment": "1", "min_qty": "1"},
}


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    temporary.replace(path)


def decimal_value(value: Any, default: Decimal = Decimal("0")) -> Decimal:
    if value is None:
        return default
    text = str(value).replace(",", "").strip()
    if not text or text.lower() in {"nan", "none", "nat", "null"}:
        return default
    return Decimal(text.split()[0])


def closed_pnls(positions: pd.DataFrame) -> list[Decimal]:
    if positions.empty:
        return []
    column = next((name for name in ("realized_pnl", "pnl") if name in positions.columns), None)
    if column is None:
        return []
    return [decimal_value(value) for value in positions[column].tolist()]


def overlap_count(positions: pd.DataFrame) -> int:
    if positions.empty or not {"ts_opened", "ts_closed"}.issubset(positions.columns):
        return 0
    intervals: list[tuple[pd.Timestamp, pd.Timestamp]] = []
    for opened, closed in zip(positions["ts_opened"], positions["ts_closed"], strict=True):
        if pd.isna(opened) or pd.isna(closed):
            continue
        intervals.append((pd.Timestamp(opened), pd.Timestamp(closed)))
    intervals.sort(key=lambda value: value[0])
    return sum(intervals[index][0] < intervals[index - 1][1] for index in range(1, len(intervals)))


def calculate_metrics(
    *,
    starting_nav: Decimal,
    final_nav: Decimal,
    evaluation_days: int,
    positions: pd.DataFrame,
    plans: list[dict[str, Any]],
    lifecycle: list[dict[str, Any]],
    detector: CausalLeaderFollowerEngine,
    errors: list[dict[str, Any]],
) -> dict[str, Any]:
    pnls = closed_pnls(positions)
    wins = [value for value in pnls if value > 0]
    losses = [value for value in pnls if value < 0]
    win_rate = len(wins) / len(pnls) if pnls else 0.0
    payoff: float | None = None
    if wins and losses:
        payoff = float((sum(wins) / len(wins)) / abs(sum(losses) / len(losses)))
    equity = starting_nav
    peak = starting_nav
    maximum_drawdown = Decimal(0)
    for pnl in pnls:
        equity += pnl
        peak = max(peak, equity)
        if peak > 0:
            maximum_drawdown = max(maximum_drawdown, (peak - equity) / peak)
    growth = (
        float((final_nav / starting_nav) ** (Decimal(1) / Decimal(evaluation_days)) - Decimal(1))
        if final_nav > 0
        else -1.0
    )
    liquidation = any("LIQUIDAT" in json.dumps(value, default=str).upper() for value in lifecycle)
    return {
        "candidate": "candidate-11-causal-cross-market-leader-follower",
        "evidence_class": "NAUTILUS_ACCOUNT_NAV",
        "starting_nav": str(starting_nav),
        "final_nav": str(final_nav),
        "net_return": float(final_nav / starting_nav - Decimal(1)),
        "daily_geometric_growth": growth,
        "evaluation_calendar_days": evaluation_days,
        "closed_trades": len(pnls),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": win_rate,
        "payoff_ratio": payoff,
        "all_closed_trades_won": bool(wins) and not losses,
        "closed_trade_max_drawdown": float(maximum_drawdown),
        "submitted_plans": len(plans),
        "symbol_counts": dict(Counter(plan["symbol"] for plan in plans)),
        "leader_counts": dict(Counter(plan["leader"] for plan in plans)),
        "direction_counts": dict(Counter(plan["direction"] for plan in plans)),
        "detector_event_counts": dict(Counter(event["type"] for event in detector.events)),
        "skip_reasons": dict(detector.skips),
        "engine_errors": errors,
        "partial_entry_fail_closed_count": sum(
            event.get("type") == "PARTIAL_ENTRY_EXPIRED_FAIL_CLOSED"
            for event in lifecycle
        ),
        "liquidation_detected": liquidation,
        "global_slot_overlap_count": overlap_count(positions),
        "success_claim": False,
    }


def run(
    *,
    protocol_path: Path,
    project_config_path: Path,
    week_id: str,
    output_dir: Path,
) -> dict[str, Any]:
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

    protocol = json.loads(protocol_path.read_text(encoding="utf-8"))
    project_config = json.loads(project_config_path.read_text(encoding="utf-8"))
    selected = protocol["weeks"][week_id]
    evaluation_start = date.fromisoformat(selected["start"])
    evaluation_end = date.fromisoformat(selected["end_exclusive"])
    warmup_start = evaluation_start - timedelta(days=int(protocol["warmup_days"]))
    execution = project_config["execution"]
    account = project_config["account"]
    output_dir.mkdir(parents=True, exist_ok=True)

    frames: dict[str, pd.DataFrame] = {}
    manifest: list[dict[str, Any]] = []
    for symbol in SYMBOLS:
        frames[symbol], files = load_symbol_bars(
            symbol,
            warmup_start,
            evaluation_end - timedelta(days=1),
            output_dir / "data",
        )
        manifest.extend(files)
    write_json(output_dir / "data_manifest.json", {
        "schema": "candidate-11-cross-market-source-manifest-v1",
        "dataset": "Binance USD-M one-minute daily klines",
        "symbols": list(SYMBOLS),
        "bar_visibility": "archive open_time plus one minute",
        "warmup_start": warmup_start.isoformat(),
        "evaluation_start": evaluation_start.isoformat(),
        "evaluation_end_exclusive": evaluation_end.isoformat(),
        "files": manifest,
    })

    base_currencies = {
        symbol: Currency.from_str(str(META[symbol]["base"]), strict=False)
        for symbol in SYMBOLS
    }
    if any(value is None for value in base_currencies.values()):
        raise RuntimeError("Nautilus currency registry could not resolve an allowed base asset")
    venue = Venue("BINANCE")
    instruments: dict[str, Any] = {}
    bar_types: dict[str, Any] = {}
    all_bars: list[Any] = []
    flow: dict[tuple[str, int], tuple[float, float, float]] = {}
    for symbol in SYMBOLS:
        meta = META[symbol]
        instrument_id = InstrumentId(symbol=Symbol(f"{symbol}-PERP"), venue=venue)
        instrument = CryptoPerpetual(
            instrument_id=instrument_id,
            raw_symbol=Symbol(symbol),
            base_currency=base_currencies[symbol],
            quote_currency=USDT,
            settlement_currency=USDT,
            is_inverse=False,
            price_precision=int(meta["price_precision"]),
            price_increment=Price.from_str(meta["price_increment"]),
            size_precision=int(meta["size_precision"]),
            size_increment=Quantity.from_str(meta["size_increment"]),
            max_quantity=Quantity.from_str("1000000000"),
            min_quantity=Quantity.from_str(meta["min_qty"]),
            max_notional=None,
            min_notional=Money(5, USDT),
            max_price=Price.from_str("10000000"),
            min_price=Price.from_str(meta["price_increment"]),
            margin_init=Decimal(account["margin_init"]),
            margin_maint=Decimal(account["margin_maint"]),
            maker_fee=Decimal(execution["effective_maker_rate"]),
            taker_fee=Decimal(execution["effective_taker_rate"]),
            ts_event=0,
            ts_init=0,
        )
        bar_type = BarType.from_str(f"{symbol}-PERP.BINANCE-1-MINUTE-LAST-EXTERNAL")
        frame = frames[symbol]
        built = build_bars(frame[["open", "high", "low", "close", "volume"]], bar_type, instrument)
        all_bars.extend(built)
        for ts, close, volume, taker in zip(
            frame.index,
            frame["close"],
            frame["volume"],
            frame["taker_buy_volume"],
            strict=True,
        ):
            flow[(str(instrument_id), int(ts.value))] = (
                float(volume),
                float(taker),
                float(close) * float(volume),
            )
        instruments[symbol] = instrument
        bar_types[symbol] = bar_type
    all_bars.sort(key=lambda bar: (int(bar.ts_init), str(bar.bar_type)))

    starting_nav = Decimal(account["starting_nav"])
    evaluation_start_ns = int(pd.Timestamp(evaluation_start, tz="UTC").value)
    evaluation_end_ns = int(pd.Timestamp(evaluation_end, tz="UTC").value)

    class CrossStrategyConfig(StrategyConfig, frozen=True):
        instrument_ids: tuple[InstrumentId, ...]
        bar_types: tuple[BarType, ...]
        evaluation_start_ns: int
        evaluation_end_ns: int
        starting_nav: Decimal

    class CrossStrategy(Strategy):
        def __init__(self, strategy_config: CrossStrategyConfig) -> None:
            super().__init__(strategy_config)
            self.detector = CausalLeaderFollowerEngine(
                SYMBOLS,
                effective_maker_rate=float(execution["effective_maker_rate"]),
                effective_taker_rate=float(execution["effective_taker_rate"]),
                minimum_net_r=1.25,
            )
            self.sizer = RiskSizer(Decimal(str(account["risk_fraction"])))
            self.buffer_ts: int | None = None
            self.buffer: dict[str, Bar] = {}
            self.active_plan: CrossMarketPlan | None = None
            self.active_symbol: str | None = None
            self.entry_pending = False
            self.position_open = False
            self.plans: list[dict[str, Any]] = []
            self.lifecycle: list[dict[str, Any]] = []
            self.errors: list[dict[str, Any]] = []
            self.last_ts_ns = 0

        def on_start(self) -> None:
            for bar_type in self.config.bar_types:
                self.subscribe_bars(bar_type)

        @staticmethod
        def _symbol(bar: Bar) -> str:
            return str(bar.bar_type.instrument_id).split("-PERP", 1)[0]

        def _open_orders(self) -> int:
            return sum(
                int(self.cache.orders_open_count(instrument_id=value, strategy_id=self.id))
                for value in self.config.instrument_ids
            )

        def _all_flat(self) -> bool:
            return all(self.portfolio.is_flat(value) for value in self.config.instrument_ids)

        def _account_values(self) -> tuple[Decimal, Decimal]:
            cached = self.cache.account_for_venue(venue)
            if cached is None:
                return self.config.starting_nav, self.config.starting_nav
            total = decimal_value(cached.balance_total(USDT))
            free = decimal_value(cached.balance_free(USDT), total) if hasattr(cached, "balance_free") else total
            return total, free

        def _release_terminal(self, ts_ns: int, reason: str) -> None:
            if self.active_plan is None or self.active_symbol is None:
                return
            instrument_id = instruments[self.active_symbol].id
            if self.entry_pending and not self.portfolio.is_flat(instrument_id):
                self.entry_pending = False
                self.position_open = True
                self.detector.mark_entry_filled(ts_ns)
                self.lifecycle.append({
                    "type": "GLOBAL_ENTRY_FILLED",
                    "ts_event": ts_ns,
                    "scenario_id": self.active_plan.scenario_id,
                    "symbol": self.active_symbol,
                })
            elif self.entry_pending and self._all_flat() and self._open_orders() == 0:
                self.detector.mark_terminal(ts_ns, reason)
                self.entry_pending = False
                self.active_plan = None
                self.active_symbol = None
            elif self.position_open and self._all_flat() and self._open_orders() == 0:
                self.detector.mark_terminal(ts_ns, reason)
                self.lifecycle.append({
                    "type": "GLOBAL_POSITION_CLOSED",
                    "ts_event": ts_ns,
                    "scenario_id": self.active_plan.scenario_id,
                    "symbol": self.active_symbol,
                })
                self.position_open = False
                self.active_plan = None
                self.active_symbol = None

        def _submit(self, plan: CrossMarketPlan) -> None:
            symbol = plan.symbol
            instrument = instruments[symbol]
            if self.active_plan is not None or not self._all_flat() or self._open_orders() > 0:
                self.detector.mark_rejected(plan, self.last_ts_ns, "GLOBAL_SLOT_OCCUPIED")
                return
            nav, free = self._account_values()
            decision = self.sizer.size(
                nav=nav,
                loss_per_unit=Decimal(str(plan.loss_per_unit)),
                entry_price=Decimal(str(plan.expected_entry)),
                quantity_increment=Decimal(str(instrument.size_increment)),
                min_quantity=Decimal(str(instrument.min_quantity)),
                min_notional=decimal_value(instrument.min_notional),
                margin_init=instrument.margin_init,
                free_balance=free,
            )
            if not decision.feasible:
                self.detector.mark_rejected(plan, self.last_ts_ns, decision.reason)
                return
            side = OrderSide.BUY if plan.direction == "LONG" else OrderSide.SELL
            try:
                order_list = self.order_factory.bracket(
                    instrument_id=instrument.id,
                    order_side=side,
                    quantity=instrument.make_qty(decision.quantity),
                    entry_order_type=OrderType.LIMIT,
                    entry_price=instrument.make_price(plan.expected_entry),
                    expire_time=datetime.fromtimestamp(plan.expire_ts_ns / 1_000_000_000, tz=UTC) + timedelta(microseconds=1),
                    time_in_force=TimeInForce.GTD,
                    entry_post_only=True,
                    tp_order_type=OrderType.LIMIT,
                    tp_price=instrument.make_price(plan.target_price),
                    tp_time_in_force=TimeInForce.GTC,
                    tp_post_only=True,
                    sl_order_type=OrderType.STOP_MARKET,
                    sl_trigger_price=instrument.make_price(plan.stop_price),
                    sl_time_in_force=TimeInForce.GTC,
                )
                self.submit_order_list(order_list)
            except Exception as exc:
                self.errors.append({
                    "type": "ORDER_LIST_SUBMISSION_EXCEPTION",
                    "ts_ns": self.last_ts_ns,
                    "symbol": symbol,
                    "exception": type(exc).__name__,
                    "message": str(exc),
                })
                self.detector.mark_rejected(plan, self.last_ts_ns, "ORDER_LIST_SUBMISSION_EXCEPTION")
                return
            self.detector.mark_submitted(plan)
            self.active_plan = plan
            self.active_symbol = symbol
            self.entry_pending = True
            self.plans.append({
                "symbol": symbol,
                "leader": plan.leader,
                "scenario_id": plan.scenario_id,
                "scenario": "CROSS_MARKET_FOLLOWER_CONVERGENCE",
                "direction": plan.direction,
                "observed_ts_ns": plan.observed_ts_ns,
                "entry": plan.expected_entry,
                "stop": plan.stop_price,
                "target": plan.target_price,
                "net_r": plan.net_r,
                "signal_score": plan.signal_score,
                "quantity": str(decision.quantity),
                "nav_before": str(nav),
                "planned_loss_budget": str(decision.planned_loss_budget),
                "expected_total_loss": str(decision.expected_total_loss),
                "required_margin": str(decision.required_margin),
                "details": plan.details,
            })
            self.lifecycle.append({
                "type": "GLOBAL_ENTRY_SUBMITTED",
                "ts_event": self.last_ts_ns,
                "scenario_id": plan.scenario_id,
                "symbol": symbol,
            })

        def _process_batch(self, ts_ns: int) -> None:
            observations: dict[str, CrossObservation] = {}
            for symbol in SYMBOLS:
                bar = self.buffer[symbol]
                volume, taker, quote = flow[(str(instruments[symbol].id), ts_ns)]
                observations[symbol] = CrossObservation(
                    ts_ns=ts_ns,
                    open=float(str(bar.open)),
                    high=float(str(bar.high)),
                    low=float(str(bar.low)),
                    close=float(str(bar.close)),
                    volume=volume,
                    quote_volume=quote,
                    taker_buy_volume=taker,
                )
            try:
                plans = self.detector.on_batch(ts_ns, observations)
            except Exception as exc:
                self.errors.append({
                    "type": "CROSS_MARKET_OBSERVATION_ERROR",
                    "ts_ns": ts_ns,
                    "exception": type(exc).__name__,
                    "message": str(exc),
                })
                return
            for plan in plans:
                if ts_ns < self.config.evaluation_start_ns:
                    self.detector.mark_rejected(plan, ts_ns, "OUTSIDE_EVALUATION_WINDOW")
                    continue
                self._submit(plan)

        def on_bar(self, bar: Bar) -> None:
            self.last_ts_ns = int(bar.ts_event)
            self._release_terminal(self.last_ts_ns, "BAR_TERMINAL_SYNC")
            if self.last_ts_ns >= self.config.evaluation_end_ns:
                if self._open_orders() > 0:
                    for value in self.config.instrument_ids:
                        if self.cache.orders_open_count(instrument_id=value, strategy_id=self.id):
                            self.cancel_all_orders(value)
                for value in self.config.instrument_ids:
                    if not self.portfolio.is_flat(value):
                        self.close_all_positions(value)
                return
            symbol = self._symbol(bar)
            if self.buffer_ts is None:
                self.buffer_ts = self.last_ts_ns
            if self.last_ts_ns != self.buffer_ts:
                if len(self.buffer) != len(SYMBOLS):
                    self.errors.append({
                        "type": "INCOMPLETE_SYNCHRONIZED_BATCH",
                        "ts_ns": self.buffer_ts,
                        "symbols": sorted(self.buffer),
                    })
                else:
                    self._process_batch(self.buffer_ts)
                self.buffer.clear()
                self.buffer_ts = self.last_ts_ns
            self.buffer[symbol] = bar
            if len(self.buffer) == len(SYMBOLS):
                self._process_batch(self.buffer_ts)
                self.buffer.clear()
                self.buffer_ts = None

        def _record_order(self, event: OrderEvent, kind: str) -> None:
            self.lifecycle.append({
                "type": kind,
                "ts_event": int(event.ts_event),
                "client_order_id": str(event.client_order_id),
                "event": str(event),
            })

        def on_order_filled(self, event: OrderEvent) -> None:
            self._record_order(event, "ORDER_FILLED")
            self._release_terminal(int(event.ts_event), "ORDER_FILLED")

        def on_order_expired(self, event: OrderEvent) -> None:
            self._record_order(event, "ORDER_EXPIRED")
            if self.position_open and self.active_symbol is not None:
                instrument_id = instruments[self.active_symbol].id
                if not self.portfolio.is_flat(instrument_id):
                    self.lifecycle.append({
                        "type": "PARTIAL_ENTRY_EXPIRED_FAIL_CLOSED",
                        "ts_event": int(event.ts_event),
                        "scenario_id": None if self.active_plan is None else self.active_plan.scenario_id,
                        "symbol": self.active_symbol,
                    })
                    if self._open_orders() > 0:
                        self.cancel_all_orders(instrument_id)
                    self.close_all_positions(instrument_id)
            self._release_terminal(int(event.ts_event), "ENTRY_EXPIRED")

        def on_order_canceled(self, event: OrderEvent) -> None:
            self._record_order(event, "ORDER_CANCELED")
            self._release_terminal(int(event.ts_event), "ORDER_CANCELED")

        def on_order_denied(self, event: OrderEvent) -> None:
            self._record_order(event, "ORDER_DENIED")
            self.errors.append({"type": "ORDER_DENIED", "event": str(event)})
            self._release_terminal(int(event.ts_event), "ORDER_DENIED")

        def on_order_rejected(self, event: OrderEvent) -> None:
            self._record_order(event, "ORDER_REJECTED")
            self.errors.append({"type": "ORDER_REJECTED", "event": str(event)})
            self._release_terminal(int(event.ts_event), "ORDER_REJECTED")

        def on_stop(self) -> None:
            for value in self.config.instrument_ids:
                if self.cache.orders_open_count(instrument_id=value, strategy_id=self.id):
                    self.cancel_all_orders(value)
                if not self.portfolio.is_flat(value):
                    self.close_all_positions(value)

    engine = BacktestEngine(config=BacktestEngineConfig(logging=LoggingConfig(log_level="ERROR")))
    fill_model = FillModel(
        prob_fill_on_limit=float(execution["prob_fill_on_limit"]),
        prob_slippage=float(execution["prob_slippage"]),
        random_seed=int(execution["random_seed"]),
    )
    strategy = CrossStrategy(CrossStrategyConfig(
        instrument_ids=tuple(instruments[symbol].id for symbol in SYMBOLS),
        bar_types=tuple(bar_types[symbol] for symbol in SYMBOLS),
        evaluation_start_ns=evaluation_start_ns,
        evaluation_end_ns=evaluation_end_ns,
        starting_nav=starting_nav,
    ))
    try:
        engine.add_venue(
            venue=venue,
            oms_type=OmsType.NETTING,
            account_type=AccountType.MARGIN,
            starting_balances=[Money(starting_nav, USDT)],
            base_currency=USDT,
            default_leverage=Decimal(account["default_leverage"]),
            fill_model=fill_model,
            bar_adaptive_high_low_ordering=bool(execution["bar_adaptive_high_low_ordering"]),
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
        cached = engine.cache.account_for_venue(venue)
        if cached is None:
            raise RuntimeError("Nautilus account unavailable after run")
        final_nav = decimal_value(cached.balance_total(USDT))
        metrics = calculate_metrics(
            starting_nav=starting_nav,
            final_nav=final_nav,
            evaluation_days=int(protocol["evaluation_days"]),
            positions=positions,
            plans=strategy.plans,
            lifecycle=strategy.lifecycle,
            detector=strategy.detector,
            errors=strategy.errors,
        )
        result = engine.get_result() if hasattr(engine, "get_result") else None
        metrics.update({
            "week_id": week_id,
            "evaluation_start": evaluation_start.isoformat(),
            "evaluation_end_exclusive": evaluation_end.isoformat(),
            "bars": len(all_bars),
            "instruments": [str(instruments[symbol].id) for symbol in SYMBOLS],
            "effective_maker_rate": execution["effective_maker_rate"],
            "effective_taker_rate": execution["effective_taker_rate"],
            "nautilus_result": {
                name: None if result is None else getattr(result, name, None)
                for name in ("iterations", "total_orders", "total_positions", "total_events")
            },
        })
        write_json(output_dir / "metrics.json", metrics)
        write_json(output_dir / "submitted_plans.json", {"plans": strategy.plans})
        write_json(output_dir / "order_lifecycle.json", {"events": strategy.lifecycle})
        with (output_dir / "scenario_events.jsonl").open("w", encoding="utf-8") as stream:
            for event in strategy.detector.events:
                stream.write(json.dumps(event, sort_keys=True, default=str) + "\n")
        write_json(output_dir / "run.json", {
            "schema": "candidate-11-cross-market-run-v1",
            "candidate": metrics["candidate"],
            "week_id": week_id,
            "protocol_path": str(protocol_path),
            "protocol_sha256": sha256(protocol_path.read_bytes()).hexdigest(),
            "project_config_sha256": sha256(project_config_path.read_bytes()).hexdigest(),
            "metrics_path": str(output_dir / "metrics.json"),
            "created_at": datetime.now(UTC).isoformat(),
            "success_claim": False,
        })
        return metrics
    finally:
        engine.dispose()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--protocol", type=Path, default=ROOT / "cross_market_protocol.json")
    parser.add_argument("--project-config", type=Path, default=ROOT / "config.json")
    parser.add_argument("--week", choices=("C1", "C2", "C3"), default="C1")
    parser.add_argument("--output", type=Path, default=ROOT / "results" / "CROSS_C1")
    args = parser.parse_args()
    metrics = run(
        protocol_path=args.protocol.resolve(),
        project_config_path=args.project_config.resolve(),
        week_id=args.week,
        output_dir=args.output.resolve(),
    )
    print(json.dumps(metrics, indent=2, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
