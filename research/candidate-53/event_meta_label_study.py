#!/usr/bin/env python3
"""Cost-aware primary-event + meta-label study for Candidate 53.

The study deliberately separates two questions:

1. A complete primary auction episode generates an actionable trade geometry.
2. A fixed regularized probabilistic selector decides whether that already-valid
   trade should consume the account's single slot.

Primary event (all completed data): a 15-minute bar whose absolute return is in
its trailing 7-day top quintile and whose quote volume is above its trailing
7-day median.  The strictly next completed 15-minute bar then resolves the
state transition:

* CONTINUATION: the next bar closes farther in the impulse direction while its
  excursion preserves the event midpoint. Entry = next close, invalidation =
  event midpoint.
* REVERSAL: the next bar closes through the event midpoint in the opposite
  direction. Entry = next close, invalidation = event extreme.

Target is not tuned: it is solved so a target fill earns +2R after the current
21 bp round-trip fee+slippage budget while a stop costs exactly -1R after cost.
Paths are evaluated on subsequent one-minute bars with stop-before-target
ordering and a 180-minute day-trade horizon.

Selector: one fixed L2-regularized logistic regression implemented with numpy
Newton steps (no hyperparameter search), trained on 2025-01..09. Candidate 53
uses probability >= 0.50, the conventional probability decision boundary.  Q4
2025 is development confirmation.  The model is then refit once on all 2025
using the exact frozen feature set/regularization/threshold and evaluated on
2026-01..07 without any adaptive changes. This file is a mechanism/selector
study, not an execution or account engine. Any promoted policy must be rebuilt
inside NautilusTrader.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from datetime import date
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

from v9_liquidation_event_study import Archive, StudyError, download_verified, read_kline

SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT")
COST_RATE = 0.0021
TARGET_NET_R = 2.0
MAX_HOLD_MINUTES = 180
TRAILING_BARS = 7 * 24 * 4
EVENT_QUANTILE = 0.80
PROBABILITY_THRESHOLD = 0.50
L2_PENALTY = 1.0
MAX_NEWTON_STEPS = 25
TRAIN_END = pd.Timestamp("2025-09-30 23:59:59", tz="UTC")
DEV_START = pd.Timestamp("2025-10-01", tz="UTC")
DEV_END = pd.Timestamp("2025-12-31 23:59:59", tz="UTC")
HOLDOUT_START = pd.Timestamp("2026-01-01", tz="UTC")
HOLDOUT_END = pd.Timestamp("2026-07-31 23:59:59", tz="UTC")

FEATURE_NAMES = (
    "action_continuation",
    "event_side",
    "event_ret_abs_z",
    "event_range_atr",
    "event_volume_ratio",
    "event_flow",
    "event_close_location_signed",
    "event_spot_alignment",
    "event_basis_z",
    "trend_2h_signed",
    "trend_8h_signed",
    "rv_4h",
    "next_ret_signed_event",
    "next_flow_signed_trade",
    "next_volume_ratio",
    "next_close_location_signed_trade",
    "peer_breadth_signed_trade",
    "peer_dispersion",
    "relative_strength_signed_trade",
    "raw_risk_rate",
    "target_distance_rate",
    "hour_sin",
    "hour_cos",
)


@dataclass(frozen=True, slots=True)
class Candidate:
    symbol: str
    family: str
    side: int
    event_ts: pd.Timestamp
    entry_ts: pd.Timestamp
    entry: float
    stop: float
    target: float
    planned_loss_rate: float
    raw_risk_rate: float
    target_distance_rate: float
    features: tuple[float, ...]


@dataclass(frozen=True, slots=True)
class Scored:
    candidate: Candidate
    exit_ts: pd.Timestamp
    exit_reason: str
    exit_price: float
    net_return: float
    net_r: float
    probability: float | None = None


def _month_labels(start: str, end: str) -> list[str]:
    return [str(x) for x in pd.period_range(start, end, freq="M")]


def load_symbol(symbol: str, cache: Path) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    for label in _month_labels("2024-12", "2026-07"):
        perp_path = download_verified(
            Archive("um", "monthly", "klines", symbol, label, "1m"),
            cache / symbol / "perp",
        )
        spot_path = download_verified(
            Archive("spot", "monthly", "klines", symbol, label, "1m"),
            cache / symbol / "spot",
        )
        perp = read_kline(perp_path, prefix="perp")
        spot = read_kline(spot_path, prefix="spot")
        perp["minute"] = pd.to_datetime(perp["minute"], utc=True, errors="raise")
        spot["minute"] = pd.to_datetime(spot["minute"], utc=True, errors="raise")
        frames.append(
            perp.merge(
                spot[["minute", "spot_open", "spot_high", "spot_low", "spot_close", "spot_quote_volume"]],
                on="minute",
                how="inner",
                validate="one_to_one",
            )
        )
    panel = (
        pd.concat(frames, ignore_index=True)
        .sort_values("minute", kind="stable")
        .drop_duplicates("minute", keep="last")
    )
    if panel["minute"].duplicated().any() or not panel["minute"].is_monotonic_increasing:
        raise StudyError(f"invalid minute clock: {symbol}")
    return panel.set_index("minute", drop=False)


def aggregate_fifteen(panel: pd.DataFrame) -> pd.DataFrame:
    g = panel.resample("15min", label="left", closed="left")
    bar = pd.DataFrame(
        {
            "open": g["perp_open"].first(),
            "high": g["perp_high"].max(),
            "low": g["perp_low"].min(),
            "close": g["perp_close"].last(),
            "quote_volume": g["perp_quote_volume"].sum(),
            "taker_buy_quote": g["perp_taker_buy_quote"].sum(),
            "spot_open": g["spot_open"].first(),
            "spot_close": g["spot_close"].last(),
            "minute_count": g["perp_close"].count(),
        }
    )
    bar = bar[bar["minute_count"] == 15].copy()
    bar.index = (bar.index + pd.Timedelta(minutes=14)).as_unit("ns")
    bar["minute"] = bar.index
    bar["ret"] = np.log(bar["close"] / bar["open"])
    bar["spot_ret"] = np.log(bar["spot_close"] / bar["spot_open"])
    bar["flow"] = 2.0 * bar["taker_buy_quote"] / bar["quote_volume"].replace(0.0, np.nan) - 1.0
    bar["close_location"] = (bar["close"] - bar["low"]) / (bar["high"] - bar["low"]).replace(0.0, np.nan)
    bar["basis"] = np.log(bar["close"] / bar["spot_close"])

    prev_close = bar["close"].shift(1)
    tr = pd.concat(
        [bar["high"] - bar["low"], (bar["high"] - prev_close).abs(), (bar["low"] - prev_close).abs()],
        axis=1,
    ).max(axis=1)
    bar["atr"] = tr.rolling(32, min_periods=32).mean().shift(1)
    bar["abs_ret_threshold"] = bar["ret"].abs().rolling(TRAILING_BARS, min_periods=TRAILING_BARS).quantile(EVENT_QUANTILE).shift(1)
    bar["abs_ret_mean"] = bar["ret"].abs().rolling(TRAILING_BARS, min_periods=TRAILING_BARS).mean().shift(1)
    bar["abs_ret_std"] = bar["ret"].abs().rolling(TRAILING_BARS, min_periods=TRAILING_BARS).std(ddof=0).shift(1)
    bar["volume_median"] = bar["quote_volume"].rolling(TRAILING_BARS, min_periods=TRAILING_BARS).median().shift(1)
    basis_mean = bar["basis"].rolling(TRAILING_BARS, min_periods=TRAILING_BARS).mean().shift(1)
    basis_std = bar["basis"].rolling(TRAILING_BARS, min_periods=TRAILING_BARS).std(ddof=0).shift(1)
    bar["basis_z"] = (bar["basis"] - basis_mean) / basis_std.replace(0.0, np.nan)
    bar["trend_2h"] = np.log(bar["close"] / bar["close"].shift(8))
    bar["trend_8h"] = np.log(bar["close"] / bar["close"].shift(32))
    bar["rv_4h"] = bar["ret"].rolling(16, min_periods=16).std(ddof=0).shift(1)
    return bar


def _sigmoid(x: np.ndarray) -> np.ndarray:
    x = np.clip(x, -35.0, 35.0)
    return 1.0 / (1.0 + np.exp(-x))


@dataclass(frozen=True, slots=True)
class LogisticModel:
    mean: np.ndarray
    scale: np.ndarray
    beta: np.ndarray

    def probability(self, x: np.ndarray) -> np.ndarray:
        z = (x - self.mean) / self.scale
        design = np.column_stack([np.ones(len(z)), z])
        return _sigmoid(design @ self.beta)


def fit_logistic(x: np.ndarray, y: np.ndarray) -> LogisticModel:
    if len(x) < 20 or len(np.unique(y)) < 2:
        raise StudyError(f"insufficient meta-label training sample: n={len(x)}, classes={np.unique(y)}")
    mean = x.mean(axis=0)
    scale = x.std(axis=0)
    scale = np.where(scale > 1e-9, scale, 1.0)
    z = (x - mean) / scale
    design = np.column_stack([np.ones(len(z)), z])
    beta = np.zeros(design.shape[1], dtype=float)
    penalty = np.eye(design.shape[1], dtype=float) * L2_PENALTY
    penalty[0, 0] = 0.0
    for _ in range(MAX_NEWTON_STEPS):
        p = _sigmoid(design @ beta)
        w = np.maximum(p * (1.0 - p), 1e-6)
        grad = design.T @ (p - y) + penalty @ beta
        hessian = design.T @ (design * w[:, None]) + penalty
        step = np.linalg.solve(hessian, grad)
        beta -= step
        if float(np.max(np.abs(step))) < 1e-7:
            break
    return LogisticModel(mean=mean, scale=scale, beta=beta)


def _cost_aware_target(side: int, entry: float, stop: float) -> tuple[float, float, float] | None:
    raw_risk = side * (entry - stop) / entry
    if not math.isfinite(raw_risk) or raw_risk <= 0.0:
        return None
    planned_loss = raw_risk + COST_RATE
    target_distance = TARGET_NET_R * planned_loss + COST_RATE
    target = entry * (1.0 + side * target_distance)
    if target <= 0.0 or not math.isfinite(target):
        return None
    return target, planned_loss, target_distance


def build_candidates(bars: dict[str, pd.DataFrame]) -> list[Candidate]:
    # Cross-sectional values are known at the same completed 15m boundary.
    common = sorted(set.intersection(*(set(frame.index) for frame in bars.values())))
    peer = pd.DataFrame({symbol: bars[symbol].reindex(common)["ret"] for symbol in SYMBOLS}, index=common)
    peer_median = peer.median(axis=1)
    peer_dispersion = peer.std(axis=1, ddof=0)

    result: list[Candidate] = []
    for symbol, frame in bars.items():
        positions = {ts: i for i, ts in enumerate(frame.index)}
        for event_ts in common:
            i = positions.get(event_ts)
            if i is None or i + 1 >= len(frame):
                continue
            event = frame.iloc[i]
            nxt = frame.iloc[i + 1]
            values = [
                event.get("ret"), event.get("abs_ret_threshold"), event.get("abs_ret_mean"), event.get("abs_ret_std"),
                event.get("quote_volume"), event.get("volume_median"), event.get("atr"), event.get("flow"),
                event.get("close_location"), event.get("spot_ret"), event.get("basis_z"), event.get("trend_2h"),
                event.get("trend_8h"), event.get("rv_4h"), nxt.get("ret"), nxt.get("flow"), nxt.get("quote_volume"),
                nxt.get("close_location"),
            ]
            if not all(math.isfinite(float(v)) for v in values):
                continue
            event_ret = float(event["ret"])
            if abs(event_ret) < float(event["abs_ret_threshold"]):
                continue
            if float(event["quote_volume"]) < float(event["volume_median"]):
                continue
            event_side = 1 if event_ret > 0.0 else -1
            midpoint = (float(event["high"]) + float(event["low"])) / 2.0
            next_close = float(nxt["close"])
            next_open = float(nxt["open"])
            family: str | None = None
            trade_side: int | None = None
            stop: float | None = None
            if (
                event_side * (next_close - float(event["close"])) > 0.0
                and ((event_side > 0 and float(nxt["low"]) > midpoint) or (event_side < 0 and float(nxt["high"]) < midpoint))
            ):
                family = "CONTINUATION"
                trade_side = event_side
                stop = midpoint
            elif event_side * (next_close - midpoint) < 0.0 and event_side * (next_close - next_open) < 0.0:
                family = "REVERSAL"
                trade_side = -event_side
                stop = float(event["high"]) if trade_side < 0 else float(event["low"])
            if family is None or trade_side is None or stop is None:
                continue
            entry = next_close
            geometry = _cost_aware_target(trade_side, entry, stop)
            if geometry is None:
                continue
            target, planned_loss, target_distance = geometry
            raw_risk = trade_side * (entry - stop) / entry
            ret_std = max(float(event["abs_ret_std"]), 1e-12)
            event_abs_z = (abs(event_ret) - float(event["abs_ret_mean"])) / ret_std
            event_range_atr = (float(event["high"]) - float(event["low"])) / max(float(event["atr"]), 1e-12)
            event_volume_ratio = float(event["quote_volume"]) / max(float(event["volume_median"]), 1e-12)
            next_volume_ratio = float(nxt["quote_volume"]) / max(float(event["volume_median"]), 1e-12)
            peer_breadth = float(np.sign(peer.loc[event_ts]).mean()) * trade_side
            dispersion = float(peer_dispersion.loc[event_ts])
            relative_strength = (event_ret - float(peer_median.loc[event_ts])) * trade_side
            hour = pd.Timestamp(nxt["minute"]).hour + pd.Timestamp(nxt["minute"]).minute / 60.0
            angle = 2.0 * math.pi * hour / 24.0
            features = (
                1.0 if family == "CONTINUATION" else 0.0,
                float(event_side),
                event_abs_z,
                event_range_atr,
                event_volume_ratio,
                float(event["flow"]) * event_side,
                (2.0 * float(event["close_location"]) - 1.0) * event_side,
                float(event["spot_ret"]) * event_side,
                float(event["basis_z"]) * event_side,
                float(event["trend_2h"]) * trade_side,
                float(event["trend_8h"]) * trade_side,
                float(event["rv_4h"]),
                float(nxt["ret"]) * event_side,
                float(nxt["flow"]) * trade_side,
                next_volume_ratio,
                (2.0 * float(nxt["close_location"]) - 1.0) * trade_side,
                peer_breadth,
                dispersion,
                relative_strength,
                raw_risk,
                target_distance,
                math.sin(angle),
                math.cos(angle),
            )
            if not all(math.isfinite(x) for x in features):
                continue
            result.append(
                Candidate(
                    symbol=symbol,
                    family=family,
                    side=trade_side,
                    event_ts=pd.Timestamp(event["minute"]),
                    entry_ts=pd.Timestamp(nxt["minute"]),
                    entry=entry,
                    stop=stop,
                    target=target,
                    planned_loss_rate=planned_loss,
                    raw_risk_rate=raw_risk,
                    target_distance_rate=target_distance,
                    features=features,
                )
            )
    return result


def score_candidate(candidate: Candidate, panel: pd.DataFrame) -> Scored:
    start_i = int(panel.index.searchsorted(candidate.entry_ts, side="right"))
    end_i = min(start_i + MAX_HOLD_MINUTES, len(panel))
    exit_ts, exit_price, reason = candidate.entry_ts, candidate.entry, "TIME"
    for i in range(start_i, end_i):
        row = panel.iloc[i]
        high, low, close = float(row["perp_high"]), float(row["perp_low"]), float(row["perp_close"])
        if candidate.side > 0:
            if low <= candidate.stop:
                exit_ts, exit_price, reason = pd.Timestamp(row["minute"]), candidate.stop, "STOP"
                break
            if high >= candidate.target:
                exit_ts, exit_price, reason = pd.Timestamp(row["minute"]), candidate.target, "TARGET"
                break
        else:
            if high >= candidate.stop:
                exit_ts, exit_price, reason = pd.Timestamp(row["minute"]), candidate.stop, "STOP"
                break
            if low <= candidate.target:
                exit_ts, exit_price, reason = pd.Timestamp(row["minute"]), candidate.target, "TARGET"
                break
        exit_ts, exit_price = pd.Timestamp(row["minute"]), close
    net_return = candidate.side * (exit_price / candidate.entry - 1.0) - COST_RATE
    return Scored(
        candidate=candidate,
        exit_ts=exit_ts,
        exit_reason=reason,
        exit_price=exit_price,
        net_return=net_return,
        net_r=net_return / candidate.planned_loss_rate,
    )


def apply_model(scored: list[Scored], model: LogisticModel) -> list[Scored]:
    if not scored:
        return []
    x = np.asarray([item.candidate.features for item in scored], dtype=float)
    probabilities = model.probability(x)
    return [
        Scored(
            candidate=item.candidate,
            exit_ts=item.exit_ts,
            exit_reason=item.exit_reason,
            exit_price=item.exit_price,
            net_return=item.net_return,
            net_r=item.net_r,
            probability=float(probability),
        )
        for item, probability in zip(scored, probabilities)
    ]


def single_slot(items: list[Scored], threshold: float = PROBABILITY_THRESHOLD) -> list[Scored]:
    eligible = [item for item in items if item.probability is not None and item.probability >= threshold]
    # At the same completed timestamp select the strongest probability.  Afterwards
    # a candidate cannot enter until the previous diagnostic path has exited.
    eligible.sort(key=lambda x: (x.candidate.entry_ts, -(x.probability or 0.0), x.candidate.symbol, x.candidate.family))
    chosen: list[Scored] = []
    occupied_until: pd.Timestamp | None = None
    i = 0
    while i < len(eligible):
        ts = eligible[i].candidate.entry_ts
        bucket = []
        while i < len(eligible) and eligible[i].candidate.entry_ts == ts:
            bucket.append(eligible[i]); i += 1
        if occupied_until is not None and ts <= occupied_until:
            continue
        winner = max(bucket, key=lambda x: x.probability or 0.0)
        chosen.append(winner)
        occupied_until = winner.exit_ts
    return chosen


def summarize(items: list[Scored]) -> dict[str, object]:
    if not items:
        return {"trades": 0, "wins": 0, "win_rate": 0.0, "mean_net_r": 0.0, "profit_factor_r": 0.0,
                "targets": 0, "stops": 0, "mean_probability": 0.0, "trades_per_calendar_day": 0.0}
    r = np.asarray([x.net_r for x in items], dtype=float)
    gains, losses = float(r[r > 0.0].sum()), float(-r[r < 0.0].sum())
    start = min(x.candidate.entry_ts for x in items).normalize()
    end = max(x.candidate.entry_ts for x in items).normalize()
    calendar_days = max(1, int((end - start).days) + 1)
    return {
        "trades": len(items),
        "wins": int((r > 0.0).sum()),
        "win_rate": float((r > 0.0).mean()),
        "mean_net_r": float(r.mean()),
        "median_net_r": float(np.median(r)),
        "profit_factor_r": gains / losses if losses > 0.0 else (999.0 if gains > 0.0 else 0.0),
        "targets": sum(x.exit_reason == "TARGET" for x in items),
        "stops": sum(x.exit_reason == "STOP" for x in items),
        "mean_probability": float(np.mean([x.probability for x in items if x.probability is not None])) if any(x.probability is not None for x in items) else 0.0,
        "trades_per_calendar_day": len(items) / calendar_days,
    }


def _period(items: list[Scored], start: pd.Timestamp, end: pd.Timestamp) -> list[Scored]:
    return [x for x in items if start <= x.candidate.entry_ts <= end]


def _model_payload(model: LogisticModel) -> dict[str, object]:
    return {
        "feature_names": list(FEATURE_NAMES),
        "mean": model.mean.tolist(),
        "scale": model.scale.tolist(),
        "beta": model.beta.tolist(),
        "l2_penalty": L2_PENALTY,
        "probability_threshold": PROBABILITY_THRESHOLD,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)

    minutes = {symbol: load_symbol(symbol, args.cache) for symbol in SYMBOLS}
    bars = {symbol: aggregate_fifteen(panel) for symbol, panel in minutes.items()}
    candidates = build_candidates(bars)
    scored = [score_candidate(c, minutes[c.symbol]) for c in candidates]

    train = [x for x in scored if x.candidate.entry_ts <= TRAIN_END]
    dev = _period(scored, DEV_START, DEV_END)
    x_train = np.asarray([x.candidate.features for x in train], dtype=float)
    y_train = np.asarray([1.0 if x.net_r > 0.0 else 0.0 for x in train], dtype=float)
    model_train = fit_logistic(x_train, y_train)
    dev_pred = apply_model(dev, model_train)
    dev_selected = single_slot(dev_pred)

    # Exact architecture is frozen. Refit once with all 2025 observations only.
    all_2025 = [x for x in scored if x.candidate.entry_ts.year == 2025]
    x_2025 = np.asarray([x.candidate.features for x in all_2025], dtype=float)
    y_2025 = np.asarray([1.0 if x.net_r > 0.0 else 0.0 for x in all_2025], dtype=float)
    frozen_model = fit_logistic(x_2025, y_2025)
    holdout = _period(scored, HOLDOUT_START, HOLDOUT_END)
    holdout_pred = apply_model(holdout, frozen_model)
    holdout_selected = single_slot(holdout_pred)

    result = {
        "study": "candidate-53-cost-aware-auction-meta-label",
        "cost_rate": COST_RATE,
        "target_net_r": TARGET_NET_R,
        "probability_threshold": PROBABILITY_THRESHOLD,
        "l2_penalty": L2_PENALTY,
        "candidate_count": len(candidates),
        "train_2025_jan_sep": summarize(train),
        "dev_2025_q4_all_primary": summarize(dev_pred),
        "dev_2025_q4_selected": summarize(dev_selected),
        "holdout_2026_jan_jul_all_primary": summarize(holdout_pred),
        "holdout_2026_jan_jul_selected": summarize(holdout_selected),
        "dev_by_family": {family: summarize([x for x in dev_selected if x.candidate.family == family]) for family in ("CONTINUATION", "REVERSAL")},
        "holdout_by_family": {family: summarize([x for x in holdout_selected if x.candidate.family == family]) for family in ("CONTINUATION", "REVERSAL")},
        "model_train": _model_payload(model_train),
        "model_frozen_2025": _model_payload(frozen_model),
    }
    (args.output / "summary.json").write_text(json.dumps(result, indent=2, sort_keys=True, allow_nan=False) + "\n")

    trades_payload = []
    for label, rows in (("DEV", dev_selected), ("HOLDOUT", holdout_selected)):
        for item in rows:
            trades_payload.append(
                {
                    "period": label,
                    "symbol": item.candidate.symbol,
                    "family": item.candidate.family,
                    "side": item.candidate.side,
                    "event_ts": item.candidate.event_ts.isoformat(),
                    "entry_ts": item.candidate.entry_ts.isoformat(),
                    "exit_ts": item.exit_ts.isoformat(),
                    "entry": item.candidate.entry,
                    "stop": item.candidate.stop,
                    "target": item.candidate.target,
                    "exit_reason": item.exit_reason,
                    "net_r": item.net_r,
                    "probability": item.probability,
                }
            )
    (args.output / "selected_trades.json").write_text(json.dumps(trades_payload, indent=2, sort_keys=True, allow_nan=False) + "\n")
    print(json.dumps(result, indent=2, sort_keys=True, allow_nan=False))


if __name__ == "__main__":
    main()
