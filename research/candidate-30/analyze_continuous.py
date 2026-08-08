#!/usr/bin/env python3
"""Screen pre-registered leverage-transition policies over one continuous span.

Candidate 30 separates evidence roles instead of stacking interchangeable
filters:

* prior state (strictly before the shock): OI expansion, crowd-side premium and
  account positioning;
* shock: a causal one-minute return/quote-volume extreme with same-minute taker
  imbalance in the shock direction;
* transition 15 completed minutes later: OI clearance or persistence, premium
  normalization or persistence, price response and later taker-flow control;
* execution proxy: next minute open, structural invalidation from the same
  shock/transition auction leg, and fixed 120-minute primary horizon.

The study is not an account backtest.  It is a multi-year mechanism gate which
can promote a fixed policy to NautilusTrader only when 2024 development, 2025
validation and untouched 2026 observations agree after 20 bps round-trip cost.
"""
from __future__ import annotations

import argparse
from datetime import date, timedelta
from hashlib import sha256
import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

INPUT_START = date(2023, 10, 1)
INPUT_END = date(2026, 8, 2)
EVALUATION_START = date(2024, 1, 1)
EVALUATION_END = date(2026, 7, 31)
HISTORY_DAYS = 30
MIN_HISTORY_DAYS = 14
CONFIRM_MINUTES = 15
REFRACTORY_MINUTES = 240
HORIZONS = (30, 60, 120, 240)
PRIMARY_HORIZON = 120
ROUND_TRIP_COST_RATE = 0.0020

POLICIES = (
    "LEVERAGE_CLEARANCE_REVERSAL",
    "CROWD_PERSISTENCE_CONTINUATION",
    "EXOGENOUS_DISCOVERY_CONTINUATION",
)


class StudyError(RuntimeError):
    """Raised when the continuous observational contract is violated."""


