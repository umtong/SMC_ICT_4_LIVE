#!/usr/bin/env python3
"""Diagnose exact trade-flow mechanics of rejected follower FAR disagreements.

This module is research-only. It never submits orders, sizes positions, infers
NautilusTrader fills, or calculates NAV. It reads the already-produced causal
plan ledger and uses official Binance USD-M aggregate trades no later than each
plan's confirmation time. Future bars enter only through the old diagnostic
TARGET/STOP/NO_FILL labels already sealed in positioning_hypotheses.json.

The economic question is deliberately narrower than generic feature search:
when peer markets disagree, did the candidate itself display either

1. aggressor-led reversal price discovery, or
2. passive absorption, where price advances despite opposing aggressor flow?

Only sign, ordering, and unit-free ratios are used in the predeclared mechanism
classifications below. Nothing in this file changes the production gate.
"""
from __future__ import annotations

import argparse
import bisect
import csv
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from io import BytesIO, TextIOWrapper
import json
from math import isfinite, log
from pathlib import Path
import time
from typing import Any, Iterable
from urllib.request import Request, urlopen
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parent
UTC = timezone.utc
MINUTE_NS = 60_000_000_000
BASE = "https://data.binance.vision/data/futures/um/daily/aggTrades"


@dataclass(frozen=True, slots=True)
class Trade:
    ts_ns: int
    price: float
    notional: float
    aggressor: int  # +1 buyer initiated, -1 seller initiated


@dataclass(frozen=True, slots=True)
class WindowStats:
    start_ns: int
    end_ns: int
    duration_seconds: float
    trades: int
    total_notional: float
    signed_notional: float
    directional_imbalance: float | None
    directional_return: float | None
    notional_rate: float
    trade_rate: float
    path_efficiency: float | None
    impact_beta: float | None
    large_trade_share: float | None
    large_trade_directional_imbalance: float | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "start_ns": self.start_ns,
            "end_ns": self.end_ns,
            "duration_seconds": self.duration_seconds,
            "trades": self.trades,
            "total_notional": self.total_notional,
            "signed_notional": self.signed_notional,
            "directional_imbalance": self.directional_imbalance,
            "directional_return": self.directional_return,
            "notional_rate": self.notional_rate,
            "trade_rate": self.trade_rate,
            "path_efficiency": self.path_efficiency,
            "impact_beta": self.impact_beta,
            "large_trade_share": self.large_trade_share,
            "large_trade_directional_imbalance": self.large_trade_directional_imbalance,
        }


def _float(value: Any) -> float:
    result = float(value)
    if not isfinite(result):
        raise ValueError(f"non-finite numeric value: {value!r}")
    return result


