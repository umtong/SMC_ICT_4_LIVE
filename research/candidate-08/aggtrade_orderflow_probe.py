"""Ten-second aggregate-trade liquidity-event diagnostic for candidate-08.

Official Binance Vision USD-M aggTrades are checksum verified and aggregated to observable
10-second bars. Completed 4-hour/day/week levels come from the existing causal detector. The probe
separates two explicit scenarios:

* sweep absorption: aggressive flow crosses a completed boundary but price closes back inside,
  followed by separate opposite-flow displacement;
* breakout acceptance: aggressive flow closes beyond a completed boundary, a lower-energy retest
  holds, then separate same-direction flow resumes.

No orders, fills, account state, or portfolio returns are simulated. Conservative first-touch paths
with the same 6 bp per fill and adverse-tick reserve determine whether either market event merits a
NautilusTrader implementation.
"""

from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
from hashlib import sha256
import json
from math import isfinite
from pathlib import Path
import sys
from typing import Any, Iterable
import zipfile

import numpy as np
import pandas as pd
from nautilus_trader.model.data import BarType

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from data import BinanceDataError, SourceFile, _download, _sha256_file, load_official_binance_bars  # noqa: E402
from range_fvg_logic import (  # noqa: E402
    ExternalLevel,
    FiveMinuteBar,
    LevelKind,
    LevelSource,
    RangeFVGConfig,
    _bar_from_row,
    _build_level_snapshots,
    aggregate_five_minute_bars,
)
from run import _build_instrument, _parse_utc  # noqa: E402


AGG_COLUMNS = [
    "agg_trade_id",
    "price",
    "quantity",
    "first_trade_id",
    "last_trade_id",
    "transact_time",
    "is_buyer_maker",
]
SOURCE_RANK = {
    LevelSource.FOUR_HOUR: 1,
    LevelSource.DAY: 2,
    LevelSource.WEEK: 3,
}
FEE_RATE_PER_FILL = 0.0006


@dataclass(frozen=True, slots=True)
class AggSource:
    day: str
    url: str
    checksum_url: str
    sha256: str
    size_bytes: int
    raw_rows: int
    ten_second_rows: int


@dataclass(slots=True)
class PendingEvent:
    scenario_id: str
    family: str
    trade_direction: int
    outward_direction: int
    boundary: ExternalLevel
    armed_position: int
    expiry_position: int
    extreme: float
    reference_high: float
    reference_low: float
    displacement_volume: float
    displacement_trade_count: float
    displacement_imbalance: float
    retest_position: int | None = None
    retest_high: float | None = None
    retest_low: float | None = None
    retest_volume: float | None = None
    retest_trade_count: float | None = None


def _daily_dates(start: datetime, end: datetime) -> Iterable[date]:
    cursor = start.date()
    final = (end - timedelta(microseconds=1)).date()
    while cursor <= final:
        yield cursor
        cursor += timedelta(days=1)


def _verified_daily_aggtrade(cache_dir: Path, symbol: str, day: date) -> tuple[Path, AggSource]:
    day_text = day.isoformat()
    filename = f"{symbol}-aggTrades-{day_text}.zip"
    base = f"https://data.binance.vision/data/futures/um/daily/aggTrades/{symbol}"
    url = f"{base}/{filename}"
    checksum_url = f"{url}.CHECKSUM"
    cache_dir.mkdir(parents=True, exist_ok=True)
    destination = cache_dir / filename
    checksum_text = _download(checksum_url, timeout=180).decode("utf-8").strip()
    expected = checksum_text.split()[0].lower()
    if len(expected) != 64:
        raise BinanceDataError(f"invalid checksum payload for {filename}: {checksum_text!r}")
    if destination.exists() and _sha256_file(destination) != expected:
        destination.unlink()
    if not destination.exists():
        payload = _download(url, timeout=300)
        actual = sha256(payload).hexdigest()
        if actual != expected:
            raise BinanceDataError(f"SHA-256 mismatch for {filename}: {actual} != {expected}")
        temporary = destination.with_suffix(".zip.tmp")
        temporary.write_bytes(payload)
        temporary.replace(destination)
    actual = _sha256_file(destination)
    return destination, AggSource(
        day=day_text,
        url=url,
        checksum_url=checksum_url,
        sha256=actual,
        size_bytes=destination.stat().st_size,
        raw_rows=0,
        ten_second_rows=0,
    )


