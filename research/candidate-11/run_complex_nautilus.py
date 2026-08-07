#!/usr/bin/env python3
"""Four-market Candidate 11 research runner through NautilusTrader.

The synchronized detector emits plans only after completed source-session
auctions and same-timestamp BTC/ETH/SOL/XRP evidence.  NautilusTrader exclusively
owns time sequencing, order matching, fills, fees, margin, positions, and NAV.
"""
from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, ROUND_DOWN
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
import time
from typing import Any
from urllib.request import Request, urlopen
from zipfile import ZipFile

import pandas as pd
from zoneinfo import ZoneInfo

from bar_adapter import build_bars
from complex_engine import (
    AuctionContext,
    BarObs,
    ComplexSCDAMEngine,
    Direction,
    EngineConfig,
    SYMBOLS,
    TradePlan,
)
from global_allocator import Candidate, GlobalCandidateMutex, SlotState
from logic import RiskSizer

ROOT = Path(__file__).resolve().parent
NY = ZoneInfo("America/New_York")
UTC = timezone.utc

COLUMNS = (
    "open_time", "open", "high", "low", "close", "volume", "close_time",
    "quote_volume", "count", "taker_buy_volume", "taker_buy_quote_volume", "ignore",
)

META = {
    "BTCUSDT": {"base": "BTC", "price_precision": 1, "price_increment": "0.1", "size_precision": 3, "size_increment": "0.001", "min_qty": "0.001"},
    "ETHUSDT": {"base": "ETH", "price_precision": 2, "price_increment": "0.01", "size_precision": 3, "size_increment": "0.001", "min_qty": "0.001"},
    "SOLUSDT": {"base": "SOL", "price_precision": 3, "price_increment": "0.001", "size_precision": 1, "size_increment": "0.1", "min_qty": "0.1"},
    "XRPUSDT": {"base": "XRP", "price_precision": 4, "price_increment": "0.0001", "size_precision": 0, "size_increment": "1", "min_qty": "1"},
}

SESSION_PAIRS = (
    ("ASIA", 20, 0, -1, "LONDON", 2, 5),
    ("LONDON", 2, 5, 0, "NYAM", 7, 10),
    ("NYAM", 7, 10, 0, "US_LATE", 16, 20),
)


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False, default=str) + "\n", encoding="utf-8")
    temp.replace(path)


def _download(url: str, destination: Path, retries: int = 4) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists() and destination.stat().st_size > 100:
        try:
            with ZipFile(destination) as archive:
                if archive.testzip() is None:
                    return
        except Exception:
            destination.unlink(missing_ok=True)
    last: Exception | None = None
    for attempt in range(retries):
        try:
            request = Request(url, headers={"User-Agent": "SMC-ICT-4-candidate-11"})
            with urlopen(request, timeout=60) as response:  # noqa: S310 frozen HTTPS data host
                payload = response.read()
            if len(payload) < 100:
                raise RuntimeError("unexpectedly small archive")
            temp = destination.with_suffix(".tmp")
            temp.write_bytes(payload)
            with ZipFile(temp) as archive:
                bad = archive.testzip()
                if bad is not None:
                    raise RuntimeError(f"corrupt member {bad}")
            temp.replace(destination)
            return
        except Exception as exc:
            last = exc
            if attempt + 1 < retries:
                time.sleep(2 ** attempt)
    raise RuntimeError(f"failed to download {url}: {last}")


def load_symbol_bars(symbol: str, start: date, end_inclusive: date, data_dir: Path) -> tuple[pd.DataFrame, list[dict[str, Any]]]:
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


def local_bounds(day: date, start_hour: int, end_hour: int) -> tuple[pd.Timestamp, pd.Timestamp]:
    start = datetime(day.year, day.month, day.day, start_hour, tzinfo=NY)
    if end_hour <= start_hour:
        end_day = day + timedelta(days=1)
    else:
        end_day = day
    end = datetime(end_day.year, end_day.month, end_day.day, end_hour, tzinfo=NY)
    return pd.Timestamp(start.astimezone(UTC)), pd.Timestamp(end.astimezone(UTC))


