#!/usr/bin/env python3
"""Dynamic price-discovery leadership evaluation of Candidate 11 SCDAM.

Each allowed market owns an independent RegionalHandoffAuctionEngine.  The
strategy logic is identical across BTCUSDT, ETHUSDT, SOLUSDT and XRPUSDT; only
Nautilus instrument metadata differs.  Plans emitted at the same completed bar
are deterministically arbitrated, while one global pending-entry/position slot
is enforced across all instruments.  NautilusTrader exclusively owns clocks,
orders, fills, fees, margin, positions and account NAV.
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
from global_allocator import Candidate, GlobalCandidateMutex, SlotState
from market_leadership import MarketLeadershipGate
from logic import BarObs, Direction, LogicConfig, RiskSizer, TradePlan
from session_engine import RegionalHandoffAuctionEngine

from smc_ict_4.event_log import EventLogError, write_events
from smc_ict_4.manifest import create_run_manifest, write_json_atomic

UTC = timezone.utc
SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT")
COLUMNS = (
    "open_time", "open", "high", "low", "close", "volume", "close_time",
    "quote_volume", "trades", "taker_buy_volume", "taker_buy_quote_volume", "ignore",
)
META = {
    "BTCUSDT": {"base": "BTC", "price_precision": 1, "price_increment": "0.1", "size_precision": 3, "size_increment": "0.001", "min_qty": "0.001"},
    "ETHUSDT": {"base": "ETH", "price_precision": 2, "price_increment": "0.01", "size_precision": 3, "size_increment": "0.001", "min_qty": "0.001"},
    "SOLUSDT": {"base": "SOL", "price_precision": 3, "price_increment": "0.001", "size_precision": 1, "size_increment": "0.1", "min_qty": "0.1"},
    "XRPUSDT": {"base": "XRP", "price_precision": 4, "price_increment": "0.0001", "size_precision": 0, "size_increment": "1", "min_qty": "1"},
}


def _write_raw_events(path: Path, events: list[Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as stream:
        for event in events:
            stream.write(json.dumps(event.to_dict(), sort_keys=True, ensure_ascii=False) + "\n")
    temporary.replace(path)
    return path


def _download(url: str, destination: Path, retries: int = 4) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists() and destination.stat().st_size > 100:
        try:
            with ZipFile(destination) as archive:
                if archive.testzip() is None:
                    return
        except Exception:
            destination.unlink(missing_ok=True)
    request = Request(url, headers={"User-Agent": "SMC-ICT-4-candidate-11-portfolio"})
    last_error: Exception | None = None
    for attempt in range(retries):
        try:
            with urlopen(request, timeout=60) as response:  # noqa: S310 frozen HTTPS host
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
        except Exception as exc:
            last_error = exc
            if attempt + 1 < retries:
                time.sleep(2**attempt)
    raise RuntimeError(f"failed to download {url}: {last_error}")


def load_symbol_bars(
    symbol: str,
    start: date,
    end_inclusive: date,
    data_dir: Path,
) -> tuple[pd.DataFrame, list[dict[str, Any]]]:
    frames: list[pd.DataFrame] = []
    manifest: list[dict[str, Any]] = []
    cursor = start
    while cursor <= end_inclusive:
        filename = f"{symbol}-1m-{cursor.isoformat()}.zip"
        url = f"https://data.binance.vision/data/futures/um/daily/klines/{symbol}/1m/{filename}"
        path = data_dir / symbol / filename
        _download(url, path)
        digest = sha256(path.read_bytes()).hexdigest()
        with ZipFile(path) as archive:
            members = archive.namelist()
            if len(members) != 1:
                raise RuntimeError(f"unexpected archive members: {filename}: {members}")
            member_bytes = archive.read(members[0])
            frame = pd.read_csv(BytesIO(member_bytes))
            if set(COLUMNS).issubset(frame.columns):
                frame = frame.loc[:, COLUMNS]
            else:
                frame = pd.read_csv(BytesIO(member_bytes), header=None, names=COLUMNS)
        frame = frame[pd.to_numeric(frame["open_time"], errors="coerce").notna()].copy()
        if len(frame.index) not in (1439, 1440, 1441):
            raise RuntimeError(f"unexpected row count {len(frame.index)} for {filename}")
        frames.append(frame)
        manifest.append({
            "symbol": symbol,
            "date": cursor.isoformat(),
            "url": url,
            "path": str(path),
            "bytes": path.stat().st_size,
            "sha256": digest,
            "rows": len(frame.index),
        })
        cursor += timedelta(days=1)

    raw = pd.concat(frames, ignore_index=True)
    raw = raw.drop_duplicates(subset=["open_time"], keep="last").sort_values("open_time", kind="stable")
    first = int(pd.to_numeric(raw["open_time"], errors="raise").iloc[0])
    if 1_000_000_000_000 <= first < 10_000_000_000_000:
        unit = "ms"
    elif 1_000_000_000_000_000 <= first < 10_000_000_000_000_000:
        unit = "us"
    else:
        raise RuntimeError(f"unsupported timestamp magnitude: {first}")
    index = pd.to_datetime(pd.to_numeric(raw["open_time"], errors="raise"), unit=unit, utc=True) + pd.Timedelta(minutes=1)
    result = pd.DataFrame(index=index)
    for name in ("open", "high", "low", "close", "volume", "taker_buy_volume"):
        result[name] = pd.to_numeric(raw[name], errors="raise").to_numpy(copy=True)
    result = result[~result.index.duplicated(keep="last")].sort_index()
    if not result.index.is_monotonic_increasing:
        raise RuntimeError(f"non-monotonic frame for {symbol}")
    return result, manifest


def _decimal(value: Any, default: Decimal = Decimal("0")) -> Decimal:
    if value is None:
        return default
    text = str(value).strip()
    if not text or text.lower() == "nan":
        return default
    return Decimal(text.split()[0])


def _closed_pnls(positions: pd.DataFrame) -> list[Decimal]:
    if positions.empty:
        return []
    column = next((name for name in ("realized_pnl", "pnl") if name in positions.columns), None)
    if column is None:
        return []
    return [_decimal(value) for value in positions[column].tolist()]


def _global_overlap_count(positions: pd.DataFrame) -> int:
    if positions.empty or not {"ts_opened", "ts_closed"}.issubset(positions.columns):
        return 0
    intervals: list[tuple[pd.Timestamp, pd.Timestamp]] = []
    for opened, closed in zip(positions["ts_opened"], positions["ts_closed"], strict=True):
        if pd.isna(opened) or pd.isna(closed):
            continue
        intervals.append((pd.Timestamp(opened), pd.Timestamp(closed)))
    intervals.sort(key=lambda item: item[0])
    return sum(intervals[index][0] < intervals[index - 1][1] for index in range(1, len(intervals)))


def calculate_metrics(
    *,
    starting_nav: Decimal,
    final_nav: Decimal,
    evaluation_days: int,
    positions: pd.DataFrame,
    plans: list[dict[str, Any]],
    logics: dict[str, RegionalHandoffAuctionEngine],
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

    overlap_count = _global_overlap_count(positions)
    liquidation_detected = any("LIQUIDAT" in json.dumps(item, default=str).upper() for item in lifecycle)
    skip_reasons: Counter[str] = Counter()
    detected_events = 0
    for engine in logics.values():
        skip_reasons.update(engine.skips)
        detected_events += len(engine.events)

    promising = gates["promising"]
    promising_pass = (
        len(pnls) >= int(promising["min_closed_trades"])
        and win_rate >= float(promising["min_win_rate"])
        and payoff_ratio is not None
        and payoff_ratio >= float(promising["min_payoff_ratio"])
        and daily_growth >= float(promising["min_daily_geometric_growth"])
        and not errors
        and not liquidation_detected
        and overlap_count == 0
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
        and overlap_count == 0
    )
    return {
        "candidate": "candidate-11-market-leadership-scdam",
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
        "submitted_plans": len(plans),
        "scenario_counts": {
            scenario: sum(plan["scenario"] == scenario for plan in plans)
            for scenario in ("FAR", "AAC")
        },
        "symbol_counts": {
            symbol: sum(plan["symbol"] == symbol for plan in plans)
            for symbol in SYMBOLS
        },
        "detected_events": detected_events,
        "skip_reasons": dict(skip_reasons),
        "engine_errors": errors,
        "partial_entry_fail_closed_count": sum(
            item.get("type") == "PARTIAL_ENTRY_EXPIRED_FAIL_CLOSED"
            for item in lifecycle
        ),
        "liquidation_detected": liquidation_detected,
        "global_slot_overlap_count": overlap_count,
        "promising_gate_passed": promising_pass,
        "complete_gate_passed": complete_pass,
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
    selected = config["selection"]["weeks"][week_id]
    evaluation_start = date.fromisoformat(selected["start"])
    evaluation_end = date.fromisoformat(selected["end_exclusive"])
    warmup_start = evaluation_start - timedelta(days=int(config["selection"]["warmup_days"]))
    output_dir.mkdir(parents=True, exist_ok=True)

    frames: dict[str, pd.DataFrame] = {}
    manifest: list[dict[str, Any]] = []
    for symbol in SYMBOLS:
        frames[symbol], files = load_symbol_bars(symbol, warmup_start, evaluation_end, output_dir / "data")
        manifest.extend(files)
    write_json_atomic(output_dir / "data_manifest.json", {
        "schema": "candidate-11-portfolio-source-manifest-v1",
        "dataset": "Binance USD-M one-minute daily klines",
        "symbols": list(SYMBOLS),
        "bar_visibility": "archive open_time plus one minute",
        "warmup_start": warmup_start.isoformat(),
        "evaluation_start": evaluation_start.isoformat(),
        "evaluation_end_exclusive": evaluation_end.isoformat(),
        "files": manifest,
    })

    account_config = config["account"]
    execution_config = config["execution"]
    logic_config = LogicConfig(**config["logic"])
    base_currencies = {
        symbol: Currency.from_str(str(META[symbol]["base"]), strict=False)
        for symbol in SYMBOLS
    }
    if any(currency is None for currency in base_currencies.values()):
        raise RuntimeError("Nautilus currency registry could not resolve an allowed base asset")

    venue = Venue("BINANCE")
    instruments: dict[str, Any] = {}
    bar_types: dict[str, Any] = {}
    all_bars: list[Any] = []
    flow: dict[tuple[str, int], tuple[float, float]] = {}
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
            max_price=Price.from_str("1000000"),
            min_price=Price.from_str(meta["price_increment"]),
            margin_init=Decimal(account_config["margin_init"]),
            margin_maint=Decimal(account_config["margin_maint"]),
            maker_fee=Decimal(execution_config["effective_maker_rate"]),
            taker_fee=Decimal(execution_config["effective_taker_rate"]),
            ts_event=0,
            ts_init=0,
        )
        bar_type = BarType.from_str(f"{symbol}-PERP.BINANCE-1-MINUTE-LAST-EXTERNAL")
        frame = frames[symbol]
        built = build_bars(frame[["open", "high", "low", "close", "volume"]], bar_type, instrument)
        all_bars.extend(built)
        for ts, volume, taker in zip(frame.index, frame["volume"], frame["taker_buy_volume"], strict=True):
            flow[(str(instrument_id), int(ts.value))] = (float(volume), float(taker))
        instruments[symbol] = instrument
        bar_types[symbol] = bar_type
    all_bars.sort(key=lambda bar: (int(bar.ts_init), str(bar.bar_type)))

    starting_nav = Decimal(account_config["starting_nav"])
    evaluation_start_ns = int(pd.Timestamp(evaluation_start, tz="UTC").value)
    evaluation_end_ns = int(pd.Timestamp(evaluation_end, tz="UTC").value)

    class PortfolioStrategyConfig(StrategyConfig, frozen=True):
        instrument_ids: tuple[InstrumentId, ...]
        bar_types: tuple[BarType, ...]
        evaluation_start_ns: int
        evaluation_end_ns: int
        starting_nav: Decimal

    class PortfolioStrategy(Strategy):
        def __init__(self, strategy_config: PortfolioStrategyConfig) -> None:
            super().__init__(strategy_config)
            self.logic = {
                symbol: RegionalHandoffAuctionEngine(logic_config, str(instruments[symbol].id))
                for symbol in SYMBOLS
            }
            self.sizer = RiskSizer(logic_config.risk_fraction)
            self.mutex = GlobalCandidateMutex()
            self.leadership = MarketLeadershipGate(SYMBOLS, lookback_bars=1440)
            self.buffer_ts: int | None = None
            self.buffer: dict[str, BarObs] = {}
            self.event_cursor = {symbol: 0 for symbol in SYMBOLS}
            self.events: list[Any] = []
            self.plans: list[dict[str, Any]] = []
            self.errors: list[dict[str, Any]] = []
            self.rejections: list[dict[str, Any]] = []
            self.lifecycle: list[dict[str, Any]] = []
            self.active_plan: TradePlan | None = None
            self.active_symbol: str | None = None
            self.last_ts_ns = 0

        def on_start(self) -> None:
            for bar_type in self.config.bar_types:
                self.subscribe_bars(bar_type)

        @staticmethod
        def _symbol(bar: Bar) -> str:
            return str(bar.bar_type.instrument_id).split("-PERP", 1)[0]

        def _capture_events(self, symbol: str) -> None:
            engine = self.logic[symbol]
            cursor = self.event_cursor[symbol]
            if cursor < len(engine.events):
                self.events.extend(engine.events[cursor:])
                self.event_cursor[symbol] = len(engine.events)

        def _open_orders(self) -> int:
            return sum(
                int(self.cache.orders_open_count(instrument_id=instrument_id, strategy_id=self.id))
                for instrument_id in self.config.instrument_ids
            )

        def _all_flat(self) -> bool:
            return all(self.portfolio.is_flat(instrument_id) for instrument_id in self.config.instrument_ids)

        def _account_values(self) -> tuple[Decimal, Decimal]:
            account = self.cache.account_for_venue(venue)
            if account is None:
                return self.config.starting_nav, self.config.starting_nav
            total = _decimal(account.balance_total(USDT))
            free = _decimal(account.balance_free(USDT), total) if hasattr(account, "balance_free") else total
            return total, free

        def _record_order_event(self, event: OrderEvent, kind: str) -> None:
            self.lifecycle.append({
                "type": kind,
                "ts_event": int(event.ts_event),
                "client_order_id": str(event.client_order_id),
                "event": str(event),
            })

        def _release_if_terminal(self, ts_ns: int, reason: str) -> None:
            if self.active_plan is None or self.active_symbol is None:
                return
            scenario_id = self.active_plan.scenario_id
            instrument_id = instruments[self.active_symbol].id
            if self.mutex.state == SlotState.ENTRY_PENDING:
                if not self.portfolio.is_flat(instrument_id):
                    self.mutex.mark_entry_filled(scenario_id)
                    self.logic[self.active_symbol].mark_entry_filled(
                        ts_ns,
                        {"scenario_id": scenario_id, "symbol": self.active_symbol},
                    )
                    self._capture_events(self.active_symbol)
                    self.lifecycle.append({
                        "type": "GLOBAL_ENTRY_FILLED",
                        "ts_event": ts_ns,
                        "scenario_id": scenario_id,
                        "symbol": self.active_symbol,
                    })
                elif self._open_orders() == 0:
                    self.mutex.mark_entry_terminal(scenario_id)
                    self.logic[self.active_symbol].mark_trade_terminal(ts_ns, reason)
                    self._capture_events(self.active_symbol)
                    self.active_plan = None
                    self.active_symbol = None
            elif self.mutex.state == SlotState.POSITION_OPEN:
                if self._all_flat() and self._open_orders() == 0:
                    self.mutex.mark_position_closed(scenario_id)
                    self.logic[self.active_symbol].mark_trade_terminal(ts_ns, reason)
                    self._capture_events(self.active_symbol)
                    self.lifecycle.append({
                        "type": "GLOBAL_POSITION_CLOSED",
                        "ts_event": ts_ns,
                        "scenario_id": scenario_id,
                        "symbol": self.active_symbol,
                    })
                    self.active_plan = None
                    self.active_symbol = None

        def _submit(self, plan: TradePlan, candidate: Candidate) -> None:
            symbol = candidate.symbol
            instrument = instruments[symbol]
            if self.mutex.state != SlotState.FREE or not self._all_flat() or self._open_orders() > 0:
                self.logic[symbol].mark_rejected(plan, self.last_ts_ns, "GLOBAL_SLOT_OCCUPIED")
                self._capture_events(symbol)
                self.rejections.append({
                    "type": "GLOBAL_CANDIDATE_REJECTED",
                    "observed_ts_ns": plan.observed_ts_ns,
                    "scenario_id": plan.scenario_id,
                    "symbol": symbol,
                    "reason": "GLOBAL_SLOT_OCCUPIED",
                    "net_structural_r": str(plan.net_r),
                })
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
                self.logic[symbol].mark_rejected(plan, self.last_ts_ns, decision.reason, {
                    "required_margin": str(decision.required_margin),
                    "free_balance": str(free_balance),
                })
                self._capture_events(symbol)
                return

            side = OrderSide.BUY if plan.direction == Direction.LONG else OrderSide.SELL
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
                record = {
                    "type": "ORDER_LIST_SUBMISSION_EXCEPTION",
                    "ts_ns": self.last_ts_ns,
                    "symbol": symbol,
                    "exception": type(exc).__name__,
                    "message": str(exc),
                }
                self.errors.append(record)
                self.logic[symbol].mark_rejected(plan, self.last_ts_ns, record["type"], record)
                self._capture_events(symbol)
                return

            self.logic[symbol].mark_submitted(
                plan,
                decision.quantity,
                {"symbol": symbol, "scenario_id": plan.scenario_id},
            )
            self._capture_events(symbol)
            self.mutex.mark_entry_submitted(candidate)
            self.active_plan = plan
            self.active_symbol = symbol
            self.plans.append({
                "symbol": symbol,
                "scenario_id": plan.scenario_id,
                "scenario": plan.scenario.value,
                "direction": plan.direction.value,
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
            self.lifecycle.append({
                "type": "GLOBAL_ENTRY_SUBMITTED",
                "ts_event": self.last_ts_ns,
                "scenario_id": plan.scenario_id,
                "symbol": symbol,
            })

        def _process_batch(self, ts_ns: int) -> None:
            # Observe the entire completed minute before any symbol can be
            # approved, preventing subscription-order or future-data bias.
            try:
                self.leadership.observe_batch(
                    ts_ns,
                    {
                        symbol: (self.buffer[symbol].close, self.buffer[symbol].volume)
                        for symbol in SYMBOLS
                    },
                )
            except Exception as exc:
                self.errors.append({
                    "type": "MARKET_LEADERSHIP_OBSERVATION_ERROR",
                    "ts_ns": ts_ns,
                    "exception": type(exc).__name__,
                    "message": str(exc),
                })
                return
            plans: list[tuple[TradePlan, Candidate]] = []
            for symbol in SYMBOLS:
                observation = self.buffer[symbol]
                plan = self.logic[symbol].on_bar(observation)
                self._capture_events(symbol)
                if plan is None:
                    continue
                if ts_ns < self.config.evaluation_start_ns:
                    self.logic[symbol].mark_rejected(plan, ts_ns, "OUTSIDE_EVALUATION_WINDOW")
                    self._capture_events(symbol)
                    continue
                leadership = self.leadership.decide(
                    symbol=symbol,
                    scenario=plan.scenario.value,
                    direction=plan.direction.value,
                    sweep_ts_ns=int(plan.details.get("sweep_ts_ns", -1)),
                    confirmation_ts_ns=ts_ns,
                )
                plan.details["market_leadership"] = leadership.to_dict()
                if not leadership.approved:
                    self.logic[symbol].mark_rejected(
                        plan,
                        ts_ns,
                        leadership.reason,
                        leadership.to_dict(),
                    )
                    self._capture_events(symbol)
                    self.rejections.append({
                        "type": "MARKET_LEADERSHIP_REJECTED",
                        "observed_ts_ns": plan.observed_ts_ns,
                        "scenario_id": plan.scenario_id,
                        "symbol": symbol,
                        "reason": leadership.reason,
                        "leader": leadership.leader,
                        "peer_returns": leadership.peer_returns,
                        "net_structural_r": str(plan.net_r),
                    })
                    continue
                candidate = Candidate(
                    symbol=symbol,
                    scenario_id=plan.scenario_id,
                    observed_ts_ns=plan.observed_ts_ns,
                    net_structural_r=Decimal(str(plan.net_r)),
                    expected_entry=Decimal(str(plan.expected_entry)),
                    expected_loss_per_unit=Decimal(str(plan.loss_per_unit)),
                )
                plans.append((plan, candidate))

            if not plans:
                return
            plan_by_id = {plan.scenario_id: (plan, candidate) for plan, candidate in plans}
            for _, candidate in plans:
                completed = self.mutex.add(candidate)
                if completed is not None:
                    for rejected, reason in completed.rejected:
                        plan, _ = plan_by_id[rejected.scenario_id]
                        self.logic[rejected.symbol].mark_rejected(plan, ts_ns, reason)
                        self._capture_events(rejected.symbol)
                        self.rejections.append({
                            "type": "GLOBAL_CANDIDATE_REJECTED",
                            "observed_ts_ns": plan.observed_ts_ns,
                            "scenario_id": plan.scenario_id,
                            "symbol": rejected.symbol,
                            "reason": reason,
                            "net_structural_r": str(plan.net_r),
                        })
            arbitration = self.mutex.flush()
            for rejected, reason in arbitration.rejected:
                plan, _ = plan_by_id[rejected.scenario_id]
                self.logic[rejected.symbol].mark_rejected(plan, ts_ns, reason)
                self._capture_events(rejected.symbol)
                self.rejections.append({
                    "type": "GLOBAL_CANDIDATE_REJECTED",
                    "observed_ts_ns": plan.observed_ts_ns,
                    "scenario_id": plan.scenario_id,
                    "symbol": rejected.symbol,
                    "reason": reason,
                    "net_structural_r": str(plan.net_r),
                })
            if arbitration.winner is not None:
                winner = plan_by_id[arbitration.winner.scenario_id]
                self._submit(winner[0], winner[1])

        def on_bar(self, bar: Bar) -> None:
            self.last_ts_ns = int(bar.ts_event)
            self._release_if_terminal(self.last_ts_ns, "BAR_TERMINAL_SYNC")
            if self.last_ts_ns >= self.config.evaluation_end_ns:
                if self.buffer_ts is not None and len(self.buffer) == len(SYMBOLS):
                    self._process_batch(self.buffer_ts)
                    self.buffer.clear()
                    self.buffer_ts = None
                self._flatten()
                return
            symbol = self._symbol(bar)
            key = (str(bar.bar_type.instrument_id), self.last_ts_ns)
            if key not in flow:
                self.errors.append({"type": "MISSING_FLOW", "symbol": symbol, "ts_ns": self.last_ts_ns})
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
                    self.errors.append({
                        "type": "INCOMPLETE_SYNCHRONIZED_BATCH",
                        "ts_ns": self.buffer_ts,
                        "symbols": sorted(self.buffer),
                    })
                else:
                    self._process_batch(self.buffer_ts)
                self.buffer.clear()
                self.buffer_ts = self.last_ts_ns
            self.buffer[symbol] = observation
            if len(self.buffer) == len(SYMBOLS):
                self._process_batch(self.buffer_ts)
                self.buffer.clear()
                self.buffer_ts = None

        def _flatten(self) -> None:
            for instrument_id in self.config.instrument_ids:
                if self.cache.orders_open_count(instrument_id=instrument_id, strategy_id=self.id):
                    self.cancel_all_orders(instrument_id)
                if not self.portfolio.is_flat(instrument_id):
                    self.close_all_positions(instrument_id)

        def _fail_close_partial_entry(self, event: OrderEvent) -> None:
            """Flatten a residual position when its GTD parent expires.

            The global slot remains POSITION_OPEN until Nautilus confirms the
            market close. This is execution safety, not an alpha filter.
            """
            if (
                self.active_plan is None
                or self.active_symbol is None
                or self.mutex.state != SlotState.POSITION_OPEN
            ):
                return
            instrument_id = instruments[self.active_symbol].id
            if self.portfolio.is_flat(instrument_id):
                return
            ts_ns = int(event.ts_event)
            record = {
                "type": "PARTIAL_ENTRY_EXPIRED_FAIL_CLOSED",
                "ts_event": ts_ns,
                "scenario_id": self.active_plan.scenario_id,
                "symbol": self.active_symbol,
                "expired_client_order_id": str(event.client_order_id),
            }
            self.lifecycle.append(record)
            if self.cache.orders_open_count(instrument_id=instrument_id, strategy_id=self.id):
                self.cancel_all_orders(instrument_id)
            self.close_all_positions(instrument_id)

        def on_order_filled(self, event: OrderEvent) -> None:
            self._record_order_event(event, "ORDER_FILLED")
            self._release_if_terminal(int(event.ts_event), "ORDER_FILLED")

        def on_order_expired(self, event: OrderEvent) -> None:
            self._record_order_event(event, "ORDER_EXPIRED")
            self._fail_close_partial_entry(event)
            self._release_if_terminal(int(event.ts_event), "ENTRY_EXPIRED")

        def on_order_canceled(self, event: OrderEvent) -> None:
            self._record_order_event(event, "ORDER_CANCELED")
            self._release_if_terminal(int(event.ts_event), "ORDER_CANCELED")

        def on_order_denied(self, event: OrderEvent) -> None:
            self._record_order_event(event, "ORDER_DENIED")
            self.errors.append({"type": "ORDER_DENIED", "event": str(event)})
            self._release_if_terminal(int(event.ts_event), "ORDER_DENIED")

        def on_order_rejected(self, event: OrderEvent) -> None:
            self._record_order_event(event, "ORDER_REJECTED")
            self.errors.append({"type": "ORDER_REJECTED", "event": str(event)})
            self._release_if_terminal(int(event.ts_event), "ORDER_REJECTED")

        def on_stop(self) -> None:
            self._flatten()

    engine = BacktestEngine(config=BacktestEngineConfig(logging=LoggingConfig(log_level="ERROR")))
    fill_model = FillModel(
        prob_fill_on_limit=float(execution_config["prob_fill_on_limit"]),
        prob_slippage=float(execution_config["prob_slippage"]),
        random_seed=int(execution_config["random_seed"]),
    )
    strategy = PortfolioStrategy(PortfolioStrategyConfig(
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
            default_leverage=Decimal(account_config["default_leverage"]),
            fill_model=fill_model,
            bar_adaptive_high_low_ordering=bool(execution_config["bar_adaptive_high_low_ordering"]),
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
        account = engine.cache.account_for_venue(venue)
        if account is None:
            raise RuntimeError("Nautilus margin account is unavailable after the run")
        final_nav = _decimal(account.balance_total(USDT))
        metrics = calculate_metrics(
            starting_nav=starting_nav,
            final_nav=final_nav,
            evaluation_days=int(config["selection"]["evaluation_days"]),
            positions=positions,
            plans=strategy.plans,
            logics=strategy.logic,
            errors=strategy.errors,
            lifecycle=strategy.lifecycle,
            gates=config["gates"],
        )
        result = engine.get_result() if hasattr(engine, "get_result") else None
        metrics.update({
            "week_id": week_id,
            "evaluation_start": evaluation_start.isoformat(),
            "evaluation_end_exclusive": evaluation_end.isoformat(),
            "bars": len(all_bars),
            "instruments": [str(instruments[symbol].id) for symbol in SYMBOLS],
            "effective_maker_rate": execution_config["effective_maker_rate"],
            "effective_taker_rate": execution_config["effective_taker_rate"],
            "candidate_rejections": strategy.rejections,
            "leadership_rejection_counts": dict(Counter(
                item.get("reason", "UNKNOWN")
                for item in strategy.rejections
                if item.get("type") == "MARKET_LEADERSHIP_REJECTED"
            )),
            "nautilus_result": {
                name: None if result is None else getattr(result, name, None)
                for name in ("iterations", "total_orders", "total_positions", "total_events")
            },
        })

        # Four independent engines are captured in symbol-subscription order.
        # Normalize the merged ledger by causal observation time before contract
        # validation; the stable original index preserves same-timestamp
        # transition order within each scenario.
        ordered_events = [
            event
            for _, event in sorted(
                enumerate(strategy.events),
                key=lambda item: (item[1].observed_time_ns, item[0]),
            )
        ]
        _write_raw_events(output_dir / "scenario_events.raw.jsonl", strategy.events)
        write_json_atomic(output_dir / "submitted_plans.json", {"plans": strategy.plans})
        write_json_atomic(output_dir / "order_lifecycle.json", {"events": strategy.lifecycle})
        event_log_error: str | None = None
        try:
            write_events(output_dir / "scenario_events.jsonl", ordered_events)
            metrics["event_log_valid"] = True
            metrics["event_log_error"] = None
        except EventLogError as exc:
            event_log_error = str(exc)
            metrics["event_log_valid"] = False
            metrics["event_log_error"] = event_log_error
            metrics["promising_gate_passed"] = False
            metrics["complete_gate_passed"] = False
            metrics["success_claim"] = False
        write_json_atomic(output_dir / "metrics.json", metrics)
        manifest_record = create_run_manifest(
            run_id=f"candidate-11-leadership-{week_id.lower()}-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}",
            candidate=metrics["candidate"],
            config_path=config_path,
            data_manifest_path=output_dir / "data_manifest.json",
            extra={
                "week_id": week_id,
                "symbols": list(SYMBOLS),
                "evaluation_start": evaluation_start.isoformat(),
                "evaluation_end_exclusive": evaluation_end.isoformat(),
                "logic": config["logic"],
                "execution": config["execution"],
                "metrics_path": str(output_dir / "metrics.json"),
                "event_log_valid": metrics["event_log_valid"],
            },
        )
        write_json_atomic(output_dir / "run.json", manifest_record)
        if event_log_error is not None:
            raise EventLogError(event_log_error)
        return metrics
    finally:
        engine.dispose()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=ROOT / "config.json")
    parser.add_argument("--week", choices=("W1", "W2", "W3", "W4", "W5", "W6", "W7", "W8", "W9"), default="W1")
    parser.add_argument("--output", type=Path, default=ROOT / "results" / "LEADERSHIP_W1")
    args = parser.parse_args()
    metrics = run(args.config.resolve(), args.week, args.output.resolve())
    print(json.dumps(metrics, indent=2, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
