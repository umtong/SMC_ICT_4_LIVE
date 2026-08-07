#!/usr/bin/env python3
"""Reproducible Candidate 11 BTC evaluation through NautilusTrader.

The candidate module emits causal trade plans only. This runner is a thin
NautilusTrader adapter: it loads immutable public data, creates official model
objects, submits contingent orders, and exports engine-owned reports and NAV.
There is deliberately no substitute matching, fill, fee, position, or PnL loop.
"""
from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from hashlib import sha256
import json
from pathlib import Path
import re
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
from logic import BarObs, Direction, LogicConfig, RiskSizer, TradePlan
from session_engine import RegionalHandoffAuctionEngine

from smc_ict_4.event_log import write_events
from smc_ict_4.manifest import create_run_manifest, write_json_atomic

COLUMNS = (
    "open_time", "open", "high", "low", "close", "volume", "close_time",
    "quote_volume", "trades", "taker_buy_volume", "taker_buy_quote_volume", "ignore",
)


def _download(url: str, destination: Path, retries: int = 4) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.is_file() and destination.stat().st_size > 100:
        return
    request = Request(url, headers={"User-Agent": "SMC-ICT-4-candidate-11/2.0"})
    last_error: Exception | None = None
    for attempt in range(retries):
        try:
            with urlopen(request, timeout=60) as response:  # noqa: S310 - frozen HTTPS host
                payload = response.read()
            if len(payload) < 100:
                raise RuntimeError(f"unexpectedly small response from {url}")
            temporary = destination.with_suffix(destination.suffix + ".tmp")
            temporary.write_bytes(payload)
            with ZipFile(temporary) as archive:
                bad = archive.testzip()
                if bad is not None:
                    raise RuntimeError(f"corrupt ZIP member {bad}")
            temporary.replace(destination)
            return
        except Exception as exc:  # network errors are retained in the final exception
            last_error = exc
            if attempt + 1 < retries:
                time.sleep(2**attempt)
    raise RuntimeError(f"failed to download {url}: {last_error}")


def load_binance_bars(
    start: date,
    end_inclusive: date,
    data_dir: Path,
) -> tuple[pd.DataFrame, list[dict[str, Any]]]:
    """Load official BTCUSDT USD-M one-minute klines with close-time visibility."""
    frames: list[pd.DataFrame] = []
    manifest: list[dict[str, Any]] = []
    cursor = start
    while cursor <= end_inclusive:
        filename = f"BTCUSDT-1m-{cursor.isoformat()}.zip"
        url = f"https://data.binance.vision/data/futures/um/daily/klines/BTCUSDT/1m/{filename}"
        path = data_dir / filename
        _download(url, path)
        digest = sha256(path.read_bytes()).hexdigest()
        with ZipFile(path) as archive:
            members = archive.namelist()
            if len(members) != 1 or not members[0].lower().endswith(".csv"):
                raise RuntimeError(f"unexpected members in {filename}: {members}")
            with archive.open(members[0]) as stream:
                frame = pd.read_csv(stream, header=None, names=COLUMNS)
        frame = frame[pd.to_numeric(frame["open_time"], errors="coerce").notna()].copy()
        if len(frame.index) not in (1439, 1440, 1441):
            raise RuntimeError(f"unexpected row count {len(frame.index)} for {filename}")
        frames.append(frame)
        manifest.append(
            {
                "date": cursor.isoformat(),
                "url": url,
                "path": str(path),
                "bytes": path.stat().st_size,
                "sha256": digest,
                "rows": len(frame.index),
            },
        )
        cursor += timedelta(days=1)

    raw = pd.concat(frames, ignore_index=True)
    raw = raw.drop_duplicates(subset=["open_time"], keep="last")
    raw = raw.sort_values("open_time", kind="stable").reset_index(drop=True)
    open_time = pd.to_numeric(raw["open_time"], errors="raise")
    first = int(open_time.iloc[0])
    if 1_000_000_000_000 <= first < 10_000_000_000_000:
        unit = "ms"
    elif 1_000_000_000_000_000 <= first < 10_000_000_000_000_000:
        unit = "us"
    else:
        raise RuntimeError(f"unsupported Binance timestamp magnitude: {first}")
    # The archive timestamp is bar open. A complete OHLC/flow observation is
    # causal only at the end of the minute.
    index = pd.to_datetime(open_time, unit=unit, utc=True) + pd.Timedelta(minutes=1)
    values: dict[str, Any] = {}
    for name in ("open", "high", "low", "close", "volume", "taker_buy_volume"):
        values[name] = pd.to_numeric(raw[name], errors="raise").to_numpy(copy=True)
    frame = pd.DataFrame(values, index=index)
    if frame.index.has_duplicates or not frame.index.is_monotonic_increasing:
        raise RuntimeError("market-data timestamps are not strictly increasing and unique")
    if (frame["volume"] < 0).any() or (frame["taker_buy_volume"] < 0).any():
        raise RuntimeError("negative volume in source data")
    if (frame["taker_buy_volume"] > frame["volume"] + 1e-9).any():
        raise RuntimeError("taker-buy volume exceeds total volume")
    return frame, manifest