def _download(url: str, path: Path, retries: int = 4) -> bytes:
    if path.is_file() and path.stat().st_size > 100:
        payload = path.read_bytes()
        with ZipFile(BytesIO(payload)) as archive:
            if archive.testzip() is None:
                return payload
        path.unlink(missing_ok=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    request = Request(url, headers={"User-Agent": "SMC-ICT-4-candidate-11-microstructure"})
    last_error: Exception | None = None
    for attempt in range(retries):
        try:
            with urlopen(request, timeout=90) as response:  # noqa: S310 fixed HTTPS host
                payload = response.read()
            if len(payload) < 100:
                raise RuntimeError(f"unexpectedly small archive: {url}")
            with ZipFile(BytesIO(payload)) as archive:
                bad = archive.testzip()
                if bad is not None:
                    raise RuntimeError(f"corrupt member {bad}: {url}")
            temporary = path.with_suffix(path.suffix + ".tmp")
            temporary.write_bytes(payload)
            temporary.replace(path)
            return payload
        except Exception as exc:
            last_error = exc
            if attempt + 1 < retries:
                time.sleep(2**attempt)
    raise RuntimeError(f"failed to download {url}: {last_error}")


def _required_files(records: Iterable[dict[str, Any]]) -> dict[tuple[str, str], tuple[int, int]]:
    required: dict[tuple[str, str], tuple[int, int]] = {}
    for record in records:
        symbol = str(record["symbol"])
        start_ns = int(record["sweep_ts_ns"]) - 30 * MINUTE_NS
        end_ns = int(record["confirmation_ts_ns"])
        cursor = datetime.fromtimestamp(start_ns / 1e9, tz=UTC).date()
        final = datetime.fromtimestamp(end_ns / 1e9, tz=UTC).date()
        while cursor <= final:
            key = (symbol, cursor.isoformat())
            previous = required.get(key)
            required[key] = (
                start_ns if previous is None else min(previous[0], start_ns),
                end_ns if previous is None else max(previous[1], end_ns),
            )
            cursor += timedelta(days=1)
    return required


def _read_trades(
    symbol: str,
    day: str,
    start_ns: int,
    end_ns: int,
    cache_dir: Path,
) -> tuple[list[Trade], dict[str, Any]]:
    filename = f"{symbol}-aggTrades-{day}.zip"
    url = f"{BASE}/{symbol}/{filename}"
    path = cache_dir / symbol / filename
    payload = _download(url, path)
    result: list[Trade] = []
    with ZipFile(BytesIO(payload)) as archive:
        members = archive.namelist()
        if len(members) != 1:
            raise RuntimeError(f"unexpected aggTrades members: {members}")
        with archive.open(members[0]) as raw:
            reader = csv.reader(TextIOWrapper(raw, encoding="utf-8-sig", newline=""))
            header = next(reader, None)
            expected = [
                "agg_trade_id", "price", "quantity", "first_trade_id",
                "last_trade_id", "transact_time", "is_buyer_maker",
            ]
            if header != expected:
                raise RuntimeError(f"unexpected aggTrades schema {symbol} {day}: {header}")
            for row in reader:
                if len(row) != len(expected):
                    continue
                ts_ns = int(row[5]) * 1_000_000
                if ts_ns < start_ns or ts_ns > end_ns:
                    continue
                price = _float(row[1])
                quantity = _float(row[2])
                buyer_maker = row[6].strip().lower() == "true"
                result.append(Trade(
                    ts_ns=ts_ns,
                    price=price,
                    notional=price * quantity,
                    aggressor=-1 if buyer_maker else 1,
                ))
    result.sort(key=lambda trade: (trade.ts_ns, trade.price, trade.aggressor))
    manifest = {
        "symbol": symbol,
        "date": day,
        "url": url,
        "member": members[0],
        "bytes": len(payload),
        "sha256": sha256(payload).hexdigest(),
        "selected_trades": len(result),
        "selected_start_ns": start_ns,
        "selected_end_ns": end_ns,
    }
    return result, manifest


def _slice(trades: list[Trade], start_ns: int, end_ns: int) -> list[Trade]:
    timestamps = [trade.ts_ns for trade in trades]
    left = bisect.bisect_left(timestamps, start_ns)
    right = bisect.bisect_right(timestamps, end_ns)
    return trades[left:right]


def _minute_buckets(trades: list[Trade], direction_sign: int) -> list[tuple[float, float]]:
    buckets: dict[int, dict[str, float]] = {}
    for trade in trades:
        minute = trade.ts_ns // MINUTE_NS
        bucket = buckets.setdefault(minute, {
            "first": trade.price,
            "last": trade.price,
            "total": 0.0,
            "signed": 0.0,
        })
        bucket["last"] = trade.price
        bucket["total"] += trade.notional
        bucket["signed"] += direction_sign * trade.aggressor * trade.notional
    result: list[tuple[float, float]] = []
    previous_close: float | None = None
    for minute in sorted(buckets):
        bucket = buckets[minute]
        if previous_close is not None and previous_close > 0 and bucket["last"] > 0:
            directional_return = direction_sign * log(bucket["last"] / previous_close)
            imbalance = bucket["signed"] / max(bucket["total"], 1e-12)
            result.append((imbalance, directional_return))
        previous_close = bucket["last"]
    return result


def _window_stats(
    trades: list[Trade],
    start_ns: int,
    end_ns: int,
    direction_sign: int,
) -> WindowStats:
    sample = _slice(trades, start_ns, end_ns)
    duration = max((end_ns - start_ns) / 1e9, 1.0)
    total = sum(trade.notional for trade in sample)
    signed = sum(direction_sign * trade.aggressor * trade.notional for trade in sample)
    imbalance = None if total <= 0 else signed / total
    directional_return = None
    if len(sample) >= 2 and sample[0].price > 0 and sample[-1].price > 0:
        directional_return = direction_sign * log(sample[-1].price / sample[0].price)
    minute_pairs = _minute_buckets(sample, direction_sign)
    path_efficiency = None
    if directional_return is not None and minute_pairs:
        realized = sum(abs(value) for _, value in minute_pairs)
        path_efficiency = directional_return / max(realized, 1e-12)
    impact_beta = None
    denominator = sum(flow * flow for flow, _ in minute_pairs)
    if denominator > 0:
        impact_beta = sum(flow * ret for flow, ret in minute_pairs) / denominator
    large_share = None
    large_imbalance = None
    if sample and total > 0:
        notionals = sorted(trade.notional for trade in sample)
        threshold = notionals[max(0, int(0.99 * (len(notionals) - 1)))]
        large = [trade for trade in sample if trade.notional >= threshold]
        large_total = sum(trade.notional for trade in large)
        large_signed = sum(
            direction_sign * trade.aggressor * trade.notional for trade in large
        )
        large_share = large_total / total
        large_imbalance = None if large_total <= 0 else large_signed / large_total
    return WindowStats(
        start_ns=start_ns,
        end_ns=end_ns,
        duration_seconds=duration,
        trades=len(sample),
        total_notional=total,
        signed_notional=signed,
        directional_imbalance=imbalance,
        directional_return=directional_return,
        notional_rate=total / duration,
        trade_rate=len(sample) / duration,
        path_efficiency=path_efficiency,
        impact_beta=impact_beta,
        large_trade_share=large_share,
        large_trade_directional_imbalance=large_imbalance,
    )


def _positive(value: float | None) -> bool:
    return value is not None and value > 0.0


def _negative(value: float | None) -> bool:
    return value is not None and value < 0.0


def _analyze_record(record: dict[str, Any], trades: list[Trade]) -> dict[str, Any]:
    sweep_ns = int(record["sweep_ts_ns"])
    confirmation_ns = int(record["confirmation_ts_ns"])
    sign = 1 if record["direction"] == "LONG" else -1
    event_duration = max(confirmation_ns - sweep_ns, MINUTE_NS)
    midpoint = sweep_ns + event_duration // 2
    windows = {
        "pre_30m": _window_stats(trades, sweep_ns - 30 * MINUTE_NS, sweep_ns - 1, sign),
        "pre_30_to_5m": _window_stats(trades, sweep_ns - 30 * MINUTE_NS, sweep_ns - 5 * MINUTE_NS - 1, sign),
        "pre_5m": _window_stats(trades, sweep_ns - 5 * MINUTE_NS, sweep_ns - 1, sign),
        "event": _window_stats(trades, sweep_ns, confirmation_ns, sign),
        "event_early": _window_stats(trades, sweep_ns, midpoint, sign),
        "event_late": _window_stats(trades, midpoint + 1, confirmation_ns, sign),
        "terminal_5m": _window_stats(trades, max(sweep_ns, confirmation_ns - 5 * MINUTE_NS), confirmation_ns, sign),
    }
    pre = windows["pre_30m"]
    pre_near = windows["pre_5m"]
    event = windows["event"]
    late = windows["event_late"]
    terminal = windows["terminal_5m"]
    notional_acceleration = (
        None if pre.notional_rate <= 0 else terminal.notional_rate / pre.notional_rate
    )
    trade_acceleration = (
        None if pre.trade_rate <= 0 else terminal.trade_rate / pre.trade_rate
    )
    flow_flip = (
        None
        if pre.directional_imbalance is None or event.directional_imbalance is None
        else event.directional_imbalance - pre.directional_imbalance
    )
    terminal_flow_flip = (
        None
        if pre_near.directional_imbalance is None or terminal.directional_imbalance is None
        else terminal.directional_imbalance - pre_near.directional_imbalance
    )
    aggressor_led = (
        _positive(event.directional_return)
        and _positive(event.directional_imbalance)
        and _positive(late.directional_return)
        and _positive(terminal.directional_return)
        and _positive(terminal.directional_imbalance)
        and notional_acceleration is not None
        and notional_acceleration >= 1.0
        and _positive(event.path_efficiency)
    )
    passive_absorption = (
        _negative(pre.directional_imbalance)
        and _positive(event.directional_return)
        and event.directional_imbalance is not None
        and event.directional_imbalance <= 0.0
        and _positive(late.directional_return)
        and _positive(terminal.directional_return)
        and _positive(event.path_efficiency)
    )
    terminal_reversal = (
        _positive(event.directional_return)
        and _positive(late.directional_return)
        and _positive(terminal.directional_return)
        and _positive(event.path_efficiency)
        and terminal_flow_flip is not None
        and terminal_flow_flip > 0.0
    )
    local_price_discovery = aggressor_led or passive_absorption
    if aggressor_led:
        mechanism = "AGGRESSOR_LED_REVERSAL_DISCOVERY"
    elif passive_absorption:
        mechanism = "PASSIVE_ABSORPTION_REVERSAL"
    elif terminal_reversal:
        mechanism = "TERMINAL_FLOW_REVERSAL"
    else:
        mechanism = "NO_LOCAL_MICROSTRUCTURE_DISCOVERY"
    return {
        "week": record["week"],
        "scenario_id": record["scenario_id"],
        "symbol": record["symbol"],
        "scenario": record["scenario"],
        "direction": record["direction"],
        "outcome": record["outcome"],
        "gate_reason": record["gate_reason"],
        "sweep_ts_ns": sweep_ns,
        "confirmation_ts_ns": confirmation_ns,
        "features": {
            "flow_flip": flow_flip,
            "terminal_flow_flip": terminal_flow_flip,
            "terminal_notional_acceleration": notional_acceleration,
            "terminal_trade_acceleration": trade_acceleration,
            "aggressor_led_reversal_discovery": aggressor_led,
            "passive_absorption_reversal": passive_absorption,
            "terminal_flow_reversal": terminal_reversal,
            "local_price_discovery": local_price_discovery,
            "mechanism": mechanism,
        },
        "windows": {name: stats.to_dict() for name, stats in windows.items()},
    }


def _contingency(records: list[dict[str, Any]], key: str) -> dict[str, dict[str, int]]:
    groups: dict[str, Counter[str]] = defaultdict(Counter)
    for record in records:
        groups[str(record["features"][key])][record["outcome"]] += 1
    return {
        group: dict(sorted(counter.items()))
        for group, counter in sorted(groups.items())
    }


def run(positioning_path: Path, output: Path, cache_dir: Path) -> dict[str, Any]:
    source = json.loads(positioning_path.read_text(encoding="utf-8"))
    selected = [
        record for record in source["records"]
        if record.get("gate_reason") == "FOLLOWER_FAR_PEER_DISAGREEMENT"
    ]
    requirements = _required_files(selected)
    trades_by_file: dict[tuple[str, str], list[Trade]] = {}
    manifest: list[dict[str, Any]] = []
    for (symbol, day), (start_ns, end_ns) in sorted(requirements.items()):
        trades, entry = _read_trades(symbol, day, start_ns, end_ns, cache_dir)
        trades_by_file[(symbol, day)] = trades
        manifest.append(entry)

    analyzed: list[dict[str, Any]] = []
    for record in selected:
        start_ns = int(record["sweep_ts_ns"]) - 30 * MINUTE_NS
        end_ns = int(record["confirmation_ts_ns"])
        cursor = datetime.fromtimestamp(start_ns / 1e9, tz=UTC).date()
        final = datetime.fromtimestamp(end_ns / 1e9, tz=UTC).date()
        trades: list[Trade] = []
        while cursor <= final:
            trades.extend(trades_by_file[(record["symbol"], cursor.isoformat())])
            cursor += timedelta(days=1)
        trades.sort(key=lambda trade: (trade.ts_ns, trade.price, trade.aggressor))
        analyzed.append(_analyze_record(record, trades))

    payload = {
        "schema": "candidate-11-peer-disagreement-microstructure-v1",
        "evidence_class": "RESEARCH_DIAGNOSTIC_NOT_NAUTILUS_PERFORMANCE",
        "warning": (
            "Old W1-W9 outcomes are used only to reject or justify a mechanism. "
            "No rule in this diagnostic is a success claim or an out-of-sample result."
        ),
        "causal_contract": {
            "trade_visibility": "aggregate trade transaction time",
            "feature_cutoff": "no trade after plan confirmation",
            "pre_sweep_baseline": "thirty completed minutes before the source sweep",
            "outcome_usage": "sealed old-week labels from positioning_hypotheses.json",
        },
        "selected_records": len(analyzed),
        "outcomes": dict(sorted(Counter(record["outcome"] for record in analyzed).items())),
        "mechanism_contingency": _contingency(analyzed, "mechanism"),
        "local_price_discovery_contingency": _contingency(analyzed, "local_price_discovery"),
        "aggressor_led_contingency": _contingency(analyzed, "aggressor_led_reversal_discovery"),
        "passive_absorption_contingency": _contingency(analyzed, "passive_absorption_reversal"),
        "terminal_reversal_contingency": _contingency(analyzed, "terminal_flow_reversal"),
        "records": analyzed,
        "data_manifest": manifest,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(output)
    return payload


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--positioning", type=Path, default=ROOT / "positioning_hypotheses.json")
    parser.add_argument("--output", type=Path, default=ROOT / "peer_disagreement_microstructure.json")
    parser.add_argument("--cache", type=Path, default=Path("/tmp/candidate-11-aggtrades"))
    args = parser.parse_args()
    payload = run(args.positioning.resolve(), args.output.resolve(), args.cache.resolve())
    print(json.dumps({
        "selected_records": payload["selected_records"],
        "outcomes": payload["outcomes"],
        "mechanism_contingency": payload["mechanism_contingency"],
        "local_price_discovery_contingency": payload["local_price_discovery_contingency"],
        "terminal_reversal_contingency": payload["terminal_reversal_contingency"],
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