def _session_range(frame: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp) -> tuple[float, float] | None:
    # Observations are timestamped at close.  A source bar belongs to a local
    # interval when its open is in [start, end), hence close is in (start, end].
    sample = frame[(frame.index > start) & (frame.index <= end)]
    expected = int((end - start) / pd.Timedelta(minutes=1))
    if len(sample.index) < expected - 2:
        return None
    return float(sample["low"].min()), float(sample["high"].max())


def _previous_utc_day_range(frame: pd.DataFrame, target_day: date) -> tuple[float, float] | None:
    start = pd.Timestamp(target_day - timedelta(days=1), tz="UTC")
    end = pd.Timestamp(target_day, tz="UTC")
    sample = frame[(frame.index > start) & (frame.index <= end)]
    if len(sample.index) < 1438:
        return None
    return float(sample["low"].min()), float(sample["high"].max())


def build_contexts(
    frames: dict[str, pd.DataFrame],
    evaluation_start: date,
    evaluation_end: date,
) -> dict[tuple[str, int], AuctionContext]:
    contexts: dict[tuple[str, int], AuctionContext] = {}
    cursor = evaluation_start
    while cursor < evaluation_end:
        for source_name, source_start, source_end, source_day_offset, target_name, target_start, target_end in SESSION_PAIRS:
            source_day = cursor + timedelta(days=source_day_offset)
            src_start, src_end = local_bounds(source_day, source_start, source_end)
            tgt_start, tgt_end = local_bounds(cursor, target_start, target_end)
            for symbol, frame in frames.items():
                source = _session_range(frame, src_start, src_end)
                previous = _previous_utc_day_range(frame, cursor)
                if source is None or previous is None:
                    continue
                source_low, source_high = source
                prev_low, prev_high = previous
                external_low = min(prev_low, source_low)
                external_high = max(prev_high, source_high)
                context = AuctionContext(
                    source_session=source_name,
                    target_session=target_name,
                    source_low=source_low,
                    source_high=source_high,
                    external_low=external_low,
                    external_high=external_high,
                    valid_until_ns=int(tgt_end.value),
                )
                target_index = frame.index[(frame.index > tgt_start) & (frame.index <= tgt_end)]
                for timestamp in target_index:
                    if timestamp >= pd.Timestamp(evaluation_end, tz="UTC"):
                        continue
                    contexts[(symbol, int(timestamp.value))] = context
        cursor += timedelta(days=1)
    return contexts


def _decimal(value: Any, default: Decimal = Decimal("0")) -> Decimal:
    if value is None:
        return default
    text = str(value).strip()
    if not text or text.lower() == "nan":
        return default
    # Money values render as "123.45 USDT".
    return Decimal(text.split()[0])


def _closed_pnls(positions: pd.DataFrame) -> list[Decimal]:
    if positions.empty:
        return []
    column = next((name for name in ("realized_pnl", "pnl") if name in positions.columns), None)
    if column is None:
        return []
    return [_decimal(value) for value in positions[column].tolist()]


def _metrics(
    *,
    starting_nav: Decimal,
    final_nav: Decimal,
    evaluation_days: int,
    positions: pd.DataFrame,
    plans: list[dict[str, Any]],
    errors: list[dict[str, Any]],
    detector: ComplexSCDAMEngine,
) -> dict[str, Any]:
    pnls = _closed_pnls(positions)
    wins = [x for x in pnls if x > 0]
    losses = [x for x in pnls if x < 0]
    win_rate = len(wins) / len(pnls) if pnls else 0.0
    payoff = None
    if wins and losses:
        payoff = float((sum(wins) / len(wins)) / abs(sum(losses) / len(losses)))
    elif wins:
        payoff = float("inf")
    growth = float((final_nav / starting_nav) ** (Decimal(1) / Decimal(evaluation_days)) - 1) if final_nav > 0 else -1.0
    equity = starting_nav
    peak = starting_nav
    max_dd = Decimal("0")
    for pnl in pnls:
        equity += pnl
        peak = max(peak, equity)
        if peak > 0:
            max_dd = max(max_dd, (peak - equity) / peak)
    promising = (
        len(pnls) >= 5
        and win_rate >= 0.50
        and payoff is not None
        and payoff >= 1.0
        and growth > 0
        and not errors
    )
    complete = (
        len(pnls) >= 7
        and win_rate >= 0.65
        and payoff is not None
        and payoff >= 1.2
        and growth >= 0.01
        and float(max_dd) <= 0.20
        and not errors
    )
    return {
        "candidate": "candidate-11-synchronized-complex-scdam",
        "evidence_class": "NAUTILUS_ACCOUNT_NAV",
        "starting_nav": str(starting_nav),
        "final_nav": str(final_nav),
        "net_return": float(final_nav / starting_nav - 1),
        "daily_geometric_growth": growth,
        "evaluation_calendar_days": evaluation_days,
        "closed_trades": len(pnls),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": win_rate,
        "payoff_ratio": None if payoff == float("inf") else payoff,
        "all_closed_trades_won": bool(wins) and not losses,
        "closed_trade_max_drawdown": float(max_dd),
        "submitted_plans": len(plans),
        "scenario_counts": {
            scenario: sum(plan["scenario"] == scenario for plan in plans)
            for scenario in ("FAR", "AAC")
        },
        "symbol_counts": {
            symbol: sum(plan["symbol"] == symbol for plan in plans)
            for symbol in SYMBOLS
        },
        "detector_events": len(detector.events),
        "skip_reasons": detector.skip_reasons,
        "engine_errors": errors,
        "liquidation_detected": False,
        "promising_gate_passed": promising,
        "complete_gate_passed": complete,
        # A screening week cannot make a durable success claim.
        "success_claim": False,
    }


