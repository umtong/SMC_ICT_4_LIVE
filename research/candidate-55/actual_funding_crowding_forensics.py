#!/usr/bin/env python3
"""Result-blind post-source audit of actual-funding crowding reversal.

This experiment reuses the strongest surviving policy from
``xxSeasonxx/quant_strategies`` without tuning it to Candidate 55:

    five completed funding settlements have negative summed funding
    + the symbol is down over 120 minutes
    + its return underperforms the four-symbol cross-section mean by >= 2.5 bps
    -> buy the strongest source-ranked crowding/dislocation setup and hold 12 hours.

The source policy is adapted only where the project contract requires it:

* BTCUSDT, ETHUSDT, SOLUSDT and XRPUSDT only;
* one global entry/position slot;
* one trade per continuous funding-plus-dislocation causal episode;
* actual settled Binance funding, never premium-index as a substitute;
* one-minute information delay plus one-minute decision lag;
* next-minute-open entry and a fixed 12-hour exit;
* 20 bps project round-trip costs, with 30/40 bps liquidity stress;
* actual funding cash flow while the long is open.

No threshold, lookback, hold, weighting exponent, session or symbol exception is
fit here.  The windows are after the external source dataset ended on
2026-04-13.  This is an episode/geometry audit, not yet a Nautilus strategy: the
source has no structural stop, so it cannot yet satisfy current-NAV 3% planned-
loss sizing.  A positive result warrants stop/invalidation research; it does
not by itself warrant production or long validation.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
import math
from pathlib import Path
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Iterable

import pandas as pd

from kline_only_inputs import load_range


SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT")
SYMBOL_PRIORITY = {symbol: index for index, symbol in enumerate(SYMBOLS)}

SOURCE_REPOSITORY = "xxSeasonxx/quant_strategies"
SOURCE_COMMIT = "027552726e08a41612cb4faeabb1abb052c06887"
SOURCE_STRATEGY = "candidates/crypto_perp_funding_crowding_reversal/strategy.py"
SOURCE_DATA_END = date(2026, 4, 13)

FUNDING_LOOKBACK_EVENTS = 5
RETURN_LOOKBACK_MINUTES = 120
RECENT_RETURN_LOOKBACK_MINUTES = 60
DECISION_INTERVAL_MINUTES = 240
DECISION_LAG_MINUTES = 1
HOLD_MINUTES = 720
MIN_ABS_FUNDING_BPS = 1.0
MIN_IDIOSYNCRATIC_RETURN_BPS = 2.5
MAX_RECENT_SAME_DIRECTION_RETURN_BPS = 1_000.0
DISLOCATION_WEIGHT_POWER = 3.0
PRICE_WARMUP_DAYS = 3
OUTCOME_DAYS = 1
PROJECT_ROUND_TRIP_COST_BPS = 20.0
STRESS_ROUND_TRIP_COST_BPS = (30.0, 40.0)

VISION_ROOT = "https://data.binance.vision/data/futures/um"
REST_FUNDING_URL = "https://fapi.binance.com/fapi/v1/fundingRate"


@dataclass(frozen=True, slots=True)
class FundingEvidence:
    symbol: str
    source: str
    period: str
    archive_or_url: str
    checksum: str | None
    sha256: str
    rows: int
    first_time: str | None
    last_time: str | None


@dataclass(frozen=True, slots=True)
class FundingEvent:
    symbol: str
    funding_time: pd.Timestamp
    available_at: pd.Timestamp
    funding_interval_hours: float | None
    rate: float


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1 << 20):
            digest.update(block)
    return digest.hexdigest()


def _download_checked(url: str, directory: Path) -> tuple[Path, Path, str]:
    directory.mkdir(parents=True, exist_ok=True)
    filename = url.rsplit("/", 1)[-1]
    archive = directory / filename
    checksum = directory / f"{filename}.CHECKSUM"
    if not archive.exists():
        urllib.request.urlretrieve(url, archive)
    if not checksum.exists():
        urllib.request.urlretrieve(url + ".CHECKSUM", checksum)
    expected = checksum.read_text(encoding="utf-8").strip().split()[0].lower()
    actual = _sha256_file(archive)
    if actual != expected:
        raise RuntimeError(f"checksum mismatch for {archive}: {actual} != {expected}")
    return archive, checksum, actual


def _parse_funding_time(values: pd.Series) -> pd.Series:
    numeric = pd.to_numeric(values, errors="coerce")
    if numeric.notna().all():
        first = abs(int(numeric.iloc[0]))
        unit = "us" if first > 10**14 else "ms"
        return pd.to_datetime(numeric.astype("int64"), unit=unit, utc=True, errors="raise")
    return pd.to_datetime(values, utc=True, errors="raise")


def _read_funding_archive(path: Path, symbol: str) -> pd.DataFrame:
    raw = pd.read_csv(path, compression="zip")
    required = {"calc_time", "funding_interval_hours", "last_funding_rate"}
    if not required.issubset(raw.columns):
        fallback = pd.read_csv(path, compression="zip", header=None)
        if fallback.shape[1] < 3:
            raise RuntimeError(f"unexpected funding archive schema in {path}: {list(raw.columns)}")
        fallback = fallback.iloc[:, :3].copy()
        fallback.columns = ["calc_time", "funding_interval_hours", "last_funding_rate"]
        first = str(fallback.iloc[0]["calc_time"]).strip().lower()
        if first in {"calc_time", "fundingtime", "funding_time"}:
            fallback = fallback.iloc[1:].copy()
        raw = fallback
    frame = raw[["calc_time", "funding_interval_hours", "last_funding_rate"]].copy()
    frame["funding_time"] = _parse_funding_time(frame["calc_time"])
    frame["funding_interval_hours"] = pd.to_numeric(
        frame["funding_interval_hours"], errors="coerce"
    )
    frame["funding_rate"] = pd.to_numeric(frame["last_funding_rate"], errors="raise")
    if not frame["funding_rate"].map(math.isfinite).all():
        raise RuntimeError(f"non-finite funding rate in {path}")
    frame["symbol"] = symbol
    return frame[
        ["symbol", "funding_time", "funding_interval_hours", "funding_rate"]
    ].sort_values("funding_time")


def _month_starts(start: date, end: date) -> Iterable[date]:
    cursor = date(start.year, start.month, 1)
    last = date(end.year, end.month, 1)
    while cursor <= last:
        yield cursor
        if cursor.month == 12:
            cursor = date(cursor.year + 1, 1, 1)
        else:
            cursor = date(cursor.year, cursor.month + 1, 1)


def _month_end(month_start: date) -> date:
    if month_start.month == 12:
        return date(month_start.year + 1, 1, 1) - timedelta(days=1)
    return date(month_start.year, month_start.month + 1, 1) - timedelta(days=1)


def _archive_rows(
    *, symbol: str, start: date, end: date, cache: Path
) -> tuple[list[pd.DataFrame], list[FundingEvidence]]:
    frames: list[pd.DataFrame] = []
    evidence: list[FundingEvidence] = []
    today = datetime.now(timezone.utc).date()
    for month_start in _month_starts(start, end):
        month_finish = _month_end(month_start)
        monthly_complete = month_finish < today
        used_monthly = False
        if monthly_complete:
            stamp = f"{month_start.year:04d}-{month_start.month:02d}"
            filename = f"{symbol}-fundingRate-{stamp}.zip"
            url = f"{VISION_ROOT}/monthly/fundingRate/{symbol}/{filename}"
            try:
                archive, checksum, digest = _download_checked(
                    url, cache / "fundingRate" / "monthly" / symbol
                )
                frame = _read_funding_archive(archive, symbol)
                frames.append(frame)
                evidence.append(
                    FundingEvidence(
                        symbol=symbol,
                        source="binance-vision-monthly-checksum-verified",
                        period=stamp,
                        archive_or_url=str(archive),
                        checksum=str(checksum),
                        sha256=digest,
                        rows=len(frame),
                        first_time=(
                            frame["funding_time"].iloc[0].isoformat() if not frame.empty else None
                        ),
                        last_time=(
                            frame["funding_time"].iloc[-1].isoformat() if not frame.empty else None
                        ),
                    )
                )
                used_monthly = True
            except urllib.error.HTTPError as exc:
                if exc.code != 404:
                    raise
        if used_monthly:
            continue

        day = max(start, month_start)
        day_end = min(end, month_finish)
        while day <= day_end:
            stamp = day.isoformat()
            filename = f"{symbol}-fundingRate-{stamp}.zip"
            url = f"{VISION_ROOT}/daily/fundingRate/{symbol}/{filename}"
            archive, checksum, digest = _download_checked(
                url, cache / "fundingRate" / "daily" / symbol
            )
            frame = _read_funding_archive(archive, symbol)
            frames.append(frame)
            evidence.append(
                FundingEvidence(
                    symbol=symbol,
                    source="binance-vision-daily-checksum-verified",
                    period=stamp,
                    archive_or_url=str(archive),
                    checksum=str(checksum),
                    sha256=digest,
                    rows=len(frame),
                    first_time=(
                        frame["funding_time"].iloc[0].isoformat() if not frame.empty else None
                    ),
                    last_time=(
                        frame["funding_time"].iloc[-1].isoformat() if not frame.empty else None
                    ),
                )
            )
            day += timedelta(days=1)
    return frames, evidence


def _rest_rows(
    *, symbol: str, start: date, end: date, cache: Path
) -> tuple[list[pd.DataFrame], list[FundingEvidence]]:
    start_ms = int(pd.Timestamp(start, tz="UTC").timestamp() * 1000)
    end_ms = int(
        (pd.Timestamp(end + timedelta(days=1), tz="UTC") - pd.Timedelta(milliseconds=1)).timestamp()
        * 1000
    )
    query = urllib.parse.urlencode(
        {"symbol": symbol, "startTime": start_ms, "endTime": end_ms, "limit": 1000}
    )
    url = f"{REST_FUNDING_URL}?{query}"
    request = urllib.request.Request(url, headers={"User-Agent": "candidate55-research/1.0"})
    with urllib.request.urlopen(request, timeout=45) as response:
        payload = response.read()
    rows = json.loads(payload)
    if not isinstance(rows, list):
        raise RuntimeError(f"unexpected funding REST response for {symbol}: {rows!r}")
    normalized = json.dumps(rows, sort_keys=True, separators=(",", ":")).encode("utf-8")
    digest = hashlib.sha256(normalized).hexdigest()
    directory = cache / "fundingRate" / "rest" / symbol
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{symbol}-{start.isoformat()}-{end.isoformat()}.json"
    path.write_bytes(normalized)
    frame = pd.DataFrame(rows)
    required = {"fundingTime", "fundingRate"}
    if not required.issubset(frame.columns):
        raise RuntimeError(f"unexpected funding REST schema for {symbol}: {list(frame.columns)}")
    result = pd.DataFrame(
        {
            "symbol": symbol,
            "funding_time": pd.to_datetime(
                pd.to_numeric(frame["fundingTime"], errors="raise").astype("int64"),
                unit="ms",
                utc=True,
            ),
            "funding_interval_hours": math.nan,
            "funding_rate": pd.to_numeric(frame["fundingRate"], errors="raise"),
        }
    ).sort_values("funding_time")
    evidence = FundingEvidence(
        symbol=symbol,
        source="binance-official-rest-fallback",
        period=f"{start.isoformat()}..{end.isoformat()}",
        archive_or_url=REST_FUNDING_URL,
        checksum=None,
        sha256=digest,
        rows=len(result),
        first_time=result["funding_time"].iloc[0].isoformat() if not result.empty else None,
        last_time=result["funding_time"].iloc[-1].isoformat() if not result.empty else None,
    )
    return [result], [evidence]


def load_funding(
    *, symbol: str, start: date, end: date, cache: Path
) -> tuple[list[FundingEvent], list[FundingEvidence]]:
    try:
        frames, evidence = _archive_rows(symbol=symbol, start=start, end=end, cache=cache)
    except (urllib.error.HTTPError, urllib.error.URLError) as archive_error:
        try:
            frames, evidence = _rest_rows(symbol=symbol, start=start, end=end, cache=cache)
        except Exception as rest_error:  # pragma: no cover - surfaced in CI evidence
            raise RuntimeError(
                f"funding unavailable from Binance Vision and REST for {symbol}: "
                f"archive={archive_error!r}; rest={rest_error!r}"
            ) from rest_error
    combined = pd.concat(frames, ignore_index=True).sort_values("funding_time")
    range_start = pd.Timestamp(start, tz="UTC")
    range_end = pd.Timestamp(end + timedelta(days=1), tz="UTC")
    combined = combined[
        (combined["funding_time"] >= range_start) & (combined["funding_time"] < range_end)
    ].copy()
    duplicated = combined["funding_time"].duplicated(keep=False)
    if duplicated.any():
        for timestamp, group in combined.loc[duplicated].groupby("funding_time", sort=False):
            if group["funding_rate"].nunique(dropna=False) > 1:
                raise RuntimeError(f"conflicting funding observations for {symbol} at {timestamp}")
        combined = combined.drop_duplicates("funding_time", keep="last")
    if combined.empty:
        raise RuntimeError(f"no funding events for {symbol} in {start}..{end}")
    if not combined["funding_time"].is_monotonic_increasing:
        raise RuntimeError(f"non-monotonic funding events for {symbol}")
    events = [
        FundingEvent(
            symbol=symbol,
            funding_time=pd.Timestamp(row.funding_time),
            available_at=pd.Timestamp(row.funding_time) + pd.Timedelta(minutes=1),
            funding_interval_hours=(
                float(row.funding_interval_hours)
                if pd.notna(row.funding_interval_hours)
                else None
            ),
            rate=float(row.funding_rate),
        )
        for row in combined.itertuples(index=False)
    ]
    return events, evidence


def _finite_float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _outcome(
    *,
    symbol: str,
    frame: pd.DataFrame,
    entry_index: int,
    funding: list[FundingEvent],
) -> dict[str, Any] | None:
    exit_index = entry_index + HOLD_MINUTES
    if entry_index < 0 or exit_index >= len(frame):
        return None
    entry = frame.iloc[entry_index]
    exit_row = frame.iloc[exit_index]
    entry_time = pd.Timestamp(entry.open_time_dt)
    exit_time = pd.Timestamp(exit_row.open_time_dt)
    entry_price = float(entry.open)
    exit_price = float(exit_row.open)
    if not (entry_price > 0.0 and exit_price > 0.0):
        return None
    window = frame.iloc[entry_index:exit_index]
    gross_price_return = exit_price / entry_price - 1.0
    held_funding = [
        item for item in funding if entry_time < item.funding_time < exit_time
    ]
    funding_cash_return = -sum(item.rate for item in held_funding)
    gross_with_funding = gross_price_return + funding_cash_return
    result: dict[str, Any] = {
        "symbol": symbol,
        "entry_time": entry_time.isoformat(),
        "exit_time": exit_time.isoformat(),
        "entry_price": entry_price,
        "exit_price": exit_price,
        "gross_price_return": gross_price_return,
        "funding_cash_return": funding_cash_return,
        "held_funding_events": len(held_funding),
        "gross_with_funding_return": gross_with_funding,
        "mfe_return_12h": float(window["high"].astype(float).max() / entry_price - 1.0),
        "mae_return_12h": float(window["low"].astype(float).min() / entry_price - 1.0),
    }
    for cost_bps in (PROJECT_ROUND_TRIP_COST_BPS, *STRESS_ROUND_TRIP_COST_BPS):
        result[f"net_return_{int(cost_bps)}bps"] = gross_with_funding - cost_bps / 10_000.0
    for horizon in (60, 240, 480, 720, 1_440):
        target_index = entry_index + horizon
        if target_index >= len(frame):
            result[f"gross_with_funding_{horizon}m"] = None
            result[f"net_20bps_{horizon}m"] = None
            continue
        target = frame.iloc[target_index]
        target_time = pd.Timestamp(target.open_time_dt)
        horizon_funding = -sum(
            item.rate for item in funding if entry_time < item.funding_time < target_time
        )
        gross = float(target.open) / entry_price - 1.0 + horizon_funding
        result[f"gross_with_funding_{horizon}m"] = gross
        result[f"net_20bps_{horizon}m"] = gross - PROJECT_ROUND_TRIP_COST_BPS / 10_000.0
    return result


def _summarize_returns(frame: pd.DataFrame, column: str) -> dict[str, Any]:
    if frame.empty or column not in frame:
        return {"trades": 0}
    values = pd.to_numeric(frame[column], errors="coerce").dropna()
    if values.empty:
        return {"trades": 0}
    positives = values[values > 0.0]
    negatives = values[values < 0.0]
    gross_profit = float(positives.sum())
    gross_loss = float(-negatives.sum())
    compounded = float((1.0 + values).prod() - 1.0)
    result: dict[str, Any] = {
        "trades": int(values.size),
        "wins": int((values > 0.0).sum()),
        "losses": int((values < 0.0).sum()),
        "win_rate": float((values > 0.0).mean()),
        "mean_return": float(values.mean()),
        "median_return": float(values.median()),
        "sum_return": float(values.sum()),
        "unit_notional_compounded_return": compounded,
        "gross_profit_return": gross_profit,
        "gross_loss_return": gross_loss,
        "profit_factor": gross_profit / gross_loss if gross_loss > 0.0 else None,
        "largest_winner_share": (
            float(positives.max() / gross_profit) if gross_profit > 0.0 else 1.0
        ),
    }
    if "symbol" in frame:
        by_symbol: dict[str, Any] = {}
        for symbol, group in frame.assign(_return=pd.to_numeric(frame[column], errors="coerce")).groupby(
            "symbol", sort=True
        ):
            returns = group["_return"].dropna()
            by_symbol[str(symbol)] = {
                "trades": int(returns.size),
                "sum_return": float(returns.sum()),
                "mean_return": float(returns.mean()) if not returns.empty else None,
                "wins": int((returns > 0.0).sum()),
            }
        result["by_symbol"] = by_symbol
    return result


def _state_summary(events: pd.DataFrame) -> dict[str, Any]:
    result: dict[str, Any] = {}
    if events.empty:
        return result
    for state, group in events.groupby("diagnostic_state", sort=True):
        result[str(state)] = _summarize_returns(group, "net_return_20bps")
    return result


def run(*, start: date, end: date, cache: Path, output: Path) -> dict[str, Any]:
    if start <= SOURCE_DATA_END:
        raise ValueError(
            f"audit must begin after external source data end {SOURCE_DATA_END}; got {start}"
        )
    if end < start:
        raise ValueError("end precedes start")
    output.mkdir(parents=True, exist_ok=True)
    cache.mkdir(parents=True, exist_ok=True)

    load_start = start - timedelta(days=PRICE_WARMUP_DAYS)
    load_end = end + timedelta(days=OUTCOME_DAYS)
    prices: dict[str, pd.DataFrame] = {}
    input_evidence: dict[str, Any] = {}
    funding_by_symbol: dict[str, list[FundingEvent]] = {}
    funding_evidence: list[FundingEvidence] = []

    reference_clock: pd.DatetimeIndex | None = None
    for symbol in SYMBOLS:
        frame, _feature_path, raw_files, evidence = load_range(
            symbol=symbol,
            start=load_start,
            end=load_end,
            cache=cache / "klines",
            output=output / "source" / symbol,
        )
        frame = frame.copy()
        frame["open_time_dt"] = pd.to_datetime(frame["open_time_dt"], utc=True)
        frame["close_time_dt"] = pd.to_datetime(frame["close_time_dt"], utc=True)
        frame = frame.sort_values("open_time_dt").reset_index(drop=True)
        clock = pd.DatetimeIndex(frame["open_time_dt"])
        if reference_clock is None:
            reference_clock = clock
        elif not clock.equals(reference_clock):
            raise RuntimeError(f"cross-symbol kline clock differs for {symbol}")
        prices[symbol] = frame
        input_evidence[symbol] = {
            "rows": len(frame),
            "raw_files": len(raw_files),
            "evidence_files": len(evidence),
            "first_open": clock[0].isoformat(),
            "last_open": clock[-1].isoformat(),
        }
        funding, funding_items = load_funding(
            symbol=symbol,
            start=load_start,
            end=load_end,
            cache=cache,
        )
        funding_by_symbol[symbol] = funding
        funding_evidence.extend(funding_items)

    assert reference_clock is not None
    start_ts = pd.Timestamp(start, tz="UTC")
    end_exclusive = pd.Timestamp(end + timedelta(days=1), tz="UTC")
    decision_indices = [
        index
        for index, timestamp in enumerate(reference_clock)
        if (
            start_ts <= timestamp < end_exclusive
            and (timestamp.hour * 60 + timestamp.minute) % DECISION_INTERVAL_MINUTES == 0
        )
    ]

    all_rows: list[dict[str, Any]] = []
    selected_rows: list[dict[str, Any]] = []
    active_episode_start: dict[str, pd.Timestamp | None] = {symbol: None for symbol in SYMBOLS}
    previous_setup_active: dict[str, bool] = {symbol: False for symbol in SYMBOLS}
    used_episode_keys: set[str] = set()
    slot_free_time = pd.Timestamp.min.tz_localize("UTC")

    for index in decision_indices:
        signal_rows = {symbol: prices[symbol].iloc[index] for symbol in SYMBOLS}
        signal_close_time = pd.Timestamp(signal_rows[SYMBOLS[0]].close_time_dt)
        information_available = signal_close_time.ceil("min")
        decision_time = information_available + pd.Timedelta(minutes=DECISION_LAG_MINUTES)
        entry_index = index + 1 + DECISION_LAG_MINUTES
        if entry_index >= len(reference_clock):
            continue

        returns_bps: dict[str, float] = {}
        recent_returns_bps: dict[str, float] = {}
        if index < RETURN_LOOKBACK_MINUTES:
            continue
        for symbol in SYMBOLS:
            frame = prices[symbol]
            current_close = float(frame.iloc[index].close)
            lookback_close = float(frame.iloc[index - RETURN_LOOKBACK_MINUTES].close)
            recent_close = float(frame.iloc[index - RECENT_RETURN_LOOKBACK_MINUTES].close)
            returns_bps[symbol] = (current_close / lookback_close - 1.0) * 10_000.0
            recent_returns_bps[symbol] = (current_close / recent_close - 1.0) * 10_000.0
        market_return_bps = sum(returns_bps.values()) / len(returns_bps)

        candidates: list[dict[str, Any]] = []
        for symbol in SYMBOLS:
            events = [
                item
                for item in funding_by_symbol[symbol]
                if item.available_at <= decision_time
            ]
            enough = len(events) >= FUNDING_LOOKBACK_EVENTS
            recent_events = events[-FUNDING_LOOKBACK_EVENTS:] if enough else []
            funding_pressure_bps = (
                sum(item.rate for item in recent_events) * 10_000.0 if enough else math.nan
            )
            latest_funding_bps = (
                recent_events[-1].rate * 10_000.0 if enough else math.nan
            )
            negative_funding = bool(enough and funding_pressure_bps <= -MIN_ABS_FUNDING_BPS)

            idiosyncratic_down_bps = market_return_bps - returns_bps[symbol]
            price_dislocation = (
                returns_bps[symbol] <= 0.0
                and idiosyncratic_down_bps >= MIN_IDIOSYNCRATIC_RETURN_BPS
                and recent_returns_bps[symbol]
                >= -MAX_RECENT_SAME_DIRECTION_RETURN_BPS
            )
            eligible = negative_funding and price_dislocation
            if eligible and not previous_setup_active[symbol]:
                active_episode_start[symbol] = decision_time
            if not eligible:
                active_episode_start[symbol] = None
            previous_setup_active[symbol] = eligible
            episode_start = active_episode_start[symbol]
            episode_key = (
                f"{symbol}:FUNDING_DISLOCATION_EPISODE:{episode_start.isoformat()}"
                if episode_start is not None
                else ""
            )

            if eligible:
                state = "NEGATIVE_FUNDING_AND_IDIOSYNCRATIC_DOWNSIDE"
            elif negative_funding:
                state = "NEGATIVE_FUNDING_ONLY"
            elif price_dislocation:
                state = "IDIOSYNCRATIC_DOWNSIDE_ONLY"
            else:
                state = "NEITHER"

            outcome = _outcome(
                symbol=symbol,
                frame=prices[symbol],
                entry_index=entry_index,
                funding=funding_by_symbol[symbol],
            )
            row: dict[str, Any] = {
                "symbol": symbol,
                "signal_time": signal_close_time.isoformat(),
                "decision_time": decision_time.isoformat(),
                "entry_index": entry_index,
                "market_return_bps_120m": market_return_bps,
                "symbol_return_bps_120m": returns_bps[symbol],
                "recent_return_bps_60m": recent_returns_bps[symbol],
                "idiosyncratic_down_bps": idiosyncratic_down_bps,
                "funding_history_ready": int(enough),
                "funding_pressure_bps_5_events": _finite_float(funding_pressure_bps),
                "latest_funding_bps": _finite_float(latest_funding_bps),
                "negative_funding_state": int(negative_funding),
                "price_dislocation_state": int(price_dislocation),
                "source_eligible": int(eligible),
                "diagnostic_state": state,
                "funding_episode_key": episode_key,
                "funding_episode_used_before_decision": int(
                    bool(episode_key) and episode_key in used_episode_keys
                ),
                "source_score": (
                    abs(funding_pressure_bps) + idiosyncratic_down_bps
                    if eligible
                    else None
                ),
                "source_dislocation_weight": (
                    idiosyncratic_down_bps**DISLOCATION_WEIGHT_POWER
                    if eligible
                    else None
                ),
                "arbitration_state": "NOT_ELIGIBLE",
                "selected_global_trade": 0,
            }
            if recent_events:
                row["funding_event_times"] = "|".join(
                    item.funding_time.isoformat() for item in recent_events
                )
                row["funding_event_rates"] = "|".join(
                    f"{item.rate:.12g}" for item in recent_events
                )
            else:
                row["funding_event_times"] = ""
                row["funding_event_rates"] = ""
            if outcome is not None:
                row.update(outcome)
            all_rows.append(row)
            if eligible and outcome is not None:
                candidates.append(row)

        if not candidates:
            continue
        candidates.sort(
            key=lambda item: (
                -float(item["source_score"]),
                -abs(float(item["funding_pressure_bps_5_events"])),
                -abs(float(item["symbol_return_bps_120m"])),
                SYMBOL_PRIORITY[str(item["symbol"])],
            )
        )
        if decision_time < slot_free_time:
            for item in candidates:
                item["arbitration_state"] = "GLOBAL_SLOT_OCCUPIED"
            continue
        unused = [
            item
            for item in candidates
            if item["funding_episode_key"]
            and item["funding_episode_key"] not in used_episode_keys
        ]
        if not unused:
            for item in candidates:
                item["arbitration_state"] = "EPISODE_ALREADY_USED"
            continue
        winner = unused[0]
        for item in candidates:
            if item is winner:
                item["arbitration_state"] = "SELECTED"
                item["selected_global_trade"] = 1
            elif item["funding_episode_key"] in used_episode_keys:
                item["arbitration_state"] = "EPISODE_ALREADY_USED"
            else:
                item["arbitration_state"] = "ARBITRATION_LOSS"
        used_episode_keys.add(str(winner["funding_episode_key"]))
        slot_free_time = pd.Timestamp(str(winner["exit_time"]))
        selected_rows.append(dict(winner))

    events = pd.DataFrame(all_rows)
    selected = pd.DataFrame(selected_rows)
    events.to_csv(output / "decision_states.csv", index=False)
    selected.to_csv(output / "selected_trades.csv", index=False)
    (output / "funding_evidence.json").write_text(
        json.dumps([asdict(item) for item in funding_evidence], indent=2, sort_keys=True)
        + "\n",
        encoding="utf-8",
    )

    selected_20 = _summarize_returns(selected, "net_return_20bps")
    selected_30 = _summarize_returns(selected, "net_return_30bps")
    selected_40 = _summarize_returns(selected, "net_return_40bps")
    source_eligible = events[events["source_eligible"] == 1] if not events.empty else events
    source_summary = _summarize_returns(source_eligible, "net_return_20bps")
    state_summary = _state_summary(events)

    trades = int(selected_20.get("trades", 0))
    support_checks = {
        "at_least_two_strict_independent_trades": trades >= 2,
        "positive_mean_after_project_20bps": (
            float(selected_20.get("mean_return", -math.inf)) > 0.0
        ),
        "positive_median_after_project_20bps": (
            float(selected_20.get("median_return", -math.inf)) > 0.0
        ),
        "positive_mean_after_30bps_liquidity_stress": (
            float(selected_30.get("mean_return", -math.inf)) > 0.0
        ),
        "not_single_winner_dominated": (
            float(selected_20.get("largest_winner_share", 1.0)) <= 0.50
        ),
    }
    summary: dict[str, Any] = {
        "candidate": "candidate-55",
        "family": "ACTUAL_FUNDING_CROWDING_REVERSAL_LONG",
        "stage": "POST_SOURCE_RESULT_BLIND_EPISODE_FORENSICS",
        "production_ready": False,
        "nautilus_implementation_ready": False,
        "reason_not_yet_nautilus_ready": (
            "The reused source has a fixed 12h exit but no causal structural invalidation; "
            "current-NAV 3% planned-loss sizing is therefore undefined."
        ),
        "source": {
            "repository": SOURCE_REPOSITORY,
            "commit": SOURCE_COMMIT,
            "strategy_path": SOURCE_STRATEGY,
            "source_data_end": SOURCE_DATA_END.isoformat(),
        },
        "evaluation_start": start.isoformat(),
        "evaluation_end": end.isoformat(),
        "calendar_days": (end - start).days + 1,
        "post_source_oos": True,
        "universe": list(SYMBOLS),
        "one_global_slot": True,
        "strict_causal_episode_ownership": True,
        "policy": {
            "funding_source": "actual settled Binance funding rate",
            "funding_lookback_events": FUNDING_LOOKBACK_EVENTS,
            "return_lookback_minutes": RETURN_LOOKBACK_MINUTES,
            "recent_return_lookback_minutes": RECENT_RETURN_LOOKBACK_MINUTES,
            "decision_interval_minutes": DECISION_INTERVAL_MINUTES,
            "decision_lag_minutes": DECISION_LAG_MINUTES,
            "hold_minutes": HOLD_MINUTES,
            "min_abs_funding_bps": MIN_ABS_FUNDING_BPS,
            "min_idiosyncratic_return_bps": MIN_IDIOSYNCRATIC_RETURN_BPS,
            "max_recent_same_direction_return_bps": (
                MAX_RECENT_SAME_DIRECTION_RETURN_BPS
            ),
            "selection_score": "abs_funding_pressure_bps_plus_raw_idiosyncratic_down_bps",
            "source_portfolio_weighting": "raw_idiosyncratic_down_bps_cubed",
            "one_slot_adaptation": "highest source selection score wins; source weighting retained as diagnostic",
            "dislocation_weight_power": DISLOCATION_WEIGHT_POWER,
            "round_trip_cost_bps": PROJECT_ROUND_TRIP_COST_BPS,
            "liquidity_stress_round_trip_cost_bps": list(
                STRESS_ROUND_TRIP_COST_BPS
            ),
        },
        "decisions": len(decision_indices),
        "decision_symbol_rows": int(len(events)),
        "source_eligible_rows": int(source_eligible.shape[0]),
        "continuous_funding_dislocation_episodes": int(
            events.loc[
                events["funding_episode_key"].astype(str).ne(""),
                "funding_episode_key",
            ].nunique()
            if not events.empty
            else 0
        ),
        "strict_selected_trades": trades,
        "strict_trades_per_day": trades / ((end - start).days + 1),
        "selected_after_20bps": selected_20,
        "selected_after_30bps": selected_30,
        "selected_after_40bps": selected_40,
        "all_source_eligible_after_20bps_correlated_rows": source_summary,
        "diagnostic_state_outcomes_after_20bps_correlated_rows": state_summary,
        "support_checks": support_checks,
        "prediction_supported_in_this_window": all(support_checks.values()),
        "input_evidence": input_evidence,
        "funding_evidence_items": len(funding_evidence),
        "falsifier": (
            "Reject or redesign this family if post-source strict independent trades do not "
            "remain positive after 20 bps, fail 30 bps liquidity stress, are dominated by one "
            "winner/symbol, or if the actual-funding conjunction does not outperform its "
            "component-only diagnostic states. Do not loosen thresholds after seeing results."
        ),
    }
    (output / "SUMMARY.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, indent=2, sort_keys=True, allow_nan=False))
    return summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    run(
        start=date.fromisoformat(args.start),
        end=date.fromisoformat(args.end),
        cache=args.cache.resolve(),
        output=args.output.resolve(),
    )


if __name__ == "__main__":
    main()
