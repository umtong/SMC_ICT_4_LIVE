#!/usr/bin/env python3
"""Hypothesis-driven reconstruction of the public ``ichiV2`` claim family.

This is deliberately not a parameter search.  It tests one causal explanation
for a public Freqtrade report whose opportunity density and return profile are
close to the project's objective.

The fixed hypotheses are:

H1. The exact public ``ichiV2`` entry state is much denser than the previously
    tested ``ichiV2_1`` 40-hour fan implementation.
H2. Combining that entry with the independently public ``ichiV2_1`` 5/3/1/0
    percent ROI clock and five-percent stop reproduces the report's ROI-heavy
    winner engine more closely than the public source management.
H3. The EMA(5)-below-EMA(18) exit is a removable loss engine.  Removing only
    that exit must preserve ROI winners and reduce loss; merely delaying the
    same losses to the stop falsifies the repair.
H4. A reusable mechanism must keep opportunity density and expectancy across
    multiple calendar quarters and market regimes, not only the report period.

Only three named policies are run.  All source observations are completed
Binance USD-M five-minute candles.  The public source itself shifts price and
Heikin-Ashi state by one completed candle.  Orders therefore enter at the next
five-minute open.  A contiguous entry condition is one causal episode.

The diagnostic account has one global slot across BTCUSDT, ETHUSDT, SOLUSDT and
XRPUSDT, current-NAV three-percent planned loss sizing, 15-bp round-trip cost,
conservative stop-before-target same-bar ordering and continuous NAV.  This is
not a replacement for NautilusTrader promotion; it identifies whether the
specific external mechanism merits that expensive step.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
from hashlib import sha256
import json
import math
from pathlib import Path
from typing import Any, Iterable, Mapping
import urllib.error
import urllib.request

import numpy as np
import pandas as pd


SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT")
SYMBOL_PRIORITY = {symbol: index for index, symbol in enumerate(SYMBOLS)}
BASE = "https://data.binance.vision/data/futures/um"
KLINE_COLUMNS = (
    "open_time", "open", "high", "low", "close", "volume", "close_time",
    "quote_volume", "count", "taker_buy_volume", "taker_buy_quote_volume", "ignore",
)
COST_ROUND_TRIP_BPS = 15.0
ONE_WAY_COST = COST_ROUND_TRIP_BPS / 20_000.0
RISK_FRACTION = 0.03
MAX_HOLD_MINUTES = 7 * 24 * 60
FIXED_HORIZONS_MINUTES = (30, 60, 120, 240, 480, 720, 1_440)
ROI_BRIDGE = ((114, 0.0), (41, 0.01), (10, 0.03), (0, 0.05))

SOURCE = {
    "claim": {
        "author": "vjaykrsna",
        "gist": "3aa41ada83ea890721e27ccda02c1d64",
        "reported_strategy": "ichiV2",
        "reported_period": "2025-01-01..2025-04-03",
        "reported_total_trades": 1056,
        "reported_daily_trades": 11.48,
        "reported_win_rate": 0.766,
        "reported_profit_factor": 6.51,
        "reported_roi_exits": 822,
        "reported_roi_exit_win_rate": 0.943,
        "reported_exit_signal_exits": 234,
        "reported_exit_signal_win_rate": 0.145,
        "reported_avg_daily_profit_pct": 38.40,
        "identity_asserted": False,
    },
    "entry_control": {
        "repository": "nicnl31/pyalgotrader",
        "commit": "c22b45dc6744b8dc3ea5c9e73858c81274686684",
        "path": "frameworks/freqtrade/user_data/strategies/ichiV2.py",
        "cloud_level": 1,
        "bullish_level": 4,
        "fan_gain": 1.0013,
        "fan_shift_count": 3,
    },
    "bridge_management": {
        "repository": "remiotore/ccxt-freqtrade",
        "commit": "44beaeb6a420cd8e9f2e4ea93e11d6cfa192ee03",
        "path": "strategies/ichiV2_1.py",
        "roi_schedule": ROI_BRIDGE,
        "stop_fraction": 0.05,
    },
}


@dataclass(frozen=True, slots=True)
class RawEvidence:
    symbol: str
    interval: str
    period: str
    archive: str
    checksum: str
    size_bytes: int
    sha256: str


@dataclass(frozen=True, slots=True)
class Policy:
    name: str
    stop_fraction: float
    roi_schedule: tuple[tuple[int, float], ...]
    use_ema_exit: bool
    trailing_offset: float | None = None
    trailing_distance: float | None = None


POLICIES = (
    Policy(
        name="public_ichiv2_source",
        stop_fraction=0.10,
        roi_schedule=((0, 0.30),),
        use_ema_exit=True,
        trailing_offset=0.08,
        trailing_distance=0.06,
    ),
    Policy(
        name="claim_bridge",
        stop_fraction=0.05,
        roi_schedule=ROI_BRIDGE,
        use_ema_exit=True,
    ),
    Policy(
        name="claim_bridge_roi_only",
        stop_fraction=0.05,
        roi_schedule=ROI_BRIDGE,
        use_ema_exit=False,
    ),
)


# ---------------------------------------------------------------------------
# Generic helpers
# ---------------------------------------------------------------------------

def _json_default(value: Any) -> Any:
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, np.floating):
        number = float(value)
        return number if math.isfinite(number) else None
    if isinstance(value, np.bool_):
        return bool(value)
    if isinstance(value, (pd.Timestamp, datetime, date)):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, set):
        return sorted(value)
    raise TypeError(f"not JSON serializable: {type(value)!r}")


def _safe_float(value: Any, default: float = math.nan) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def _sha256_file(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        while block := stream.read(1 << 20):
            digest.update(block)
    return digest.hexdigest()


def _profit_factor(values: pd.Series | Iterable[float]) -> float:
    array = np.asarray(list(values), dtype=float)
    array = array[np.isfinite(array)]
    gains = float(array[array > 0.0].sum())
    losses = float(-array[array < 0.0].sum())
    return gains / losses if losses > 0.0 else (math.inf if gains > 0.0 else 0.0)


def _quarter_label(timestamp: pd.Timestamp) -> str:
    return f"{timestamp.year}-Q{((timestamp.month - 1) // 3) + 1}"


def _month_starts(start: date, end: date) -> Iterable[date]:
    cursor = date(start.year, start.month, 1)
    terminal = date(end.year, end.month, 1)
    while cursor <= terminal:
        yield cursor
        if cursor.month == 12:
            cursor = date(cursor.year + 1, 1, 1)
        else:
            cursor = date(cursor.year, cursor.month + 1, 1)


# ---------------------------------------------------------------------------
# Checksum-verified Binance monthly 5m data
# ---------------------------------------------------------------------------

def _download_checked(
    url: str,
    cache: Path,
    *,
    symbol: str,
    period: str,
) -> tuple[Path, RawEvidence]:
    cache.mkdir(parents=True, exist_ok=True)
    filename = url.rsplit("/", 1)[-1]
    archive = cache / filename
    checksum = cache / f"{filename}.CHECKSUM"
    if not archive.exists():
        urllib.request.urlretrieve(url, archive)
    if not checksum.exists():
        urllib.request.urlretrieve(url + ".CHECKSUM", checksum)
    expected = checksum.read_text(encoding="utf-8").strip().split()[0].lower()
    actual = _sha256_file(archive)
    if actual != expected:
        raise RuntimeError(f"checksum mismatch for {archive}: {actual} != {expected}")
    return archive, RawEvidence(
        symbol=symbol,
        interval="5m",
        period=period,
        archive=str(archive),
        checksum=str(checksum),
        size_bytes=archive.stat().st_size,
        sha256=actual,
    )


def _read_kline_archive(path: Path) -> pd.DataFrame:
    raw = pd.read_csv(path, compression="zip", header=None)
    if raw.shape[1] != len(KLINE_COLUMNS):
        with_header = pd.read_csv(path, compression="zip")
        if not set(KLINE_COLUMNS).issubset(with_header.columns):
            raise RuntimeError(f"unexpected Binance kline schema in {path}")
        raw = with_header[list(KLINE_COLUMNS)].copy()
    else:
        raw.columns = list(KLINE_COLUMNS)
        first = str(raw.iloc[0]["open_time"]).strip()
        if not first.lstrip("-").isdigit():
            raw = raw.iloc[1:].copy()
    for column in KLINE_COLUMNS:
        raw[column] = pd.to_numeric(raw[column], errors="raise")
    first_value = int(raw["open_time"].iloc[0])
    unit = "us" if abs(first_value) > 10**14 else "ms"
    raw["time"] = pd.to_datetime(raw["open_time"].astype("int64"), unit=unit, utc=True)
    output = raw[
        ["time", "open", "high", "low", "close", "volume", "quote_volume", "count"]
    ].copy()
    return output.sort_values("time").drop_duplicates("time", keep="last")


def load_symbol(
    *,
    symbol: str,
    data_start: date,
    data_end: date,
    cache: Path,
) -> tuple[pd.DataFrame, list[RawEvidence]]:
    frames: list[pd.DataFrame] = []
    evidence: list[RawEvidence] = []
    for month in _month_starts(data_start, data_end):
        stamp = month.strftime("%Y-%m")
        relative = f"monthly/klines/{symbol}/5m/{symbol}-5m-{stamp}.zip"
        archive, item = _download_checked(
            f"{BASE}/{relative}",
            cache / symbol / "monthly",
            symbol=symbol,
            period=stamp,
        )
        frames.append(_read_kline_archive(archive))
        evidence.append(item)
    frame = pd.concat(frames, ignore_index=True).sort_values("time")
    frame = frame.drop_duplicates("time", keep="last").set_index("time")
    start_ts = pd.Timestamp(data_start, tz="UTC")
    terminal_ts = pd.Timestamp(data_end + timedelta(days=1), tz="UTC")
    frame = frame[(frame.index >= start_ts) & (frame.index < terminal_ts)].copy()
    expected = pd.date_range(start_ts, terminal_ts, freq="5min", inclusive="left")
    missing = expected.difference(frame.index)
    if len(missing):
        raise RuntimeError(
            f"{symbol}: missing {len(missing)} five-minute bars, "
            f"first={missing[0]}, last={missing[-1]}"
        )
    numeric = ("open", "high", "low", "close", "volume", "quote_volume", "count")
    frame[list(numeric)] = frame[list(numeric)].astype(float)
    return frame, evidence


# ---------------------------------------------------------------------------
# Exact public source state
# ---------------------------------------------------------------------------

def _ema_talib(series: pd.Series, period: int) -> pd.Series:
    """TA-Lib-style seeded EMA beginning at the first finite contiguous window."""
    values = series.to_numpy(dtype=float)
    output = np.full(len(values), np.nan, dtype=float)
    if period <= 0 or len(values) < period:
        return pd.Series(output, index=series.index)
    start: int | None = None
    for index in range(period - 1, len(values)):
        window = values[index - period + 1:index + 1]
        if np.isfinite(window).all():
            start = index
            break
    if start is None:
        return pd.Series(output, index=series.index)
    current = float(np.mean(values[start - period + 1:start + 1]))
    output[start] = current
    alpha = 2.0 / (period + 1.0)
    for index in range(start + 1, len(values)):
        value = float(values[index])
        if not math.isfinite(value):
            continue
        current = alpha * value + (1.0 - alpha) * current
        output[index] = current
    return pd.Series(output, index=series.index)


def _heikin_ashi(frame: pd.DataFrame) -> pd.DataFrame:
    """Exact qtpylib recursive Heikin-Ashi construction."""
    ha_close = frame[["open", "high", "low", "close"]].mean(axis=1)
    ha_open = np.full(len(frame), np.nan, dtype=float)
    if len(frame):
        ha_open[0] = (float(frame["open"].iloc[0]) + float(frame["close"].iloc[0])) / 2.0
        for index in range(1, len(frame)):
            ha_open[index] = (ha_open[index - 1] + float(ha_close.iloc[index - 1])) / 2.0
    output = pd.DataFrame(index=frame.index)
    output["open"] = ha_open
    output["close"] = ha_close.to_numpy(dtype=float)
    output["high"] = np.maximum.reduce(
        [frame["high"].to_numpy(dtype=float), ha_open, ha_close.to_numpy(dtype=float)]
    )
    output["low"] = np.minimum.reduce(
        [frame["low"].to_numpy(dtype=float), ha_open, ha_close.to_numpy(dtype=float)]
    )
    return output


def _ichimoku_shifted(frame: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
    """technical.indicators.ichimoku on ``dataframe.shift(1)`` as in source."""
    shifted = frame.shift(1)
    tenkan = (
        shifted["high"].rolling(20, min_periods=20).max()
        + shifted["low"].rolling(20, min_periods=20).min()
    ) / 2.0
    kijun = (
        shifted["high"].rolling(60, min_periods=60).max()
        + shifted["low"].rolling(60, min_periods=60).min()
    ) / 2.0
    leading_a = (tenkan + kijun) / 2.0
    leading_b = (
        shifted["high"].rolling(120, min_periods=120).max()
        + shifted["low"].rolling(120, min_periods=120).min()
    ) / 2.0
    # technical shifts by displacement - 1.
    return leading_a.shift(29), leading_b.shift(29)


def build_source_state(frame: pd.DataFrame, symbol: str) -> pd.DataFrame:
    source = frame[["open", "high", "low", "close", "volume"]].copy()
    ha = _heikin_ashi(source)
    # Public ichiV2 overwrites open/high/low, but retains ordinary close.
    source["open"] = ha["open"]
    source["high"] = ha["high"]
    source["low"] = ha["low"]

    prior_close = source["close"].shift(1)
    prior_open = source["open"].shift(1)
    close_periods: Mapping[int, int | None] = {
        5: None, 15: 3, 30: 6, 60: 12, 90: 18,
        120: 24, 240: 48, 360: 72, 480: 96,
    }
    open_periods: Mapping[int, int | None] = {
        5: None, 15: 3, 30: 6, 60: 12,
        120: 24, 240: 48, 360: 72, 480: 96,
    }
    state = pd.DataFrame(index=frame.index)
    for label, period in close_periods.items():
        state[f"close_{label}"] = prior_close if period is None else _ema_talib(prior_close, period)
    for label, period in open_periods.items():
        state[f"open_{label}"] = prior_open if period is None else _ema_talib(prior_open, period)

    state["fan_magnitude"] = state["close_60"] / state["close_480"]
    state["fan_gain"] = state["fan_magnitude"] / state["fan_magnitude"].shift(1)
    senkou_a, senkou_b = _ichimoku_shifted(source)
    state["senkou_a"] = senkou_a
    state["senkou_b"] = senkou_b
    state["cloud_top"] = state[["senkou_a", "senkou_b"]].max(axis=1)

    cloud_ok = (
        (state["close_5"] > state["senkou_a"])
        & (state["close_5"] > state["senkou_b"])
    )
    bullish_ok = (
        (state["close_5"] > state["open_5"])
        & (state["close_15"] > state["open_15"])
        & (state["close_30"] > state["open_30"])
        & (state["close_60"] > state["open_60"])
    )
    accelerating = (
        (state["fan_magnitude"] > state["fan_magnitude"].shift(1))
        & (state["fan_magnitude"] > state["fan_magnitude"].shift(2))
        & (state["fan_magnitude"] > state["fan_magnitude"].shift(3))
    )
    state["entry_condition"] = (
        cloud_ok
        & bullish_ok
        & (state["fan_gain"] >= 1.0013)
        & (state["fan_magnitude"] > 1.0)
        & accelerating
    ).fillna(False)
    state["entry_edge"] = (
        state["entry_condition"]
        & ~state["entry_condition"].shift(1, fill_value=False)
    )
    state["ema_exit"] = (
        (state["close_5"] < state["close_90"])
        & (state["close_5"].shift(1) >= state["close_90"].shift(1))
    ).fillna(False)

    alignment_margin = sum(
        (state[f"close_{period}"] / state[f"open_{period}"] - 1.0).clip(lower=0.0)
        for period in (5, 15, 30, 60)
    )
    state["score"] = (
        10_000.0 * (state["fan_gain"] - 1.0013).clip(lower=0.0)
        + 1_000.0 * (state["fan_magnitude"] - 1.0).clip(lower=0.0)
        + 1_000.0 * alignment_margin
        + 100.0 * (state["close_5"] / state["cloud_top"] - 1.0).clip(lower=0.0)
    ).fillna(0.0)
    state["symbol"] = symbol
    return state


def signal_table(
    states: Mapping[str, pd.DataFrame],
    frames: Mapping[str, pd.DataFrame],
    *,
    evaluation_start: date,
    evaluation_end: date,
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    start_ts = pd.Timestamp(evaluation_start, tz="UTC")
    end_ts = pd.Timestamp(evaluation_end + timedelta(days=1), tz="UTC")
    for symbol, state in states.items():
        for signal_bar_time, row in state[state["entry_edge"]].iterrows():
            entry_time = pd.Timestamp(signal_bar_time) + pd.Timedelta(minutes=5)
            if not (start_ts <= entry_time < end_ts) or entry_time not in frames[symbol].index:
                continue
            rows.append(
                {
                    "source_signal_id": f"{symbol}:{signal_bar_time.isoformat()}",
                    "symbol": symbol,
                    "signal_bar_time": signal_bar_time,
                    "entry_time": entry_time,
                    "entry_price": float(frames[symbol].at[entry_time, "open"]),
                    "score": float(row["score"]),
                    "fan_magnitude": float(row["fan_magnitude"]),
                    "fan_gain": float(row["fan_gain"]),
                    "cloud_clearance_bps": float(
                        10_000.0 * (row["close_5"] / row["cloud_top"] - 1.0)
                    ),
                    "close_5": float(row["close_5"]),
                    "close_90": float(row["close_90"]),
                    "signal_quarter": _quarter_label(pd.Timestamp(signal_bar_time)),
                }
            )
    columns = [
        "source_signal_id", "symbol", "signal_bar_time", "entry_time", "entry_price",
        "score", "fan_magnitude", "fan_gain", "cloud_clearance_bps", "close_5",
        "close_90", "signal_quarter",
    ]
    if not rows:
        return pd.DataFrame(columns=columns)
    output = pd.DataFrame(rows)
    output["symbol_priority"] = output["symbol"].map(SYMBOL_PRIORITY)
    output = output.sort_values(
        ["entry_time", "score", "symbol_priority"],
        ascending=[True, False, True],
        kind="stable",
    ).drop(columns=["symbol_priority"])
    return output.reset_index(drop=True)


# ---------------------------------------------------------------------------
# One-slot causal account simulation
# ---------------------------------------------------------------------------

def _roi_threshold(policy: Policy, held_minutes: int) -> float:
    for minute, threshold in policy.roi_schedule:
        if held_minutes >= minute:
            return float(threshold)
    return float(policy.roi_schedule[-1][1])


def _costed_pnl(quantity: float, entry: float, exit_price: float) -> float:
    return quantity * (exit_price - entry) - quantity * (
        entry * ONE_WAY_COST + exit_price * ONE_WAY_COST
    )


def _risk_quantity(nav: float, entry: float, stop_fraction: float) -> tuple[float, float]:
    # Planned loss includes entry and stop-side costs.
    stop_price = entry * (1.0 - stop_fraction)
    loss_per_unit = (entry - stop_price) + entry * ONE_WAY_COST + stop_price * ONE_WAY_COST
    budget = nav * RISK_FRACTION
    return budget / loss_per_unit, loss_per_unit


def _source_exit_times(state: pd.DataFrame) -> set[pd.Timestamp]:
    return {
        pd.Timestamp(index) + pd.Timedelta(minutes=5)
        for index in state.index[state["ema_exit"]]
    }


def _close_trade(
    position: dict[str, Any],
    *,
    exit_time: pd.Timestamp,
    exit_price: float,
    exit_reason: str,
    nav_before: float,
) -> tuple[dict[str, Any], float]:
    pnl = _costed_pnl(
        float(position["quantity"]),
        float(position["entry_price"]),
        float(exit_price),
    )
    nav_after = nav_before + pnl
    planned_budget = float(position["risk_budget"])
    record = {
        **position,
        "exit_time": exit_time,
        "exit_price": float(exit_price),
        "exit_reason": exit_reason,
        "held_minutes": int((exit_time - position["entry_time"]).total_seconds() // 60),
        "pnl": pnl,
        "return_on_nav": pnl / nav_before if nav_before > 0.0 else math.nan,
        "r_multiple": pnl / planned_budget if planned_budget > 0.0 else math.nan,
        "nav_before_exit": nav_before,
        "nav_after_exit": nav_after,
        "winner": pnl > 0.0,
        "calendar_quarter": _quarter_label(exit_time),
    }
    return record, nav_after


def _mark_equity(nav: float, position: dict[str, Any] | None, mark_price: float | None) -> float:
    if position is None or mark_price is None:
        return nav
    return nav + _costed_pnl(
        float(position["quantity"]),
        float(position["entry_price"]),
        float(mark_price),
    )


def _process_position_bar(
    *,
    position: dict[str, Any],
    policy: Policy,
    timestamp: pd.Timestamp,
    candle: pd.Series,
    ema_exit_available: bool,
) -> tuple[float | None, str | None, bool]:
    """Return exit price, reason and whether the exit occurred intrabar.

    Open-price decisions are resolved before intrabar paths.  Stop is checked
    before target whenever both could occur in one candle.  Trailing uses the
    peak known before the current candle, avoiding a favourable high-before-low
    assumption.
    """
    entry = float(position["entry_price"])
    held = int((timestamp - position["entry_time"]).total_seconds() // 60)
    opened = float(candle["open"])
    high = float(candle["high"])
    low = float(candle["low"])
    prior_peak = float(position["peak_price"])
    hard_stop = entry * (1.0 - policy.stop_fraction)

    # Exchange stop gap dominates discretionary open-price decisions.
    if opened <= hard_stop:
        return opened, "HARD_STOP_GAP", False

    trailing_stop: float | None = None
    if (
        policy.trailing_offset is not None
        and policy.trailing_distance is not None
        and prior_peak >= entry * (1.0 + policy.trailing_offset)
    ):
        trailing_stop = prior_peak * (1.0 - policy.trailing_distance)
        position["trailing_activated"] = True
        if opened <= trailing_stop:
            return opened, "TRAILING_STOP_GAP", False

    if policy.use_ema_exit and ema_exit_available:
        return opened, "EMA18_EXIT_SIGNAL", False

    threshold = _roi_threshold(policy, held)
    roi_price = entry * (1.0 + threshold)
    if threshold <= 0.0 and opened >= entry:
        return opened, "ROI_0_CLOCK", False
    if threshold > 0.0 and opened >= roi_price:
        return opened, f"ROI_{threshold:.4f}_GAP", False
    if held >= MAX_HOLD_MINUTES:
        return opened, "MAX_SOURCE_HOLD", False

    # Intrabar path: stop first, then trailing, then ROI.
    if low <= hard_stop:
        return hard_stop, "HARD_STOP", True
    if trailing_stop is not None and low <= trailing_stop:
        return trailing_stop, "TRAILING_STOP", True
    if threshold <= 0.0:
        if high >= entry:
            return entry, "ROI_0_TOUCH", True
    elif high >= roi_price:
        return roi_price, f"ROI_{threshold:.4f}", True

    # Surviving candle updates path state only after decisions.
    position["mfe_fraction"] = max(
        float(position["mfe_fraction"]),
        high / entry - 1.0,
    )
    position["mae_fraction"] = min(
        float(position["mae_fraction"]),
        low / entry - 1.0,
    )
    position["peak_price"] = max(prior_peak, high)
    return None, None, False


def simulate_policy(
    *,
    policy: Policy,
    frames: Mapping[str, pd.DataFrame],
    states: Mapping[str, pd.DataFrame],
    signals: pd.DataFrame,
    evaluation_start: date,
    evaluation_end: date,
    terminal_end: date,
) -> dict[str, Any]:
    start_ts = pd.Timestamp(evaluation_start, tz="UTC")
    evaluation_terminal = pd.Timestamp(evaluation_end + timedelta(days=1), tz="UTC")
    terminal_ts = pd.Timestamp(terminal_end + timedelta(days=1), tz="UTC")
    timeline = frames[SYMBOLS[0]].index
    timeline = timeline[(timeline >= start_ts) & (timeline < terminal_ts)]
    signal_groups = {
        pd.Timestamp(timestamp): group.sort_values(
            ["score", "symbol"], ascending=[False, True], kind="stable"
        )
        for timestamp, group in signals.groupby("entry_time", sort=True)
    }
    exit_times = {symbol: _source_exit_times(states[symbol]) for symbol in SYMBOLS}

    nav = 100_000.0
    peak_equity = nav
    max_drawdown = 0.0
    position: dict[str, Any] | None = None
    trades: list[dict[str, Any]] = []
    rejected_signals: list[dict[str, Any]] = []
    daily_equity: dict[str, float] = {}
    entry_count = 0

    for timestamp in timeline:
        exited_intrabar = False
        # Manage a position carried into this candle.
        if position is not None:
            symbol = str(position["symbol"])
            candle = frames[symbol].loc[timestamp]
            exit_price, exit_reason, exited_intrabar = _process_position_bar(
                position=position,
                policy=policy,
                timestamp=pd.Timestamp(timestamp),
                candle=candle,
                ema_exit_available=(
                    timestamp in exit_times[symbol]
                    and pd.Timestamp(position["entry_time"]) < pd.Timestamp(timestamp)
                ),
            )
            if exit_reason is not None and exit_price is not None:
                record, nav = _close_trade(
                    position,
                    exit_time=pd.Timestamp(timestamp),
                    exit_price=exit_price,
                    exit_reason=exit_reason,
                    nav_before=nav,
                )
                trades.append(record)
                position = None

        candidates = signal_groups.get(pd.Timestamp(timestamp))
        if candidates is not None and len(candidates):
            if position is not None:
                for item in candidates.to_dict(orient="records"):
                    rejected_signals.append(
                        {
                            **item,
                            "reject_reason": "GLOBAL_SLOT_OCCUPIED",
                            "occupying_signal_id": position["source_signal_id"],
                            "policy": policy.name,
                        }
                    )
            elif exited_intrabar:
                for item in candidates.to_dict(orient="records"):
                    rejected_signals.append(
                        {
                            **item,
                            "reject_reason": "SLOT_RELEASED_AFTER_ENTRY_OPEN",
                            "occupying_signal_id": None,
                            "policy": policy.name,
                        }
                    )
            elif timestamp < evaluation_terminal:
                selected = candidates.iloc[0].to_dict()
                for _, losing in candidates.iloc[1:].iterrows():
                    rejected_signals.append(
                        {
                            **losing.to_dict(),
                            "reject_reason": "SAME_TIMESTAMP_ARBITRATION",
                            "occupying_signal_id": selected["source_signal_id"],
                            "policy": policy.name,
                        }
                    )
                entry = float(selected["entry_price"])
                quantity, loss_per_unit = _risk_quantity(nav, entry, policy.stop_fraction)
                entry_count += 1
                position = {
                    **selected,
                    "policy": policy.name,
                    "entry_index": entry_count,
                    "quantity": quantity,
                    "risk_budget": nav * RISK_FRACTION,
                    "planned_loss_per_unit": loss_per_unit,
                    "planned_account_loss": quantity * loss_per_unit,
                    "nav_at_entry": nav,
                    "mfe_fraction": 0.0,
                    "mae_fraction": 0.0,
                    "peak_price": entry,
                    "trailing_activated": False,
                }
                # Entry occurs at this candle open; stop/ROI can execute intrabar.
                candle = frames[str(position["symbol"])].loc[timestamp]
                exit_price, exit_reason, _ = _process_position_bar(
                    position=position,
                    policy=policy,
                    timestamp=pd.Timestamp(timestamp),
                    candle=candle,
                    ema_exit_available=False,
                )
                if exit_reason is not None and exit_price is not None:
                    record, nav = _close_trade(
                        position,
                        exit_time=pd.Timestamp(timestamp),
                        exit_price=exit_price,
                        exit_reason=exit_reason,
                        nav_before=nav,
                    )
                    trades.append(record)
                    position = None

        mark: float | None = None
        if position is not None:
            mark = float(frames[str(position["symbol"])].at[timestamp, "close"])
        equity = _mark_equity(nav, position, mark)
        peak_equity = max(peak_equity, equity)
        max_drawdown = max(max_drawdown, 1.0 - equity / max(peak_equity, 1e-12))
        daily_equity[pd.Timestamp(timestamp).date().isoformat()] = equity

    if position is not None:
        symbol = str(position["symbol"])
        last_time = pd.Timestamp(timeline[-1])
        exit_price = float(frames[symbol].at[last_time, "close"])
        record, nav = _close_trade(
            position,
            exit_time=last_time,
            exit_price=exit_price,
            exit_reason="TERMINAL_FORCE_EXIT",
            nav_before=nav,
        )
        trades.append(record)
        position = None
        peak_equity = max(peak_equity, nav)
        max_drawdown = max(max_drawdown, 1.0 - nav / max(peak_equity, 1e-12))
        daily_equity[last_time.date().isoformat()] = nav

    trade_frame = pd.DataFrame(trades)
    if trade_frame.empty:
        gross_profit = gross_loss = win_rate = mean_r = 0.0
        profit_factor = 0.0
    else:
        gross_profit = float(trade_frame.loc[trade_frame["pnl"] > 0.0, "pnl"].sum())
        gross_loss = float(-trade_frame.loc[trade_frame["pnl"] < 0.0, "pnl"].sum())
        win_rate = float((trade_frame["pnl"] > 0.0).mean())
        profit_factor = gross_profit / gross_loss if gross_loss > 0.0 else math.inf
        mean_r = float(trade_frame["r_multiple"].mean())

    calendar_days = (evaluation_end - evaluation_start).days + 1
    geometric_daily = (
        (nav / 100_000.0) ** (1.0 / calendar_days) - 1.0 if nav > 0.0 else -1.0
    )
    return {
        "policy": asdict(policy),
        "starting_nav": 100_000.0,
        "ending_nav": nav,
        "calendar_days": calendar_days,
        "trades": len(trades),
        "trades_per_day": len(trades) / calendar_days,
        "wins": int(sum(bool(item["winner"]) for item in trades)),
        "losses": int(sum(not bool(item["winner"]) for item in trades)),
        "win_rate": win_rate,
        "gross_profit": gross_profit,
        "gross_loss": gross_loss,
        "profit_factor": profit_factor,
        "mean_r": mean_r,
        "geometric_daily_growth": geometric_daily,
        "max_drawdown": max_drawdown,
        "rejected_signals": rejected_signals,
        "daily_equity": daily_equity,
        "trade_records": trades,
    }


# ---------------------------------------------------------------------------
# Episode and result anatomy
# ---------------------------------------------------------------------------

def _forward_signal_anatomy(
    *,
    signals: pd.DataFrame,
    frames: Mapping[str, pd.DataFrame],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in signals.to_dict(orient="records"):
        symbol = str(item["symbol"])
        entry_time = pd.Timestamp(item["entry_time"])
        entry = float(item["entry_price"])
        frame = frames[symbol]
        if entry_time not in frame.index:
            continue
        location = int(frame.index.get_loc(entry_time))
        row: dict[str, Any] = dict(item)
        for horizon in FIXED_HORIZONS_MINUTES:
            steps = horizon // 5
            terminal = location + steps
            if terminal >= len(frame):
                row[f"ret_{horizon}m_bps"] = math.nan
                row[f"mfe_{horizon}m_bps"] = math.nan
                row[f"mae_{horizon}m_bps"] = math.nan
                continue
            sample = frame.iloc[location:terminal + 1]
            exit_price = float(frame.iloc[terminal]["open"])
            row[f"ret_{horizon}m_bps"] = (
                10_000.0 * (exit_price / entry - 1.0) - COST_ROUND_TRIP_BPS
            )
            row[f"mfe_{horizon}m_bps"] = 10_000.0 * (
                float(sample["high"].max()) / entry - 1.0
            )
            row[f"mae_{horizon}m_bps"] = 10_000.0 * (
                float(sample["low"].min()) / entry - 1.0
            )
        rows.append(row)
    return rows


def _summary(values: Iterable[float]) -> dict[str, Any]:
    array = np.asarray(
        [float(value) for value in values if math.isfinite(float(value))], dtype=float
    )
    if not len(array):
        return {"n": 0}
    return {
        "n": int(len(array)),
        "mean": float(array.mean()),
        "median": float(np.median(array)),
        "win_rate": float(np.mean(array > 0.0)),
        "profit_factor": _profit_factor(array),
        "q10": float(np.quantile(array, 0.10)),
        "q25": float(np.quantile(array, 0.25)),
        "q75": float(np.quantile(array, 0.75)),
        "q90": float(np.quantile(array, 0.90)),
        "sum": float(array.sum()),
    }


def _exit_reason_rows(trades: pd.DataFrame, policy_name: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if trades.empty:
        return rows
    for reason, group in trades.groupby("exit_reason", sort=True):
        rows.append(
            {
                "policy": policy_name,
                "exit_reason": str(reason),
                "trades": int(len(group)),
                "wins": int((group["pnl"] > 0.0).sum()),
                "losses": int((group["pnl"] <= 0.0).sum()),
                "win_rate": float((group["pnl"] > 0.0).mean()),
                "pnl": float(group["pnl"].sum()),
                "mean_r": float(group["r_multiple"].mean()),
                "median_r": float(group["r_multiple"].median()),
                "mean_hold_minutes": float(group["held_minutes"].mean()),
                "mean_mfe_fraction": float(group["mfe_fraction"].mean()),
                "mean_mae_fraction": float(group["mae_fraction"].mean()),
            }
        )
    return rows


def _quarter_rows(trades: pd.DataFrame, policy_name: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if trades.empty:
        return rows
    for quarter, group in trades.groupby("calendar_quarter", sort=True):
        rows.append(
            {
                "policy": policy_name,
                "quarter": str(quarter),
                "trades": int(len(group)),
                "wins": int((group["pnl"] > 0.0).sum()),
                "win_rate": float((group["pnl"] > 0.0).mean()),
                "pnl": float(group["pnl"].sum()),
                "mean_r": float(group["r_multiple"].mean()),
                "profit_factor": _profit_factor(group["pnl"]),
            }
        )
    return rows


def _paired_exit_assessment(
    bridge: pd.DataFrame,
    roi_only: pd.DataFrame,
) -> dict[str, Any]:
    if bridge.empty or roi_only.empty:
        return {"common_signal_ids": 0}
    left = bridge.set_index("source_signal_id")
    right = roi_only.set_index("source_signal_id")
    common = left.index.intersection(right.index)
    if not len(common):
        return {"common_signal_ids": 0}
    delta = right.loc[common, "r_multiple"] - left.loc[common, "r_multiple"]
    ema_mask = left.loc[common, "exit_reason"].eq("EMA18_EXIT_SIGNAL")
    ema_ids = common[ema_mask.to_numpy()]
    return {
        "common_signal_ids": int(len(common)),
        "mean_r_change_no_exit_minus_bridge": float(delta.mean()),
        "median_r_change_no_exit_minus_bridge": float(delta.median()),
        "improved_common_share": float((delta > 0.0).mean()),
        "worsened_common_share": float((delta < 0.0).mean()),
        "ema_exit_common": int(ema_mask.sum()),
        "ema_exit_mean_r_bridge": (
            float(left.loc[ema_ids, "r_multiple"].mean()) if len(ema_ids) else None
        ),
        "same_signal_mean_r_no_exit": (
            float(right.loc[ema_ids, "r_multiple"].mean()) if len(ema_ids) else None
        ),
        "same_signal_stop_share_no_exit": (
            float(right.loc[ema_ids, "exit_reason"].str.startswith("HARD_STOP").mean())
            if len(ema_ids) else None
        ),
    }


def _compact_result(
    *,
    evaluation_start: date,
    evaluation_end: date,
    warmup_start: date,
    terminal_end: date,
    states: Mapping[str, pd.DataFrame],
    signals: pd.DataFrame,
    forward: list[dict[str, Any]],
    policies: Mapping[str, dict[str, Any]],
    evidence: list[RawEvidence],
) -> dict[str, Any]:
    condition_counts = {
        symbol: int(
            states[symbol].loc[
                (states[symbol].index >= pd.Timestamp(evaluation_start, tz="UTC"))
                & (states[symbol].index < pd.Timestamp(evaluation_end + timedelta(days=1), tz="UTC")),
                "entry_condition",
            ].sum()
        )
        for symbol in SYMBOLS
    }
    edge_counts = (
        signals.groupby("symbol").size().reindex(SYMBOLS, fill_value=0).astype(int).to_dict()
        if len(signals) else {symbol: 0 for symbol in SYMBOLS}
    )
    forward_frame = pd.DataFrame(forward)
    fixed_horizon: dict[str, Any] = {}
    for horizon in FIXED_HORIZONS_MINUTES:
        column = f"ret_{horizon}m_bps"
        fixed_horizon[str(horizon)] = (
            _summary(forward_frame[column].tolist()) if column in forward_frame else {"n": 0}
        )

    policy_summary: dict[str, Any] = {}
    all_exit_rows: list[dict[str, Any]] = []
    all_quarter_rows: list[dict[str, Any]] = []
    trade_frames: dict[str, pd.DataFrame] = {}
    for name, payload in policies.items():
        trades = pd.DataFrame(payload["trade_records"])
        trade_frames[name] = trades
        exit_rows = _exit_reason_rows(trades, name)
        quarter_rows = _quarter_rows(trades, name)
        all_exit_rows.extend(exit_rows)
        all_quarter_rows.extend(quarter_rows)
        policy_summary[name] = {
            key: payload[key]
            for key in (
                "starting_nav", "ending_nav", "calendar_days", "trades", "trades_per_day",
                "wins", "losses", "win_rate", "gross_profit", "gross_loss",
                "profit_factor", "mean_r", "geometric_daily_growth", "max_drawdown",
            )
        }
        policy_summary[name]["exit_reasons"] = exit_rows
        policy_summary[name]["quarters"] = quarter_rows

    bridge_summary = policy_summary["claim_bridge"]
    bridge_exit = {row["exit_reason"]: row for row in bridge_summary["exit_reasons"]}
    roi_trades = sum(
        int(row["trades"])
        for reason, row in bridge_exit.items()
        if reason.startswith("ROI_")
    )
    ema_row = bridge_exit.get("EMA18_EXIT_SIGNAL", {})
    paired = _paired_exit_assessment(
        trade_frames["claim_bridge"], trade_frames["claim_bridge_roi_only"]
    )
    bridge_quarters = bridge_summary["quarters"]
    no_exit_quarters = policy_summary["claim_bridge_roi_only"]["quarters"]
    calendar_days = (evaluation_end - evaluation_start).days + 1

    assessments = {
        "H1_signal_density": {
            "condition_bars": condition_counts,
            "independent_edges": int(len(signals)),
            "edges_by_symbol": edge_counts,
            "calendar_days": calendar_days,
            "edges_per_day": len(signals) / calendar_days,
            "prediction": "materially denser than prior all8/40h implementation",
        },
        "H2_claim_anatomy": {
            "bridge_trades": bridge_summary["trades"],
            "roi_trades": roi_trades,
            "roi_share": roi_trades / bridge_summary["trades"] if bridge_summary["trades"] else 0.0,
            "ema_exit_trades": int(ema_row.get("trades", 0)),
            "ema_exit_win_rate": float(ema_row.get("win_rate", 0.0)),
            "ema_exit_pnl": float(ema_row.get("pnl", 0.0)),
            "prediction": "ROI-dominated winner engine with loss-heavy EMA exits",
        },
        "H3_exit_repair": {
            "bridge_mean_r": bridge_summary["mean_r"],
            "roi_only_mean_r": policy_summary["claim_bridge_roi_only"]["mean_r"],
            "bridge_gross_loss": bridge_summary["gross_loss"],
            "roi_only_gross_loss": policy_summary["claim_bridge_roi_only"]["gross_loss"],
            "paired": paired,
            "falsification": "EMA losses migrate to hard stops/long holds without winner preservation",
        },
        "H4_regime_robustness": {
            "bridge_positive_quarter_share": (
                sum(float(row["pnl"]) > 0.0 for row in bridge_quarters)
                / max(1, len(bridge_quarters))
            ),
            "roi_only_positive_quarter_share": (
                sum(float(row["pnl"]) > 0.0 for row in no_exit_quarters)
                / max(1, len(no_exit_quarters))
            ),
            "bridge_quarters": bridge_quarters,
            "roi_only_quarters": no_exit_quarters,
            "prediction": "opportunity and expectancy survive multiple quarters",
        },
    }

    return {
        "schema_version": 2,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "purpose": "hypothesis-driven causal reconstruction; not final NautilusTrader evidence",
        "source": SOURCE,
        "evaluation_start": evaluation_start.isoformat(),
        "evaluation_end": evaluation_end.isoformat(),
        "warmup_start": warmup_start.isoformat(),
        "terminal_end": terminal_end.isoformat(),
        "cost_round_trip_bps": COST_ROUND_TRIP_BPS,
        "risk_fraction": RISK_FRACTION,
        "global_position_limit": 1,
        "policy_summary": policy_summary,
        "fixed_horizon": fixed_horizon,
        "hypothesis_assessments": assessments,
        "raw_evidence": [asdict(item) for item in evidence],
        "interpretation_contract": (
            "This artifact diagnoses one external reconstruction.  It is not a strategy "
            "promotion.  A promising mechanism must be frozen and executed in NautilusTrader "
            "with actual order lifecycle, funding, adverse slippage, one global position, "
            "current-NAV risk sizing and continuous account state."
        ),
    }


def _write_compact_files(
    compact: dict[str, Any],
    output: Path,
) -> None:
    output.mkdir(parents=True, exist_ok=True)
    (output / "ANATOMY.json").write_text(
        json.dumps(compact, indent=2, sort_keys=True, default=_json_default) + "\n",
        encoding="utf-8",
    )
    policy_rows: list[dict[str, Any]] = []
    exit_rows: list[dict[str, Any]] = []
    quarter_rows: list[dict[str, Any]] = []
    for name, summary in compact["policy_summary"].items():
        policy_rows.append(
            {key: value for key, value in {"policy": name, **summary}.items()
             if key not in {"exit_reasons", "quarters"}}
        )
        exit_rows.extend(summary["exit_reasons"])
        quarter_rows.extend(summary["quarters"])
    pd.DataFrame(policy_rows).to_csv(output / "POLICIES.csv", index=False)
    pd.DataFrame(exit_rows).to_csv(output / "EXIT_REASONS.csv", index=False)
    pd.DataFrame(quarter_rows).to_csv(output / "QUARTERS.csv", index=False)

    md: list[str] = [
        "# Public ichiV2 claim reconstruction",
        "",
        f"- period: {compact['evaluation_start']} through {compact['evaluation_end']}",
        f"- cost: {COST_ROUND_TRIP_BPS:.1f} bp round trip",
        "- risk: current NAV x 3% planned loss",
        "- account: one global slot across BTC, ETH, SOL and XRP",
        "- causal episode: rising edge of one contiguous public source condition",
        "- purpose: mechanism falsification, not promotion",
        "",
        "## Policy anatomy",
        "",
        "| policy | trades | trades/day | win | PF | mean R | ending NAV | geom/day | max DD |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for name, summary in compact["policy_summary"].items():
        md.append(
            "| {name} | {trades} | {rate:.3f} | {win:.1f}% | {pf:.3f} | {mean_r:.3f} | "
            "{nav:.2f} | {daily:.4%} | {dd:.2%} |".format(
                name=name,
                trades=summary["trades"],
                rate=summary["trades_per_day"],
                win=100.0 * summary["win_rate"],
                pf=summary["profit_factor"],
                mean_r=summary["mean_r"],
                nav=summary["ending_nav"],
                daily=summary["geometric_daily_growth"],
                dd=summary["max_drawdown"],
            )
        )
    md.extend([
        "",
        "## Exit reason anatomy",
        "",
        "| policy | reason | trades | wins | win | pnl | mean R | mean hold min |",
        "|---|---|---:|---:|---:|---:|---:|---:|",
    ])
    for name, summary in compact["policy_summary"].items():
        for row in summary["exit_reasons"]:
            md.append(
                "| {policy} | {reason} | {trades} | {wins} | {win:.1f}% | {pnl:.2f} | "
                "{mean_r:.3f} | {hold:.1f} |".format(
                    policy=name,
                    reason=row["exit_reason"],
                    trades=row["trades"],
                    wins=row["wins"],
                    win=100.0 * row["win_rate"],
                    pnl=row["pnl"],
                    mean_r=row["mean_r"],
                    hold=row["mean_hold_minutes"],
                )
            )
    md.extend([
        "",
        "## Hypothesis assessment inputs",
        "",
        "```json",
        json.dumps(compact["hypothesis_assessments"], indent=2, sort_keys=True, default=_json_default),
        "```",
        "",
        "## Interpretation contract",
        "",
        compact["interpretation_contract"],
        "",
    ])
    (output / "ANATOMY.md").write_text("\n".join(md), encoding="utf-8")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def run(args: argparse.Namespace) -> None:
    evaluation_start = date.fromisoformat(args.start)
    evaluation_end = date.fromisoformat(args.end)
    if evaluation_end < evaluation_start:
        raise ValueError("end precedes start")
    warmup_start = evaluation_start - timedelta(days=int(args.warmup_days))
    terminal_end = evaluation_end + timedelta(days=int(args.forward_days))
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    cache = Path(args.cache)

    frames: dict[str, pd.DataFrame] = {}
    states: dict[str, pd.DataFrame] = {}
    evidence: list[RawEvidence] = []
    for symbol in SYMBOLS:
        frame, raw = load_symbol(
            symbol=symbol,
            data_start=warmup_start,
            data_end=terminal_end,
            cache=cache,
        )
        frames[symbol] = frame
        states[symbol] = build_source_state(frame, symbol)
        evidence.extend(raw)

    signals = signal_table(
        states,
        frames,
        evaluation_start=evaluation_start,
        evaluation_end=evaluation_end,
    )
    forward = _forward_signal_anatomy(signals=signals, frames=frames)
    policies = {
        policy.name: simulate_policy(
            policy=policy,
            frames=frames,
            states=states,
            signals=signals,
            evaluation_start=evaluation_start,
            evaluation_end=evaluation_end,
            terminal_end=terminal_end,
        )
        for policy in POLICIES
    }
    compact = _compact_result(
        evaluation_start=evaluation_start,
        evaluation_end=evaluation_end,
        warmup_start=warmup_start,
        terminal_end=terminal_end,
        states=states,
        signals=signals,
        forward=forward,
        policies=policies,
        evidence=evidence,
    )
    _write_compact_files(compact, output)

    raw_result = {
        "schema_version": 2,
        "compact": compact,
        "signals": forward,
        "policies": policies,
    }
    (output / "result.json").write_text(
        json.dumps(raw_result, indent=2, sort_keys=True, default=_json_default) + "\n",
        encoding="utf-8",
    )
    signals.to_csv(output / "signals.csv", index=False)
    for name, payload in policies.items():
        pd.DataFrame(payload["trade_records"]).to_csv(
            output / f"trades_{name}.csv", index=False
        )
    print(
        json.dumps(
            {
                "period": f"{evaluation_start}..{evaluation_end}",
                "edges": len(signals),
                "policies": {
                    name: {
                        key: summary[key]
                        for key in (
                            "trades", "trades_per_day", "win_rate", "profit_factor",
                            "mean_r", "ending_nav", "geometric_daily_growth", "max_drawdown",
                        )
                    }
                    for name, summary in compact["policy_summary"].items()
                },
            },
            indent=2,
            default=_json_default,
        )
    )


def self_test(_: argparse.Namespace) -> None:
    index = pd.date_range("2025-01-01", periods=16, freq="5min", tz="UTC")
    series = pd.Series([math.nan, *range(1, 16)], index=index, dtype=float)
    ema = _ema_talib(series, 3)
    assert math.isnan(float(ema.iloc[2]))
    assert math.isclose(float(ema.iloc[3]), 2.0)
    assert math.isclose(float(ema.iloc[4]), 3.0)

    frame = pd.DataFrame(
        {
            "open": [10.0, 11.0, 12.0],
            "high": [12.0, 13.0, 14.0],
            "low": [9.0, 10.0, 11.0],
            "close": [11.0, 12.0, 13.0],
        },
        index=index[:3],
    )
    ha = _heikin_ashi(frame)
    assert math.isclose(float(ha["open"].iloc[0]), 10.5)
    expected_close0 = (10.0 + 12.0 + 9.0 + 11.0) / 4.0
    assert math.isclose(float(ha["close"].iloc[0]), expected_close0)
    assert math.isclose(float(ha["open"].iloc[1]), (10.5 + expected_close0) / 2.0)

    quantity, per_unit = _risk_quantity(100_000.0, 100.0, 0.05)
    assert math.isclose(quantity * per_unit, 3_000.0, rel_tol=1e-12)

    position = {
        "entry_price": 100.0,
        "entry_time": index[0],
        "quantity": 1.0,
        "risk_budget": 3.0,
        "mfe_fraction": 0.0,
        "mae_fraction": 0.0,
        "peak_price": 100.0,
        "trailing_activated": False,
    }
    candle = pd.Series({"open": 100.0, "high": 106.0, "low": 94.0, "close": 101.0})
    exit_price, reason, intrabar = _process_position_bar(
        position=position,
        policy=POLICIES[1],
        timestamp=index[1],
        candle=candle,
        ema_exit_available=False,
    )
    assert intrabar and reason == "HARD_STOP" and math.isclose(float(exit_price), 95.0)

    print(json.dumps({"self_test": "passed", "tests": 4}, indent=2))


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser()
    commands = root.add_subparsers(dest="command", required=True)

    run_parser = commands.add_parser("run")
    run_parser.add_argument("--start", required=True)
    run_parser.add_argument("--end", required=True)
    run_parser.add_argument("--warmup-days", type=int, default=30)
    run_parser.add_argument("--forward-days", type=int, default=7)
    run_parser.add_argument(
        "--cache", type=Path, default=Path(".cache/candidate-51-ichiv2-claim")
    )
    run_parser.add_argument("--output", type=Path, required=True)
    run_parser.set_defaults(func=run)

    test_parser = commands.add_parser("self-test")
    test_parser.set_defaults(func=self_test)
    return root


def main() -> None:
    args = parser().parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