def sha256_file(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        while block := stream.read(1 << 20):
            digest.update(block)
    return digest.hexdigest()


def _load(input_root: Path, symbol: str) -> tuple[pd.DataFrame, list[dict[str, Any]]]:
    paths = sorted(input_root.rglob("month_manifest.json"))
    if not paths:
        raise StudyError(f"no month manifests under {input_root}")
    records: list[tuple[dict[str, Any], Path]] = []
    for path in paths:
        manifest = json.loads(path.read_text(encoding="utf-8"))
        if manifest.get("symbol") != symbol:
            raise StudyError(f"unexpected symbol in {path}: {manifest.get('symbol')}")
        records.append((manifest, path.parent))
    records.sort(key=lambda item: item[0]["core_start"])

    expected = INPUT_START
    frames: list[pd.DataFrame] = []
    evidence: list[dict[str, Any]] = []
    for manifest, directory in records:
        start = date.fromisoformat(manifest["core_start"])
        end = date.fromisoformat(manifest["core_end"])
        if start != expected:
            raise StudyError(f"input gap: expected {expected}, got {start}")
        expected = end + timedelta(days=1)
        path = directory / "minute_state.csv.gz"
        actual = sha256_file(path)
        claimed = manifest["files"]["minute_state.csv.gz"]["sha256"]
        if actual != claimed:
            raise StudyError(f"hash mismatch for {path}: {actual} != {claimed}")
        frame = pd.read_csv(path, compression="infer")
        if len(frame) != int(manifest["rows"]):
            raise StudyError(f"row count mismatch for {path}")
        frames.append(frame)
        evidence.append(
            {
                "core_start": manifest["core_start"],
                "core_end": manifest["core_end"],
                "rows": int(manifest["rows"]),
                "sha256": actual,
                "metrics_boundary_policy": manifest["metrics_boundary_policy"],
            },
        )
    if expected != INPUT_END + timedelta(days=1):
        raise StudyError(
            f"input ended at {expected - timedelta(days=1)}, expected {INPUT_END}",
        )

    frame = pd.concat(frames, ignore_index=True)
    frame["time"] = pd.to_datetime(frame["time"], utc=True, errors="raise")
    frame["observed_time_ns"] = pd.to_numeric(
        frame["observed_time_ns"],
        errors="raise",
    ).astype("int64")
    frame = frame.sort_values("observed_time_ns").reset_index(drop=True)
    expected_rows = ((INPUT_END - INPUT_START).days + 1) * 1_440
    if len(frame) != expected_rows:
        raise StudyError(f"assembled rows={len(frame)} expected={expected_rows}")
    if frame["observed_time_ns"].duplicated().any():
        raise StudyError("duplicate minute timestamps after assembly")
    expected_grid = pd.date_range(
        pd.Timestamp(INPUT_START, tz="UTC"),
        pd.Timestamp(INPUT_END + timedelta(days=1), tz="UTC") - pd.Timedelta(minutes=1),
        freq="1min",
    )
    actual_grid = pd.DatetimeIndex(frame["time"])
    if not actual_grid.equals(expected_grid):
        missing = expected_grid.difference(actual_grid)[:10]
        extra = actual_grid.difference(expected_grid)[:10]
        raise StudyError(
            f"assembled input is not continuous: missing={list(map(str, missing))} "
            f"extra={list(map(str, extra))}",
        )
    return frame, evidence


def _bool(values: pd.Series) -> np.ndarray:
    if values.dtype == bool:
        return values.to_numpy(dtype=np.bool_, copy=True)
    return (
        values.astype(str)
        .str.strip()
        .str.lower()
        .isin({"true", "1", "yes"})
        .to_numpy(dtype=np.bool_, copy=True)
    )


def _numeric(frame: pd.DataFrame, column: str) -> np.ndarray:
    return pd.to_numeric(frame[column], errors="coerce").to_numpy(dtype=np.float64)


def _safe_quantile(values: np.ndarray, quantile: float) -> float:
    finite = values[np.isfinite(values)]
    return float(np.quantile(finite, quantile)) if finite.size else float("nan")


def _daily_prior_thresholds(
    *,
    times: pd.DatetimeIndex,
    series: dict[str, np.ndarray],
) -> dict[str, np.ndarray]:
    """Map each UTC day to distributional cuts from prior days only."""
    result = {
        "shock_return_cut": np.full(len(times), np.nan),
        "shock_quote_cut": np.full(len(times), np.nan),
        "oi_expand_cut": np.full(len(times), np.nan),
        "premium_abs_cut": np.full(len(times), np.nan),
        "account_median": np.full(len(times), np.nan),
        "oi_clear_cut": np.full(len(times), np.nan),
    }
    values_ns = times.asi8
    first_day = times[0].floor("D")
    last_day = times[-1].floor("D")
    for day in pd.date_range(first_day, last_day, freq="1D", tz="UTC"):
        day_start_ns = int(day.value)
        next_day_ns = int((day + pd.Timedelta(days=1)).value)
        current_start = int(np.searchsorted(values_ns, day_start_ns, side="left"))
        current_end = int(np.searchsorted(values_ns, next_day_ns, side="left"))
        history_start_ns = int((day - pd.Timedelta(days=HISTORY_DAYS)).value)
        history_start = int(np.searchsorted(values_ns, history_start_ns, side="left"))
        history_end = current_start
        observed_days = (day.date() - times[history_start].date()).days if history_end > history_start else 0
        if observed_days < MIN_HISTORY_DAYS or history_end <= history_start:
            continue
        window = slice(history_start, history_end)
        result["shock_return_cut"][current_start:current_end] = _safe_quantile(
            series["abs_ret_1m_bps"][window],
            0.99,
        )
        result["shock_quote_cut"][current_start:current_end] = _safe_quantile(
            series["quote_volume"][window],
            0.95,
        )
        result["oi_expand_cut"][current_start:current_end] = max(
            0.0,
            _safe_quantile(series["oi_change_4h"][window], 0.75),
        )
        result["premium_abs_cut"][current_start:current_end] = _safe_quantile(
            np.abs(series["premium_index"][window]),
            0.85,
        )
        result["account_median"][current_start:current_end] = _safe_quantile(
            series["account_ratio"][window],
            0.50,
        )
        result["oi_clear_cut"][current_start:current_end] = min(
            0.0,
            _safe_quantile(series["oi_change_15m"][window], 0.10),
        )
    return result


def _state(
    *,
    direction: int,
    oi_change_4h: float,
    oi_expand_cut: float,
    premium: float,
    premium_abs_cut: float,
    account_ratio: float,
    account_median: float,
) -> tuple[str, dict[str, bool]]:
    oi_expanded = oi_change_4h >= oi_expand_cut and oi_change_4h > 0.0
    crowd_premium = (-direction) * premium >= premium_abs_cut
    crowd_accounts = (
        account_ratio >= account_median if direction < 0 else account_ratio <= account_median
    )
    score = int(oi_expanded) + int(crowd_premium) + int(crowd_accounts)
    if score == 3:
        label = "ENDOGENOUS_CROWD"
    elif score == 2:
        label = "POSITIONING_BUILDUP"
    elif score == 0:
        label = "EXOGENOUS_SHOCK"
    else:
        label = "MIXED_STATE"
    return label, {
        "oi_expanded": oi_expanded,
        "crowd_premium": crowd_premium,
        "crowd_accounts": crowd_accounts,
    }


def _policy(state: str, route: str) -> str:
    buildup = state in {"ENDOGENOUS_CROWD", "POSITIONING_BUILDUP"}
    if route == "REVERSAL" and buildup:
        return "LEVERAGE_CLEARANCE_REVERSAL"
    if route == "CONTINUATION" and buildup:
        return "CROWD_PERSISTENCE_CONTINUATION"
    if route == "CONTINUATION" and state == "EXOGENOUS_SHOCK":
        return "EXOGENOUS_DISCOVERY_CONTINUATION"
    return "NO_POLICY"


def _segment(timestamp: pd.Timestamp) -> str:
    if timestamp.year == 2024:
        return "DEVELOPMENT_2024"
    if timestamp.year == 2025:
        return "VALIDATION_2025"
    if timestamp.year == 2026:
        return "UNTOUCHED_2026"
    return "WARMUP"


def detect(frame: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, Any]]:
    times = pd.DatetimeIndex(frame["time"])
    close = _numeric(frame, "close")
    open_price = _numeric(frame, "open")
    high = _numeric(frame, "high")
    low = _numeric(frame, "low")
    quote = _numeric(frame, "quote_volume")
    taker_buy_quote = _numeric(frame, "taker_buy_quote_volume")
    oi = _numeric(frame, "sum_open_interest")
    premium = _numeric(frame, "premium_index")
    account = _numeric(frame, "count_long_short_ratio")
    top_position = _numeric(frame, "sum_toptrader_long_short_ratio")
    metrics_ready = _bool(frame["metrics_ready"])
    basis_ready = _bool(frame["basis_ready"])

    ret_1m_bps = np.full(len(frame), np.nan)
    ret_1m_bps[1:] = np.log(close[1:] / close[:-1]) * 10_000.0
    abs_ret = np.abs(ret_1m_bps)
    oi_change_15m = np.full(len(frame), np.nan)
    oi_change_4h = np.full(len(frame), np.nan)
    oi_change_15m[15:] = oi[15:] / oi[:-15] - 1.0
    oi_change_4h[240:] = oi[240:] / oi[:-240] - 1.0
    taker_imbalance_1m = np.divide(
        2.0 * taker_buy_quote,
        quote,
        out=np.full(len(frame), np.nan),
        where=quote > 0.0,
    ) - 1.0
    quote_series = pd.Series(quote)
    buy_series = pd.Series(taker_buy_quote)
    quote_5m = quote_series.rolling(5, min_periods=5).sum().to_numpy()
    buy_5m = buy_series.rolling(5, min_periods=5).sum().to_numpy()
    taker_imbalance_5m = np.divide(
        2.0 * buy_5m,
        quote_5m,
        out=np.full(len(frame), np.nan),
        where=quote_5m > 0.0,
    ) - 1.0

    series = {
        "abs_ret_1m_bps": abs_ret,
        "quote_volume": quote,
        "oi_change_4h": oi_change_4h,
        "premium_index": premium,
        "account_ratio": account,
        "oi_change_15m": oi_change_15m,
    }
    cuts = _daily_prior_thresholds(times=times, series=series)

    evaluation_open = pd.Timestamp(EVALUATION_START, tz="UTC")
    evaluation_close = pd.Timestamp(EVALUATION_END + timedelta(days=1), tz="UTC")
    finite_cut = np.isfinite(cuts["shock_return_cut"]) & np.isfinite(cuts["shock_quote_cut"])
    candidate = (
        finite_cut
        & (abs_ret >= cuts["shock_return_cut"])
        & (quote >= cuts["shock_quote_cut"])
        & metrics_ready
        & basis_ready
        & np.isfinite(taker_imbalance_1m)
    )
    candidate_indices = np.flatnonzero(candidate)

    episodes: list[dict[str, Any]] = []
    blocked = 0
    available_index = 0
    for shock_index in candidate_indices:
        shock_index = int(shock_index)
        if shock_index < available_index:
            blocked += 1
            continue
        shock_time = times[shock_index]
        if not evaluation_open <= shock_time < evaluation_close:
            continue
        prior_index = shock_index - 1
        confirm_index = shock_index + CONFIRM_MINUTES
        entry_index = confirm_index + 1
        if prior_index < 240 or entry_index + max(HORIZONS) >= len(frame):
            continue
        direction = 1 if ret_1m_bps[shock_index] > 0.0 else -1
        if direction * taker_imbalance_1m[shock_index] <= 0.0:
            continue
        required_values = (
            oi_change_4h[prior_index],
            cuts["oi_expand_cut"][prior_index],
            premium[prior_index],
            cuts["premium_abs_cut"][prior_index],
            account[prior_index],
            cuts["account_median"][prior_index],
            oi[prior_index],
            oi[confirm_index],
            premium[confirm_index],
            close[shock_index],
            close[confirm_index],
            taker_imbalance_5m[confirm_index],
            cuts["oi_clear_cut"][confirm_index],
        )
        if not all(math.isfinite(float(value)) for value in required_values):
            continue
        if not metrics_ready[prior_index] or not metrics_ready[confirm_index]:
            continue
        state, state_flags = _state(
            direction=direction,
            oi_change_4h=float(oi_change_4h[prior_index]),
            oi_expand_cut=float(cuts["oi_expand_cut"][prior_index]),
            premium=float(premium[prior_index]),
            premium_abs_cut=float(cuts["premium_abs_cut"][prior_index]),
            account_ratio=float(account[prior_index]),
            account_median=float(cuts["account_median"][prior_index]),
        )

        oi_delta = float(oi[confirm_index] / oi[prior_index] - 1.0)
        price_response_bps = float(
            direction * math.log(close[confirm_index] / close[shock_index]) * 10_000.0
        )
        prior_crowd_premium = (-direction) * float(premium[prior_index])
        confirm_crowd_premium = (-direction) * float(premium[confirm_index])
        premium_normalized = (
            confirm_crowd_premium <= 0.0
            or abs(float(premium[confirm_index]))
            <= 0.5 * max(abs(float(premium[prior_index])), float(cuts["premium_abs_cut"][prior_index]))
        )
        premium_persistent = not premium_normalized
        oi_cleared = (
            oi_delta <= float(cuts["oi_clear_cut"][confirm_index]) and oi_delta < 0.0
        )
        oi_persistent = oi_delta >= 0.0
        price_reversed = price_response_bps < 0.0
        price_extended = price_response_bps > 0.0
        flow_exhausted = direction * float(taker_imbalance_5m[confirm_index]) <= 0.0
        flow_persistent = direction * float(taker_imbalance_5m[confirm_index]) > 0.0

        if oi_cleared and premium_normalized and price_reversed and flow_exhausted:
            route = "REVERSAL"
            trade_side = -direction
        elif oi_persistent and premium_persistent and price_extended and flow_persistent:
            route = "CONTINUATION"
            trade_side = direction
        else:
            route = "UNRESOLVED"
            trade_side = 0
        policy = _policy(state, route)
        available_index = shock_index + REFRACTORY_MINUTES

        leg = slice(shock_index, confirm_index + 1)
        entry = float(open_price[entry_index])
        stop = (
            float(np.nanmin(low[leg]))
            if trade_side > 0
            else float(np.nanmax(high[leg]))
            if trade_side < 0
            else float("nan")
        )
        valid_geometry = (
            trade_side > 0 and stop < entry
        ) or (
            trade_side < 0 and stop > entry
        )
        risk_rate = abs(math.log(entry / stop)) if valid_geometry and stop > 0.0 else float("nan")
        record: dict[str, Any] = {
            "shock_time": shock_time.isoformat(),
            "segment": _segment(shock_time),
            "shock_direction": direction,
            "state": state,
            "route": route,
            "policy": policy,
            "trade_side": trade_side,
            "entry_time": times[entry_index].isoformat(),
            "entry_price": entry,
            "structural_stop": stop,
            "structural_risk_rate": risk_rate,
            "valid_geometry": bool(valid_geometry),
            "shock_return_bps": float(ret_1m_bps[shock_index]),
            "shock_return_cut_bps": float(cuts["shock_return_cut"][shock_index]),
            "shock_quote_volume": float(quote[shock_index]),
            "shock_quote_cut": float(cuts["shock_quote_cut"][shock_index]),
            "shock_taker_imbalance": float(taker_imbalance_1m[shock_index]),
            "prior_oi_change_4h": float(oi_change_4h[prior_index]),
            "prior_oi_expand_cut": float(cuts["oi_expand_cut"][prior_index]),
            "prior_premium": float(premium[prior_index]),
            "prior_premium_abs_cut": float(cuts["premium_abs_cut"][prior_index]),
            "prior_account_ratio": float(account[prior_index]),
            "prior_account_median": float(cuts["account_median"][prior_index]),
            "prior_top_position_ratio": float(top_position[prior_index]),
            "oi_delta_to_confirmation": oi_delta,
            "oi_clear_cut": float(cuts["oi_clear_cut"][confirm_index]),
            "confirmation_price_response_bps": price_response_bps,
            "confirmation_premium": float(premium[confirm_index]),
            "confirmation_taker_imbalance_5m": float(taker_imbalance_5m[confirm_index]),
            "oi_expanded": state_flags["oi_expanded"],
            "crowd_premium": state_flags["crowd_premium"],
            "crowd_accounts": state_flags["crowd_accounts"],
            "oi_cleared": oi_cleared,
            "premium_normalized": premium_normalized,
            "price_reversed": price_reversed,
            "flow_exhausted": flow_exhausted,
            "oi_persistent": oi_persistent,
            "premium_persistent": premium_persistent,
            "price_extended": price_extended,
            "flow_persistent": flow_persistent,
        }
        for horizon in HORIZONS:
            future_index = entry_index + horizon - 1
            gross = (
                trade_side * math.log(float(close[future_index]) / entry)
                if trade_side != 0 and valid_geometry
                else float("nan")
            )
            record[f"gross_return_{horizon}m"] = gross
            record[f"net_return_{horizon}m"] = (
                gross - ROUND_TRIP_COST_RATE if math.isfinite(gross) else float("nan")
            )
        path = slice(entry_index, entry_index + max(HORIZONS))
        if trade_side > 0 and valid_geometry:
            record["mfe_240m"] = float(np.nanmax(high[path]) / entry - 1.0)
            record["mae_240m"] = float(np.nanmin(low[path]) / entry - 1.0)
        elif trade_side < 0 and valid_geometry:
            record["mfe_240m"] = float(1.0 - np.nanmin(low[path]) / entry)
            record["mae_240m"] = float(1.0 - np.nanmax(high[path]) / entry)
        else:
            record["mfe_240m"] = float("nan")
            record["mae_240m"] = float("nan")
        episodes.append(record)

    result = pd.DataFrame(episodes)
    diagnostics = {
        "candidate_shock_minutes": int(candidate.sum()),
        "independent_episodes": int(len(result)),
        "blocked_by_refractory": int(blocked),
        "route_counts": (
            {str(k): int(v) for k, v in result["route"].value_counts().sort_index().items()}
            if not result.empty
            else {}
        ),
        "state_counts": (
            {str(k): int(v) for k, v in result["state"].value_counts().sort_index().items()}
            if not result.empty
            else {}
        ),
        "policy_counts": (
            {str(k): int(v) for k, v in result["policy"].value_counts().sort_index().items()}
            if not result.empty
            else {}
        ),
    }
    return result, diagnostics