def _decimal(value: Any, default: Decimal | None = None) -> Decimal:
    if value is None:
        if default is None:
            raise ValueError("decimal value is unavailable")
        return default
    if hasattr(value, "as_decimal"):
        return Decimal(value.as_decimal())
    text = str(value).replace(",", "").strip()
    match = re.search(r"[-+]?\d+(?:\.\d+)?", text)
    if match is None:
        if default is None:
            raise ValueError(f"cannot parse decimal from {value!r}")
        return default
    return Decimal(match.group(0))


def _closed_pnls(positions: pd.DataFrame) -> list[Decimal]:
    if positions.empty:
        return []
    closed = positions
    for column in ("ts_closed", "closed_time", "close_time"):
        if column in closed.columns:
            closed = closed[closed[column].notna()]
            break
    pnl_column = next(
        (column for column in ("realized_pnl", "realized_return", "pnl") if column in closed.columns),
        None,
    )
    if pnl_column is None:
        return []
    return [_decimal(value, Decimal("0")) for value in closed[pnl_column].tolist()]


def calculate_metrics(
    *,
    starting_nav: Decimal,
    final_nav: Decimal,
    evaluation_days: int,
    positions: pd.DataFrame,
    orders: pd.DataFrame,
    plans: list[dict[str, Any]],
    logic: RegionalHandoffAuctionEngine,
    errors: list[dict[str, Any]],
    lifecycle: list[dict[str, Any]],
    gates: dict[str, Any],
) -> dict[str, Any]:
    pnls = _closed_pnls(positions)
    wins = [value for value in pnls if value > 0]
    losses = [value for value in pnls if value < 0]
    win_rate = len(wins) / len(pnls) if pnls else 0.0
    payoff_ratio: float | None = None
    if wins and losses:
        payoff_ratio = float((sum(wins) / len(wins)) / abs(sum(losses) / len(losses)))
    elif wins:
        payoff_ratio = float("inf")

    if final_nav > 0:
        daily_growth = float((final_nav / starting_nav) ** (Decimal(1) / Decimal(evaluation_days)) - Decimal(1))
    else:
        daily_growth = -1.0
    equity = starting_nav
    peak = starting_nav
    max_drawdown = Decimal("0")
    for pnl in pnls:
        equity += pnl
        peak = max(peak, equity)
        if peak > 0:
            max_drawdown = max(max_drawdown, (peak - equity) / peak)

    scenario_counts: dict[str, int] = {}
    for plan in plans:
        scenario = str(plan["scenario"])
        scenario_counts[scenario] = scenario_counts.get(scenario, 0) + 1

    liquidation_detected = any("LIQUIDAT" in json.dumps(item, default=str).upper() for item in lifecycle)
    promising = gates["promising"]
    promising_pass = (
        len(pnls) >= int(promising["min_closed_trades"])
        and win_rate >= float(promising["min_win_rate"])
        and payoff_ratio is not None
        and payoff_ratio >= float(promising["min_payoff_ratio"])
        and daily_growth >= float(promising["min_daily_geometric_growth"])
        and not errors
        and not liquidation_detected
    )
    complete = gates["complete"]
    complete_pass = (
        len(pnls) >= int(complete["min_closed_trades_per_week"])
        and win_rate >= float(complete["min_win_rate"])
        and payoff_ratio is not None
        and payoff_ratio >= float(complete["min_payoff_ratio"])
        and daily_growth >= float(complete["min_daily_geometric_growth"])
        and float(max_drawdown) <= float(complete["max_closed_trade_drawdown"])
        and final_nav > 0
        and not errors
        and not liquidation_detected
    )
    return {
        "evidence_class": "NAUTILUS_ACCOUNT_NAV",
        "starting_nav": str(starting_nav),
        "final_nav": str(final_nav),
        "net_return": float(final_nav / starting_nav - 1),
        "daily_geometric_growth": daily_growth,
        "evaluation_calendar_days": evaluation_days,
        "closed_trades": len(pnls),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": win_rate,
        "payoff_ratio": None if payoff_ratio == float("inf") else payoff_ratio,
        "all_closed_trades_won": bool(wins) and not losses,
        "closed_trade_max_drawdown": float(max_drawdown),
        "drawdown_scope": "closed-position realized PnL path; account NAV is authoritative",
        "submitted_plans": len(plans),
        "scenario_counts": scenario_counts,
        "detected_events": len(logic.events),
        "skip_reasons": dict(logic.skips),
        "order_report_rows": len(orders.index),
        "lifecycle_events": len(lifecycle),
        "liquidation_detected": liquidation_detected,
        "engine_errors": errors,
        "promising_gate_passed": promising_pass,
        "complete_gate_passed": complete_pass,
        "success_claim": complete_pass,
    }