def run(config_path: Path, week_id: str, output_dir: Path) -> dict[str, Any]:
    from nautilus_trader.backtest.engine import BacktestEngine
    from nautilus_trader.backtest.models import FillModel
    from nautilus_trader.config import BacktestEngineConfig, LoggingConfig, StrategyConfig
    from nautilus_trader.model.currencies import USDT
    from nautilus_trader.model.data import Bar, BarType
    from nautilus_trader.model.enums import AccountType, OmsType, OrderSide, OrderType, TimeInForce
    from nautilus_trader.model.identifiers import InstrumentId, Symbol, Venue
    from nautilus_trader.model.instruments import CryptoPerpetual
    from nautilus_trader.model.objects import Currency, Money, Price, Quantity
    from nautilus_trader.trading.strategy import Strategy

    config = json.loads(config_path.read_text(encoding="utf-8"))
    selected = config["weeks"][week_id]
    evaluation_start = date.fromisoformat(selected["start"])
    evaluation_end = date.fromisoformat(selected["end_exclusive"])
    warmup_start = evaluation_start - timedelta(days=int(config["warmup_days"]))
    output_dir.mkdir(parents=True, exist_ok=True)

    frames: dict[str, pd.DataFrame] = {}
    manifest: list[dict[str, Any]] = []
    for symbol in SYMBOLS:
        frames[symbol], files = load_symbol_bars(
            symbol,
            warmup_start,
            evaluation_end,
            output_dir / "data",
        )
        manifest.extend(files)
    contexts = build_contexts(frames, evaluation_start, evaluation_end)
    write_json(output_dir / "data_manifest.json", {
        "schema": "candidate-11-complex-source-manifest-v1",
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
    if any(currency is None for currency in base_currencies.values()):
        raise RuntimeError("Nautilus currency registry could not resolve an allowed base asset")
    venue = Venue("BINANCE")
    account_cfg = config["account"]
    execution_cfg = config["execution"]
    instruments: dict[str, Any] = {}
    bar_types: dict[str, Any] = {}
    all_bars: list[Any] = []
    flow: dict[tuple[str, int], tuple[float, float]] = {}
    for symbol in SYMBOLS:
        meta = META[symbol]
        instrument_id = InstrumentId(symbol=Symbol(f"{symbol}-PERP"), venue=venue)
        price_precision = int(meta["price_precision"])
        size_precision = int(meta["size_precision"])
        instrument = CryptoPerpetual(
            instrument_id=instrument_id,
            raw_symbol=Symbol(symbol),
            base_currency=base_currencies[symbol],
            quote_currency=USDT,
            settlement_currency=USDT,
            is_inverse=False,
            price_precision=price_precision,
            price_increment=Price.from_str(meta["price_increment"]),
            size_precision=size_precision,
            size_increment=Quantity.from_str(meta["size_increment"]),
            max_quantity=Quantity.from_str(format(1_000_000_000, f".{size_precision}f")),
            min_quantity=Quantity.from_str(meta["min_qty"]),
            max_notional=None,
            min_notional=Money(10.0, USDT),
            max_price=Price.from_str(format(10_000_000, f".{price_precision}f")),
            min_price=Price.from_str(meta["price_increment"]),
            margin_init=Decimal(account_cfg["margin_init"]),
            margin_maint=Decimal(account_cfg["margin_maint"]),
            maker_fee=Decimal(execution_cfg["effective_maker_rate"]),
            taker_fee=Decimal(execution_cfg["effective_taker_rate"]),
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

    logic_cfg = EngineConfig(**config["logic"])
    starting_nav = Decimal(account_cfg["starting_nav"])
    evaluation_start_ns = int(pd.Timestamp(evaluation_start, tz="UTC").value)
    evaluation_end_ns = int(pd.Timestamp(evaluation_end, tz="UTC").value)

    class ComplexStrategyConfig(StrategyConfig, frozen=True):
        instrument_ids: tuple[InstrumentId, ...]
        bar_types: tuple[BarType, ...]
        evaluation_start_ns: int
        evaluation_end_ns: int
        starting_nav: Decimal

    class ComplexStrategy(Strategy):
        def __init__(self, strategy_config: ComplexStrategyConfig) -> None:
            super().__init__(strategy_config)
            self.detector = ComplexSCDAMEngine(logic_cfg)
            self.mutex = GlobalCandidateMutex()
            self.buffer_ts: int | None = None
            self.buffer: dict[str, BarObs] = {}
            self.plans: list[dict[str, Any]] = []
            self.errors: list[dict[str, Any]] = []
            self.rejections: list[dict[str, Any]] = []
            self.lifecycle: list[dict[str, Any]] = []
            self.active_plan: TradePlan | None = None
            self.last_ts_ns = 0

        def on_start(self) -> None:
            for bar_type in self.config.bar_types:
                self.subscribe_bars(bar_type)

        def _symbol(self, bar: Bar) -> str:
            instrument_id = str(bar.bar_type.instrument_id)
            return instrument_id.split("-PERP", 1)[0]

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

        def _release_if_terminal(self, ts_ns: int, reason: str) -> None:
            if not self._all_flat() or self._open_orders() != 0 or self.active_plan is None:
                return
            try:
                if self.mutex.state == SlotState.ENTRY_PENDING:
                    self.mutex.mark_entry_terminal(self.active_plan.scenario_id)
                elif self.mutex.state == SlotState.POSITION_OPEN:
                    self.mutex.mark_position_closed(self.active_plan.scenario_id)
            except Exception as exc:
                self.errors.append({"type": "MUTEX_RELEASE_ERROR", "message": str(exc), "ts_ns": ts_ns})
            self.lifecycle.append({"type": "GLOBAL_SLOT_RELEASED", "ts_ns": ts_ns, "reason": reason, "scenario_id": self.active_plan.scenario_id})
            self.active_plan = None

        def _submit(self, plan: TradePlan) -> None:
            if self.mutex.state != SlotState.FREE or not self._all_flat() or self._open_orders() != 0:
                return
            instrument = instruments[plan.symbol]
            nav, free = self._account_values()
            decision = RiskSizer(0.03).size(
                nav=nav,
                loss_per_unit=Decimal(str(plan.loss_per_unit)),
                entry_price=Decimal(str(plan.expected_entry)),
                quantity_increment=Decimal(str(instrument.size_increment)),
                min_quantity=Decimal(str(instrument.min_quantity)),
                min_notional=_decimal(instrument.min_notional),
                margin_init=instrument.margin_init,
                free_balance=free,
            )
            if not decision.feasible:
                record = {
                    "type": "RISK_SIZE_REJECTED",
                    "reason": decision.reason,
                    "symbol": plan.symbol,
                    "scenario_id": plan.scenario_id,
                    "required_margin": str(decision.required_margin),
                    "free_balance": str(free),
                }
                self.rejections.append(record)
                self.lifecycle.append({"ts_ns": self.last_ts_ns, **record})
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
                candidate = Candidate(
                    symbol=plan.symbol,
                    scenario_id=plan.scenario_id,
                    observed_ts_ns=plan.observed_ts_ns,
                    net_structural_r=Decimal(str(plan.net_r)),
                    expected_entry=Decimal(str(plan.expected_entry)),
                    expected_loss_per_unit=Decimal(str(plan.loss_per_unit)),
                )
                self.mutex.mark_entry_submitted(candidate)
                self.active_plan = plan
                self.submit_order_list(order_list)
            except Exception as exc:
                if self.mutex.state == SlotState.ENTRY_PENDING and self.active_plan is not None:
                    self.mutex.mark_entry_terminal(self.active_plan.scenario_id)
                self.active_plan = None
                self.errors.append({
                    "type": "ORDER_LIST_SUBMISSION_EXCEPTION",
                    "exception": type(exc).__name__,
                    "message": str(exc),
                    "symbol": plan.symbol,
                    "scenario_id": plan.scenario_id,
                })
                return
            record = {
                "symbol": plan.symbol,
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
            }
            self.plans.append(record)
            self.lifecycle.append({"type": "ENTRY_ORDER_LIST_SUBMITTED", "ts_ns": self.last_ts_ns, **record})

        def _process_snapshot(self) -> None:
            if self.buffer_ts is None or len(self.buffer) != len(SYMBOLS):
                return
            ts_ns = self.buffer_ts
            context_map = {
                symbol: context
                for symbol in SYMBOLS
                if (context := contexts.get((symbol, ts_ns))) is not None
            }
            plans = self.detector.on_snapshot(dict(self.buffer), context_map)
            if ts_ns < self.config.evaluation_start_ns:
                return
            for plan in plans:
                self.mutex.add(Candidate(
                    symbol=plan.symbol,
                    scenario_id=plan.scenario_id,
                    observed_ts_ns=plan.observed_ts_ns,
                    net_structural_r=Decimal(str(plan.net_r)),
                    expected_entry=Decimal(str(plan.expected_entry)),
                    expected_loss_per_unit=Decimal(str(plan.loss_per_unit)),
                ))
            arbitration = self.mutex.flush()
            for rejected, reason in arbitration.rejected:
                self.rejections.append({
                    "type": "GLOBAL_CANDIDATE_REJECTED",
                    "reason": reason,
                    "symbol": rejected.symbol,
                    "scenario_id": rejected.scenario_id,
                    "observed_ts_ns": rejected.observed_ts_ns,
                    "net_structural_r": str(rejected.net_structural_r),
                })
            if arbitration.winner is None:
                return
            by_id = {plan.scenario_id: plan for plan in plans}
            winner = by_id.get(arbitration.winner.scenario_id)
            if winner is not None:
                self._submit(winner)

        def on_bar(self, bar: Bar) -> None:
            self.last_ts_ns = int(bar.ts_event)
            self._release_if_terminal(self.last_ts_ns, "FLAT_NO_WORKING_ORDERS")
            if self.last_ts_ns >= self.config.evaluation_end_ns:
                if self._open_orders() > 0:
                    for instrument_id in self.config.instrument_ids:
                        self.cancel_all_orders(instrument_id)
                for instrument_id in self.config.instrument_ids:
                    if not self.portfolio.is_flat(instrument_id):
                        self.close_all_positions(instrument_id)
                return
            symbol = self._symbol(bar)
            if self.buffer_ts is None:
                self.buffer_ts = self.last_ts_ns
            if self.last_ts_ns != self.buffer_ts:
                self._process_snapshot()
                self.buffer.clear()
                self.buffer_ts = self.last_ts_ns
            volume, taker = flow[(str(bar.bar_type.instrument_id), self.last_ts_ns)]
            self.buffer[symbol] = BarObs(
                symbol=symbol,
                ts_ns=self.last_ts_ns,
                open=float(str(bar.open)),
                high=float(str(bar.high)),
                low=float(str(bar.low)),
                close=float(str(bar.close)),
                volume=volume,
                taker_buy_volume=taker,
            )
            if len(self.buffer) == len(SYMBOLS):
                self._process_snapshot()
                self.buffer.clear()
                self.buffer_ts = None

        def on_order_filled(self, event: Any) -> None:
            self.lifecycle.append({"type": "ORDER_FILLED", "ts_ns": int(event.ts_event), "event": str(event)})
            if self.active_plan is not None and self.mutex.state == SlotState.ENTRY_PENDING:
                try:
                    self.mutex.mark_entry_filled(self.active_plan.scenario_id)
                except Exception as exc:
                    self.errors.append({"type": "MUTEX_FILL_ERROR", "message": str(exc)})
            self._release_if_terminal(int(event.ts_event), "ORDER_FILL_TERMINAL")

        def on_order_expired(self, event: Any) -> None:
            self.lifecycle.append({"type": "ORDER_EXPIRED", "ts_ns": int(event.ts_event), "event": str(event)})
            self._release_if_terminal(int(event.ts_event), "ENTRY_EXPIRED")

        def on_order_canceled(self, event: Any) -> None:
            self.lifecycle.append({"type": "ORDER_CANCELED", "ts_ns": int(event.ts_event), "event": str(event)})
            self._release_if_terminal(int(event.ts_event), "ORDER_CANCELED")

        def on_order_denied(self, event: Any) -> None:
            self.errors.append({"type": "ORDER_DENIED", "event": str(event)})
            self._release_if_terminal(int(event.ts_event), "ORDER_DENIED")

        def on_order_rejected(self, event: Any) -> None:
            self.errors.append({"type": "ORDER_REJECTED", "event": str(event)})
            self._release_if_terminal(int(event.ts_event), "ORDER_REJECTED")

        def on_stop(self) -> None:
            for instrument_id in self.config.instrument_ids:
                self.cancel_all_orders(instrument_id)
                if not self.portfolio.is_flat(instrument_id):
                    self.close_all_positions(instrument_id)

    engine = BacktestEngine(config=BacktestEngineConfig(logging=LoggingConfig(log_level="ERROR")))
    fill_model = FillModel(
        prob_fill_on_limit=float(execution_cfg["prob_fill_on_limit"]),
        prob_slippage=float(execution_cfg["prob_slippage"]),
        random_seed=int(execution_cfg["random_seed"]),
    )
    strategy = ComplexStrategy(ComplexStrategyConfig(
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
            default_leverage=Decimal(account_cfg["default_leverage"]),
            fill_model=fill_model,
            bar_adaptive_high_low_ordering=True,
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
            raise RuntimeError("Nautilus account missing")
        final_nav = _decimal(account.balance_total(USDT))
        metrics = _metrics(
            starting_nav=starting_nav,
            final_nav=final_nav,
            evaluation_days=(evaluation_end - evaluation_start).days,
            positions=positions,
            plans=strategy.plans,
            errors=strategy.errors,
            detector=strategy.detector,
        )
        metrics["candidate_rejections"] = strategy.rejections
        metrics["liquidation_detected"] = any(
            "LIQUIDAT" in json.dumps(item, default=str).upper()
            for item in strategy.lifecycle
        )
        metrics.update({
            "week_id": week_id,
            "evaluation_start": evaluation_start.isoformat(),
            "evaluation_end_exclusive": evaluation_end.isoformat(),
            "bars": len(all_bars),
            "contexts": len(contexts),
            "nautilus_result": {
                name: getattr(engine.get_result(), name, None)
                for name in ("iterations", "total_orders", "total_positions", "total_events")
            },
        })
        write_json(output_dir / "metrics.json", metrics)
        write_json(output_dir / "submitted_plans.json", {"plans": strategy.plans})
        write_json(output_dir / "order_lifecycle.json", {"events": strategy.lifecycle})
        write_json(output_dir / "detector_events.json", {"events": strategy.detector.events})
        write_json(output_dir / "run.json", {
            "candidate": metrics["candidate"],
            "week_id": week_id,
            "config": str(config_path),
            "config_sha256": sha256(config_path.read_bytes()).hexdigest(),
            "data_manifest_sha256": sha256((output_dir / "data_manifest.json").read_bytes()).hexdigest(),
            "metrics_sha256": sha256((output_dir / "metrics.json").read_bytes()).hexdigest(),
            "generated_at": datetime.now(UTC).isoformat(),
        })
        return metrics
    finally:
        engine.dispose()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=ROOT / "complex_config.json")
    parser.add_argument("--week", choices=("W1", "W2", "W3"), default="W1")
    parser.add_argument("--output", type=Path, default=ROOT / "results" / "COMPLEX_W1")
    args = parser.parse_args()
    result = run(args.config.resolve(), args.week, args.output.resolve())
    print(json.dumps(result, indent=2, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
