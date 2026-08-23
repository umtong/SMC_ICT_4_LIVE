"""Fast causal 5-minute replay for the replacement campaign policy.

The harness is intentionally small and independent of NautilusTrader.  It is
for inspecting market logic before running the exact same policy through the
production adapter.  In particular it does not score, filter, or manufacture
trading opportunities.

Causal clock
------------
An intent emitted from a completed frame cannot fill in that frame.  Existing
orders and positions are advanced first, and the policy then observes the
completed frame.  A pending limit fills when a later bar trades through its
declared entry.  The target is never credited on the fill bar, a stop is.  On
a later bar where both barriers trade, the stop wins.  An adverse stop gap is
filled at the bar open.  There is no time exit and an unresolved position is
reported as ``OPEN``.

The policy may expose two optional research-only methods without changing its
execution API::

    intent_replay_context(intent_id) -> Mapping
    drain_replay_opportunities() -> Iterable[Mapping]

The first supplies source/owner/route labels for a submitted intent.  The
second supplies all candidate opportunities considered at the last decision;
unselected records are deterministically sampled for chart inspection.  The
records are observations only and never feed back into policy decisions.
"""
from __future__ import annotations

import argparse
import csv
from dataclasses import asdict, dataclass, field
from hashlib import sha256
import importlib
import json
import math
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping, Sequence
import zipfile

from smc_ict_4.episode_policy_live.domain import Bar
from smc_ict_4.episode_policy_live.neutral_policy import (
    ExecutionFeedback,
    MARKET_SYMBOLS,
    MarketFrame,
    OrderIntent,
    PolicyOutput,
    TradingPolicy,
)


class FastReplayError(RuntimeError):
    """The input data or policy violated the causal replay contract."""


@dataclass(frozen=True, slots=True)
class IntentReplayContext:
    source_id: str = ""
    owner: str = ""
    route: str = ""
    evidence: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class NoTradeOpportunity:
    decision_time_ns: int
    symbol: str
    side: str
    source_id: str
    owner: str
    route: str
    reason: str
    entry: float | None
    stop: float | None
    target: float | None
    evidence: Mapping[str, Any]
    sample_key: str


@dataclass(frozen=True, slots=True)
class TradePath:
    intent_id: str
    symbol: str
    side: str
    source_id: str
    owner: str
    route: str
    decision_time_ns: int
    entry_time_ns: int
    exit_time_ns: int | None
    entry_bar_open_time_ns: int
    exit_bar_open_time_ns: int | None
    entry: float
    stop: float
    target: float
    exit_price: float | None
    outcome: str
    gross_r: float | None
    mfe_r: float
    mae_r: float
    holding_minutes: float | None
    bars_held: int
    evidence: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class ReplayResult:
    frames: int
    intents_submitted: int
    pending_canceled: int
    no_trade_opportunities_seen: int
    trades: tuple[TradePath, ...]
    sampled_no_trades: tuple[NoTradeOpportunity, ...]
    pending_intent_id: str | None
    open_intent_id: str | None


@dataclass(slots=True)
class _OpenTrade:
    intent: OrderIntent
    context: IntentReplayContext
    fill_price: float
    entry_time_ns: int
    entry_bar_open_time_ns: int
    risk: float
    mfe_r: float = 0.0
    mae_r: float = 0.0
    bars_held: int = 0


class _DeterministicSample:
    """Keep the smallest content hashes, independent of replay order."""

    def __init__(self, capacity: int) -> None:
        if capacity < 0:
            raise ValueError("sample capacity cannot be negative")
        self.capacity = capacity
        self.seen = 0
        self._items: dict[str, NoTradeOpportunity] = {}

    def add(self, item: NoTradeOpportunity) -> None:
        self.seen += 1
        if self.capacity == 0:
            return
        self._items[item.sample_key] = item
        if len(self._items) > self.capacity:
            del self._items[max(self._items)]

    def items(self) -> tuple[NoTradeOpportunity, ...]:
        return tuple(self._items[key] for key in sorted(self._items))


