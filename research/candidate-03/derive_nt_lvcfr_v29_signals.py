#!/usr/bin/env python3
"""Derive V29 cross-asset consensus catch-up signals.

Economic mechanism
------------------
Information and order flow can diffuse across related assets with a short lag.
V29 treats ETH, SOL and XRP as a causal leader basket and BTC as the traded
laggard. It does not assume a fixed leader. A signal requires:

* a 3-minute common leader shock, standardized only with prior data;
* at least two leaders with futures/spot return and taker-flow agreement;
* a positive rolling BTC beta to the common factor;
* BTC underreaction relative to the causal beta-implied move;
* a second completed 3-minute block where leaders hold and BTC starts catch-up;
* a completed one-minute BTC pullback defense with BTC and leader flow aligned.

The beta-implied fair value is the structural target. The pullback bar and local
ATR define invalidation. No evaluation-week outcome is used and this module
never simulates orders, fills, fees, funding, positions, PnL or NAV.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import urllib.request
import zipfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT")
LEADERS = SYMBOLS[1:]
BAR_MINUTES = 3
VOL_WINDOW = 7 * 24 * 60 // BAR_MINUTES
VOL_MIN_PERIODS = 3 * 24 * 60 // BAR_MINUTES
BETA_WINDOW = 30 * 24 * 60 // BAR_MINUTES
BETA_MIN_PERIODS = 14 * 24 * 60 // BAR_MINUTES
SHOCK_Z = 1.5
UNDERREACTION_Z = 0.50
RETEST_EXPIRY_MINUTES = 9
STOP_BUFFER_ATR = 0.20
MIN_SIGNAL_SEPARATION_MINUTES = 30


def month_starts(start: date, end: date) -> list[date]:
    current = date(start.year, start.month, 1)
    final = date(end.year, end.month, 1)
    output: list[date] = []
    while current <= final:
        output.append(current)
        current = date(
            current.year + (current.month == 12),
            1 if current.month == 12 else current.month + 1,
            1,
        )
    return output


def fetch(url: str, target: Path) -> None:
    if target.exists() and target.stat().st_size > 0:
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".part")
    request = urllib.request.Request(url, headers={"User-Agent": "candidate-03-v29"})
    with urllib.request.urlopen(request, timeout=180) as response, temporary.open("wb") as stream:
        while chunk := response.read(1024 * 1024):
            stream.write(chunk)
    temporary.replace(target)


def archive_path(*, symbol: str, market: str, month: date, root: Path) -> Path:
    stamp = month.strftime("%Y-%m")
    if market == "futures":
        prefix = f"https://data.binance.vision/data/futures/um/monthly/klines/{symbol}/1m"
    elif market == "spot":
        prefix = f"https://data.binance.vision/data/spot/monthly/klines/{symbol}/1m"
    else:
        raise ValueError(market)
    name = f"{symbol}-1m-{stamp}.zip"
    target = root / market / symbol / name
    fetch(f"{prefix}/{name}", target)
    return target


def read_archive(path: Path, prefix: str) -> pd.DataFrame:
    with zipfile.ZipFile(path) as archive:
        members = [name for name in archive.namelist() if name.lower().endswith(".csv")]
        if len(members) != 1:
            raise ValueError(f"expected one CSV in {path}: {members}")
        with archive.open(members[0]) as stream:
            raw = pd.read_csv(stream, header=None, low_memory=False)
    numeric_time = pd.to_numeric(raw.iloc[:, 0], errors="coerce")
    valid = numeric_time.notna()
    frame = raw.loc[valid]
    timestamps = numeric_time.loc[valid].to_numpy(dtype=np.int64)
    timestamps = np.where(timestamps >= 100_000_000_000_000, timestamps // 1_000, timestamps)

    def values(column: int) -> np.ndarray:
        return pd.to_numeric(frame.iloc[:, column], errors="raise").to_numpy(dtype=float)

    return pd.DataFrame(
        {
            "open_time_ms": timestamps.astype(np.int64),
            f"{prefix}_open": values(1),
            f"{prefix}_high": values(2),
            f"{prefix}_low": values(3),
            f"{prefix}_close": values(4),
            f"{prefix}_quote": values(7),
            f"{prefix}_buy_quote": values(10),
        }
    )


def load_minutes(
    *, start: datetime, end: datetime, root: Path
) -> tuple[pd.DataFrame, list[dict[str, Any]]]:
    by_symbol: list[pd.DataFrame] = []
    sources: list[dict[str, Any]] = []
    for symbol in SYMBOLS:
        markets: list[pd.DataFrame] = []
        for market in ("futures", "spot"):
            pieces: list[pd.DataFrame] = []
            for month in month_starts(start.date(), end.date()):
                path = archive_path(symbol=symbol, market=market, month=month, root=root)
                prefix = f"{symbol.lower()}_{market}"
                pieces.append(read_archive(path, prefix))
                sources.append(
                    {
                        "symbol": symbol,
                        "market": market,
                        "month": month.strftime("%Y-%m"),
                        "path": str(path),
                        "size_bytes": path.stat().st_size,
                    }
                )
            market_frame = pd.concat(pieces, ignore_index=True).drop_duplicates("open_time_ms")
            markets.append(market_frame)
        by_symbol.append(markets[0].merge(markets[1], on="open_time_ms", how="inner"))
    merged = by_symbol[0]
    for frame in by_symbol[1:]:
        merged = merged.merge(frame, on="open_time_ms", how="inner")
    start_ms = int(start.timestamp() * 1000)
    end_ms = int(end.timestamp() * 1000)
    merged = merged[
        (merged.open_time_ms >= start_ms) & (merged.open_time_ms < end_ms)
    ].sort_values("open_time_ms").drop_duplicates("open_time_ms").reset_index(drop=True)
    if merged.empty:
        raise ValueError("no aligned cross-asset minute data")
    expected = np.arange(
        int(merged.open_time_ms.iloc[0]),
        int(merged.open_time_ms.iloc[-1]) + 60_000,
        60_000,
        dtype=np.int64,
    )
    actual = merged.open_time_ms.to_numpy(dtype=np.int64)
    if not np.array_equal(expected, actual):
        raise ValueError("cross-asset minute data is not continuous")
    return merged, sources


def aggregate_bars(minutes: pd.DataFrame) -> pd.DataFrame:
    frame = minutes.copy()
    frame["bucket"] = frame.open_time_ms // (BAR_MINUTES * 60_000)
    aggregations: dict[str, str] = {"open_time_ms": "first"}
    for symbol in SYMBOLS:
        lower = symbol.lower()
        for market in ("futures", "spot"):
            prefix = f"{lower}_{market}"
            aggregations[f"{prefix}_open"] = "first"
            aggregations[f"{prefix}_high"] = "max"
            aggregations[f"{prefix}_low"] = "min"
            aggregations[f"{prefix}_close"] = "last"
            aggregations[f"{prefix}_quote"] = "sum"
            aggregations[f"{prefix}_buy_quote"] = "sum"
    bars = frame.groupby("bucket", sort=True).agg(aggregations)
    counts = frame.groupby("bucket", sort=True).size()
    bars = bars.loc[counts == BAR_MINUTES].reset_index(drop=True)
    bars["end_time_ms"] = bars.open_time_ms + BAR_MINUTES * 60_000
    for symbol in SYMBOLS:
        lower = symbol.lower()
        for market in ("futures", "spot"):
            prefix = f"{lower}_{market}"
            bars[f"{prefix}_return"] = np.log(
                bars[f"{prefix}_close"] / bars[f"{prefix}_close"].shift(1)
            )
            bars[f"{prefix}_flow"] = (
                2.0 * bars[f"{prefix}_buy_quote"] - bars[f"{prefix}_quote"]
            ) / bars[f"{prefix}_quote"].replace(0.0, np.nan)
    leader_returns = np.column_stack(
        [bars[f"{symbol.lower()}_futures_return"].to_numpy() for symbol in LEADERS]
    )
    bars["common_return"] = np.nanmedian(leader_returns, axis=1)
    bars["common_vol"] = (
        bars.common_return.shift(1)
        .rolling(VOL_WINDOW, min_periods=VOL_MIN_PERIODS)
        .std()
    )
    bars["btc_vol"] = (
        bars.btcusdt_futures_return.shift(1)
        .rolling(VOL_WINDOW, min_periods=VOL_MIN_PERIODS)
        .std()
    )
    common_lag = bars.common_return.shift(1)
    btc_lag = bars.btcusdt_futures_return.shift(1)
    covariance = btc_lag.rolling(BETA_WINDOW, min_periods=BETA_MIN_PERIODS).cov(common_lag)
    variance = common_lag.rolling(BETA_WINDOW, min_periods=BETA_MIN_PERIODS).var()
    bars["beta"] = covariance / variance.replace(0.0, np.nan)
    previous_close = bars.btcusdt_futures_close.shift(1)
    true_range = pd.concat(
        [
            bars.btcusdt_futures_high - bars.btcusdt_futures_low,
            (bars.btcusdt_futures_high - previous_close).abs(),
            (bars.btcusdt_futures_low - previous_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    bars["btc_atr"] = true_range.shift(1).rolling(20, min_periods=20).mean()
    return bars


def aligned_leaders(row: Any, direction: int) -> list[str]:
    output: list[str] = []
    for symbol in LEADERS:
        lower = symbol.lower()
        values = (
            direction * float(getattr(row, f"{lower}_futures_return")),
            direction * float(getattr(row, f"{lower}_spot_return")),
            direction * float(getattr(row, f"{lower}_futures_flow")),
            direction * float(getattr(row, f"{lower}_spot_flow")),
        )
        if all(math.isfinite(value) and value > 0.0 for value in values):
            output.append(symbol)
    return output


def minute_flow(row: Any, symbol: str, market: str) -> float:
    prefix = f"{symbol.lower()}_{market}"
    quote = float(getattr(row, f"{prefix}_quote"))
    buy = float(getattr(row, f"{prefix}_buy_quote"))
    return (2.0 * buy - quote) / quote if quote > 0.0 else 0.0


def leader_minute_alignment(row: Any, direction: int) -> int:
    count = 0
    for symbol in LEADERS:
        close = float(getattr(row, f"{symbol.lower()}_futures_close"))
        open_ = float(getattr(row, f"{symbol.lower()}_futures_open"))
        spot_close = float(getattr(row, f"{symbol.lower()}_spot_close"))
        spot_open = float(getattr(row, f"{symbol.lower()}_spot_open"))
        if (
            direction * (close - open_) > 0.0
            and direction * (spot_close - spot_open) > 0.0
            and direction * minute_flow(row, symbol, "futures") > 0.0
            and direction * minute_flow(row, symbol, "spot") > 0.0
        ):
            count += 1
    return count


def find_retest(
    minutes: pd.DataFrame,
    *,
    start_ms: int,
    direction: int,
    zone_mid: float,
) -> Any | None:
    future = minutes[
        (minutes.open_time_ms >= start_ms)
        & (minutes.open_time_ms < start_ms + RETEST_EXPIRY_MINUTES * 60_000)
    ]
    for row in future.itertuples(index=False):
        close = float(row.btcusdt_futures_close)
        open_ = float(row.btcusdt_futures_open)
        if direction > 0:
            touched = float(row.btcusdt_futures_low) <= zone_mid
            defended = close > zone_mid and close > open_
        else:
            touched = float(row.btcusdt_futures_high) >= zone_mid
            defended = close < zone_mid and close < open_
        if not touched or not defended:
            continue
        if (
            direction * minute_flow(row, "BTCUSDT", "futures") <= 0.0
            or direction * minute_flow(row, "BTCUSDT", "spot") <= 0.0
            or leader_minute_alignment(row, direction) < 2
        ):
            continue
        return row
    return None


def derive_v29(
    *, week_start: date, prepared_root: Path, output_manifest: Path
) -> list[dict[str, Any]]:
    evaluation_start = datetime.combine(
        week_start, datetime.min.time(), tzinfo=timezone.utc
    )
    evaluation_end = evaluation_start + timedelta(days=7)
    warmup_start = evaluation_start - timedelta(days=45)
    minutes, sources = load_minutes(
        start=warmup_start,
        end=evaluation_end + timedelta(days=1),
        root=prepared_root / "cross_asset_data",
    )
    bars = aggregate_bars(minutes)
    start_ms = int(evaluation_start.timestamp() * 1000)
    end_ms = int(evaluation_end.timestamp() * 1000)
    signals: list[dict[str, Any]] = []
    no_trade: dict[str, int] = {}
    last_signal_ms = -10**30

    for index in range(1, len(bars) - 1):
        event = bars.iloc[index]
        confirm = bars.iloc[index + 1]
        event_end = int(event.end_time_ms)
        if not start_ms <= event_end < end_ms:
            continue
        values = (
            event.common_return, event.common_vol, event.btc_vol, event.beta,
            event.btcusdt_futures_return, event.btc_atr,
        )
        if not all(math.isfinite(float(value)) for value in values):
            continue
        if float(event.beta) <= 0.0:
            no_trade["NON_POSITIVE_CAUSAL_BETA"] = no_trade.get("NON_POSITIVE_CAUSAL_BETA", 0) + 1
            continue
        common_z = float(event.common_return / event.common_vol)
        if abs(common_z) < SHOCK_Z:
            continue
        direction = 1 if common_z > 0.0 else -1
        leaders = aligned_leaders(event, direction)
        if len(leaders) < 2:
            no_trade["LEADER_SPOT_FUTURES_FLOW_DISAGREEMENT"] = no_trade.get(
                "LEADER_SPOT_FUTURES_FLOW_DISAGREEMENT", 0
            ) + 1
            continue
        expected_event_return = float(event.beta * event.common_return)
        residual = expected_event_return - float(event.btcusdt_futures_return)
        if direction * residual < UNDERREACTION_Z * float(event.btc_vol):
            no_trade["BTC_NOT_CAUSALLY_UNDERREACTING"] = no_trade.get(
                "BTC_NOT_CAUSALLY_UNDERREACTING", 0
            ) + 1
            continue
        if not all(
            math.isfinite(float(value))
            for value in (
                confirm.common_return,
                confirm.btcusdt_futures_return,
                confirm.btcusdt_futures_flow,
                confirm.btcusdt_spot_flow,
            )
        ):
            continue
        if (
            direction * (float(event.common_return) + float(confirm.common_return)) <= 0.0
            or direction * float(confirm.btcusdt_futures_return) <= 0.0
            or direction * float(confirm.btcusdt_futures_flow) <= 0.0
            or direction * float(confirm.btcusdt_spot_flow) <= 0.0
            or len(aligned_leaders(confirm, direction)) < 2
        ):
            no_trade["CROSS_ASSET_CONFIRMATION_FAILED"] = no_trade.get(
                "CROSS_ASSET_CONFIRMATION_FAILED", 0
            ) + 1
            continue
        common_total = float(event.common_return + confirm.common_return)
        pre_event_close = float(bars.iloc[index - 1].btcusdt_futures_close)
        fair_value = pre_event_close * math.exp(float(event.beta) * common_total)
        confirmation_close = float(confirm.btcusdt_futures_close)
        if direction * (fair_value - confirmation_close) <= 0.0:
            no_trade["CAUSAL_FAIR_VALUE_NOT_AHEAD"] = no_trade.get(
                "CAUSAL_FAIR_VALUE_NOT_AHEAD", 0
            ) + 1
            continue
        zone_mid = (
            float(confirm.btcusdt_futures_open) + confirmation_close
        ) / 2.0
        retest = find_retest(
            minutes,
            start_ms=int(confirm.end_time_ms),
            direction=direction,
            zone_mid=zone_mid,
        )
        if retest is None:
            no_trade["BTC_PULLBACK_DEFENSE_UNRESOLVED"] = no_trade.get(
                "BTC_PULLBACK_DEFENSE_UNRESOLVED", 0
            ) + 1
            continue
        signal_ms = int(retest.open_time_ms) + 60_000
        if signal_ms - last_signal_ms < MIN_SIGNAL_SEPARATION_MINUTES * 60_000:
            no_trade["INDEPENDENT_EVENT_COOLDOWN"] = no_trade.get(
                "INDEPENDENT_EVENT_COOLDOWN", 0
            ) + 1
            continue
        entry_reference = float(retest.btcusdt_futures_close)
        if direction * (fair_value - entry_reference) <= 0.0:
            no_trade["FAIR_VALUE_CONSUMED_BEFORE_RETEST"] = no_trade.get(
                "FAIR_VALUE_CONSUMED_BEFORE_RETEST", 0
            ) + 1
            continue
        stop = (
            float(retest.btcusdt_futures_low) - STOP_BUFFER_ATR * float(event.btc_atr)
            if direction > 0
            else float(retest.btcusdt_futures_high) + STOP_BUFFER_ATR * float(event.btc_atr)
        )
        if direction * (entry_reference - stop) <= 0.0:
            continue
        confirm_ns = signal_ms * 1_000_000
        suffix = hashlib.sha256(
            f"{confirm_ns}|{direction}|{fair_value:.12g}|{stop:.12g}".encode()
        ).hexdigest()[:16]
        signals.append(
            {
                "scenario_id": f"NT-LVCFR-V29-CROSS-ASSET-CATCHUP-{suffix}",
                "scenario_kind": "CROSS_ASSET_CONSENSUS_CATCHUP",
                "entry_kind": "CONTINUATION",
                "confirm_time_ns": confirm_ns,
                "eligible_time_ns": confirm_ns,
                "direction": direction,
                "initial_stop": stop,
                "atr": float(event.btc_atr),
                "first_start_time_ns": int(event.open_time_ms) * 1_000_000,
                "first_end_time_ns": int(confirm.end_time_ms) * 1_000_000,
                "structural_target": fair_value,
                "target_mode": "STRUCTURAL_LIQUIDITY_OBJECTIVE",
                "disable_rapid_failure_reversal": True,
                "details": {
                    "scenario_kind": "CROSS_ASSET_CONSENSUS_CATCHUP",
                    "entry_kind": "CONTINUATION",
                    "leader_symbols": list(LEADERS),
                    "event_aligned_leaders": leaders,
                    "common_shock_z": common_z,
                    "causal_beta": float(event.beta),
                    "btc_event_return": float(event.btcusdt_futures_return),
                    "expected_btc_event_return": expected_event_return,
                    "underreaction_residual": residual,
                    "common_total_return": common_total,
                    "fair_value_target": fair_value,
                    "pullback_zone_mid": zone_mid,
                    "retest_open_time_ms": int(retest.open_time_ms),
                    "minimum_signal_separation_minutes": MIN_SIGNAL_SEPARATION_MINUTES,
                },
            }
        )
        last_signal_ms = signal_ms

    (prepared_root / "signals.json").write_text(
        json.dumps(signals, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    manifest = {
        "candidate": "candidate-03-nt-lvcfr-v29-cross-asset-consensus-catchup",
        "engine_status": "causal_cross_asset_schedule_only_no_backtest",
        "week_start": week_start.isoformat(),
        "warmup_start": warmup_start.isoformat(),
        "derived_signal_count": len(signals),
        "signals_per_day": len(signals) / 7.0,
        "state_counts": {"CROSS_ASSET_CONSENSUS_CATCHUP": len(signals)},
        "no_trade_reasons": dict(sorted(no_trade.items())),
        "symbols": list(SYMBOLS),
        "bar_minutes": BAR_MINUTES,
        "volatility_window_bars": VOL_WINDOW,
        "beta_window_bars": BETA_WINDOW,
        "shock_z": SHOCK_Z,
        "underreaction_z": UNDERREACTION_Z,
        "retest_expiry_minutes": RETEST_EXPIRY_MINUTES,
        "source_files": sources,
        "selection_policy": (
            "causal rolling beta and volatility; at least two independent leader "
            "spot/futures flow confirmations; BTC underreaction; second-block "
            "catch-up; completed pullback defense; no evaluation outcomes"
        ),
    }
    output_manifest.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return signals


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--week-start", type=date.fromisoformat, required=True)
    parser.add_argument("--prepared-root", type=Path, required=True)
    parser.add_argument("--output-manifest", type=Path, required=True)
    args = parser.parse_args()
    signals = derive_v29(
        week_start=args.week_start,
        prepared_root=args.prepared_root.resolve(),
        output_manifest=args.output_manifest.resolve(),
    )
    print(json.dumps({"candidate": "V29", "signals": len(signals)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