def _summary(frame: pd.DataFrame) -> dict[str, Any]:
    column = f"net_return_{PRIMARY_HORIZON}m"
    values = pd.to_numeric(frame[column], errors="coerce").dropna()
    if values.empty:
        return {
            "episodes": 0,
            "positive_rate": 0.0,
            "mean_net_return": 0.0,
            "median_net_return": 0.0,
            "profit_factor": 0.0,
            "mean_net_r": 0.0,
            "median_net_r": 0.0,
            "largest_positive_share": 1.0,
        }
    positive = values[values > 0.0]
    negative = values[values < 0.0]
    risks = pd.to_numeric(frame.loc[values.index, "structural_risk_rate"], errors="coerce")
    net_r = values / risks.replace(0.0, np.nan)
    return {
        "episodes": int(len(values)),
        "positive_rate": float((values > 0.0).mean()),
        "mean_net_return": float(values.mean()),
        "median_net_return": float(values.median()),
        "profit_factor": (
            float(positive.sum() / abs(negative.sum()))
            if not positive.empty and not negative.empty
            else float("inf") if not positive.empty else 0.0
        ),
        "mean_net_r": float(net_r.mean()) if not net_r.dropna().empty else 0.0,
        "median_net_r": float(net_r.median()) if not net_r.dropna().empty else 0.0,
        "median_structural_risk": float(risks.median()),
        "median_mfe_240m": float(pd.to_numeric(frame.loc[values.index, "mfe_240m"], errors="coerce").median()),
        "median_mae_240m": float(pd.to_numeric(frame.loc[values.index, "mae_240m"], errors="coerce").median()),
        "largest_positive_share": (
            float(positive.max() / positive.sum()) if not positive.empty else 1.0
        ),
    }


