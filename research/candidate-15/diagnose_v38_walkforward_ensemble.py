#!/usr/bin/env python3
"""Candidate 15 V38 purged walk-forward ridge ensemble screen."""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import date
import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

import diagnose_v28_adaptive_6h_trend as common

SYMBOLS = common.SYMBOLS
ROUTE = "PRIOR_ONLY_WALKFORWARD_ENSEMBLE"


def load_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise TypeError(f"{path} must contain an object")
    return value


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    tmp.replace(path)


def resample_thirty(frame: pd.DataFrame) -> pd.DataFrame:
    grouped = frame.resample("30min", closed="right", label="right", origin="start_day")
    out = pd.DataFrame(index=grouped.size().index)
    out["count"] = grouped.size()
    out["open"] = grouped["open"].first()
    out["high"] = grouped["high"].max()
    out["low"] = grouped["low"].min()
    out["close"] = grouped["close"].last()
    out["quote_volume"] = grouped["quote_volume"].sum()
    out["taker_buy_quote_volume"] = grouped["taker_buy_quote_volume"].sum()
    return out[out["count"] == 6].copy()


def own_features(symbol: str, five: pd.DataFrame, rules: dict[str, Any]) -> pd.DataFrame:
    frame = resample_thirty(five)
    close = frame["close"].astype(float)
    previous = close.shift(1)
    log_return = np.log(close / previous.replace(0.0, np.nan))
    tr = pd.concat([
        frame["high"] - frame["low"],
        (frame["high"] - previous).abs(),
        (frame["low"] - previous).abs(),
    ], axis=1).max(axis=1)
    atr = tr.rolling(int(rules["atr_lookback_bars"]), min_periods=int(rules["atr_lookback_bars"])).mean()
    vol_short = log_return.rolling(int(rules["volatility_short_bars"]), min_periods=int(rules["volatility_short_bars"])).std(ddof=0)
    vol_long = log_return.rolling(int(rules["volatility_long_bars"]), min_periods=int(rules["volatility_long_bars"])).std(ddof=0)
    volume_median = frame["quote_volume"].shift(1).rolling(
        int(rules["volume_median_lookback_bars"]),
        min_periods=max(96, int(rules["volume_median_lookback_bars"]) // 2),
    ).median()
    result = frame.copy()
    result["symbol"] = symbol
    result["event_ts"] = result.index
    result["log_return_1"] = log_return
    result["atr_30m"] = atr
    result["range_atr"] = (frame["high"] - frame["low"]) / atr.replace(0.0, np.nan)
    result["body_fraction"] = (frame["close"] - frame["open"]).abs() / (frame["high"] - frame["low"]).replace(0.0, np.nan)
    result["body_direction"] = np.sign(frame["close"] - frame["open"])
    result["taker_pressure"] = (2.0 * frame["taker_buy_quote_volume"] / frame["quote_volume"].replace(0.0, np.nan) - 1.0).clip(-1.0, 1.0)
    result["volume_ratio"] = frame["quote_volume"] / volume_median.replace(0.0, np.nan)
    result["vol_short"] = vol_short
    result["vol_long"] = vol_long
    result["vol_ratio"] = vol_short / vol_long.replace(0.0, np.nan)
    for horizon in rules["feature_return_horizons_bars"]:
        h = int(horizon)
        r = np.log(close / close.shift(h))
        result[f"ret_{h}"] = r
        result[f"ret_norm_{h}"] = r / (vol_long * math.sqrt(h)).replace(0.0, np.nan)
    forward = np.log(close.shift(-int(rules["prediction_horizon_bars"])) / close)
    result["label"] = forward
    result["label_end"] = result.index + pd.Timedelta(minutes=30 * int(rules["prediction_horizon_bars"]))
    return result.replace([np.inf, -np.inf], np.nan)


def panel_features(frames: dict[str, pd.DataFrame], rules: dict[str, Any]) -> tuple[pd.DataFrame, list[str]]:
    returns_by_h: dict[int, pd.DataFrame] = {}
    for horizon in rules["feature_return_horizons_bars"]:
        h = int(horizon)
        returns_by_h[h] = pd.DataFrame({symbol: frame[f"ret_{h}"] for symbol, frame in frames.items()})
    rows: list[pd.DataFrame] = []
    feature_names: list[str] = []
    base = ["range_atr", "body_fraction", "taker_pressure", "volume_ratio", "vol_short", "vol_long", "vol_ratio"]
    feature_names.extend(base)
    for h in rules["feature_return_horizons_bars"]:
        feature_names.extend([f"ret_{int(h)}", f"ret_norm_{int(h)}", f"cross_median_{int(h)}", f"residual_{int(h)}", f"rank_{int(h)}"])
    feature_names.extend(["hour_sin", "hour_cos", "dow_sin", "dow_cos"])
    feature_names.extend([f"symbol_{symbol}" for symbol in SYMBOLS])
    for symbol, frame in frames.items():
        current = frame.copy()
        for horizon in rules["feature_return_horizons_bars"]:
            h = int(horizon)
            table = returns_by_h[h]
            median = table.median(axis=1)
            current[f"cross_median_{h}"] = median
            current[f"residual_{h}"] = table[symbol] - median
            current[f"rank_{h}"] = table.rank(axis=1, pct=True)[symbol]
        hour = current.index.hour + current.index.minute / 60.0
        dow = current.index.dayofweek
        current["hour_sin"] = np.sin(2.0 * np.pi * hour / 24.0)
        current["hour_cos"] = np.cos(2.0 * np.pi * hour / 24.0)
        current["dow_sin"] = np.sin(2.0 * np.pi * dow / 7.0)
        current["dow_cos"] = np.cos(2.0 * np.pi * dow / 7.0)
        for name in SYMBOLS:
            current[f"symbol_{name}"] = 1.0 if name == symbol else 0.0
        rows.append(current.reset_index(drop=True))
    panel = pd.concat(rows, ignore_index=True).sort_values(["event_ts", "symbol"], kind="stable")
    return panel.reset_index(drop=True), feature_names


def fit_ridge(train: pd.DataFrame, features: list[str], alpha: float) -> dict[str, Any]:
    x = train[features].to_numpy(dtype=float)
    y = train["label"].to_numpy(dtype=float)
    mean = np.nanmean(x, axis=0)
    std = np.nanstd(x, axis=0)
    std[~np.isfinite(std) | (std < 1e-12)] = 1.0
    xs = (x - mean) / std
    design = np.column_stack([np.ones(len(xs)), xs])
    penalty = np.eye(design.shape[1]) * alpha
    penalty[0, 0] = 0.0
    beta = np.linalg.solve(design.T @ design + penalty, design.T @ y)
    return {"mean": mean, "std": std, "beta": beta}


def predict(model: dict[str, Any], frame: pd.DataFrame, features: list[str]) -> np.ndarray:
    x = frame[features].to_numpy(dtype=float)
    xs = (x - model["mean"]) / model["std"]
    return np.column_stack([np.ones(len(xs)), xs]) @ model["beta"]


def monthly_starts(start: pd.Timestamp, end: pd.Timestamp) -> list[pd.Timestamp]:
    return list(pd.date_range(start=start.normalize().replace(day=1), end=end, freq="MS", tz="UTC"))


def model_for_window(train: pd.DataFrame, month_start: pd.Timestamp, days: int, features: list[str], rules: dict[str, Any]) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    lower = month_start - pd.Timedelta(days=days)
    sample = train[(train["event_ts"] >= lower) & (train["label_end"] < month_start)].dropna(subset=features + ["label"])
    validation_start = month_start - pd.Timedelta(days=int(rules["validation_days"]))
    fit = sample[sample["event_ts"] < validation_start]
    validation = sample[sample["event_ts"] >= validation_start]
    min_fit_rows = int(rules["minimum_fit_days"]) * 48 * len(SYMBOLS) // 2
    if len(fit.index) < min_fit_rows or len(validation.index) < int(rules["minimum_validation_trades"]):
        return None, {"active": False, "reason": "INSUFFICIENT_PRIOR_ROWS", "fit_rows": len(fit.index), "validation_rows": len(validation.index)}
    validation_model = fit_ridge(fit, features, float(rules["ridge_alpha"]))
    validation_pred = predict(validation_model, validation, features)
    threshold = max(
        float(rules["minimum_predicted_gross_bps"]) / 10_000.0,
        float(np.quantile(np.abs(validation_pred), float(rules["validation_prediction_quantile"]))),
    )
    mask = np.abs(validation_pred) >= threshold
    selected = validation.loc[mask].copy()
    selected_pred = validation_pred[mask]
    if len(selected.index):
        directional = np.sign(selected_pred) * selected["label"].to_numpy(dtype=float)
        net = directional - (float(rules["execution_round_trip_cost_bps"]) + float(rules["funding_and_unmodeled_impact_reserve_bps"])) / 10_000.0
        accuracy = float((np.sign(selected_pred) == np.sign(selected["label"].to_numpy(dtype=float))).mean())
        mean_net_bps = float(net.mean() * 10_000.0)
        residual_std = float(np.std(selected["label"].to_numpy(dtype=float) - selected_pred, ddof=0))
    else:
        accuracy, mean_net_bps, residual_std = 0.0, -math.inf, math.inf
    active = (
        len(selected.index) >= int(rules["minimum_validation_trades"])
        and mean_net_bps >= float(rules["minimum_validation_mean_net_bps"])
        and accuracy >= float(rules["minimum_validation_directional_accuracy"])
    )
    record = {"active": active, "fit_rows": len(fit.index), "validation_rows": len(validation.index), "validation_selected": len(selected.index), "validation_mean_net_bps": mean_net_bps, "validation_accuracy": accuracy, "prediction_threshold": threshold, "residual_std": residual_std}
    if not active:
        return None, record
    full_model = fit_ridge(sample, features, float(rules["ridge_alpha"]))
    return full_model, record


def walkforward_predictions(panel: pd.DataFrame, features: list[str], protocol: dict[str, Any]) -> tuple[pd.DataFrame, list[dict[str, Any]]]:
    rules = protocol["fixed_rules"]
    eval_start = pd.Timestamp(protocol["evaluation"]["development_start"], tz="UTC")
    eval_end = pd.Timestamp(protocol["data"]["end_exclusive"], tz="UTC")
    rows: list[pd.DataFrame] = []
    diagnostics: list[dict[str, Any]] = []
    starts = monthly_starts(eval_start, eval_end)
    for month_start in starts:
        month_end = min(month_start + pd.offsets.MonthBegin(1), eval_end)
        current = panel[(panel["event_ts"] >= month_start) & (panel["event_ts"] < month_end)].dropna(subset=features).copy()
        if current.empty:
            continue
        short_model, short_record = model_for_window(panel, month_start, int(rules["short_training_days"]), features, rules)
        long_model, long_record = model_for_window(panel, month_start, int(rules["long_training_days"]), features, rules)
        diagnostics.append({"month": month_start.isoformat(), "short": short_record, "long": long_record})
        if short_model is None or long_model is None:
            continue
        short_pred = predict(short_model, current, features)
        long_pred = predict(long_model, current, features)
        conservative = np.minimum(np.abs(short_pred), np.abs(long_pred))
        direction = np.where(np.sign(short_pred) == np.sign(long_pred), np.sign(short_pred), 0.0)
        threshold = max(
            float(short_record["prediction_threshold"]),
            float(long_record["prediction_threshold"]),
            float(rules["minimum_predicted_gross_bps"]) / 10_000.0,
        )
        uncertainty = float(rules["prediction_uncertainty_fraction"]) * max(float(short_record["residual_std"]), float(long_record["residual_std"]))
        mask = (direction != 0.0) & (conservative >= threshold + uncertainty)
        selected = current.loc[mask].copy()
        selected["short_prediction"] = short_pred[mask]
        selected["long_prediction"] = long_pred[mask]
        selected["conservative_prediction"] = conservative[mask]
        selected["direction_value"] = direction[mask]
        selected["prediction_threshold"] = threshold
        rows.append(selected)
    return (pd.concat(rows, ignore_index=True).sort_values(["event_ts", "symbol"], kind="stable") if rows else panel.iloc[0:0].copy()), diagnostics


def confirm_and_simulate(item: pd.Series, five: pd.DataFrame, thirty: pd.DataFrame, rules: dict[str, Any]) -> dict[str, Any] | None:
    event_ts = pd.Timestamp(item["event_ts"])
    direction = float(item["direction_value"])
    confirmation_end = event_ts + pd.Timedelta(minutes=int(rules["confirmation_minutes"]))
    confirmation = five[(five.index > event_ts) & (five.index <= confirmation_end)]
    if len(confirmation.index) != int(rules["confirmation_minutes"]) // 5:
        return None
    opening, closing = float(confirmation.iloc[0]["open"]), float(confirmation.iloc[-1]["close"])
    high, low = float(confirmation["high"].max()), float(confirmation["low"].min())
    body = abs(closing - opening) / max(high - low, 1e-12)
    pressure = 2.0 * float(confirmation["taker_buy_quote_volume"].sum()) / max(float(confirmation["quote_volume"].sum()), 1e-12) - 1.0
    confirm_return = closing / opening - 1.0
    if direction * confirm_return <= 0.0 or body < float(rules["minimum_confirmation_body_fraction"]) or direction * pressure < float(rules["minimum_directional_taker_pressure"]):
        return None
    entry = closing
    atr = float(item["atr_30m"])
    buffer = float(rules["initial_stop_atr_buffer"]) * atr
    stop = float(item["low"]) - buffer if direction > 0.0 else float(item["high"]) + buffer
    if direction * (entry - stop) <= 0.0:
        return None
    cap = confirmation_end + pd.Timedelta(minutes=int(rules["maximum_hold_minutes"]))
    path = five[(five.index > confirmation_end) & (five.index <= cap)]
    if path.empty:
        return None
    stop_current, best_close = stop, entry
    exit_ts = exit_price = None
    reason = ""
    updates = 0
    atr_series = thirty["atr_30m"]
    for timestamp, bar in path.iterrows():
        if direction > 0.0 and float(bar["low"]) <= stop_current:
            exit_ts, exit_price, reason = pd.Timestamp(timestamp), stop_current, "TRAILING_STOP"
            break
        if direction < 0.0 and float(bar["high"]) >= stop_current:
            exit_ts, exit_price, reason = pd.Timestamp(timestamp), stop_current, "TRAILING_STOP"
            break
        if timestamp.minute in (0, 30):
            c = float(bar["close"])
            best_close = max(best_close, c) if direction > 0.0 else min(best_close, c)
            current_atr = atr_series.get(timestamp, np.nan)
            if pd.isna(current_atr):
                current_atr = atr
            new_stop = max(stop_current, best_close - float(rules["trailing_stop_atr"]) * float(current_atr)) if direction > 0.0 else min(stop_current, best_close + float(rules["trailing_stop_atr"]) * float(current_atr))
            if abs(new_stop - stop_current) > 1e-12:
                stop_current = new_stop
                updates += 1
    if exit_ts is None:
        exit_ts = pd.Timestamp(path.index[-1])
        exit_price = float(path.iloc[-1]["close"])
        reason = "FOUR_HOUR_CAP"
    gross = direction * (float(exit_price) / entry - 1.0)
    cost = (float(rules["execution_round_trip_cost_bps"]) + float(rules["funding_and_unmodeled_impact_reserve_bps"])) / 10_000.0
    return {
        "event_ts": event_ts, "entry_ts": confirmation_end, "exit_ts": exit_ts,
        "symbol": str(item["symbol"]), "route": ROUTE,
        "direction": "LONG" if direction > 0.0 else "SHORT", "direction_value": direction,
        "short_prediction": float(item["short_prediction"]), "long_prediction": float(item["long_prediction"]),
        "conservative_prediction": float(item["conservative_prediction"]), "prediction_threshold": float(item["prediction_threshold"]),
        "confirmation_return": confirm_return, "confirmation_body_fraction": body, "confirmation_taker_pressure": pressure,
        "entry_price": entry, "initial_stop": stop, "final_stop": stop_current,
        "exit_price": float(exit_price), "exit_reason": reason, "trailing_updates": updates,
        "holding_minutes": (exit_ts - confirmation_end).total_seconds() / 60.0,
        "rank_score": float(item["conservative_prediction"]), "gross_return": gross, "net_return": gross - cost,
    }


def build_candidates(predictions: pd.DataFrame, five_frames: dict[str, pd.DataFrame], thirty_frames: dict[str, pd.DataFrame], rules: dict[str, Any]) -> pd.DataFrame:
    rows = []
    for _, item in predictions.iterrows():
        result = confirm_and_simulate(item, five_frames[str(item["symbol"])], thirty_frames[str(item["symbol"])], rules)
        if result is not None:
            rows.append(result)
    return pd.DataFrame(rows).sort_values(["entry_ts", "rank_score", "symbol"], ascending=[True, False, True], kind="stable") if rows else pd.DataFrame()


def arbitrate(frame: pd.DataFrame, rules: dict[str, Any]) -> tuple[pd.DataFrame, Counter[str]]:
    if frame.empty:
        return frame.copy(), Counter()
    selected, skips = [], Counter()
    free_at = pd.Timestamp.min.tz_localize("UTC")
    last_symbol: dict[str, pd.Timestamp] = {}
    cooldown = pd.Timedelta(minutes=int(rules["same_symbol_cooldown_minutes"]))
    for entry_ts, group in frame.groupby("entry_ts", sort=True):
        winner = group.iloc[0]
        skips["SAME_EVENT_LOSER"] += max(0, len(group.index) - 1)
        timestamp, symbol = pd.Timestamp(entry_ts), str(winner["symbol"])
        if timestamp < free_at:
            skips["GLOBAL_POSITION_OCCUPIED"] += 1
            continue
        if symbol in last_symbol and timestamp < last_symbol[symbol] + cooldown:
            skips["SAME_SYMBOL_COOLDOWN"] += 1
            continue
        selected.append(winner)
        free_at, last_symbol[symbol] = pd.Timestamp(winner["exit_ts"]), timestamp
    return (pd.DataFrame(selected).reset_index(drop=True) if selected else frame.iloc[0:0].copy()), skips


def t_stat(values: pd.Series) -> float | None:
    if len(values.index) < 2:
        return None
    std = float(values.std(ddof=1))
    return None if not math.isfinite(std) or std <= 0.0 else float(values.mean() / (std / math.sqrt(len(values.index))))


def payoff(values: pd.Series) -> float | None:
    wins, losses = values[values > 0.0], values[values < 0.0]
    return None if wins.empty or losses.empty else float(wins.mean() / abs(losses.mean()))


def summarize(frame: pd.DataFrame, start: str, end: str) -> dict[str, Any]:
    lower, upper = pd.Timestamp(start, tz="UTC"), pd.Timestamp(end, tz="UTC")
    sample = frame[(frame["entry_ts"] >= lower) & (frame["entry_ts"] < upper)].copy()
    days = int((upper - lower).total_seconds() // 86_400)
    if sample.empty:
        return {"start": start, "end_exclusive": end, "calendar_days": days, "trades": 0, "trades_per_day": 0.0, "mean_gross_bps": None, "mean_net_bps": None, "win_rate": None, "payoff_ratio": None, "net_t_stat": None, "positive_months": 0, "active_months": 0, "positive_month_share": 0.0, "symbol_counts": {}, "exit_reasons": {}}
    sample["month"] = sample["entry_ts"].dt.to_period("M").astype(str)
    monthly = sample.groupby("month")["net_return"].sum()
    return {"start": start, "end_exclusive": end, "calendar_days": days, "trades": len(sample.index), "trades_per_day": len(sample.index) / max(days, 1), "mean_gross_bps": float(sample["gross_return"].mean() * 10_000.0), "mean_net_bps": float(sample["net_return"].mean() * 10_000.0), "win_rate": float((sample["net_return"] > 0.0).mean()), "payoff_ratio": payoff(sample["net_return"]), "net_t_stat": t_stat(sample["net_return"]), "positive_months": int((monthly > 0.0).sum()), "active_months": len(monthly.index), "positive_month_share": float((monthly > 0.0).mean()), "symbol_counts": {str(k): int(v) for k, v in sample["symbol"].value_counts().items()}, "exit_reasons": {str(k): int(v) for k, v in sample["exit_reason"].value_counts().items()}}


def execute(protocol_path: Path, output: Path) -> dict[str, Any]:
    protocol = load_object(protocol_path)
    if protocol["schema"] != "candidate-15-v38-walkforward-ensemble-v1":
        raise RuntimeError("unexpected V38 protocol")
    output.mkdir(parents=True, exist_ok=True)
    paths, manifest = common.download_data(protocol, output)
    write_json(output / "data_manifest.json", {"schema": "candidate-15-v38-data-manifest-v1", "files": manifest})
    start, end = date.fromisoformat(protocol["data"]["start"]), date.fromisoformat(protocol["data"]["end_exclusive"])
    five_frames = {symbol: common.load_symbol(paths[symbol], start, end) for symbol in SYMBOLS}
    thirty_frames = {symbol: own_features(symbol, frame, protocol["fixed_rules"]) for symbol, frame in five_frames.items()}
    panel, features = panel_features(thirty_frames, protocol["fixed_rules"])
    predictions, diagnostics = walkforward_predictions(panel, features, protocol)
    candidates = build_candidates(predictions, five_frames, thirty_frames, protocol["fixed_rules"])
    selected, skips = arbitrate(candidates, protocol["fixed_rules"])
    write_json(output / "model_diagnostics.json", {"features": features, "months": diagnostics})
    predictions.to_csv(output / "all_model_predictions.csv", index=False)
    candidates.to_csv(output / "all_executable_candidates.csv", index=False)
    selected.to_csv(output / "selected_trades.csv", index=False)
    evaluation = protocol["evaluation"]
    summaries = {name: summarize(selected, evaluation[f"{name}_start"], evaluation[f"{name}_end_exclusive"]) for name in ("development", "stability", "july_confirmation", "latest_pulse")}
    development, stability, july, pulse = summaries["development"], summaries["stability"], summaries["july_confirmation"], summaries["latest_pulse"]
    gate = protocol["advance_gate"]
    total = sum(stability["symbol_counts"].values())
    max_symbol_share = max(stability["symbol_counts"].values(), default=0) / max(total, 1)
    checks = {
        "positive_development_mean_net": development["mean_net_bps"] is not None and development["mean_net_bps"] > 0.0,
        "positive_stability_mean_net": stability["mean_net_bps"] is not None and stability["mean_net_bps"] >= float(gate["minimum_stability_mean_net_bps"]),
        "stability_net_t_stat": stability["net_t_stat"] is not None and stability["net_t_stat"] >= float(gate["minimum_stability_net_t_stat"]),
        "stability_positive_month_share": stability["positive_month_share"] >= float(gate["minimum_stability_positive_month_share"]),
        "stability_frequency": stability["trades_per_day"] >= float(gate["minimum_stability_trades_per_calendar_day"]),
        "positive_july_confirmation_mean_net": july["mean_net_bps"] is not None and july["mean_net_bps"] > 0.0,
        "july_confirmation_trade_count": july["trades"] >= int(gate["minimum_july_confirmation_trades"]),
        "positive_latest_pulse_mean_net": pulse["mean_net_bps"] is not None and pulse["mean_net_bps"] >= float(gate["minimum_latest_pulse_mean_net_bps"]),
        "latest_pulse_trade_count": pulse["trades"] >= int(gate["minimum_latest_pulse_trades"]),
        "symbol_concentration": max_symbol_share <= float(gate["maximum_single_symbol_share"]),
    }
    positive_all = all(summaries[name]["mean_net_bps"] is not None and summaries[name]["mean_net_bps"] > 0.0 for name in summaries)
    advance = all(checks.values()) and positive_all
    classification = "V38_WALKFORWARD_ENSEMBLE_ADVANCE_TO_NAUTILUS" if advance else "V38_WALKFORWARD_ENSEMBLE_REJECTED_OR_UNDERPOWERED"
    decision = "The frozen ensemble survived every split; implement it online in NautilusTrader." if advance else "The frozen walk-forward ensemble did not survive every split. Do not tune windows, features, validation activation, confirmation or stops."
    summary = {"schema": "candidate-15-v38-summary-v1", "classification": classification, "advance_to_nautilus": advance, "model_prediction_rows": len(predictions.index), "executable_candidates": len(candidates.index), "selected_trades": len(selected.index), "arbitration_skips": dict(skips), **summaries, "advance_checks": checks, "positive_across_all_declared_splits": positive_all, "maximum_stability_symbol_share": max_symbol_share, "decision": decision}
    write_json(output / "summary.json", summary)
    lines = ["# Candidate 15 V38 — Prior-only walk-forward ensemble", "", f"**{classification}**", "", "Two fixed ridge models with 180-day and 365-day training windows are activated only by their own purged prior validation and must agree before a later fifteen-minute execution confirmation.", ""]
    for title, name in (("Development", "development"), ("Year-long stability", "stability"), ("July 2026 confirmation", "july_confirmation"), ("Latest August 1-7 pulse", "latest_pulse")):
        r = summaries[name]
        lines.extend([f"## {title}", f"- interval: `{r['start']} -> {r['end_exclusive']}`", f"- trades / day: `{r['trades']} / {r['trades_per_day']}`", f"- gross / net mean: `{r['mean_gross_bps']} / {r['mean_net_bps']}` bp", f"- win rate / payoff: `{r['win_rate']} / {r['payoff_ratio']}`", f"- net t-stat: `{r['net_t_stat']}`", f"- positive months: `{r['positive_months']} / {r['active_months']}`", f"- symbol counts: `{r['symbol_counts']}`", f"- exit reasons: `{r['exit_reasons']}`", ""])
    lines.extend(["## Advance checks", *[f"- {k}: `{v}`" for k, v in checks.items()], "", "## Decision", decision, "", "This is an economic mechanism screen. A pass still requires the same online model updates inside frozen NautilusTrader execution, current-NAV 3% sizing and one continuous account."])
    (output / "RESULT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2, sort_keys=True, default=str))
    return summary


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--protocol", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    execute(args.protocol.resolve(), args.output.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