def discover_binance_vision_5m(
    cache_root: str | Path,
    *,
    months: Sequence[str] | None = None,
) -> dict[str, tuple[Path, ...]]:
    """Discover one official monthly 5m archive per symbol/month."""

    root = Path(cache_root)
    if not root.is_dir():
        raise FastReplayError(f"cache root does not exist: {root}")
    wanted = None if months is None else {str(month) for month in months}
    result: dict[str, tuple[Path, ...]] = {}
    for symbol in MARKET_SYMBOLS:
        matches = sorted(root.rglob(f"{symbol}-5m-*.zip"))
        selected: list[Path] = []
        month_paths: dict[str, Path] = {}
        prefix = f"{symbol}-5m-"
        for path in matches:
            month = path.stem[len(prefix) :]
            if wanted is not None and month not in wanted:
                continue
            previous = month_paths.get(month)
            if previous is not None and previous.resolve() != path.resolve():
                raise FastReplayError(
                    f"duplicate {symbol} 5m archive for {month}: {previous}, {path}",
                )
            month_paths[month] = path
        if wanted is not None:
            missing = wanted - set(month_paths)
            if missing:
                raise FastReplayError(f"missing {symbol} archives: {sorted(missing)}")
        selected.extend(month_paths[key] for key in sorted(month_paths))
        if not selected:
            raise FastReplayError(f"no 5m archives found for {symbol}")
        result[symbol] = tuple(selected)
    return result


def _iter_archive(symbol: str, path: Path) -> Iterator[Bar]:
    try:
        archive = zipfile.ZipFile(path)
    except (OSError, zipfile.BadZipFile) as exc:
        raise FastReplayError(f"cannot read archive: {path}") from exc
    with archive:
        csv_members = [name for name in archive.namelist() if name.lower().endswith(".csv")]
        if len(csv_members) != 1:
            raise FastReplayError(f"archive must contain one CSV: {path}")
        with archive.open(csv_members[0]) as raw:
            rows = (line.decode("utf-8") for line in raw)
            reader = csv.DictReader(rows)
            required = {
                "open_time", "open", "high", "low", "close", "volume",
                "close_time", "quote_volume", "count", "taker_buy_quote_volume",
            }
            if reader.fieldnames is None or not required.issubset(reader.fieldnames):
                raise FastReplayError(f"unexpected Binance kline schema: {path}")
            previous_open: int | None = None
            for row in reader:
                open_ms = int(row["open_time"])
                close_ms = int(row["close_time"])
                logical_close_ms = open_ms + 5 * 60 * 1_000
                if close_ms not in {logical_close_ms - 1, logical_close_ms}:
                    raise FastReplayError(f"unexpected 5m close clock in {path}")
                if previous_open is not None and open_ms <= previous_open:
                    raise FastReplayError(f"non-increasing bars in {path}")
                previous_open = open_ms
                yield Bar(
                    symbol=symbol,
                    interval_minutes=5,
                    open_time_ns=open_ms * 1_000_000,
                    # Internal event time uses the exclusive logical close.
                    # Binance archives encode the final included millisecond.
                    close_time_ns=logical_close_ms * 1_000_000,
                    open=float(row["open"]),
                    high=float(row["high"]),
                    low=float(row["low"]),
                    close=float(row["close"]),
                    volume=float(row["volume"]),
                    quote_volume=float(row["quote_volume"]),
                    taker_buy_quote_volume=float(row["taker_buy_quote_volume"]),
                    trade_count=int(row["count"]),
                )


def _iter_symbol(symbol: str, archives: Sequence[Path]) -> Iterator[Bar]:
    previous_open: int | None = None
    for path in archives:
        for bar in _iter_archive(symbol, path):
            if previous_open is not None and bar.open_time_ns <= previous_open:
                raise FastReplayError(f"overlapping or unordered {symbol} archives")
            previous_open = bar.open_time_ns
            yield bar