def _policy_result(episodes: pd.DataFrame, policy: str) -> dict[str, Any]:
    selected = episodes[
        (episodes["policy"] == policy)
        & episodes["valid_geometry"].astype(bool)
    ].copy()
    total = _summary(selected)
    segments = {
        segment: _summary(selected[selected["segment"] == segment])
        for segment in ("DEVELOPMENT_2024", "VALIDATION_2025", "UNTOUCHED_2026")
    }
    checks = {
        "independent_episodes_at_least_30": total["episodes"] >= 30,
        "each_calendar_segment_at_least_5": all(
            item["episodes"] >= 5 for item in segments.values()
        ),
        "total_positive_rate_at_least_55pct": total["positive_rate"] >= 0.55,
        "total_mean_net_120m_positive": total["mean_net_return"] > 0.0,
        "total_median_net_120m_positive": total["median_net_return"] > 0.0,
        "total_profit_factor_at_least_1_25": total["profit_factor"] >= 1.25,
        "total_median_net_r_positive": total["median_net_r"] > 0.0,
        "largest_positive_event_share_at_most_35pct": total["largest_positive_share"] <= 0.35,
        "each_calendar_segment_mean_net_positive": all(
            item["mean_net_return"] > 0.0 for item in segments.values()
        ),
        "each_calendar_segment_positive_rate_at_least_45pct": all(
            item["positive_rate"] >= 0.45 for item in segments.values()
        ),
    }
    return {
        "policy": policy,
        "primary_horizon_minutes": PRIMARY_HORIZON,
        "round_trip_cost_rate": ROUND_TRIP_COST_RATE,
        "total": total,
        "segments": segments,
        "checks": checks,
        "promote": all(checks.values()),
    }


