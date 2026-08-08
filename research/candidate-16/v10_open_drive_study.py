#!/usr/bin/env python3
"""External Auction-Market Open-Drive mechanism study for Candidate 16 v10.

This is deliberately not an opening-range breakout.  It mechanizes the Open
Drive decision policy mined from Market Profile/Auction Market Theory:

* a globally meaningful session hand-off creates a new auction reference;
* the first ten completed minutes move one way, do not revisit the open, and
  carry above-normal volume relative to the same session;
* spot participates, so the move is broad price discovery rather than a
  derivatives-only wick;
* no order exists on the opening impulse;
* the first later counter-direction micro-pullback must preserve the session
  open, then a strictly later bar resumes with aligned spot return and taker
  flow;
* entry, pullback invalidation, and a past-known liquidity objective all belong
  to that later leg.

The study uses checksum-verified Binance Vision one-minute spot and USD-M bars.
It evaluates exact causal entry/stop/target geometry with conservative one-minute
first-touch ordering and the project's 20 bp round-trip cost screen.  It creates
no fills, account, portfolio or NAV; a mechanism must pass 2023 development and
then untouched 2024 before any NautilusTrader strategy is built.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time, timezone
import json
import math
from pathlib import Path
from statistics import median
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from v9_liquidation_event_study import Archive
from v9_liquidation_event_study import StudyError
from v9_liquidation_event_study import download_verified
from v9_liquidation_event_study import read_kline


SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT")
DEVELOPMENT_YEAR = 2023
HOLDOUT_YEAR = 2024
OPENING_MINUTES = 10
PULLBACK_SEARCH_MINUTES = 20
MAX_PULLBACK_BARS = 5
MAX_HOLD_MINUTES = 60
MIN_SESSION_HISTORY = 40
OPENING_DISPLACEMENT_QUANTILE = 0.75
ROUND_TRIP_COST_RATE = 0.0020
MIN_TARGET_NET_R = 1.0
GLOBAL_ENTRY_CLUSTER_MINUTES = 3


@dataclass(frozen=True, slots=True)
class SessionOpen:
    name: str
    ts: pd.Timestamp


@dataclass(slots=True)
class ThresholdHistory:
    absolute_displacements: list[float]
    quote_volumes: list[float]

    def ready(self) -> bool:
        return (
            len(self.absolute_displacements) >= MIN_SESSION_HISTORY
            and len(self.quote_volumes) >= MIN_SESSION_HISTORY
        )

    def displacement_threshold(self) -> float:
        return float(
            np.quantile(
                np.asarray(self.absolute_displacements, dtype=float),
                OPENING_DISPLACEMENT_QUANTILE,
            ),
        )

    def volume_threshold(self) -> float:
        return float(median(self.quote_volumes))

    def observe(self, displacement: float, volume: float) -> None:
        if math.isfinite(displacement) and displacement >= 0.0:
            self.absolute_displacements.append(float(displacement))
        if math.isfinite(volume) and volume > 0.0:
            self.quote_volumes.append(float(volume))


@dataclass(frozen=True, slots=True)
class CandidateTrade:
    symbol: str
    session: str
    session_open_ts: pd.Timestamp
    entry_ts: pd.Timestamp
    side: int
    entry: float
    stop: float
    target: float
    target_source: str
    planned_loss_rate: float
    target_net_r: float
    drive_score: float
    opening_displacement: float
    opening_volume: float
    opening_range: float
    prior_4h_high: float
    prior_4h_low: float
    prior_24h_high: float
    prior_24h_low: float


@dataclass(frozen=True, slots=True)
class ScoredTrade:
    candidate: CandidateTrade
    exit_ts: pd.Timestamp
    exit_reason: str
    exit_price: float
    net_return: float
    net_r: float
    mfe: float
    mae: float


def _canonical_minute(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.copy()
    result["minute"] = pd.to_datetime(
        result["minute"],
        utc=True,
        errors="raise",
    ).astype("datetime64[ns, UTC]")
    return result


def _month_labels(start_year: int, end_year: int) -> list[str]:
    periods = pd.period_range(
        f"{start_year}-01",
        f"{end_year}-12",
        freq="M",
    )
    return [str(period) for period in periods]


def load_symbol(symbol: str, cache: Path) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    for month in _month_labels(DEVELOPMENT_YEAR, HOLDOUT_YEAR):
        futures = download_verified(
            Archive("um", "monthly", "klines", symbol, month, "1m"),
            cache / symbol / "futures",
        )
        spot = download_verified(
            Archive("spot", "monthly", "klines", symbol, month, "1m"),
            cache / symbol / "spot",
        )
        perp = _canonical_minute(read_kline(futures, prefix="perp"))
        cash = _canonical_minute(read_kline(spot, prefix="spot"))
        current = perp.merge(
            cash[
                [
                    "minute",
                    "spot_open",
                    "spot_high",
                    "spot_low",
                    "spot_close",
                    "spot_quote_volume",
                ]
            ],
            on="minute",
            how="inner",
            validate="one_to_one",
        )
        frames.append(current)
    panel = (
        pd.concat(frames, ignore_index=True)
        .sort_values("minute", kind="stable")
        .drop_duplicates("minute", keep="last")
        .reset_index(drop=True)
    )
    if panel["minute"].duplicated().any() or not panel["minute"].is_monotonic_increasing:
        raise StudyError(f"invalid minute clock for {symbol}")
    expected_days = 365 + 366
    if len(panel) < expected_days * 1_400:
        raise StudyError(f"incomplete 2023-2024 panel for {symbol}: {len(panel)}")
    panel["symbol"] = symbol
    panel["perp_flow"] = (
        2.0
        * panel["perp_taker_buy_quote"]
        / panel["perp_quote_volume"].replace(0.0, np.nan)
        - 1.0
    )
    panel["spot_ret_1m"] = np.log(panel["spot_close"]).diff()
    panel["prior_4h_high"] = panel["perp_high"].rolling(240, min_periods=240).max().shift(1)
    panel["prior_4h_low"] = panel["perp_low"].rolling(240, min_periods=240).min().shift(1)
    panel["prior_24h_high"] = panel["perp_high"].rolling(1_440, min_periods=1_440).max().shift(1)
    panel["prior_24h_low"] = panel["perp_low"].rolling(1_440, min_periods=1_440).min().shift(1)
    return panel.set_index("minute", drop=False)


def _ny_open(day: date) -> pd.Timestamp:
    local = datetime.combine(
        day,
        time(9, 30),
        tzinfo=ZoneInfo("America/New_York"),
    )
    return pd.Timestamp(local.astimezone(timezone.utc)).as_unit("ns")


def session_opens(start: date, end: date) -> list[SessionOpen]:
    result: list[SessionOpen] = []
    for day in pd.date_range(start, end, freq="D"):
        if day.weekday() >= 5:
            continue
        base = pd.Timestamp(day.date(), tz="UTC").as_unit("ns")
        result.extend(
            [
                SessionOpen("ASIA_0000_UTC", base),
                SessionOpen("EUROPE_0800_UTC", base + pd.Timedelta(hours=8)),
                SessionOpen("NEW_YORK_0930_LOCAL", _ny_open(day.date())),
            ],
        )
    return sorted(result, key=lambda item: item.ts)


def _window(panel: pd.DataFrame, start: pd.Timestamp, minutes: int) -> pd.DataFrame | None:
    expected = pd.date_range(start, periods=minutes, freq="min", tz="UTC").as_unit("ns")
    sample = panel.reindex(expected)
    if sample["perp_close"].isna().any() or sample["spot_close"].isna().any():
        return None
    return sample


def _past_levels(row: pd.Series) -> tuple[float, float, float, float] | None:
    values = tuple(
        float(row[name])
        for name in (
            "prior_4h_high",
            "prior_4h_low",
            "prior_24h_high",
            "prior_24h_low",
        )
    )
    return values if all(math.isfinite(value) and value > 0.0 for value in values) else None


def _directional_target(
    *,
    side: int,
    entry: float,
    prior_4h_high: float,
    prior_4h_low: float,
    prior_24h_high: float,
    prior_24h_low: float,
) -> tuple[float, str] | None:
    if side > 0:
        values = [
            (prior_4h_high, "PRIOR_4H_HIGH"),
            (prior_24h_high, "PRIOR_24H_HIGH"),
        ]
        eligible = [(price, name) for price, name in values if price > entry]
        return min(eligible, key=lambda item: item[0]) if eligible else None
    values = [
        (prior_4h_low, "PRIOR_4H_LOW"),
        (prior_24h_low, "PRIOR_24H_LOW"),
    ]
    eligible = [(price, name) for price, name in values if price < entry]
    return max(eligible, key=lambda item: item[0]) if eligible else None


def detect_candidate(
    *,
    symbol: str,
    panel: pd.DataFrame,
    session: SessionOpen,
    history: ThresholdHistory,
) -> CandidateTrade | None:
    opening = _window(panel, session.ts, OPENING_MINUTES)
    if opening is None:
        return None
    open_price = float(opening.iloc[0]["perp_open"])
    opening_close = float(opening.iloc[-1]["perp_close"])
    spot_open = float(opening.iloc[0]["spot_open"])
    spot_close = float(opening.iloc[-1]["spot_close"])
    if min(open_price, opening_close, spot_open, spot_close) <= 0.0:
        return None
    signed = math.log(opening_close / open_price)
    side = 1 if signed > 0.0 else -1 if signed < 0.0 else 0
    displacement = abs(signed)
    quote_volume = float(opening["perp_quote_volume"].sum())
    levels = _past_levels(opening.iloc[0])

    threshold_ready = history.ready()
    displacement_threshold = history.displacement_threshold() if threshold_ready else math.nan
    volume_threshold = history.volume_threshold() if threshold_ready else math.nan
    history.observe(displacement, quote_volume)
    if side == 0 or levels is None or not threshold_ready:
        return None

    # State evidence: after the first two completed minutes the session open is
    # never revisited by either close or extreme, matching the Open-Drive concept.
    mature = opening.iloc[2:]
    if side > 0:
        no_retest = bool(
            (mature["perp_close"] > open_price).all()
            and (mature["perp_low"] > open_price).all()
        )
    else:
        no_retest = bool(
            (mature["perp_close"] < open_price).all()
            and (mature["perp_high"] < open_price).all()
        )
    spot_alignment = side * math.log(spot_close / spot_open) > 0.0
    if not (
        no_retest
        and spot_alignment
        and displacement > displacement_threshold
        and quote_volume > volume_threshold
    ):
        return None

    continuation = _window(
        panel,
        session.ts + pd.Timedelta(minutes=OPENING_MINUTES),
        PULLBACK_SEARCH_MINUTES + MAX_PULLBACK_BARS + 1,
    )
    if continuation is None:
        return None

    pullback_start: int | None = None
    pullback_extreme = math.nan
    pullback_break = math.nan
    for position in range(PULLBACK_SEARCH_MINUTES):
        row = continuation.iloc[position]
        # The session open remains the scenario invalidation throughout waiting.
        if side > 0 and float(row["perp_low"]) <= open_price:
            return None
        if side < 0 and float(row["perp_high"]) >= open_price:
            return None
        body = side * (float(row["perp_close"]) - float(row["perp_open"]))
        if pullback_start is None:
            if body >= 0.0:
                continue
            pullback_start = position
            pullback_extreme = (
                float(row["perp_low"]) if side > 0 else float(row["perp_high"])
            )
            pullback_break = (
                float(row["perp_high"]) if side > 0 else float(row["perp_low"])
            )
            continue

        pullback_bars = position - pullback_start
        if pullback_bars > MAX_PULLBACK_BARS:
            return None
        previous = continuation.iloc[pullback_start:position]
        if side > 0:
            pullback_extreme = min(pullback_extreme, float(previous["perp_low"].min()))
            pullback_break = max(pullback_break, float(previous["perp_high"].max()))
            resumed = float(row["perp_close"]) > pullback_break
        else:
            pullback_extreme = max(pullback_extreme, float(previous["perp_high"].max()))
            pullback_break = min(pullback_break, float(previous["perp_low"].min()))
            resumed = float(row["perp_close"]) < pullback_break
        flow = float(row["perp_flow"])
        spot_return = float(row["spot_ret_1m"])
        confirmation = (
            resumed
            and body > 0.0
            and math.isfinite(flow)
            and side * flow > 0.0
            and math.isfinite(spot_return)
            and side * spot_return > 0.0
        )
        if not confirmation:
            continue

        entry = float(row["perp_close"])
        stop = float(pullback_extreme)
        if (side > 0 and not stop < entry) or (side < 0 and not entry < stop):
            return None
        prior_4h_high, prior_4h_low, prior_24h_high, prior_24h_low = levels
        target_values = _directional_target(
            side=side,
            entry=entry,
            prior_4h_high=prior_4h_high,
            prior_4h_low=prior_4h_low,
            prior_24h_high=prior_24h_high,
            prior_24h_low=prior_24h_low,
        )
        if target_values is None:
            return None
        target, source = target_values
        planned_loss_rate = side * (entry - stop) / entry + ROUND_TRIP_COST_RATE
        net_target_return = side * (target - entry) / entry - ROUND_TRIP_COST_RATE
        if planned_loss_rate <= 0.0 or net_target_return <= 0.0:
            return None
        target_net_r = net_target_return / planned_loss_rate
        if target_net_r + 1e-12 < MIN_TARGET_NET_R:
            return None
        opening_range = float(opening["perp_high"].max() - opening["perp_low"].min())
        drive_score = (
            displacement / max(displacement_threshold, 1e-12)
            * quote_volume / max(volume_threshold, 1e-12)
        )
        return CandidateTrade(
            symbol=symbol,
            session=session.name,
            session_open_ts=session.ts,
            entry_ts=pd.Timestamp(row.name),
            side=side,
            entry=entry,
            stop=stop,
            target=float(target),
            target_source=source,
            planned_loss_rate=planned_loss_rate,
            target_net_r=target_net_r,
            drive_score=drive_score,
            opening_displacement=displacement,
            opening_volume=quote_volume,
            opening_range=opening_range,
            prior_4h_high=prior_4h_high,
            prior_4h_low=prior_4h_low,
            prior_24h_high=prior_24h_high,
            prior_24h_low=prior_24h_low,
        )
    return None


def score_candidate(candidate: CandidateTrade, panel: pd.DataFrame) -> ScoredTrade | None:
    future = _window(
        panel,
        candidate.entry_ts + pd.Timedelta(minutes=1),
        MAX_HOLD_MINUTES,
    )
    if future is None:
        return None
    exit_reason = "TIME_EXIT"
    exit_price = float(future.iloc[-1]["perp_close"])
    exit_ts = pd.Timestamp(future.iloc[-1].name)
    favorable = 0.0
    adverse = 0.0
    for _, row in future.iterrows():
        high = float(row["perp_high"])
        low = float(row["perp_low"])
        if candidate.side > 0:
            favorable = max(favorable, high / candidate.entry - 1.0)
            adverse = min(adverse, low / candidate.entry - 1.0)
            stop_hit = low <= candidate.stop
            target_hit = high >= candidate.target
        else:
            favorable = max(favorable, 1.0 - low / candidate.entry)
            adverse = min(adverse, 1.0 - high / candidate.entry)
            stop_hit = high >= candidate.stop
            target_hit = low <= candidate.target
        # One-minute ambiguity is resolved against the system.
        if stop_hit:
            exit_reason = "STOP"
            exit_price = candidate.stop
            exit_ts = pd.Timestamp(row.name)
            break
        if target_hit:
            exit_reason = "TARGET"
            exit_price = candidate.target
            exit_ts = pd.Timestamp(row.name)
            break
    net_return = (
        candidate.side * (exit_price - candidate.entry) / candidate.entry
        - ROUND_TRIP_COST_RATE
    )
    net_r = net_return / candidate.planned_loss_rate
    return ScoredTrade(
        candidate=candidate,
        exit_ts=exit_ts,
        exit_reason=exit_reason,
        exit_price=exit_price,
        net_return=net_return,
        net_r=net_r,
        mfe=favorable,
        mae=adverse,
    )


def _collapse_entry_clusters(candidates: list[CandidateTrade]) -> list[CandidateTrade]:
    if not candidates:
        return []
    ordered = sorted(candidates, key=lambda item: item.entry_ts)
    clusters: list[list[CandidateTrade]] = []
    current: list[CandidateTrade] = []
    anchor: pd.Timestamp | None = None
    for candidate in ordered:
        if (
            anchor is None
            or candidate.entry_ts - anchor > pd.Timedelta(minutes=GLOBAL_ENTRY_CLUSTER_MINUTES)
        ):
            if current:
                clusters.append(current)
            current = [candidate]
            anchor = candidate.entry_ts
        else:
            current.append(candidate)
    if current:
        clusters.append(current)
    return [
        max(
            cluster,
            key=lambda item: (
                item.drive_score,
                item.target_net_r,
                item.symbol,
            ),
        )
        for cluster in clusters
    ]


def discover_candidates(
    panels: dict[str, pd.DataFrame],
) -> tuple[list[CandidateTrade], dict[str, Any]]:
    histories: dict[tuple[str, str], ThresholdHistory] = defaultdict(
        lambda: ThresholdHistory([], []),
    )
    candidates: list[CandidateTrade] = []
    funnel: dict[str, int] = defaultdict(int)
    opens = session_opens(date(DEVELOPMENT_YEAR, 1, 1), date(HOLDOUT_YEAR, 12, 31))
    for session in opens:
        for symbol, panel in panels.items():
            funnel["session_symbol_observations"] += 1
            history = histories[(symbol, session.name)]
            before = len(history.absolute_displacements)
            candidate = detect_candidate(
                symbol=symbol,
                panel=panel,
                session=session,
                history=history,
            )
            if before >= MIN_SESSION_HISTORY:
                funnel["threshold_ready_observations"] += 1
            if candidate is not None:
                funnel["complete_open_drive_candidates"] += 1
                candidates.append(candidate)
    collapsed = _collapse_entry_clusters(candidates)
    funnel["global_cluster_representatives"] = len(collapsed)
    return collapsed, dict(sorted(funnel.items()))


def enforce_one_global_slot(
    candidates: list[CandidateTrade],
    panels: dict[str, pd.DataFrame],
) -> tuple[list[ScoredTrade], int]:
    scored: list[ScoredTrade] = []
    active_until = pd.Timestamp.min.tz_localize("UTC")
    conflicts = 0
    for candidate in sorted(candidates, key=lambda item: item.entry_ts):
        if candidate.entry_ts <= active_until:
            conflicts += 1
            continue
        result = score_candidate(candidate, panels[candidate.symbol])
        if result is None:
            continue
        scored.append(result)
        active_until = result.exit_ts
    return scored, conflicts


def _records(scored: list[ScoredTrade]) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for item in scored:
        candidate = item.candidate
        rows.append(
            {
                "symbol": candidate.symbol,
                "session": candidate.session,
                "session_open_ts": candidate.session_open_ts,
                "entry_ts": candidate.entry_ts,
                "exit_ts": item.exit_ts,
                "side": candidate.side,
                "entry": candidate.entry,
                "stop": candidate.stop,
                "target": candidate.target,
                "target_source": candidate.target_source,
                "planned_loss_rate": candidate.planned_loss_rate,
                "target_net_r": candidate.target_net_r,
                "drive_score": candidate.drive_score,
                "opening_displacement": candidate.opening_displacement,
                "opening_volume": candidate.opening_volume,
                "opening_range": candidate.opening_range,
                "exit_reason": item.exit_reason,
                "exit_price": item.exit_price,
                "net_return": item.net_return,
                "net_r": item.net_r,
                "mfe": item.mfe,
                "mae": item.mae,
            },
        )
    return pd.DataFrame(rows)


def _profit_factor(values: pd.Series) -> float | None:
    gross_profit = float(values[values > 0.0].sum())
    gross_loss = float(-values[values < 0.0].sum())
    if gross_loss <= 0.0:
        return None if gross_profit <= 0.0 else math.inf
    return gross_profit / gross_loss


def summarize(frame: pd.DataFrame) -> dict[str, Any]:
    if frame.empty:
        return {
            "trades": 0,
            "wins": 0,
            "losses": 0,
            "win_rate": 0.0,
            "mean_net_r": 0.0,
            "median_net_r": 0.0,
            "profit_factor": 0.0,
            "median_mfe": 0.0,
            "median_mae": 0.0,
            "symbols_positive_mean_r": 0,
            "sessions_positive_mean_r": 0,
            "by_symbol": {},
            "by_session": {},
        }
    values = pd.to_numeric(frame["net_r"], errors="raise")
    by_symbol = {
        str(symbol): {
            "trades": int(len(group)),
            "wins": int((group["net_r"] > 0.0).sum()),
            "mean_net_r": float(group["net_r"].mean()),
        }
        for symbol, group in frame.groupby("symbol", sort=True)
    }
    by_session = {
        str(session): {
            "trades": int(len(group)),
            "wins": int((group["net_r"] > 0.0).sum()),
            "mean_net_r": float(group["net_r"].mean()),
        }
        for session, group in frame.groupby("session", sort=True)
    }
    factor = _profit_factor(values)
    return {
        "trades": int(len(frame)),
        "wins": int((values > 0.0).sum()),
        "losses": int((values < 0.0).sum()),
        "win_rate": float((values > 0.0).mean()),
        "mean_net_r": float(values.mean()),
        "median_net_r": float(values.median()),
        "profit_factor": None if factor is not None and math.isinf(factor) else factor,
        "profit_factor_infinite": bool(factor is not None and math.isinf(factor)),
        "median_mfe": float(frame["mfe"].median()),
        "median_mae": float(frame["mae"].median()),
        "active_days": int(pd.to_datetime(frame["entry_ts"], utc=True).dt.date.nunique()),
        "largest_winner_share": (
            float(values[values > 0.0].max() / values[values > 0.0].sum())
            if (values > 0.0).any()
            else 1.0
        ),
        "symbols_positive_mean_r": sum(
            item["mean_net_r"] > 0.0 for item in by_symbol.values()
        ),
        "sessions_positive_mean_r": sum(
            item["mean_net_r"] > 0.0 for item in by_session.values()
        ),
        "by_symbol": by_symbol,
        "by_session": by_session,
        "exit_reasons": {
            str(key): int(value)
            for key, value in frame["exit_reason"].value_counts().sort_index().items()
        },
    }


def promotion_checks(summary: dict[str, Any]) -> dict[str, bool]:
    profit_factor = summary.get("profit_factor")
    factor_pass = bool(summary.get("profit_factor_infinite")) or (
        profit_factor is not None and float(profit_factor) >= 1.5
    )
    return {
        "trades_at_least_30": int(summary.get("trades", 0)) >= 30,
        "wins_at_least_15": int(summary.get("wins", 0)) >= 15,
        "win_rate_at_least_50pct": float(summary.get("win_rate", 0.0)) >= 0.50,
        "mean_net_r_at_least_0_25": float(summary.get("mean_net_r", 0.0)) >= 0.25,
        "profit_factor_at_least_1_5": factor_pass,
        "median_mfe_covers_round_trip_cost": float(summary.get("median_mfe", 0.0)) >= ROUND_TRIP_COST_RATE,
        "positive_on_at_least_three_symbols": int(summary.get("symbols_positive_mean_r", 0)) >= 3,
        "positive_on_at_least_two_sessions": int(summary.get("sessions_positive_mean_r", 0)) >= 2,
        "largest_winner_share_at_most_35pct": float(summary.get("largest_winner_share", 1.0)) <= 0.35,
    }


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def run(cache: Path, output: Path) -> dict[str, Any]:
    panels = {symbol: load_symbol(symbol, cache) for symbol in SYMBOLS}
    candidates, funnel = discover_candidates(panels)
    scored, conflicts = enforce_one_global_slot(candidates, panels)
    frame = _records(scored)
    if frame.empty:
        development = frame
        holdout = frame
    else:
        years = pd.to_datetime(frame["entry_ts"], utc=True).dt.year
        development = frame[years == DEVELOPMENT_YEAR].copy()
        holdout = frame[years == HOLDOUT_YEAR].copy()
    development_summary = summarize(development)
    development_checks = promotion_checks(development_summary)
    development_pass = all(development_checks.values())

    if development_pass:
        holdout_summary = summarize(holdout)
        holdout_checks = promotion_checks(holdout_summary)
        holdout_opened = True
        holdout_pass = all(holdout_checks.values())
    else:
        holdout_summary = None
        holdout_checks = None
        holdout_opened = False
        holdout_pass = False

    if development_pass and holdout_pass:
        decision = "PROMOTE_OPEN_DRIVE_TO_NAUTILUS_CONTINUOUS_ACCOUNT_PROTOTYPE"
    elif development_pass:
        decision = "DISCARD_OPEN_DRIVE_AFTER_UNTOUCHED_2024_FAILURE"
    else:
        decision = "DISCARD_OPEN_DRIVE_AFTER_2023_DEVELOPMENT_MECHANISM_FAILURE"

    output.mkdir(parents=True, exist_ok=True)
    development.to_csv(output / "development_trades.csv", index=False)
    if holdout_opened:
        holdout.to_csv(output / "holdout_trades.csv", index=False)
    result = {
        "schema": "candidate-16-v10-open-drive-study-v1",
        "role": "mechanism and geometry study; no fills, account, portfolio, or NAV claim",
        "external_policy": {
            "family": "Auction Market Theory / Market Profile Open Drive",
            "opening_state": (
                "first ten completed minutes directional, no mature open retest, "
                "top-quartile same-session displacement, above-median volume, spot aligned"
            ),
            "transition": (
                "first counter-direction micro-pullback preserves open; strictly later "
                "close breaks pullback with spot return and taker flow aligned"
            ),
            "invalidation": "later-leg pullback extreme; session open remains scenario boundary",
            "objective": "nearest past-known prior-4h or prior-24h directional extreme",
        },
        "data": {
            "source": "checksum-verified Binance Vision spot and USD-M 1m monthly klines",
            "symbols": list(SYMBOLS),
            "years": [DEVELOPMENT_YEAR, HOLDOUT_YEAR],
        },
        "sessions": [
            "ASIA_0000_UTC",
            "EUROPE_0800_UTC",
            "NEW_YORK_0930_LOCAL_WITH_DST",
        ],
        "cost_and_geometry": {
            "round_trip_cost_rate": ROUND_TRIP_COST_RATE,
            "minimum_target_net_r": MIN_TARGET_NET_R,
            "same_bar_stop_before_target": True,
            "max_hold_minutes": MAX_HOLD_MINUTES,
            "global_entry_or_position_slot": 1,
        },
        "funnel": funnel,
        "global_slot_conflicts_skipped": conflicts,
        "development": {
            "period": "2023-01-01 through 2023-12-31",
            "summary": development_summary,
            "checks": development_checks,
            "passed": development_pass,
        },
        "holdout": {
            "period": "2024-01-01 through 2024-12-31",
            "opened": holdout_opened,
            "summary": holdout_summary,
            "checks": holdout_checks,
            "passed": holdout_pass,
        },
        "promote": development_pass and holdout_pass,
        "decision": decision,
    }
    write_json(output / "study.json", result)
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = run(args.cache.resolve(), args.output.resolve())
    print(json.dumps(result, indent=2, sort_keys=True, allow_nan=False))


if __name__ == "__main__":
    main()