def iter_synchronized_frames(
    archives: Mapping[str, Sequence[Path]],
) -> Iterator[MarketFrame]:
    """Stream strict four-symbol frames without pandas or full-data loading."""

    if set(archives) != set(MARKET_SYMBOLS):
        raise FastReplayError("archives must contain exactly the four market symbols")
    iterators = {symbol: iter(_iter_symbol(symbol, archives[symbol])) for symbol in MARKET_SYMBOLS}
    previous_close: int | None = None
    while True:
        bars: list[Bar] = []
        ended: list[str] = []
        for symbol in MARKET_SYMBOLS:
            try:
                bars.append(next(iterators[symbol]))
            except StopIteration:
                ended.append(symbol)
        if ended:
            if len(ended) != len(MARKET_SYMBOLS):
                raise FastReplayError(f"symbols end at different clocks: {ended}")
            return
        clocks = {(bar.open_time_ns, bar.close_time_ns) for bar in bars}
        if len(clocks) != 1:
            by_symbol = {bar.symbol: bar.open_time_ns for bar in bars}
            raise FastReplayError(f"missing or misaligned synchronized bar: {by_symbol}")
        frame = MarketFrame(tuple(bars))
        if previous_close is not None and frame.close_time_ns <= previous_close:
            raise FastReplayError("frames are not strictly increasing")
        previous_close = frame.close_time_ns
        yield frame


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _intent_context(policy: TradingPolicy, intent: OrderIntent) -> IntentReplayContext:
    provider = getattr(policy, "intent_replay_context", None)
    payload = _mapping(provider(intent.intent_id)) if callable(provider) else {}
    known = {"source_id", "source", "owner", "route", "evidence"}
    evidence = _mapping(payload.get("evidence"))
    evidence.update({key: value for key, value in payload.items() if key not in known})
    return IntentReplayContext(
        source_id=str(payload.get("source_id", payload.get("source", ""))),
        owner=str(payload.get("owner", "")),
        route=str(payload.get("route", "")),
        evidence=evidence,
    )


