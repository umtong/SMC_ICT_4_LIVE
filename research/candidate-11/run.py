#!/usr/bin/env python3
"""Reproducible Candidate 11 BTC weekly evaluation through NautilusTrader."""
from __future__ import annotations

import argparse
import csv
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

sys.path.insert(0, str(Path(__file__).parent))
from logic import BarObs, CausalAuctionEngine, Direction, LogicConfig, RiskSizer

from smc_ict_4.event_log import write_events
from smc_ict_4.manifest import create_run_manifest, write_json_atomic


COLUMNS = [
    "open_time", "open", "high", "low", "close", "volume", "close_time",
    "quote_volume", "trades", "taker_buy_volume", "taker_buy_quote_volume", "ignore",
]


def _download(url: str, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    for attempt in range(4):
        try:
            req = Request(url, headers={"User-Agent": "SMC-ICT-4-candidate-11/1.0"})
            with urlopen(req, timeout=60) as response:  # noqa: S310 - fixed HTTPS source
                payload = response.read()
            if len(payload) < 100:
                raise RuntimeError(f"unexpectedly small response from {url}")
            destination.write_bytes(payload)
            with ZipFile(destination) as archive:
                bad = archive.testzip()
                if bad:
                    raise RuntimeError(f"corrupt ZIP member {bad}")
            return
        except Exception:
            if attempt == 3:
                raise
            time.sleep(2 ** attempt)


def load_binance_bars(start: date, end_inclusive: date, data_dir: Path) -> tuple[pd.DataFrame, list[dict[str, Any]]]:
    frames: list[pd.DataFrame] = []
    manifest: list[dict[str, Any]] = []
    cursor = start
    while cursor <= end_inclusive:
        name = f"BTCUSDT-1m-{cursor.isoformat()}.zip"
        url = f"https://data.binance.vision/data/futures/um/daily/klines/BTCUSDT/1m/{name}"
        path = data_dir / name
        if not path.exists():
            _download(url, path)
        digest = sha256(path.read_bytes()).hexdigest()
        with ZipFile(path) as archive:
            members = archive.namelist()
            if len(members) != 1:
                raise RuntimeError(f"unexpected members in {name}: {members}")
            with archive.open(members[0]) as stream:
                frame = pd.read_csv(stream, header=None, names=COLUMNS)
        frame = frame[pd.to_numeric(frame["open_time"], errors="coerce").notna()].reset_index(drop=True)
        if len(frame) not in (1439, 1440, 1441):
            raise RuntimeError(f"unexpected row count {len(frame)} for {name}")
        frames.append(frame)
        manifest.append({"date": cursor.isoformat(), "url": url, "path": str(path), "bytes": path.stat().st_size, "sha256": digest, "rows": len(frame)})
        cursor += timedelta(days=1)

    raw = pd.concat(frames, ignore_index=True)
    open_time = pd.to_numeric(raw["open_time"], errors="raise")
    unit = "us" if int(open_time.iloc[0]) > 10**14 else "ms"
    # Binance timestamps identify bar opening. Shift one minute so the completed
    # OHLC/taker-flow observation only becomes visible at bar close.
    index = pd.to_datetime(open_time, unit=unit, utc=True) + pd.Timedelta(minutes=1)
    frame = pd.DataFrame(
        {
            "open": pd.to_numeric(raw["open"], errors="raise").to_numpy(),
            "high": pd.to_numeric(raw["high"], errors="raise").to_numpy(),
            "low": pd.to_numeric(raw["low"], errors="raise").to_numpy(),
            "close": pd.to_numeric(raw["close"], errors="raise").to_numpy(),
            "volume": pd.to_numeric(raw["volume"], errors="raise").to_numpy(),
            "taker_buy_volume": pd.to_numeric(raw["taker_buy_volume"], errors="raise").to_numpy(),
        },
        index=index,
    )
    if not frame.index.is_monotonic_increasing or frame.index.has_duplicates:
        raise RuntimeError("Binance bar timestamps are not strictly monotonic")
    return frame, manifest


def _number(value: Any) -> Decimal:
    if value is None:
        return Decimal("0")
    text = str(value).replace(",", "").strip()
    if not text or text.lower() in {"nan", "none", "nat"}:
        return Decimal("0")
    return Decimal(text.split()[0])


def _closed_pnls(positions: pd.DataFrame) -> list[Decimal]:
    if positions.empty:
        return []
    closed = positions
    for candidate in ("ts_closed", "closed_time", "close_time"):
        if candidate in closed.columns:
            closed = closed[closed[candidate].notna()]
            break
    column = next((c for c in ("realized_pnl", "realized_return", "pnl") if c in closed.columns), None)
    if column is None:
        return []
    return [_number(value) for value in closed[column].tolist()]


def calculate_metrics(starting_nav: Decimal, final_nav: Decimal, days: int, positions: pd.DataFrame, plans: list[dict[str, Any]], logic: CausalAuctionEngine, errors: list[dict[str, Any]], gates: dict[str, Any]) -> dict[str, Any]:
    pnls = _closed_pnls(positions)
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    win_rate = float(len(wins) / len(pnls)) if pnls else 0.0
    payoff = None
    if wins and losses:
        payoff = float((sum(wins) / len(wins)) / abs(sum(losses) / len(losses)))
    growth = float((final_nav / starting_nav) ** (Decimal(1) / Decimal(days)) - Decimal(1)) if final_nav > 0 else -1.0
    equity, peak, max_dd = starting_nav, starting_nav, Decimal("0")
    for pnl in pnls:
        equity += pnl
        peak = max(peak, equity)
        if peak > 0:
            max_dd = max(max_dd, (peak - equity) / peak)
    scenario_counts: dict[str, int] = {}
    for plan in plans:
        scenario_counts[plan["scenario"]] = scenario_counts.get(plan["scenario"], 0) + 1
    promising = gates["promising"]
    promising_pass = (
        len(pnls) >= int(promising["min_closed_trades"])
        and win_rate >= float(promising["min_win_rate"])
        and (payoff is not None and payoff >= float(promising["min_payoff_ratio"]))
        and growth >= float(promising["min_daily_geometric_growth"])
        and not errors
    )
    complete = gates["complete"]
    complete_pass = (
        len(pnls) >= int(complete["min_closed_trades_per_week"])
        and win_rate >= float(complete["min_win_rate"])
        and (payoff is not None and payoff >= float(complete["min_payoff_ratio"]))
        and growth >= float(complete["min_daily_geometric_growth"])
        and float(max_dd) <= float(complete["max_closed_trade_drawdown"])
        and final_nav > 0
        and not errors
    )
    return {
        "starting_nav": str(starting_nav),
        "final_nav": str(final_nav),
        "net_return": float(final_nav / starting_nav - 1),
        "daily_geometric_growth": growth,
        "closed_trades": len(pnls),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": win_rate,
        "payoff_ratio": payoff,
        "closed_trade_max_drawdown": float(max_dd),
        "submitted_plans": len(plans),
        "scenario_counts": scenario_counts,
        "detected_events": len(logic.events),
        "skip_reasons": dict(logic.skips),
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
    from nautilus_trader.model.enums import AccountType, OmsType, OrderSide, TimeInForce
    from nautilus_trader.model.identifiers import InstrumentId, Symbol, Venue
    from nautilus_trader.model.instruments import CryptoPerpetual
    from nautilus_trader.model.objects import Money, Price, Quantity
    from nautilus_trader.persistence.wranglers import BarDataWrangler
    from nautilus_trader.trading.strategy import Strategy

    config = json.loads(config_path.read_text(encoding="utf-8"))
    if week_id not in config["selection"]["weeks"]:
        raise ValueError(f"unknown week {week_id}")
    chosen = config["selection"]["weeks"][week_id]
    eval_start = date.fromisoformat(chosen["start"])
    eval_end = date.fromisoformat(chosen["end_exclusive"])
    warmup_start = eval_start - timedelta(days=int(config["selection"]["warmup_days"]))
    output_dir.mkdir(parents=True, exist_ok=True)
    frame, files = load_binance_bars(warmup_start, eval_end, output_dir / "data")
    write_json_atomic(output_dir / "data_manifest.json", {"dataset": "Binance USD-M BTCUSDT 1m daily klines", "bar_visibility": "open_time plus one minute", "files": files})

    account_cfg, execution_cfg = config["account"], config["execution"]
    instrument = CryptoPerpetual(
        instrument_id=InstrumentId(symbol=Symbol("BTCUSDT-PERP"), venue=Venue("BINANCE")),
        raw_symbol=Symbol("BTCUSDT"), base_currency=BTC, quote_currency=USDT, settlement_currency=USDT,
        is_inverse=False, price_precision=1, price_increment=Price.from_str("0.1"),
        size_precision=3, size_increment=Quantity.from_str("0.001"),
        max_quantity=Quantity.from_str("1000.000"), min_quantity=Quantity.from_str("0.001"),
        max_notional=None, min_notional=Money(10.00, USDT),
        max_price=Price.from_str("809484.0"), min_price=Price.from_str("261.1"),
        margin_init=Decimal(account_cfg["margin_init"]), margin_maint=Decimal(account_cfg["margin_maint"]),
        maker_fee=Decimal(execution_cfg["effective_maker_rate"]), taker_fee=Decimal(execution_cfg["effective_taker_rate"]),
        ts_event=0, ts_init=0,
    )
    bar_type = BarType.from_str("BTCUSDT-PERP.BINANCE-1-MINUTE-LAST-EXTERNAL")
    wrangle_frame = frame[["open", "high", "low", "close", "volume"]].copy()
    bars = BarDataWrangler(bar_type, instrument).process(wrangle_frame)
    flows = frame["taker_buy_volume"].astype(float).tolist()
    if len(bars) != len(flows):
        raise RuntimeError("bar/flow alignment failed")

    logic_cfg = LogicConfig(**config["logic"])
    eval_start_ns = int(pd.Timestamp(eval_start, tz="UTC").value)
    eval_end_ns = int(pd.Timestamp(eval_end, tz="UTC").value)

    class CandidateConfig(StrategyConfig, frozen=True):
        instrument_id: InstrumentId
        bar_type: BarType
        evaluation_start_ns: int
        evaluation_end_ns: int
        starting_nav: Decimal

    class CandidateStrategy(Strategy):
        def __init__(self, strategy_config: CandidateConfig) -> None:
            super().__init__(strategy_config)
            self.logic = CausalAuctionEngine(logic_cfg, str(strategy_config.instrument_id))
            self.sizer = RiskSizer(logic_cfg.risk_fraction)
            self.flow_index = 0
            self.plans: list[dict[str, Any]] = []
            self.errors: list[dict[str, Any]] = []
            self.last_ts = 0

        def on_start(self) -> None:
            self.subscribe_bars(self.config.bar_type)

        def _open_orders(self) -> int:
            return int(self.cache.orders_open_count(instrument_id=self.config.instrument_id))

        def _money(self) -> tuple[Decimal, Decimal]:
            account = self.cache.account_for_venue(self.config.instrument_id.venue)
            if account is None:
                return self.config.starting_nav, self.config.starting_nav
            total = _number(account.balance_total(USDT))
            free_value = account.balance_free(USDT) if hasattr(account, "balance_free") else account.balance_total(USDT)
            return total, _number(free_value)

        def _terminal_if_flat(self, ts_ns: int) -> None:
            if self.logic.active_trade_id and self.portfolio.is_flat(self.config.instrument_id) and self._open_orders() == 0:
                self.logic.mark_trade_terminal(ts_ns, "NAUTILUS_POSITION_AND_ORDERS_FLAT")

        def on_bar(self, bar: Bar) -> None:
            self.last_ts = int(bar.ts_event)
            self._terminal_if_flat(self.last_ts)
            if self.last_ts >= self.config.evaluation_end_ns:
                if not self.portfolio.is_flat(self.config.instrument_id):
                    self.cancel_all_orders(self.config.instrument_id)
                    self.close_all_positions(self.config.instrument_id)
                return
            flow = flows[self.flow_index] if self.flow_index < len(flows) else 0.0
            self.flow_index += 1
            obs = BarObs(
                self.last_ts, float(str(bar.open)), float(str(bar.high)), float(str(bar.low)),
                float(str(bar.close)), float(str(bar.volume)), flow,
            )
            plan = self.logic.on_bar(obs, allow_entry=self.last_ts >= self.config.evaluation_start_ns)
            if plan is None:
                return
            nav, free = self._money()
            decision = self.sizer.size(
                nav=nav, loss_per_unit=Decimal(str(plan.loss_per_unit)), entry_price=Decimal(str(plan.expected_entry)),
                quantity_increment=Decimal(str(instrument.size_increment)), min_quantity=Decimal(str(instrument.min_quantity)),
                min_notional=_number(instrument.min_notional), margin_init=instrument.margin_init, free_balance=free,
            )
            if not decision.feasible:
                self.logic.mark_rejected(plan, self.last_ts, decision.reason, {"required_margin": str(decision.required_margin), "free_balance": str(free)})
                return
            side = OrderSide.BUY if plan.direction == Direction.LONG else OrderSide.SELL
            orders = self.order_factory.bracket(
                instrument_id=self.config.instrument_id, order_side=side,
                quantity=instrument.make_qty(decision.quantity), time_in_force=TimeInForce.GTC,
                tp_price=instrument.make_price(plan.target_price), sl_trigger_price=instrument.make_price(plan.stop_price),
            )
            self.submit_order_list(orders)
            record = {
                "scenario_id": plan.scenario_id, "scenario": plan.scenario.value, "direction": plan.direction.value,
                "observed_ts_ns": plan.observed_ts_ns, "entry": plan.expected_entry, "stop": plan.stop_price,
                "target": plan.target_price, "net_r": plan.net_r, "quantity": str(decision.quantity),
                "nav_before": str(nav), "planned_loss_budget": str(decision.planned_loss_budget),
                "expected_total_loss": str(decision.expected_total_loss), "required_margin": str(decision.required_margin),
            }
            self.plans.append(record)
            self.logic.mark_submitted(plan, decision.quantity, record)

        def on_order_denied(self, event: Any) -> None:
            self.errors.append({"type": "ORDER_DENIED", "event": str(event)})

        def on_order_rejected(self, event: Any) -> None:
            self.errors.append({"type": "ORDER_REJECTED", "event": str(event)})

        def on_stop(self) -> None:
            self.cancel_all_orders(self.config.instrument_id)
            if not self.portfolio.is_flat(self.config.instrument_id):
                self.close_all_positions(self.config.instrument_id)

    venue = Venue("BINANCE")
    starting_nav = Decimal(account_cfg["starting_nav"])
    engine = BacktestEngine(config=BacktestEngineConfig(logging=LoggingConfig(log_level="ERROR")))
    fill_model = FillModel(
        prob_fill_on_limit=float(execution_cfg["prob_fill_on_limit"]),
        prob_slippage=float(execution_cfg["prob_slippage"]),
        random_seed=int(execution_cfg["random_seed"]),
    )
    strategy = CandidateStrategy(CandidateConfig(
        instrument_id=instrument.id, bar_type=bar_type, evaluation_start_ns=eval_start_ns,
        evaluation_end_ns=eval_end_ns, starting_nav=starting_nav,
    ))
    try:
        engine.add_venue(
            venue=venue, oms_type=OmsType.NETTING, account_type=AccountType.MARGIN,
            starting_balances=[Money(starting_nav, USDT)], base_currency=USDT,
            default_leverage=Decimal(account_cfg["default_leverage"]), fill_model=fill_model,
            bar_adaptive_high_low_ordering=bool(execution_cfg["bar_adaptive_high_low_ordering"]),
        )
        engine.add_instrument(instrument)
        engine.add_data(bars)
        engine.add_strategy(strategy)
        engine.run()
        orders = engine.generate_order_fills_report()
        positions = engine.generate_positions_report()
        account_report = engine.generate_account_report(venue)
        orders.to_csv(output_dir / "orders.csv", index=False)
        positions.to_csv(output_dir / "positions.csv", index=False)
        account_report.to_csv(output_dir / "account.csv", index=False)
        account = engine.cache.account_for_venue(venue)
        if account is None:
            raise RuntimeError("Nautilus account unavailable after run")
        final_nav = _number(account.balance_total(USDT))
        metrics = calculate_metrics(starting_nav, final_nav, int(config["selection"]["evaluation_days"]), positions, strategy.plans, strategy.logic, strategy.errors, config["gates"])
        result = engine.get_result() if hasattr(engine, "get_result") else None
        metrics.update({
            "week_id": week_id, "evaluation_start": eval_start.isoformat(), "evaluation_end_exclusive": eval_end.isoformat(),
            "bars": len(bars), "instrument": str(instrument.id),
            "effective_maker_rate": str(instrument.maker_fee), "effective_taker_rate": str(instrument.taker_fee),
            "nautilus_result": {name: getattr(result, name, None) for name in ("iterations", "total_orders", "total_positions", "total_events")},
        })
        write_events(output_dir / "scenario_events.jsonl", strategy.logic.events)
        write_json_atomic(output_dir / "submitted_plans.json", {"plans": strategy.plans})
        write_json_atomic(output_dir / "metrics.json", metrics)
        manifest = create_run_manifest(
            run_id=f"candidate-11-{week_id.lower()}-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}",
            candidate=config["candidate"], config_path=config_path, data_manifest_path=output_dir / "data_manifest.json",
            extra={"week_id": week_id, "bar_type": str(bar_type), "evaluation_start": eval_start.isoformat(), "evaluation_end_exclusive": eval_end.isoformat()},
        )
        write_json_atomic(output_dir / "run.json", manifest)
        return metrics
    finally:
        engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=Path(__file__).with_name("config.json"))
    parser.add_argument("--week", choices=("W1", "W2", "W3"), default="W1")
    parser.add_argument("--output", type=Path, default=Path(__file__).with_name("results") / "W1")
    args = parser.parse_args()
    metrics = run(args.config, args.week, args.output)
    print(json.dumps(metrics, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