def _coerce_bool(series: pd.Series) -> pd.Series:
    return series.astype(str).str.strip().str.lower().isin({"true", "1"})


def _aggregate_chunk(chunk: pd.DataFrame) -> pd.DataFrame:
    if chunk.empty:
        return pd.DataFrame()
    first_value = str(chunk.iloc[0, 0]).strip().lower()
    if first_value in {"agg_trade_id", "aggregate tradeid", "a"} or not first_value.lstrip("-").isdigit():
        chunk = chunk.iloc[1:].copy()
    if chunk.empty:
        return pd.DataFrame()
    if chunk.shape[1] < 7:
        raise BinanceDataError(f"aggTrades file exposed {chunk.shape[1]} columns, expected at least 7")
    chunk = chunk.iloc[:, :7].copy()
    chunk.columns = AGG_COLUMNS
    for column in (
        "agg_trade_id",
        "price",
        "quantity",
        "first_trade_id",
        "last_trade_id",
        "transact_time",
    ):
        chunk[column] = pd.to_numeric(chunk[column], errors="coerce")
    chunk = chunk.dropna(
        subset=[
            "price",
            "quantity",
            "first_trade_id",
            "last_trade_id",
            "transact_time",
            "is_buyer_maker",
        ]
    )
    if chunk.empty:
        return pd.DataFrame()
    chunk["buyer_maker"] = _coerce_bool(chunk["is_buyer_maker"])
    chunk["signed_quantity"] = np.where(
        chunk["buyer_maker"],
        -chunk["quantity"],
        chunk["quantity"],
    )
    chunk["underlying_trade_count"] = (
        chunk["last_trade_id"] - chunk["first_trade_id"] + 1.0
    ).clip(lower=1.0)
    timestamps = pd.to_datetime(chunk["transact_time"].astype("int64"), unit="ms", utc=True)
    chunk.index = timestamps
    bucket = chunk.index.floor("10s") + pd.Timedelta(seconds=10)
    grouped = chunk.groupby(bucket, sort=True).agg(
        open=("price", "first"),
        high=("price", "max"),
        low=("price", "min"),
        close=("price", "last"),
        volume=("quantity", "sum"),
        signed_volume=("signed_quantity", "sum"),
        trade_count=("underlying_trade_count", "sum"),
        agg_trade_count=("agg_trade_id", "size"),
    )
    return grouped


def _read_daily_ten_second(path: Path) -> tuple[pd.DataFrame, int]:
    pieces: list[pd.DataFrame] = []
    raw_rows = 0
    with zipfile.ZipFile(path) as archive:
        members = [name for name in archive.namelist() if name.lower().endswith(".csv")]
        if len(members) != 1:
            raise BinanceDataError(f"expected one CSV in {path.name}, found {members}")
        with archive.open(members[0]) as handle:
            for chunk in pd.read_csv(handle, header=None, chunksize=500_000, low_memory=False):
                raw_rows += len(chunk.index)
                aggregate = _aggregate_chunk(chunk)
                if not aggregate.empty:
                    pieces.append(aggregate)
    if not pieces:
        raise BinanceDataError(f"no valid aggTrades rows in {path.name}")
    combined = pd.concat(pieces).sort_index()
    combined = combined.groupby(level=0, sort=True).agg(
        open=("open", "first"),
        high=("high", "max"),
        low=("low", "min"),
        close=("close", "last"),
        volume=("volume", "sum"),
        signed_volume=("signed_volume", "sum"),
        trade_count=("trade_count", "sum"),
        agg_trade_count=("agg_trade_count", "sum"),
    )
    return combined, raw_rows


