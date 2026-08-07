#!/usr/bin/env python3
"""Run the BTC aggressor-impact auction through NautilusTrader.

Binance aggregate trades are reduced to causally completed one-second OHLCV and
signed aggressor-flow bars.  NautilusTrader exclusively owns order matching,
partial fills, contingent orders, fees, margin, positions, and account NAV.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
import sys
import time
from typing import Any
from urllib.request import Request, urlopen
from zipfile import ZipFile

import pandas as pd

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bar_adapter import build_bars
from logic import RiskSizer
from microstructure import AggressorImpactAuctionEngine, FlowBar, MicroPlan

UTC = timezone.utc
SYMBOL = "BTCUSDT"
AGG_COLUMNS = (
    "agg_trade_id", "price", "quantity", "first_trade_id",
    "last_trade_id", "transact_time", "is_buyer_maker",
)


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    temporary.replace(path)


def download(url: str, destination: Path, retries: int = 5) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists() and destination.stat().st_size > 100:
        try:
            with ZipFile(destination) as archive:
                if archive.testzip() is None:
                    return
        except Exception:
            destination.unlink(missing_ok=True)
    request = Request(url, headers={"User-Agent": "SMC-ICT-4-candidate-11-microstructure"})
    error: Exception | None = None
    for attempt in range(retries):
        try:
            with urlopen(request, timeout=180) as response:  # noqa: S310 fixed HTTPS archive host
                payload = response.read()
            if len(payload) < 100:
                raise RuntimeError(f"small archive response: {url}")
            temporary = destination.with_suffix(destination.suffix + ".tmp")
            temporary.write_bytes(payload)
            with ZipFile(temporary) as archive:
                bad = archive.testzip()
                if bad is not None:
                    raise RuntimeError(f"corrupt ZIP member: {bad}")
            temporary.replace(destination)
            return
        except Exception as exc:
            error = exc
            if attempt + 1 < retries:
                time.sleep(2**attempt)
    raise RuntimeError(f"failed to download {url}: {error}")


def timestamp_unit(value: int) -> str:
    magnitude = abs(int(value))
    if 1_000_000_000_000 <= magnitude < 10_000_000_000_000:
        return "ms"
    if 1_000_000_000_000_000 <= magnitude < 10_000_000_000_000_000:
        return "us"
    if magnitude >= 1_000_000_000_000_000_000:
        return "ns"
    raise ValueError(f"unsupported timestamp magnitude: {value}")


def parse_bool(series: pd.Series) -> pd.Series:
    if series.dtype == bool:
        return series
    return series.astype(str).str.strip().str.lower().isin({"true", "1", "t", "yes"})


def aggregate_day(path: Path) -> pd.DataFrame:
    with ZipFile(path) as archive:
        members = archive.namelist()
        if len(members) != 1:
            raise RuntimeError(f"unexpected archive members for {path.name}: {members}")
        payload = archive.read(members[0])
    frame = pd.read_csv(BytesIO(payload))
    if not set(AGG_COLUMNS).issubset(frame.columns):
        frame = pd.read_csv(BytesIO(payload), header=None, names=AGG_COLUMNS)
    frame = frame.loc[:, AGG_COLUMNS].copy()
    for name in ("price", "quantity", "transact_time"):
        frame[name] = pd.to_numeric(frame[name], errors="coerce")
    frame = frame.dropna(subset=["price", "quantity", "transact_time"])
    frame = frame[(frame["price"] > 0) & (frame["quantity"] > 0)]
    if frame.empty:
        raise RuntimeError(f"empty aggregate-trade archive: {path.name}")
    unit = timestamp_unit(int(frame["transact_time"].iloc[0]))
    frame["second"] = pd.to_datetime(frame["transact_time"].astype("int64"), unit=unit, utc=True).dt.floor("s")
    buyer_maker = parse_bool(frame["is_buyer_maker"])
    frame["quote"] = frame["price"] * frame["quantity"]
    frame["buy_volume"] = frame["quantity"].where(~buyer_maker, 0.0)
    frame["sell_volume"] = frame["quantity"].where(buyer_maker, 0.0)
    frame["signed_notional"] = frame["quote"].where(~buyer_maker, -frame["quote"])
    frame["trade_notional"] = frame["quote"]
    grouped = frame.groupby("second", sort=True, observed=True)
    result = grouped.agg(
        open=("price", "first"),
        high=("price", "max"),
        low=("price", "min"),
        close=("price", "last"),
        volume=("quantity", "sum"),
        buy_volume=("buy_volume", "sum"),
        sell_volume=("sell_volume", "sum"),
        quote_notional=("quote", "sum"),
        signed_notional=("signed_notional", "sum"),
        trade_count=("price", "size"),
        max_trade_notional=("trade_notional", "max"),
    )
    result.index = result.index + pd.Timedelta(seconds=1)
    return result


def load_one_second_bars(
    start: date,
    end_inclusive: date,
    data_dir: Path,
) -> tuple[pd.DataFrame, list[dict[str, Any]]]:
    frames: list[pd.DataFrame] = []
    manifest: list[dict[str, Any]] = []
    cursor = start
    while cursor <= end_inclusive:
        filename = f"{SYMBOL}-aggTrades-{cursor.isoformat()}.zip"
        url = f"https://data.binance.vision/data/futures/um/daily/aggTrades/{SYMBOL}/{filename}"
        path = data_dir / filename
        download(url, path)
        digest = sha256(path.read_bytes()).hexdigest()
        aggregate = aggregate_day(path)
        frames.append(aggregate)
        manifest.append({
            "symbol": SYMBOL,
            "date": cursor.isoformat(),
            "url": url,
            "path": str(path),
            "bytes": path.stat().st_size,
            "sha256": digest,
            "aggregate_trade_rows": int(aggregate["trade_count"].sum()),
            "one_second_rows_with_trades": len(aggregate.index),
        })
        cursor += timedelta(days=1)

    frame = pd.concat(frames).sort_index(kind="stable")
    frame = frame[~frame.index.duplicated(keep="last")]
    start_index = pd.Timestamp(start, tz="UTC") + pd.Timedelta(seconds=1)
    end_index = pd.Timestamp(end_inclusive + timedelta(days=1), tz="UTC")
    index = pd.date_range(start_index, end_index, freq="1s", tz="UTC")
    frame = frame.reindex(index)
    frame["close"] = frame["close"].ffill().bfill()
    for name in ("open", "high", "low"):
        frame[name] = frame[name].fillna(frame["close"])
    for name in (
        "volume", "buy_volume", "sell_volume", "quote_notional",
        "signed_notional", "trade_count", "max_trade_notional",
    ):
        frame[name] = frame[name].fillna(0.0)
    if not frame.index.is_monotonic_increasing or frame.index.has_duplicates:
        raise RuntimeError("invalid one-second aggregate index")
    return frame, manifest


def decimal_value(value: Any, default: Decimal = Decimal("0")) -> Decimal:
    if value is None:
        return default
    text = str(value).strip()
    if not text or text.lower() in {"nan", "none", "nat"}:
        return default
    return Decimal(text.split()[0].replace(",", ""))


def closed_pnls(positions: pd.DataFrame) -> list[Decimal]:
    if positions.empty:
        return []
    column = next((name for name in ("realized_pnl", "pnl") if name in positions.columns), None)
    if column is None:
        return []
    return [decimal_value(value) for value in positions[column].tolist()]


def calculate_metrics(
    *,
    starting_nav: Decimal,
    final_nav: Decimal,
    evaluation_days: int,
    positions: pd.DataFrame,
    plans: list[dict[str, Any]],
    lifecycle: list[dict[str, Any]],
    detector: AggressorImpactAuctionEngine,
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
    max_drawdown = Decimal(0)
    for pnl in pnls:
        equity += pnl
        peak = max(peak, equity)
        if peak > 0:
            max_drawdown = max(max_drawdown, (peak - equity) / peak)
    growth = (
        float((final_nav / starting_nav) ** (Decimal(1) / Decimal(evaluation_days)) - Decimal(1))
        if final_nav > 0
        else -1.0
    )
    liquidation = any("LIQUIDAT" in json.dumps(value, default=str).upper() for value in lifecycle)
    return {
        "candidate": "candidate-11-btc-aggressor-impact-auction",
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
        "closed_trade_max_drawdown": float(max_drawdown),
        "submitted_plans": len(plans),
        "scenario_counts": dict(Counter(plan["scenario"] for plan in plans)),
        "detector_event_counts": dict(Counter(event["type"] for event in detector.events)),
        "skip_reasons": dict(detector.skips),
        "engine_errors": errors,
        "partial_entry_fail_closed_count": sum(
            value.get("type") == "PARTIAL_ENTRY_EXPIRED_FAIL_CLOSED"
            for value in lifecycle
        ),
        "liquidation_detected": liquidation,
        "global_slot_overlap_count": 0,
        "success_claim": False,
    }


def run(
    *,
    protocol_path: Path,
    week_id: str,
    output_dir: Path,
    data_dir: Path,
    project_config_path: Path,
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
    selected = protocol["weeks"][week_id]
    evaluation_start = date.fromisoformat(selected["start"])
    evaluation_end = date.fromisoformat(selected["end_exclusive"])
    warmup_start = evaluation_start - timedelta(days=int(protocol["warmup_days"]))
    project_config = json.loads(project_config_path.read_text(encoding="utf-8"))
    account_config = project_config["account"]
    execution_config = project_config["execution"]
    output_dir.mkdir(parents=True, exist_ok=True)
    data_dir.mkdir(parents=True, exist_ok=True)

    frame, files = load_one_second_bars(warmup_start, evaluation_end, data_dir)
    write_json(output_dir / "data_manifest.json", {
        "schema": "candidate-11-aggtrades-source-manifest-v1",
        "dataset": "Binance USD-M daily aggregate trades",
        "symbol": SYMBOL,
        "bar_visibility": "aggregate trade timestamp floored to second plus one second",
        "warmup_start": warmup_start.isoformat(),
        "evaluation_start": evaluation_start.isoformat(),
        "evaluation_end_exclusive": evaluation_end.isoformat(),
        "files": files,
        "one_second_rows": len(frame.index),
    })

    base_currency = Currency.from_str("BTC", strict=False)
    if base_currency is None:
        raise RuntimeError("BTC currency unavailable")
    venue = Venue("BINANCE")
    instrument_id = InstrumentId(symbol=Symbol("BTCUSDT-PERP"), venue=venue)
    instrument = CryptoPerpetual(
        instrument_id=instrument_id,
        raw_symbol=Symbol(SYMBOL),
        base_currency=base_currency,
        quote_currency=USDT,
        settlement_currency=USDT,
        is_inverse=False,
        price_precision=1,
        price_increment=Price.from_str("0.1"),
        size_precision=3,
        size_increment=Quantity.from_str("0.001"),
        max_quantity=Quantity.from_str("1000000"),
        min_quantity=Quantity.from_str("0.001"),
        max_notional=None,
        min_notional=Money(5, USDT),
        max_price=Price.from_str("10000000"),
        min_price=Price.from_str("0.1"),
        margin_init=Decimal(account_config["margin_init"]),
        margin_maint=Decimal(account_config["margin_maint"]),
        maker_fee=Decimal(execution_config["effective_maker_rate"]),
        taker_fee=Decimal(execution_config["effective_taker_rate"]),
        ts_event=0,
        ts_init=0,
    )
    bar_type = BarType.from_str("BTCUSDT-PERP.BINANCE-1-SECOND-LAST-EXTERNAL")
    bars = build_bars(frame[["open", "high", "low", "close", "volume"]], bar_type, instrument)
    flow = {
        int(ts.value): {
            "buy_volume": float(row.buy_volume),
            "sell_volume": float(row.sell_volume),
            "quote_notional": float(row.quote_notional),
            "signed_notional": float(row.signed_notional),
            "trade_count": int(row.trade_count),
            "max_trade_notional": float(row.max_trade_notional),
        }
        for ts, row in frame[[
            "buy_volume", "sell_volume", "quote_notional", "signed_notional",
            "trade_count", "max_trade_notional",
        ]].iterrows()
    }

    starting_nav = Decimal(account_config["starting_nav"])
    evaluation_start_ns = int(pd.Timestamp(evaluation_start, tz="UTC").value)
    evaluation_end_ns = int(pd.Timestamp(evaluation_end, tz="UTC").value)

    class MicroStrategyConfig(StrategyConfig, frozen=True):
        instrument_id: InstrumentId
        bar_type: BarType
        evaluation_start_ns: int
        evaluation_end_ns: int
        starting_nav: Decimal

    class MicroStrategy(Strategy):
        def __init__(self, strategy_config: MicroStrategyConfig) -> None:
            super().__init__(strategy_config)
            self.detector = AggressorImpactAuctionEngine(
                str(instrument.id),
                effective_maker_rate=float(execution_config["effective_maker_rate"]),
                effective_taker_rate=float(execution_config["effective_taker_rate"]),
                minimum_net_r=1.25,
            )
            self.sizer = RiskSizer(Decimal(str(account_config["risk_fraction"])))
            self.active_plan: MicroPlan | None = None
            self.entry_pending = False
            self.position_open = False
            self.plans: list[dict[str, Any]] = []
            self.lifecycle: list[dict[str, Any]] = []
            self.errors: list[dict[str, Any]] = []
            self.last_ts_ns = 0

        def on_start(self) -> None:
            self.subscribe_bars(self.config.bar_type)

        def _open_orders(self) -> int:
            return int(self.cache.orders_open_count(instrument_id=instrument.id, strategy_id=self.id))

        def _account_values(self) -> tuple[Decimal, Decimal]:
            account = self.cache.account_for_venue(venue)
            if account is None:
                return self.config.starting_nav, self.config.starting_nav
            total = decimal_value(account.balance_total(USDT))
            free = decimal_value(account.balance_free(USDT), total) if hasattr(account, "balance_free") else total
            return total, free

        def _release_terminal(self, ts_ns: int, reason: str) -> None:
            if self.active_plan is None:
                return
            flat = self.portfolio.is_flat(instrument.id)
            open_orders = self._open_orders()
            if self.entry_pending and not flat:
                self.entry_pending = False
                self.position_open = True
                self.detector.mark_entry_filled(ts_ns)
                self.lifecycle.append({"type": "ENTRY_FILLED", "ts_event": ts_ns, "scenario_id": self.active_plan.scenario_id})
            elif self.entry_pending and flat and open_orders == 0:
                self.detector.mark_terminal(ts_ns, reason)
                self.entry_pending = False
                self.active_plan = None
            elif self.position_open and flat and open_orders == 0:
                self.detector.mark_terminal(ts_ns, reason)
                self.lifecycle.append({"type": "POSITION_CLOSED", "ts_event": ts_ns, "scenario_id": self.active_plan.scenario_id})
                self.position_open = False
                self.active_plan = None

        def _submit(self, plan: MicroPlan) -> None:
            if self.active_plan is not None or not self.portfolio.is_flat(instrument.id) or self._open_orders() > 0:
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
                    "exception": type(exc).__name__,
                    "message": str(exc),
                })
                self.detector.mark_rejected(plan, self.last_ts_ns, "ORDER_LIST_SUBMISSION_EXCEPTION")
                return
            self.detector.mark_submitted(plan)
            self.active_plan = plan
            self.entry_pending = True
            self.plans.append({
                "scenario_id": plan.scenario_id,
                "scenario": plan.scenario,
                "direction": plan.direction,
                "observed_ts_ns": plan.observed_ts_ns,
                "entry": plan.expected_entry,
                "stop": plan.stop_price,
                "target": plan.target_price,
                "net_r": plan.net_r,
                "quantity": str(decision.quantity),
                "nav_before": str(nav),
                "planned_loss_budget": str(decision.planned_loss_budget),
                "expected_total_loss": str(decision.expected_total_loss),
                "required_margin": str(decision.required_margin),
                "details": plan.details,
            })
            self.lifecycle.append({"type": "ENTRY_SUBMITTED", "ts_event": self.last_ts_ns, "scenario_id": plan.scenario_id})

        def on_bar(self, bar: Bar) -> None:
            self.last_ts_ns = int(bar.ts_event)
            self._release_terminal(self.last_ts_ns, "BAR_TERMINAL_SYNC")
            if self.last_ts_ns >= self.config.evaluation_end_ns:
                if self._open_orders() > 0:
                    self.cancel_all_orders(instrument.id)
                if not self.portfolio.is_flat(instrument.id):
                    self.close_all_positions(instrument.id)
                return
            values = flow.get(self.last_ts_ns)
            if values is None:
                self.errors.append({"type": "MISSING_FLOW_BAR", "ts_ns": self.last_ts_ns})
                return
            observation = FlowBar(
                ts_ns=self.last_ts_ns,
                open=float(str(bar.open)),
                high=float(str(bar.high)),
                low=float(str(bar.low)),
                close=float(str(bar.close)),
                volume=float(str(bar.volume)),
                buy_volume=values["buy_volume"],
                sell_volume=values["sell_volume"],
                quote_notional=values["quote_notional"],
                signed_notional=values["signed_notional"],
                trade_count=values["trade_count"],
                max_trade_notional=values["max_trade_notional"],
            )
            plan = self.detector.on_bar(observation)
            if plan is None:
                return
            if self.last_ts_ns < self.config.evaluation_start_ns:
                self.detector.mark_rejected(plan, self.last_ts_ns, "OUTSIDE_EVALUATION_WINDOW")
                return
            self._submit(plan)

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
            if self.position_open and not self.portfolio.is_flat(instrument.id):
                self.lifecycle.append({
                    "type": "PARTIAL_ENTRY_EXPIRED_FAIL_CLOSED",
                    "ts_event": int(event.ts_event),
                    "scenario_id": None if self.active_plan is None else self.active_plan.scenario_id,
                })
                if self._open_orders() > 0:
                    self.cancel_all_orders(instrument.id)
                self.close_all_positions(instrument.id)
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
            if self._open_orders() > 0:
                self.cancel_all_orders(instrument.id)
            if not self.portfolio.is_flat(instrument.id):
                self.close_all_positions(instrument.id)

    engine = BacktestEngine(config=BacktestEngineConfig(logging=LoggingConfig(log_level="ERROR")))
    fill_model = FillModel(
        prob_fill_on_limit=float(execution_config["prob_fill_on_limit"]),
        prob_slippage=float(execution_config["prob_slippage"]),
        random_seed=int(execution_config["random_seed"]),
    )
    strategy = MicroStrategy(MicroStrategyConfig(
        instrument_id=instrument.id,
        bar_type=bar_type,
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
            default_leverage=Decimal(account_config["default_leverage"]),
            fill_model=fill_model,
            bar_adaptive_high_low_ordering=bool(execution_config["bar_adaptive_high_low_ordering"]),
        )
        engine.add_instrument(instrument)
        engine.add_data(bars)
        engine.add_strategy(strategy)
        engine.run()
        orders = engine.trader.generate_order_fills_report()
        positions = engine.trader.generate_positions_report()
        account_report = engine.trader.generate_account_report(venue)
        orders.to_csv(output_dir / "orders.csv", index=False)
        positions.to_csv(output_dir / "positions.csv", index=False)
        account_report.to_csv(output_dir / "account.csv", index=False)
        account = engine.cache.account_for_venue(venue)
        if account is None:
            raise RuntimeError("Nautilus account unavailable")
        final_nav = decimal_value(account.balance_total(USDT))
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
            "one_second_bars": len(bars),
            "effective_maker_rate": execution_config["effective_maker_rate"],
            "effective_taker_rate": execution_config["effective_taker_rate"],
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
            "schema": "candidate-11-microstructure-run-v1",
            "candidate": metrics["candidate"],
            "week_id": week_id,
            "protocol_path": str(protocol_path),
            "protocol_sha256": sha256(protocol_path.read_bytes()).hexdigest(),
            "project_config_sha256": sha256(project_config_path.read_bytes()).hexdigest(),
            "metrics_path": str(output_dir / "metrics.json"),
            "success_claim": False,
            "created_at": datetime.now(UTC).isoformat(),
        })
        return metrics
    finally:
        engine.dispose()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--protocol", type=Path, default=ROOT / "microstructure_protocol.json")
    parser.add_argument("--week", choices=("M1", "M2", "M3"), default="M1")
    parser.add_argument("--output", type=Path, default=ROOT / "results" / "MICRO_M1")
    parser.add_argument("--data-dir", type=Path, default=Path("/tmp/candidate-11-microstructure-data"))
    parser.add_argument("--project-config", type=Path, default=ROOT / "config.json")
    args = parser.parse_args()
    metrics = run(
        protocol_path=args.protocol.resolve(),
        week_id=args.week,
        output_dir=args.output.resolve(),
        data_dir=args.data_dir.resolve(),
        project_config_path=args.project_config.resolve(),
    )
    print(json.dumps(metrics, indent=2, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