def run(config_path: Path, week_id: str, output_dir: Path) -> dict[str, Any]:
    from nautilus_trader.backtest.engine import BacktestEngine
    from nautilus_trader.backtest.models import FillModel
    from nautilus_trader.config import BacktestEngineConfig, LoggingConfig, StrategyConfig
    from nautilus_trader.model.currencies import BTC, USDT
    from nautilus_trader.model.data import Bar, BarType
    from nautilus_trader.model.enums import AccountType, OmsType, OrderSide, OrderType, TimeInForce
    from nautilus_trader.model.events import OrderEvent
    from nautilus_trader.model.identifiers import InstrumentId, Symbol, Venue
    from nautilus_trader.model.instruments import CryptoPerpetual
    from nautilus_trader.model.objects import Money, Price, Quantity
    from nautilus_trader.trading.strategy import Strategy

    config = json.loads(config_path.read_text(encoding="utf-8"))
    if week_id not in config["selection"]["weeks"]:
        raise ValueError(f"unknown frozen week {week_id!r}")
    selected = config["selection"]["weeks"][week_id]
    evaluation_start = date.fromisoformat(selected["start"])
    evaluation_end = date.fromisoformat(selected["end_exclusive"])
    warmup_start = evaluation_start - timedelta(days=int(config["selection"]["warmup_days"]))
    output_dir.mkdir(parents=True, exist_ok=True)
    frame, source_files = load_binance_bars(warmup_start, evaluation_end, output_dir / "data")
    write_json_atomic(
        output_dir / "data_manifest.json",
        {
            "schema": "candidate-11-source-manifest-v2",
            "dataset": "Binance USD-M BTCUSDT one-minute daily klines",
            "bar_visibility": "archive open_time plus one minute",
            "selection_seed": config["selection"]["seed"],
            "warmup_start": warmup_start.isoformat(),
            "evaluation_start": evaluation_start.isoformat(),
            "evaluation_end_exclusive": evaluation_end.isoformat(),
            "files": source_files,
        },
    )

    account_config = config["account"]
    execution_config = config["execution"]
    instrument = CryptoPerpetual(
        instrument_id=InstrumentId(symbol=Symbol("BTCUSDT-PERP"), venue=Venue("BINANCE")),
        raw_symbol=Symbol("BTCUSDT"),
        base_currency=BTC,
        quote_currency=USDT,
        settlement_currency=USDT,
        is_inverse=False,
        price_precision=1,
        price_increment=Price.from_str("0.1"),
        size_precision=3,
        size_increment=Quantity.from_str("0.001"),
        max_quantity=Quantity.from_str("1000.000"),
        min_quantity=Quantity.from_str("0.001"),
        max_notional=None,
        min_notional=Money(10.0, USDT),
        max_price=Price.from_str("809484.0"),
        min_price=Price.from_str("261.1"),
        margin_init=Decimal(account_config["margin_init"]),
        margin_maint=Decimal(account_config["margin_maint"]),
        maker_fee=Decimal(execution_config["effective_maker_rate"]),
        taker_fee=Decimal(execution_config["effective_taker_rate"]),
        ts_event=0,
        ts_init=0,
    )
    bar_type = BarType.from_str("BTCUSDT-PERP.BINANCE-1-MINUTE-LAST-EXTERNAL")
    bars = build_bars(frame[["open", "high", "low", "close", "volume"]], bar_type, instrument)
    source_volumes = frame["volume"].astype(float).tolist()
    taker_buy_volumes = frame["taker_buy_volume"].astype(float).tolist()
    if len(bars) != len(taker_buy_volumes) or len(bars) != len(source_volumes):
        raise RuntimeError("bar and aggressor-flow streams are not aligned")

    logic_config = LogicConfig(**config["logic"])
    evaluation_start_ns = int(pd.Timestamp(evaluation_start, tz="UTC").value)
    evaluation_end_ns = int(pd.Timestamp(evaluation_end, tz="UTC").value)
    starting_nav = Decimal(account_config["starting_nav"])

    class CandidateStrategyConfig(StrategyConfig, frozen=True):
        instrument_id: InstrumentId
        bar_type: BarType
        evaluation_start_ns: int
        evaluation_end_ns: int
        starting_nav: Decimal

    class CandidateStrategy(Strategy):
        def __init__(self, strategy_config: CandidateStrategyConfig) -> None:
            super().__init__(strategy_config)
            self.logic = RegionalHandoffAuctionEngine(logic_config, str(strategy_config.instrument_id))
            self.sizer = RiskSizer(logic_config.risk_fraction)
            self.flow_index = 0
            self.plans: list[dict[str, Any]] = []
            self.errors: list[dict[str, Any]] = []
            self.lifecycle: list[dict[str, Any]] = []
            self.last_ts_ns = 0

        def on_start(self) -> None:
            self.subscribe_bars(self.config.bar_type)

        def _open_orders(self) -> int:
            return int(
                self.cache.orders_open_count(
                    instrument_id=self.config.instrument_id,
                    strategy_id=self.id,
                ),
            )

        def _account_values(self) -> tuple[Decimal, Decimal]:
            account = self.cache.account_for_venue(self.config.instrument_id.venue)
            if account is None:
                return self.config.starting_nav, self.config.starting_nav
            total = _decimal(account.balance_total(USDT))
            free = _decimal(account.balance_free(USDT), total) if hasattr(account, "balance_free") else total
            return total, free

        def _terminal_if_flat(self, ts_ns: int, reason: str) -> None:
            if (
                self.logic.active_trade_id is not None
                and self.portfolio.is_flat(self.config.instrument_id)
                and self._open_orders() == 0
            ):
                self.logic.mark_trade_terminal(ts_ns, reason)

        def _submit_plan(self, plan: TradePlan) -> None:
            if not self.portfolio.is_flat(self.config.instrument_id) or self._open_orders() > 0:
                self.logic.mark_rejected(plan, self.last_ts_ns, "GLOBAL_SLOT_OCCUPIED")
                return
            nav, free_balance = self._account_values()
            decision = self.sizer.size(
                nav=nav,
                loss_per_unit=Decimal(str(plan.loss_per_unit)),
                entry_price=Decimal(str(plan.expected_entry)),
                quantity_increment=Decimal(str(instrument.size_increment)),
                min_quantity=Decimal(str(instrument.min_quantity)),
                min_notional=_decimal(instrument.min_notional),
                margin_init=instrument.margin_init,
                free_balance=free_balance,
            )
            if not decision.feasible:
                self.logic.mark_rejected(
                    plan,
                    self.last_ts_ns,
                    decision.reason,
                    {
                        "required_margin": str(decision.required_margin),
                        "free_balance": str(free_balance),
                    },
                )
                return

            side = OrderSide.BUY if plan.direction == Direction.LONG else OrderSide.SELL
            try:
                order_list = self.order_factory.bracket(
                    instrument_id=self.config.instrument_id,
                    order_side=side,
                    quantity=instrument.make_qty(decision.quantity),
                    entry_order_type=OrderType.LIMIT,
                    entry_price=instrument.make_price(plan.expected_entry),
                    expire_time=plan.expire_ts_ns,
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
                record = {
                    "type": "ORDER_LIST_SUBMISSION_EXCEPTION",
                    "ts_ns": self.last_ts_ns,
                    "exception": type(exc).__name__,
                    "message": str(exc),
                }
                self.errors.append(record)
                self.logic.mark_rejected(plan, self.last_ts_ns, record["type"], record)
                return

            record = {
                "scenario_id": plan.scenario_id,
                "scenario": plan.scenario.value,
                "direction": plan.direction.value,
                "observed_ts_ns": plan.observed_ts_ns,
                "entry_order_type": plan.entry_order_type,
                "entry_post_only": plan.entry_post_only,
                "expire_ts_ns": plan.expire_ts_ns,
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
            }
            self.plans.append(record)
            self.logic.mark_submitted(plan, decision.quantity, record)

        def on_bar(self, bar: Bar) -> None:
            self.last_ts_ns = int(bar.ts_event)
            self._terminal_if_flat(self.last_ts_ns, "NAUTILUS_FLAT_NO_WORKING_ORDERS")
            if self.last_ts_ns >= self.config.evaluation_end_ns:
                if self._open_orders() > 0:
                    self.cancel_all_orders(self.config.instrument_id)
                if not self.portfolio.is_flat(self.config.instrument_id):
                    self.close_all_positions(self.config.instrument_id)
                return
            if self.flow_index >= len(taker_buy_volumes):
                raise RuntimeError("aggressor-flow stream exhausted before Nautilus bars")
            taker_buy = taker_buy_volumes[self.flow_index]
            self.flow_index += 1
            observation = BarObs(
                ts_ns=self.last_ts_ns,
                open=float(str(bar.open)),
                high=float(str(bar.high)),
                low=float(str(bar.low)),
                close=float(str(bar.close)),
                volume=source_volumes[self.flow_index - 1],
                taker_buy_volume=taker_buy,
            )
            plan = self.logic.on_bar(
                observation,
                allow_entry=self.last_ts_ns >= self.config.evaluation_start_ns,
            )
            if plan is not None:
                self._submit_plan(plan)

        def _record_order_event(self, event: OrderEvent, kind: str) -> None:
            self.lifecycle.append(
                {
                    "type": kind,
                    "ts_event": int(event.ts_event),
                    "client_order_id": str(event.client_order_id),
                    "event": str(event),
                },
            )

        def on_order_filled(self, event: Any) -> None:
            self._record_order_event(event, "ORDER_FILLED")
            self.logic.mark_entry_filled(
                int(event.ts_event),
                {"client_order_id": str(event.client_order_id), "event": str(event)},
            )

        def on_order_expired(self, event: Any) -> None:
            self._record_order_event(event, "ORDER_EXPIRED")
            self._terminal_if_flat(int(event.ts_event), "GTD_ENTRY_EXPIRED_UNFILLED")

        def on_order_canceled(self, event: Any) -> None:
            self._record_order_event(event, "ORDER_CANCELED")
            self._terminal_if_flat(int(event.ts_event), "ORDERS_CANCELED_FLAT")

        def on_order_denied(self, event: Any) -> None:
            self._record_order_event(event, "ORDER_DENIED")
            self.errors.append({"type": "ORDER_DENIED", "event": str(event)})
            self._terminal_if_flat(int(event.ts_event), "ORDER_DENIED")

        def on_order_rejected(self, event: Any) -> None:
            self._record_order_event(event, "ORDER_REJECTED")
            self.errors.append({"type": "ORDER_REJECTED", "event": str(event)})
            self._terminal_if_flat(int(event.ts_event), "ORDER_REJECTED")

        def on_stop(self) -> None:
            self.cancel_all_orders(self.config.instrument_id)
            if not self.portfolio.is_flat(self.config.instrument_id):
                self.close_all_positions(self.config.instrument_id)

    venue = Venue("BINANCE")
    engine = BacktestEngine(config=BacktestEngineConfig(logging=LoggingConfig(log_level="ERROR")))
    fill_model = FillModel(
        prob_fill_on_limit=float(execution_config["prob_fill_on_limit"]),
        prob_slippage=float(execution_config["prob_slippage"]),
        random_seed=int(execution_config["random_seed"]),
    )
    strategy = CandidateStrategy(
        CandidateStrategyConfig(
            instrument_id=instrument.id,
            bar_type=bar_type,
            evaluation_start_ns=evaluation_start_ns,
            evaluation_end_ns=evaluation_end_ns,
            starting_nav=starting_nav,
        ),
    )
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
            raise RuntimeError("Nautilus margin account is unavailable after the run")
        final_nav = _decimal(account.balance_total(USDT))
        metrics = calculate_metrics(
            starting_nav=starting_nav,
            final_nav=final_nav,
            evaluation_days=int(config["selection"]["evaluation_days"]),
            positions=positions,
            orders=orders,
            plans=strategy.plans,
            logic=strategy.logic,
            errors=strategy.errors,
            lifecycle=strategy.lifecycle,
            gates=config["gates"],
        )
        result = engine.get_result() if hasattr(engine, "get_result") else None
        metrics.update(
            {
                "week_id": week_id,
                "evaluation_start": evaluation_start.isoformat(),
                "evaluation_end_exclusive": evaluation_end.isoformat(),
                "bars": len(bars),
                "instrument": str(instrument.id),
                "effective_maker_rate": str(instrument.maker_fee),
                "effective_taker_rate": str(instrument.taker_fee),
                "nautilus_result": {
                    name: None if result is None else getattr(result, name, None)
                    for name in ("iterations", "total_orders", "total_positions", "total_events")
                },
            },
        )
        write_events(output_dir / "scenario_events.jsonl", strategy.logic.events)
        write_json_atomic(output_dir / "submitted_plans.json", {"plans": strategy.plans})
        write_json_atomic(output_dir / "order_lifecycle.json", {"events": strategy.lifecycle})
        write_json_atomic(output_dir / "metrics.json", metrics)
        manifest = create_run_manifest(
            run_id=f"candidate-11-{week_id.lower()}-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}",
            candidate=config["candidate"],
            config_path=config_path,
            data_manifest_path=output_dir / "data_manifest.json",
            extra={
                "week_id": week_id,
                "bar_type": str(bar_type),
                "evaluation_start": evaluation_start.isoformat(),
                "evaluation_end_exclusive": evaluation_end.isoformat(),
                "logic": config["logic"],
                "execution": config["execution"],
                "metrics_path": str(output_dir / "metrics.json"),
            },
        )
        write_json_atomic(output_dir / "run.json", manifest)
        return metrics
    finally:
        engine.dispose()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=ROOT / "config.json")
    parser.add_argument("--week", choices=("W1", "W2", "W3"), default="W1")
    parser.add_argument("--output", type=Path, default=ROOT / "results" / "W1")
    args = parser.parse_args()
    metrics = run(args.config.resolve(), args.week, args.output.resolve())
    print(json.dumps(metrics, indent=2, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