def load_ten_second_aggtrades(
    *,
    symbol: str,
    start: datetime,
    end: datetime,
    cache_dir: Path,
) -> tuple[pd.DataFrame, tuple[AggSource, ...], dict[str, Any]]:
    frames: list[pd.DataFrame] = []
    sources: list[AggSource] = []
    for day in _daily_dates(start, end):
        path, source = _verified_daily_aggtrade(cache_dir / symbol, symbol, day)
        frame, raw_rows = _read_daily_ten_second(path)
        frames.append(frame)
        sources.append(
            AggSource(
                day=source.day,
                url=source.url,
                checksum_url=source.checksum_url,
                sha256=source.sha256,
                size_bytes=source.size_bytes,
                raw_rows=raw_rows,
                ten_second_rows=len(frame.index),
            )
        )
    data = pd.concat(frames).sort_index()
    data = data.loc[(data.index > start) & (data.index <= end)].copy()
    data = data.loc[~data.index.duplicated(keep="last")]
    data["imbalance"] = data["signed_volume"] / data["volume"].replace(0, np.nan)
    data["volume_median"] = data["volume"].shift(1).rolling(180, min_periods=90).median()
    data["trade_median"] = data["trade_count"].shift(1).rolling(180, min_periods=90).median()
    data["volume_ratio"] = data["volume"] / data["volume_median"].replace(0, np.nan)
    data["trade_ratio"] = data["trade_count"] / data["trade_median"].replace(0, np.nan)
    spread = data["high"] - data["low"]
    data["close_location"] = (data["close"] - data["low"]) / spread.replace(0, np.nan)
    deltas = data.index.to_series().diff().dropna().dt.total_seconds()
    quality = {
        "rows": len(data.index),
        "first_observed_time": data.index[0].isoformat() if not data.empty else None,
        "last_observed_time": data.index[-1].isoformat() if not data.empty else None,
        "gap_count_over_11_seconds": int((deltas > 11.0).sum()),
        "max_gap_seconds": float(deltas.max()) if not deltas.empty else 0.0,
        "raw_rows": sum(source.raw_rows for source in sources),
        "source_bytes": sum(source.size_bytes for source in sources),
    }
    return data, tuple(sources), quality


def _context(
    kline_frame: pd.DataFrame,
    config: RangeFVGConfig,
) -> tuple[np.ndarray, tuple[FiveMinuteBar, ...], tuple[tuple[ExternalLevel, ...], ...]]:
    five = aggregate_five_minute_bars(kline_frame, config)
    bars: list[FiveMinuteBar] = []
    for index, (timestamp, row) in enumerate(five.iterrows()):
        converted = _bar_from_row(index, timestamp, row)
        if converted is not None:
            bars.append(converted)
    immutable = tuple(bars)
    snapshots = _build_level_snapshots(immutable, config)
    times = np.asarray([bar.ts_event_ns for bar in immutable], dtype=np.int64)
    return times, immutable, snapshots


def _snapshot_after_latest_complete(
    timestamp_ns: int,
    times: np.ndarray,
    bars: tuple[FiveMinuteBar, ...],
    snapshots: tuple[tuple[ExternalLevel, ...], ...],
) -> tuple[FiveMinuteBar, tuple[ExternalLevel, ...]] | None:
    position = int(np.searchsorted(times, timestamp_ns, side="right") - 1)
    if position < 0:
        return None
    snapshot_position = min(position + 1, len(snapshots) - 1)
    return bars[position], snapshots[snapshot_position]


def _crossed_boundary(
    levels: tuple[ExternalLevel, ...],
    *,
    previous_close: float,
    high: float,
    low: float,
    atr: float,
    consumed: set[str],
) -> tuple[ExternalLevel, int] | None:
    high_levels = [
        level
        for level in levels
        if level.level_id not in consumed
        and level.kind is LevelKind.HIGH
        and previous_close <= level.level
        and high >= level.level + 0.02 * atr
    ]
    low_levels = [
        level
        for level in levels
        if level.level_id not in consumed
        and level.kind is LevelKind.LOW
        and previous_close >= level.level
        and low <= level.level - 0.02 * atr
    ]
    if high_levels and low_levels:
        return None
    if high_levels:
        return max(
            high_levels,
            key=lambda level: (SOURCE_RANK[level.source], level.level),
        ), 1
    if low_levels:
        return max(
            low_levels,
            key=lambda level: (SOURCE_RANK[level.source], -level.level),
        ), -1
    return None


def _select_target(
    levels: tuple[ExternalLevel, ...],
    *,
    direction: int,
    entry: float,
    excluded_level_id: str,
) -> ExternalLevel | None:
    if direction > 0:
        candidates = [
            level
            for level in levels
            if level.level_id != excluded_level_id
            and level.kind is LevelKind.HIGH
            and level.level > entry
        ]
    else:
        candidates = [
            level
            for level in levels
            if level.level_id != excluded_level_id
            and level.kind is LevelKind.LOW
            and level.level < entry
        ]
    if not candidates:
        return None
    return min(candidates, key=lambda level: abs(level.level - entry))