def run(input_root: Path, output: Path, symbol: str) -> dict[str, Any]:
    frame, evidence = _load(input_root.resolve(), symbol)
    episodes, diagnostics = detect(frame)
    output.mkdir(parents=True, exist_ok=True)
    episodes.to_csv(output / "episodes.csv", index=False)
    policies = {policy: _policy_result(episodes, policy) for policy in POLICIES}
    promoted = [policy for policy, result in policies.items() if result["promote"]]
    result = {
        "schema_version": 1,
        "candidate": "candidate-30-lightweight-leverage-state",
        "role": "multi-year causal mechanism screen; no account or PnL claim",
        "symbol": symbol,
        "input_start": INPUT_START.isoformat(),
        "input_end": INPUT_END.isoformat(),
        "evaluation_start": EVALUATION_START.isoformat(),
        "evaluation_end": EVALUATION_END.isoformat(),
        "evaluation_calendar_days": (EVALUATION_END - EVALUATION_START).days + 1,
        "continuous_observation_grid": True,
        "daily_threshold_policy": (
            f"prior {HISTORY_DAYS} UTC days only; minimum {MIN_HISTORY_DAYS} days"
        ),
        "confirmation_minutes": CONFIRM_MINUTES,
        "refractory_minutes": REFRACTORY_MINUTES,
        "primary_horizon_minutes": PRIMARY_HORIZON,
        "round_trip_cost_rate": ROUND_TRIP_COST_RATE,
        "diagnostics": diagnostics,
        "policies": policies,
        "promoted_policies": promoted,
        "promote": bool(promoted),
        "decision": (
            "PROMOTE_FIXED_POLICY_TO_CONTINUOUS_NAUTILUS_ACCOUNT"
            if promoted
            else "DISCARD_OR_REDESIGN_BEFORE_ACCOUNT_BACKTEST"
        ),
        "input_chunks": evidence,
    }
    (output / "study.json").write_text(
        json.dumps(result, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(result, indent=2, sort_keys=True, allow_nan=False))
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--symbol", default="BTCUSDT")
    args = parser.parse_args()
    run(args.input_root, args.output, args.symbol)


if __name__ == "__main__":
    main()