def _no_trade_records(policy: TradingPolicy, decision_time_ns: int) -> Iterable[NoTradeOpportunity]:
    provider = getattr(policy, "drain_replay_opportunities", None)
    if not callable(provider):
        return ()
    records: list[NoTradeOpportunity] = []
    for raw in provider():
        payload = _mapping(raw)
        if bool(payload.get("selected", False)):
            continue
        known = {
            "decision_time_ns", "symbol", "side", "source_id", "source", "owner",
            "route", "reason", "entry", "stop", "target", "selected", "evidence",
        }
        evidence = _mapping(payload.get("evidence"))
        evidence.update({key: value for key, value in payload.items() if key not in known})
        canonical = json.dumps(
            {**payload, "decision_time_ns": int(payload.get("decision_time_ns", decision_time_ns))},
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
        sample_key = sha256(canonical.encode("utf-8")).hexdigest()

        def optional_price(name: str) -> float | None:
            value = payload.get(name)
            if value is None:
                return None
            result = float(value)
            if not math.isfinite(result):
                raise FastReplayError(f"non-finite no-trade {name}")
            return result

        records.append(
            NoTradeOpportunity(
                decision_time_ns=int(payload.get("decision_time_ns", decision_time_ns)),
                symbol=str(payload.get("symbol", "")),
                side=str(payload.get("side", "")),
                source_id=str(payload.get("source_id", payload.get("source", ""))),
                owner=str(payload.get("owner", "")),
                route=str(payload.get("route", "")),
                reason=str(payload.get("reason", "not_selected")),
                entry=optional_price("entry"),
                stop=optional_price("stop"),
                target=optional_price("target"),
                evidence=evidence,
                sample_key=sample_key,
            )
        )
    return tuple(records)


def _feedback(
    policy: TradingPolicy,
    intent: OrderIntent,
    event_time_ns: int,
    status: str,
    *,
    fill_price: float | None = None,
    reason: str | None = None,
) -> None:
    policy.on_execution_feedback(
        ExecutionFeedback(
            intent_id=intent.intent_id,
            event_time_ns=event_time_ns,
            status=status,
            fill_price=fill_price,
            filled_quantity=1.0 if fill_price is not None else None,
            reason=reason,
        )
    )


def _entry_fill(intent: OrderIntent, bar: Bar) -> float | None:
    """Return the causal trigger fill, including directional gap slippage."""

    if intent.entry_order_type == "STOP":
        if intent.side == "LONG":
            return max(intent.entry, bar.open) if bar.high >= intent.entry else None
        return min(intent.entry, bar.open) if bar.low <= intent.entry else None
    if intent.side == "LONG":
        return min(intent.entry, bar.open) if bar.low <= intent.entry else None
    return max(intent.entry, bar.open) if bar.high >= intent.entry else None


def _stop_hit(intent: OrderIntent, bar: Bar) -> bool:
    return bar.low <= intent.stop if intent.side == "LONG" else bar.high >= intent.stop


def _target_hit(intent: OrderIntent, bar: Bar) -> bool:
    return bar.high >= intent.target if intent.side == "LONG" else bar.low <= intent.target


def _stop_fill(intent: OrderIntent, bar: Bar) -> float:
    if intent.side == "LONG" and bar.open < intent.stop:
        return bar.open
    if intent.side == "SHORT" and bar.open > intent.stop:
        return bar.open
    return intent.stop


def _signed_r(intent: OrderIntent, price: float, *, fill_price: float | None = None) -> float:
    direction = 1.0 if intent.side == "LONG" else -1.0
    entry = intent.entry if fill_price is None else fill_price
    return direction * (price - entry) / intent.risk_distance


def _finish(
    trade: _OpenTrade,
    bar: Bar,
    outcome: str,
    exit_price: float,
) -> TradePath:
    intent = trade.intent
    gross_r = _signed_r(intent, exit_price, fill_price=trade.fill_price)
    if outcome == "TARGET":
        # The adverse extreme may have occurred before the known target touch.
        adverse = _signed_r(
            intent,
            bar.low if intent.side == "LONG" else bar.high,
            fill_price=trade.fill_price,
        )
        trade.mae_r = min(trade.mae_r, adverse)
        trade.mfe_r = max(
            trade.mfe_r,
            _signed_r(intent, intent.target, fill_price=trade.fill_price),
        )
    else:
        # Stop-first convention: do not credit an unknowable favorable move on
        # the terminal bar, but retain the actual adverse gap in MAE.
        trade.mae_r = min(trade.mae_r, gross_r)
    trade.bars_held += 1
    holding = (bar.close_time_ns - trade.entry_time_ns) / 60_000_000_000
    return TradePath(
        intent_id=intent.intent_id,
        symbol=intent.symbol,
        side=intent.side,
        source_id=trade.context.source_id,
        owner=trade.context.owner,
        route=trade.context.route,
        decision_time_ns=intent.decision_time_ns,
        entry_time_ns=trade.entry_time_ns,
        exit_time_ns=bar.close_time_ns,
        entry_bar_open_time_ns=trade.entry_bar_open_time_ns,
        exit_bar_open_time_ns=bar.open_time_ns,
        entry=trade.fill_price,
        stop=intent.stop,
        target=intent.target,
        exit_price=exit_price,
        outcome=outcome,
        gross_r=gross_r,
        mfe_r=trade.mfe_r,
        mae_r=trade.mae_r,
        holding_minutes=holding,
        bars_held=trade.bars_held,
        evidence=trade.context.evidence,
    )


def _open_path(trade: _OpenTrade) -> TradePath:
    intent = trade.intent
    return TradePath(
        intent_id=intent.intent_id,
        symbol=intent.symbol,
        side=intent.side,
        source_id=trade.context.source_id,
        owner=trade.context.owner,
        route=trade.context.route,
        decision_time_ns=intent.decision_time_ns,
        entry_time_ns=trade.entry_time_ns,
        exit_time_ns=None,
        entry_bar_open_time_ns=trade.entry_bar_open_time_ns,
        exit_bar_open_time_ns=None,
        entry=trade.fill_price,
        stop=intent.stop,
        target=intent.target,
        exit_price=None,
        outcome="OPEN",
        gross_r=None,
        mfe_r=trade.mfe_r,
        mae_r=trade.mae_r,
        holding_minutes=None,
        bars_held=trade.bars_held,
        evidence=trade.context.evidence,
    )


def run_fast_replay(
    policy: TradingPolicy,
    frames: Iterable[MarketFrame],
    *,
    no_trade_sample_limit: int = 500,
) -> ReplayResult:
    """Run one continuous global-slot account over completed market frames."""

    sampler = _DeterministicSample(no_trade_sample_limit)
    pending: tuple[OrderIntent, IntentReplayContext] | None = None
    position: _OpenTrade | None = None
    trades: list[TradePath] = []
    seen_intent_ids: set[str] = set()
    frame_count = submitted = canceled = 0
    previous_close: int | None = None

    for frame in frames:
        frame_count += 1
        if frame.interval_minutes != 5:
            raise FastReplayError("fast campaign replay requires completed 5m frames")
        if previous_close is not None and frame.close_time_ns <= previous_close:
            raise FastReplayError("replay frames must be strictly increasing")
        previous_close = frame.close_time_ns

        # Existing exposure advances before the policy sees this completed bar.
        if position is not None:
            bar = frame.bar(position.intent.symbol)
            stop_hit = _stop_hit(position.intent, bar)
            target_hit = _target_hit(position.intent, bar)
            if stop_hit:
                path = _finish(position, bar, "STOP", _stop_fill(position.intent, bar))
                trades.append(path)
                _feedback(policy, position.intent, bar.close_time_ns, "STOP_FILLED", fill_price=path.exit_price)
                position = None
            elif target_hit:
                path = _finish(position, bar, "TARGET", position.intent.target)
                trades.append(path)
                _feedback(policy, position.intent, bar.close_time_ns, "TARGET_FILLED", fill_price=path.exit_price)
                position = None
            else:
                favorable_price = bar.high if position.intent.side == "LONG" else bar.low
                adverse_price = bar.low if position.intent.side == "LONG" else bar.high
                position.mfe_r = max(
                    position.mfe_r,
                    _signed_r(
                        position.intent,
                        favorable_price,
                        fill_price=position.fill_price,
                    ),
                )
                position.mae_r = min(
                    position.mae_r,
                    _signed_r(
                        position.intent,
                        adverse_price,
                        fill_price=position.fill_price,
                    ),
                )
                position.bars_held += 1

        if pending is not None and position is None:
            intent, context = pending
            bar = frame.bar(intent.symbol)
            if intent.valid_until_ns is not None and bar.open_time_ns >= intent.valid_until_ns:
                _feedback(policy, intent, bar.open_time_ns, "CANCELED", reason="valid_until")
                pending = None
                canceled += 1
            else:
                fill_price = _entry_fill(intent, bar)
            if pending is not None and fill_price is not None:
                _feedback(policy, intent, bar.close_time_ns, "FILLED", fill_price=fill_price)
                opened = _OpenTrade(
                    intent=intent,
                    context=context,
                    fill_price=fill_price,
                    entry_time_ns=bar.close_time_ns,
                    entry_bar_open_time_ns=bar.open_time_ns,
                    risk=intent.risk_distance,
                )
                # Entry-bar target is deliberately ignored.  Adverse movement
                # is retained; favorable movement is not credited because it
                # may have happened before the unknown intrabar fill time.
                if _stop_hit(intent, bar):
                    path = _finish(opened, bar, "STOP", _stop_fill(intent, bar))
                    trades.append(path)
                    _feedback(policy, intent, bar.close_time_ns, "STOP_FILLED", fill_price=path.exit_price)
                else:
                    adverse_price = bar.low if intent.side == "LONG" else bar.high
                    opened.mae_r = min(
                        0.0,
                        _signed_r(intent, adverse_price, fill_price=fill_price),
                    )
                    position = opened
                pending = None

        output = policy.on_market_frame(frame)
        if not isinstance(output, PolicyOutput):
            raise FastReplayError("policy must return PolicyOutput")
        for record in _no_trade_records(policy, frame.close_time_ns):
            sampler.add(record)

        # A close-time invalidation cannot undo an intrabar fill above.
        for verdict in output.validity:
            if pending is not None and verdict.intent_id == pending[0].intent_id:
                if not verdict.valid:
                    _feedback(
                        policy, pending[0], frame.close_time_ns, "CANCELED", reason=verdict.reason,
                    )
                    pending = None
                    canceled += 1
            elif verdict.intent_id not in seen_intent_ids:
                raise FastReplayError(f"validity update for unknown intent: {verdict.intent_id}")

        if len(output.intents) > 1:
            raise FastReplayError("policy emitted more than one global-slot intent")
        if output.intents:
            intent = output.intents[0]
            if pending is not None or position is not None:
                raise FastReplayError("policy emitted a new intent while the global slot was occupied")
            if intent.intent_id in seen_intent_ids:
                raise FastReplayError(f"policy reused intent id: {intent.intent_id}")
            if intent.decision_time_ns != frame.close_time_ns:
                raise FastReplayError("intent decision time must equal the completed frame close")
            seen_intent_ids.add(intent.intent_id)
            pending = (intent, _intent_context(policy, intent))
            submitted += 1
            _feedback(policy, intent, frame.close_time_ns, "SUBMITTED")

    result_trades = list(trades)
    if position is not None:
        result_trades.append(_open_path(position))
    return ReplayResult(
        frames=frame_count,
        intents_submitted=submitted,
        pending_canceled=canceled,
        no_trade_opportunities_seen=sampler.seen,
        trades=tuple(result_trades),
        sampled_no_trades=sampler.items(),
        pending_intent_id=None if pending is None else pending[0].intent_id,
        open_intent_id=None if position is None else position.intent.intent_id,
    )


def write_replay_result(result: ReplayResult, output_dir: str | Path) -> None:
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    trade_rows = [asdict(item) for item in result.trades]
    fields = [field.name for field in TradePath.__dataclass_fields__.values()]
    with (root / "trade_paths.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in trade_rows:
            row["evidence"] = json.dumps(row["evidence"], sort_keys=True, separators=(",", ":"), default=str)
            writer.writerow(row)
    with (root / "no_trade_sample.jsonl").open("w", encoding="utf-8") as handle:
        for item in result.sampled_no_trades:
            handle.write(json.dumps(asdict(item), sort_keys=True, default=str) + "\n")
    summary = {
        "frames": result.frames,
        "intents_submitted": result.intents_submitted,
        "pending_canceled": result.pending_canceled,
        "no_trade_opportunities_seen": result.no_trade_opportunities_seen,
        "trades": len(result.trades),
        "sampled_no_trades": len(result.sampled_no_trades),
        "pending_intent_id": result.pending_intent_id,
        "open_intent_id": result.open_intent_id,
        "causal_conventions": {
            "same_decision_bar_fill": False,
            "fill_bar_target_credit": False,
            "fill_bar_stop_credit": True,
            "same_bar_barrier_priority": "STOP",
            "adverse_stop_gap": "BAR_OPEN",
            "time_exit": False,
        },
    }
    (root / "run.json").write_text(
        json.dumps(summary, sort_keys=True, indent=2) + "\n", encoding="utf-8",
    )


def _load_factory(spec: str) -> TradingPolicy:
    module_name, separator, attribute = spec.partition(":")
    if not separator or not module_name or not attribute:
        raise FastReplayError("policy factory must be module.path:callable")
    factory = getattr(importlib.import_module(module_name), attribute)
    policy = factory()
    if not isinstance(policy, TradingPolicy):
        raise FastReplayError("factory result does not implement TradingPolicy")
    return policy


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache-root", required=True, type=Path)
    parser.add_argument("--policy-factory", required=True, help="module.path:callable")
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--months", nargs="*")
    parser.add_argument("--no-trade-sample-limit", type=int, default=500)
    args = parser.parse_args(argv)
    archives = discover_binance_vision_5m(args.cache_root, months=args.months)
    result = run_fast_replay(
        _load_factory(args.policy_factory),
        iter_synchronized_frames(archives),
        no_trade_sample_limit=args.no_trade_sample_limit,
    )
    write_replay_result(result, args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