def _net(direction: int, entry: float, exit_price: float, tick: float) -> float:
    return direction * (exit_price - entry) - FEE_RATE_PER_FILL * (entry + exit_price) - 2.0 * tick


def _first_touch(
    future: pd.DataFrame,
    *,
    direction: int,
    stop: float,
    target: float,
) -> tuple[str, str | None]:
    for timestamp, row in future.iterrows():
        stop_hit = float(row["low"]) <= stop if direction > 0 else float(row["high"]) >= stop
        target_hit = float(row["high"]) >= target if direction > 0 else float(row["low"]) <= target
        if stop_hit and target_hit:
            return "STOP_AMBIGUOUS_FIRST", timestamp.isoformat()
        if stop_hit:
            return "STOP", timestamp.isoformat()
        if target_hit:
            return "TARGET", timestamp.isoformat()
    return "TIMEOUT", None


def _trade_record(
    *,
    pending: PendingEvent,
    confirmation_position: int,
    data: pd.DataFrame,
    levels: tuple[ExternalLevel, ...],
    atr: float,
    tick: float,
) -> tuple[dict[str, Any] | None, str]:
    row = data.iloc[confirmation_position]
    direction = pending.trade_direction
    entry = float(row["close"]) + direction * tick
    minimum_distance = 0.10 * atr
    buffer = 0.03 * atr
    if direction > 0:
        stop = min(pending.extreme - buffer, entry - minimum_distance)
    else:
        stop = max(pending.extreme + buffer, entry + minimum_distance)
    target_level = _select_target(
        levels,
        direction=direction,
        entry=entry,
        excluded_level_id=pending.boundary.level_id,
    )
    if target_level is None:
        return None, "NO_CAUSAL_EXTERNAL_TARGET"
    target = target_level.level
    geometry = stop < entry < target if direction > 0 else target < entry < stop
    if not geometry:
        return None, "INVALID_EXTERNAL_GEOMETRY"
    expected_loss = -_net(direction, entry, stop, tick)
    expected_gain = _net(direction, entry, target, tick)
    net_rr = expected_gain / expected_loss if expected_loss > 0 else float("-inf")
    if expected_gain <= 0 or net_rr < 1.20:
        return None, "INSUFFICIENT_COST_AFTER_EXTERNAL_TARGET"
    trigger_time = data.index[confirmation_position]
    future = data.iloc[confirmation_position + 1 : confirmation_position + 1 + 1440]
    outcome, outcome_time = _first_touch(
        future,
        direction=direction,
        stop=stop,
        target=target,
    )
    if outcome == "TARGET":
        proxy = net_rr
    elif outcome in ("STOP", "STOP_AMBIGUOUS_FIRST"):
        proxy = -1.0
    elif future.empty:
        proxy = 0.0
    else:
        proxy = _net(direction, entry, float(future.iloc[-1]["close"]), tick) / expected_loss
    favorable = (
        float(future["high"].max()) if direction > 0 else float(future["low"].min())
    ) if not future.empty else entry
    adverse = (
        float(future["low"].min()) if direction > 0 else float(future["high"].max())
    ) if not future.empty else entry
    return {
        "scenario_id": pending.scenario_id,
        "family": pending.family,
        "direction": "LONG" if direction > 0 else "SHORT",
        "boundary_id": pending.boundary.level_id,
        "boundary_source": pending.boundary.source.value,
        "boundary_level": pending.boundary.level,
        "target_id": target_level.level_id,
        "target_source": target_level.source.value,
        "target": target,
        "armed_time": data.index[pending.armed_position].isoformat(),
        "confirmation_time": trigger_time.isoformat(),
        "confirmation_delay_seconds": (confirmation_position - pending.armed_position) * 10,
        "entry": entry,
        "stop": stop,
        "net_reward_risk": net_rr,
        "expected_loss_per_unit": expected_loss,
        "expected_gain_per_unit": expected_gain,
        "outcome_240m": outcome,
        "outcome_time": outcome_time,
        "net_r_proxy_240m": proxy,
        "net_mfe_240m_r": _net(direction, entry, favorable, tick) / expected_loss,
        "net_mae_240m_r": -_net(direction, entry, adverse, tick) / expected_loss,
        "armed_imbalance": pending.displacement_imbalance,
        "armed_volume_ratio": float(data.iloc[pending.armed_position]["volume_ratio"]),
        "armed_trade_ratio": float(data.iloc[pending.armed_position]["trade_ratio"]),
        "confirmation_imbalance": float(row["imbalance"]),
        "confirmation_volume_ratio": float(row["volume_ratio"]),
        "confirmation_trade_ratio": float(row["trade_ratio"]),
        "retest_position": pending.retest_position,
    }, "TRADEABLE_EVENT"


def _summary(records: list[dict[str, Any]]) -> dict[str, Any]:
    proxies = np.asarray([record["net_r_proxy_240m"] for record in records], dtype=float)
    return {
        "trades": len(records),
        "outcomes": dict(sorted(Counter(record["outcome_240m"] for record in records).items())),
        "total_net_r_proxy": float(proxies.sum()) if proxies.size else 0.0,
        "mean_net_r_proxy": float(proxies.mean()) if proxies.size else 0.0,
        "median_net_r_proxy": float(np.median(proxies)) if proxies.size else 0.0,
        "positive_share": float((proxies > 0).mean()) if proxies.size else 0.0,
        "median_net_reward_risk": float(np.median([record["net_reward_risk"] for record in records])) if records else 0.0,
    }


def detect_orderflow_events(
    *,
    data: pd.DataFrame,
    context_times: np.ndarray,
    context_bars: tuple[FiveMinuteBar, ...],
    snapshots: tuple[tuple[ExternalLevel, ...], ...],
    tick: float,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    records: list[dict[str, Any]] = []
    diagnostics: Counter[str] = Counter()
    consumed: set[str] = set()
    pending: PendingEvent | None = None
    scenario_counter = 0

    for position in range(1, len(data.index)):
        row = data.iloc[position]
        required = (
            float(row["open"]),
            float(row["high"]),
            float(row["low"]),
            float(row["close"]),
            float(row["imbalance"]),
            float(row["volume_ratio"]),
            float(row["trade_ratio"]),
            float(row["close_location"]),
        )
        if not all(isfinite(value) for value in required):
            continue
        timestamp_ns = int(data.index[position].as_unit("ns").value)
        context = _snapshot_after_latest_complete(
            timestamp_ns,
            context_times,
            context_bars,
            snapshots,
        )
        if context is None:
            continue
        five_bar, levels = context
        atr = five_bar.atr

        if pending is not None:
            if position > pending.expiry_position:
                diagnostics[f"{pending.family}_TIMEOUT"] += 1
                pending = None
            else:
                direction = pending.trade_direction
                if pending.family == "SWEEP_ABSORPTION_REVERSAL":
                    invalid = (
                        float(row["high"]) > pending.extreme + 0.10 * atr
                        if direction < 0
                        else float(row["low"]) < pending.extreme - 0.10 * atr
                    )
                    if invalid:
                        diagnostics["ABSORPTION_INVALIDATED"] += 1
                        pending = None
                    else:
                        break_structure = (
                            float(row["close"]) < pending.reference_low - 0.01 * atr
                            if direction < 0
                            else float(row["close"]) > pending.reference_high + 0.01 * atr
                        )
                        located = (
                            float(row["close_location"]) <= 0.35
                            if direction < 0
                            else float(row["close_location"]) >= 0.65
                        )
                        directional_body = direction * float(row["close"] - row["open"])
                        directional_flow = direction * float(row["imbalance"])
                        confirmed = (
                            break_structure
                            and located
                            and directional_body >= 0.05 * atr
                            and directional_flow >= 0.10
                            and float(row["volume_ratio"]) >= 1.10
                            and float(row["trade_ratio"]) >= 1.05
                        )
                        if confirmed:
                            record, reason = _trade_record(
                                pending=pending,
                                confirmation_position=position,
                                data=data,
                                levels=levels,
                                atr=atr,
                                tick=tick,
                            )
                            diagnostics[reason] += 1
                            if record is not None:
                                records.append(record)
                            pending = None
                else:
                    outward = pending.outward_direction
                    reclaim = (
                        float(row["close"]) < pending.boundary.level - 0.02 * atr
                        if outward > 0
                        else float(row["close"]) > pending.boundary.level + 0.02 * atr
                    )
                    if reclaim:
                        diagnostics["ACCEPTANCE_RECLAIMED"] += 1
                        pending = None
                    elif pending.retest_position is None:
                        touched = (
                            float(row["low"]) <= pending.boundary.level + 0.05 * atr
                            if outward > 0
                            else float(row["high"]) >= pending.boundary.level - 0.05 * atr
                        )
                        held = (
                            float(row["close"]) >= pending.boundary.level
                            if outward > 0
                            else float(row["close"]) <= pending.boundary.level
                        )
                        contracted = (
                            float(row["volume"]) <= 0.80 * pending.displacement_volume
                            and float(row["trade_count"]) <= 0.90 * pending.displacement_trade_count
                            and abs(float(row["imbalance"])) < abs(pending.displacement_imbalance)
                        )
                        if touched and held and contracted:
                            pending.retest_position = position
                            pending.retest_high = float(row["high"])
                            pending.retest_low = float(row["low"])
                            pending.retest_volume = float(row["volume"])
                            pending.retest_trade_count = float(row["trade_count"])
                            pending.expiry_position = position + 3
                            diagnostics["ACCEPTANCE_RETEST_HELD"] += 1
                    else:
                        assert pending.retest_high is not None and pending.retest_low is not None
                        assert pending.retest_volume is not None and pending.retest_trade_count is not None
                        break_structure = (
                            float(row["close"]) > pending.retest_high + 0.01 * atr
                            if outward > 0
                            else float(row["close"]) < pending.retest_low - 0.01 * atr
                        )
                        located = (
                            float(row["close_location"]) >= 0.65
                            if outward > 0
                            else float(row["close_location"]) <= 0.35
                        )
                        directional_body = outward * float(row["close"] - row["open"])
                        directional_flow = outward * float(row["imbalance"])
                        confirmed = (
                            break_structure
                            and located
                            and directional_body >= 0.05 * atr
                            and directional_flow >= 0.10
                            and float(row["volume"]) >= pending.retest_volume
                            and float(row["trade_count"]) >= pending.retest_trade_count
                        )
                        if confirmed:
                            record, reason = _trade_record(
                                pending=pending,
                                confirmation_position=position,
                                data=data,
                                levels=levels,
                                atr=atr,
                                tick=tick,
                            )
                            diagnostics[reason] += 1
                            if record is not None:
                                records.append(record)
                            pending = None
            if pending is not None:
                continue

        crossed = _crossed_boundary(
            levels,
            previous_close=float(data.iloc[position - 1]["close"]),
            high=float(row["high"]),
            low=float(row["low"]),
            atr=atr,
            consumed=consumed,
        )
        if crossed is None:
            continue
        boundary, outward = crossed
        outward_flow = outward * float(row["imbalance"])
        outward_body = outward * float(row["close"] - row["open"])
        high_activity = float(row["volume_ratio"]) >= 1.50 and float(row["trade_ratio"]) >= 1.20
        closes_inside = (
            float(row["close"]) <= boundary.level
            if outward > 0
            else float(row["close"]) >= boundary.level
        )
        absorption_location = (
            float(row["close_location"]) <= 0.45
            if outward > 0
            else float(row["close_location"]) >= 0.55
        )
        accepted = (
            float(row["close"]) >= boundary.level + 0.05 * atr
            if outward > 0
            else float(row["close"]) <= boundary.level - 0.05 * atr
        )
        acceptance_location = (
            float(row["close_location"]) >= 0.65
            if outward > 0
            else float(row["close_location"]) <= 0.35
        )

        scenario_counter += 1
        if (
            closes_inside
            and absorption_location
            and high_activity
            and outward_flow >= 0.15
        ):
            consumed.add(boundary.level_id)
            pending = PendingEvent(
                scenario_id=f"agg-abs-{scenario_counter:06d}",
                family="SWEEP_ABSORPTION_REVERSAL",
                trade_direction=-outward,
                outward_direction=outward,
                boundary=boundary,
                armed_position=position,
                expiry_position=position + 6,
                extreme=float(row["high"] if outward > 0 else row["low"]),
                reference_high=float(row["high"]),
                reference_low=float(row["low"]),
                displacement_volume=float(row["volume"]),
                displacement_trade_count=float(row["trade_count"]),
                displacement_imbalance=float(row["imbalance"]),
            )
            diagnostics["ABSORPTION_ARMED"] += 1
        elif (
            accepted
            and acceptance_location
            and high_activity
            and outward_flow >= 0.20
            and outward_body >= 0.08 * atr
        ):
            consumed.add(boundary.level_id)
            pending = PendingEvent(
                scenario_id=f"agg-acc-{scenario_counter:06d}",
                family="BREAKOUT_ACCEPTANCE_CONTINUATION",
                trade_direction=outward,
                outward_direction=outward,
                boundary=boundary,
                armed_position=position,
                expiry_position=position + 6,
                extreme=float(row["high"] if outward > 0 else row["low"]),
                reference_high=float(row["high"]),
                reference_low=float(row["low"]),
                displacement_volume=float(row["volume"]),
                displacement_trade_count=float(row["trade_count"]),
                displacement_imbalance=float(row["imbalance"]),
            )
            diagnostics["ACCEPTANCE_ARMED"] += 1
        else:
            diagnostics["BOUNDARY_INTERACTION_UNCLASSIFIED"] += 1

    return records, dict(sorted(diagnostics.items()))


def run(config_path: Path, output: Path, data_cache: Path) -> dict[str, Any]:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    window = config["suites"]["first"][0]
    start = _parse_utc(str(window["start"]))
    end = _parse_utc(str(window["end"]))
    instrument = _build_instrument(config)
    bar_type = BarType.from_str(str(config["bar_type"]))
    loaded = load_official_binance_bars(
        symbol="BTCUSDT",
        interval="1m",
        load_start=start - timedelta(days=10),
        load_end=end + timedelta(hours=4, minutes=10),
        bar_type=bar_type,
        instrument=instrument,
        cache_dir=data_cache / "klines",
    )
    ten, agg_sources, agg_quality = load_ten_second_aggtrades(
        symbol="BTCUSDT",
        start=start,
        end=end + timedelta(hours=4),
        cache_dir=data_cache / "aggTrades",
    )
    pattern_config = RangeFVGConfig.from_mapping(dict(config["pattern"]))
    context_times, context_bars, snapshots = _context(loaded.frame, pattern_config)
    records, diagnostics = detect_orderflow_events(
        data=ten,
        context_times=context_times,
        context_bars=context_bars,
        snapshots=snapshots,
        tick=0.1,
    )
    in_window = [
        record
        for record in records
        if start <= pd.Timestamp(record["confirmation_time"]) < end
    ]
    families = {
        family: _summary([record for record in in_window if record["family"] == family])
        for family in ("SWEEP_ABSORPTION_REVERSAL", "BREAKOUT_ACCEPTANCE_CONTINUATION")
    }
    sources = {
        source: _summary([record for record in in_window if record["boundary_source"] == source])
        for source in sorted({record["boundary_source"] for record in in_window})
    }
    result = {
        "candidate": "candidate-08-aggtrade-orderflow-probe",
        "purpose": "causal ten-second order-flow path diagnostic; not NautilusTrader execution evidence",
        "window": window,
        "scenario_contract": {
            "absorption": "completed boundary swept by high outward aggressive flow but close returns inside; later opposite-flow displacement breaks the sweep bar",
            "acceptance": "high outward aggressive flow closes beyond completed boundary; lower-energy retest holds; later same-direction flow resumes",
            "stop": "beyond observed sweep/displacement extreme with 0.03 five-minute ATR buffer and 0.10 ATR minimum distance",
            "target": "nearest still-active completed 4-hour/day/week external level; no R fallback",
            "cost": "6 bp per fill plus two adverse ticks",
            "path": "conservative first touch for 240 minutes",
        },
        "diagnostics": diagnostics,
        "combined": _summary(in_window),
        "by_family": families,
        "by_boundary_source": sources,
        "records": in_window,
        "aggtrade_data_quality": agg_quality,
        "aggtrade_source_files": [asdict(source) for source in agg_sources],
        "kline_data_quality": loaded.quality,
        "kline_source_files": [asdict(source) for source in loaded.source_files],
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=HERE / "config_range_fvg.json")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--data-cache", type=Path, required=True)
    args = parser.parse_args()
    result = run(args.config.resolve(), args.output.resolve(), args.data_cache.resolve())
    print(json.dumps({
        "diagnostics": result["diagnostics"],
        "combined": result["combined"],
        "by_family": result["by_family"],
        "by_boundary_source": result["by_boundary_source"],
        "aggtrade_data_quality": result["aggtrade_data_quality"],
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
