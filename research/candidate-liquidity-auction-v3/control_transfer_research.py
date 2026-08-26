#!/usr/bin/env python3
"""Episode-conditioned failed-auction control-transfer research.

The policy does not treat a sweep or reclaim as an entry. A public liquidity
boundary must first be swept and reclaimed, then produce an efficient inward
initiative, a real pullback, and renewed directional control. Direction is
estimated out of period from the causal destination-state census. Entry is the
next open after reacceleration, stop is the causal event/pullback invalidation,
and a one-risk-unit first-reaction target is used only when a pre-existing route
objective lies beyond it.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import ExtraTreesClassifier, HistGradientBoostingClassifier
from sklearn.impute import SimpleImputer
from sklearn.pipeline import make_pipeline

SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT")
TICKS = {"BTCUSDT": 0.1, "ETHUSDT": 0.01, "SOLUSDT": 0.001, "XRPUSDT": 0.0001}
RISK_FRACTION = 0.03
ENTRY_FEE = 0.0005
STOP_FEE = 0.0005
TARGET_FEE = 0.0002
ENTRY_SLIPPAGE_TICKS = 2
STOP_SLIPPAGE_TICKS = 2
MAX_HOLD_MINUTES = 240


def _period_from_dir(path: Path) -> str:
    name = path.name
    for token in ("dev-", "eval-"):
        pos = name.find(token)
        if pos >= 0:
            return name[pos:]
    raise ValueError(f"cannot infer period from {path}")


def read_universe(root: Path) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, dict[str, pd.DataFrame]]]:
    actions, states = [], []
    raw: dict[str, dict[str, pd.DataFrame]] = {}
    for directory in sorted(path for path in root.iterdir() if path.is_dir()):
        action_path = directory / "coherent_actions.csv"
        state_path = directory / "destination_states.csv"
        if not action_path.exists() or not state_path.exists():
            continue
        period = _period_from_dir(directory)
        a = pd.read_csv(action_path)
        s = pd.read_csv(state_path)
        a["period"] = period
        s["period"] = period
        actions.append(a)
        states.append(s)
        raw_path = directory / "chart" / "raw_universe_1m.csv.gz"
        if not raw_path.exists():
            matches = list(directory.glob("**/raw_universe_1m.csv.gz"))
            raw_path = matches[0] if matches else raw_path
        if raw_path.exists():
            frame = pd.read_csv(raw_path, parse_dates=["open_time_dt"])
            frame["open_time_dt"] = pd.to_datetime(frame["open_time_dt"], utc=True)
            raw[period] = {
                symbol: group.set_index("open_time_dt").sort_index().copy()
                for symbol, group in frame.groupby("symbol")
            }
    if not actions or not states:
        raise RuntimeError(f"no action/state universes under {root}")
    return pd.concat(actions, ignore_index=True), pd.concat(states, ignore_index=True), raw


def attach_external_raw(raw: dict[str, dict[str, pd.DataFrame]], raw_root: Path | None) -> None:
    if raw_root is None:
        return
    for directory in raw_root.iterdir():
        if not directory.is_dir():
            continue
        try:
            period = _period_from_dir(directory)
        except ValueError:
            continue
        if period in raw:
            continue
        path = directory / "raw_universe_1m.csv.gz"
        if not path.exists():
            continue
        frame = pd.read_csv(path, parse_dates=["open_time_dt"])
        frame["open_time_dt"] = pd.to_datetime(frame["open_time_dt"], utc=True)
        raw[period] = {
            symbol: group.set_index("open_time_dt").sort_index().copy()
            for symbol, group in frame.groupby("symbol")
        }


def _direction_features(frame: pd.DataFrame) -> list[str]:
    forbidden_tokens = (
        "destination_", "resolution_", "actual_", "fill_", "outcome", "net_r",
        "mfe", "mae", "order_terminal", "upper_price", "lower_price",
        "diagnostic_source_lower", "diagnostic_source_upper", "diagnostic_target",
        "diagnostic_event_extreme", "diagnostic_retest_extreme", "diagnostic_zone",
        "entry", "stop", "target", "gross_rr", "risk_bps", "time_ns", "_index",
        "post_cost", "reward_risk", "route_obstacle", "route_profile_target",
    )
    columns = []
    for column in frame.select_dtypes(include=[np.number, "bool"]).columns:
        low = str(column).lower()
        if any(token in low for token in forbidden_tokens):
            continue
        if column in {"period"}:
            continue
        columns.append(column)
    return sorted(columns)


def destination_probabilities(states: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, object]]:
    resolved = states[states["destination_label"].isin(["UPPER_FIRST", "LOWER_FIRST"])].copy()
    resolved = resolved.drop_duplicates(["period", "state_id"]).reset_index(drop=True)
    side_long = resolved["action_side"].astype(str).eq("LONG")
    y = ((side_long & resolved.destination_label.eq("UPPER_FIRST")) |
         (~side_long & resolved.destination_label.eq("LOWER_FIRST"))).astype(int).to_numpy()
    columns = _direction_features(resolved)
    X = resolved[columns].replace([np.inf, -np.inf], np.nan)
    dev_periods = sorted(resolved.loc[resolved.period.str.startswith("dev-"), "period"].unique())
    probability = np.full(len(resolved), np.nan)
    for period in dev_periods:
        train = resolved.period.str.startswith("dev-") & resolved.period.ne(period)
        test = resolved.period.eq(period)
        if train.sum() < 80 or test.sum() == 0 or np.unique(y[train]).size < 2:
            continue
        probability[test] = _fit_predict(X.loc[train], y[train], X.loc[test])
    train = resolved.period.str.startswith("dev-")
    test = resolved.period.str.startswith("eval-")
    if train.sum() >= 80 and test.any() and np.unique(y[train]).size == 2:
        probability[test] = _fit_predict(X.loc[train], y[train], X.loc[test])
    base = float(y[train].mean()) if train.any() else float(y.mean())
    probability = np.where(np.isfinite(probability), probability, base)
    resolved["direction_probability"] = np.clip(probability, 0.01, 0.99)
    return resolved, {
        "resolved_states": int(len(resolved)),
        "development_periods": dev_periods,
        "features": columns,
        "development_base_rate": base,
    }


def _fit_predict(x_train: pd.DataFrame, y_train: np.ndarray, x_test: pd.DataFrame) -> np.ndarray:
    extra = make_pipeline(
        SimpleImputer(strategy="median"),
        ExtraTreesClassifier(
            n_estimators=240,
            min_samples_leaf=12,
            max_features=0.55,
            class_weight="balanced",
            n_jobs=-1,
            random_state=8173,
        ),
    )
    hist = make_pipeline(
        SimpleImputer(strategy="median"),
        HistGradientBoostingClassifier(
            learning_rate=0.045,
            max_iter=140,
            max_leaf_nodes=23,
            min_samples_leaf=24,
            l2_regularization=3.0,
            random_state=27191,
        ),
    )
    extra.fit(x_train, y_train)
    hist.fit(x_train, y_train)
    return 0.5 * extra.predict_proba(x_test)[:, 1] + 0.5 * hist.predict_proba(x_test)[:, 1]


def add_common_state(raw: dict[str, dict[str, pd.DataFrame]]) -> dict[str, pd.DataFrame]:
    output = {}
    for period, symbols in raw.items():
        common_index = None
        for frame in symbols.values():
            common_index = frame.index if common_index is None else common_index.intersection(frame.index)
        returns = pd.DataFrame(index=common_index)
        for symbol in SYMBOLS:
            if symbol in symbols:
                returns[symbol] = np.log(symbols[symbol].loc[common_index, "close"]).diff()
        output[period] = pd.DataFrame({
            "factor_return": returns.mean(axis=1),
            "breadth": np.sign(returns).mean(axis=1),
        }, index=common_index)
    return output


def _prior_atr(frame: pd.DataFrame, index: int, window: int = 90) -> float:
    start = max(1, index - window)
    prior = frame.iloc[start:index]
    previous = frame.close.shift(1).iloc[start:index]
    tr = pd.concat([
        prior.high - prior.low,
        (prior.high - previous).abs(),
        (prior.low - previous).abs(),
    ], axis=1).max(axis=1)
    return float(tr.median())


def _source_side(row: pd.Series) -> str:
    branch = str(row.get("narrative_branch", ""))
    action = str(row.action_side)
    if "FAILED" in branch:
        return "LOW" if action == "LONG" else "HIGH"
    return "HIGH" if action == "LONG" else "LOW"


def _route_candidates(frame: pd.DataFrame, index: int, entry: float, side: str, state: pd.Series) -> list[tuple[float, float, str]]:
    sign = 1.0 if side == "LONG" else -1.0
    candidates: list[tuple[float, float, str]] = []
    for horizon in (30, 60, 120, 240, 480):
        history = frame.iloc[max(0, index - horizon):index]
        if len(history) < max(12, horizon // 3):
            continue
        price = float(history.high.max()) if side == "LONG" else float(history.low.min())
        distance = sign * (price - entry)
        if distance > 0:
            candidates.append((distance, price, f"PRIOR_{'HIGH' if side == 'LONG' else 'LOW'}_{horizon}"))
    day = frame.index[index].normalize()
    previous_day = frame[(frame.index >= day - pd.Timedelta(days=1)) & (frame.index < day)]
    if len(previous_day) >= 120:
        price = float(previous_day.high.max()) if side == "LONG" else float(previous_day.low.min())
        distance = sign * (price - entry)
        if distance > 0:
            candidates.append((distance, price, "PDH" if side == "LONG" else "PDL"))
    state_price = state.get("upper_price") if side == "LONG" else state.get("lower_price")
    if pd.notna(state_price):
        price = float(state_price)
        distance = sign * (price - entry)
        if distance > 0:
            candidates.append((distance, price, "ACTIVE_SEMANTIC_LIQUIDITY"))
    history = frame.iloc[max(0, index - 1440):index]
    if len(history) >= 180 and float(history.quote_volume.sum()) > 0:
        typical = ((history.high + history.low + history.close) / 3.0).to_numpy(float)
        weight = history.quote_volume.to_numpy(float)
        lo, hi = np.quantile(typical, [0.01, 0.99])
        if hi > lo:
            edges = np.linspace(lo, hi, 65)
            volume, _ = np.histogram(typical, bins=edges, weights=weight)
            positive = volume[volume > 0]
            if len(positive) >= 8:
                threshold = np.quantile(positive, 0.75)
                for bin_index, value in enumerate(volume):
                    left = volume[bin_index - 1] if bin_index else -np.inf
                    right = volume[bin_index + 1] if bin_index + 1 < len(volume) else -np.inf
                    if value < threshold or value < left or value < right:
                        continue
                    price = float(edges[bin_index]) if side == "LONG" else float(edges[bin_index + 1])
                    distance = sign * (price - entry)
                    if distance > 0:
                        candidates.append((distance, price, "CAUSAL_24H_VOLUME_NODE"))
    return sorted(candidates, key=lambda item: (item[0], item[2]))


@dataclass
class Label:
    outcome: str
    net_r: float
    exit_time: pd.Timestamp
    hold_minutes: int
    actual_target_r: float


def _label(frame: pd.DataFrame, entry_index: int, side: str, entry: float, stop: float, target: float, tick: float) -> Label:
    sign = 1.0 if side == "LONG" else -1.0
    actual_entry = float(frame.open.iloc[entry_index]) + sign * ENTRY_SLIPPAGE_TICKS * tick
    stop_fill = stop - sign * STOP_SLIPPAGE_TICKS * tick
    risk_price = abs(actual_entry - stop_fill)
    raw_stop = sign * (stop_fill - actual_entry) / risk_price - (ENTRY_FEE * actual_entry + STOP_FEE * stop_fill) / risk_price
    normalization = max(abs(raw_stop), 1e-12)
    raw_target = sign * (target - actual_entry) / risk_price - (ENTRY_FEE * actual_entry + TARGET_FEE * target) / risk_price
    target_r = raw_target / normalization
    end = min(len(frame) - 1, entry_index + MAX_HOLD_MINUTES - 1)
    for position in range(entry_index, end + 1):
        bar = frame.iloc[position]
        stop_hit = float(bar.low) <= stop if side == "LONG" else float(bar.high) >= stop
        target_hit = float(bar.high) >= target if side == "LONG" else float(bar.low) <= target
        hold = position - entry_index + 1
        if stop_hit:
            return Label("STOP_FIRST", -1.0, frame.index[position] + pd.Timedelta(minutes=1), hold, target_r)
        if target_hit:
            return Label("TARGET_FIRST", target_r, frame.index[position] + pd.Timedelta(minutes=1), hold, target_r)
    exit_price = float(frame.close.iloc[end]) - sign * STOP_SLIPPAGE_TICKS * tick
    raw_exit = sign * (exit_price - actual_entry) / risk_price - (ENTRY_FEE * actual_entry + STOP_FEE * exit_price) / risk_price
    return Label("TIME_EXIT", raw_exit / normalization, frame.index[end] + pd.Timedelta(minutes=1), end - entry_index + 1, target_r)


def detect_rejection(state: pd.Series, frame: pd.DataFrame, common: pd.DataFrame) -> dict[str, object] | None:
    if "FAILED_AUCTION_REVERSAL" not in str(state.get("narrative_branch", "")):
        return None
    side = str(state.action_side)
    sign = 1.0 if side == "LONG" else -1.0
    source_side = _source_side(state)
    outward = 1.0 if source_side == "HIGH" else -1.0
    event_ns = state.get("diagnostic_event_time_ns", state.get("emission_time_ns"))
    if pd.isna(event_ns):
        return None
    event_time = pd.to_datetime(int(event_ns), utc=True)
    i0 = int(frame.index.searchsorted(event_time))
    if i0 < 90 or i0 >= len(frame) - 15:
        return None
    atr = _prior_atr(frame, i0)
    tick = TICKS[str(state.symbol)]
    if not math.isfinite(atr) or atr <= 4 * tick:
        return None
    lower = float(state.diagnostic_source_lower)
    upper = float(state.diagnostic_source_upper)
    center = 0.5 * (lower + upper)
    end = min(len(frame) - 2, i0 + 90)
    reclaim = None
    for j in range(i0 + 1, min(end, i0 + 35) + 1):
        segment = frame.iloc[i0:j + 1]
        probe = (float(segment.high.max()) - upper) / atr if outward > 0 else (lower - float(segment.low.min())) / atr
        close = float(frame.close.iloc[j])
        inside = close < upper if outward > 0 else close > lower
        inward = -outward * (close - center) / atr
        if probe >= 0.12 and inside and inward >= 0.35:
            reclaim = j
            break
    if reclaim is None:
        return None
    peak = reclaim
    best = sign * float(frame.close.iloc[peak])
    pullback_start = None
    for j in range(reclaim + 1, min(end, reclaim + 18) + 1):
        value = sign * float(frame.close.iloc[j])
        if value > best:
            best, peak = value, j
        extreme = float(frame.close.iloc[peak])
        retracement = sign * (extreme - float(frame.close.iloc[j])) / atr
        total = sign * (extreme - float(frame.close.iloc[i0])) / atr
        if retracement >= 0.18 and retracement >= 0.22 * max(total, 1e-9):
            pullback_start = j
            break
    if pullback_start is None:
        return None
    pullback = pullback_start
    worst = sign * float(frame.close.iloc[pullback])
    confirm = None
    for j in range(pullback_start + 1, min(end, pullback_start + 18) + 1):
        value = sign * float(frame.close.iloc[j])
        if value < worst:
            worst, pullback = value, j
        close = float(frame.close.iloc[j])
        if (outward > 0 and close > upper + 0.20 * atr) or (outward < 0 and close < lower - 0.20 * atr):
            break
        if j - pullback < 2:
            continue
        reacceleration = sign * (close - float(frame.close.iloc[pullback])) / atr
        recent = frame.iloc[j - 2:j + 1]
        body = sign * float((recent.close - recent.open).sum()) / atr
        broke = close > float(frame.high.iloc[j - 2:j].max()) if sign > 0 else close < float(frame.low.iloc[j - 2:j].min())
        breadth = sign * float(common.breadth.iloc[max(i0, j - 4):j + 1].mean())
        if reacceleration >= 0.18 and body >= 0.10 and broke and breadth >= -0.25:
            confirm = j
            break
    if confirm is None:
        return None
    drive = frame.iloc[i0:peak + 1]
    move_atr = sign * (float(frame.close.iloc[peak]) - float(frame.close.iloc[i0])) / atr
    travel = float(np.abs(np.diff(frame.close.iloc[i0:peak + 1])).sum()) / atr if peak > i0 else 0.0
    efficiency = move_atr / max(travel, 1e-9)
    activity = float(drive.quote_volume.mean() / max(frame.quote_volume.iloc[max(0, i0 - 60):i0].median(), 1e-9))
    if move_atr < 0.50 or move_atr > 5.0 or efficiency < 0.60 or activity > 3.0:
        return None
    entry_index = confirm + 1
    entry = float(frame.open.iloc[entry_index]) + sign * ENTRY_SLIPPAGE_TICKS * tick
    noise = max(2 * tick, 0.22 * float(frame.true_range.iloc[max(0, i0 - 60):i0].median()))
    pullback_extreme = float(frame.low.iloc[max(peak, pullback - 1):confirm + 1].min()) if side == "LONG" else float(frame.high.iloc[max(peak, pullback - 1):confirm + 1].max())
    stop = pullback_extreme - noise if side == "LONG" else pullback_extreme + noise
    event_window = frame.iloc[i0:confirm + 1]
    event_extreme = float(event_window.low.min()) if side == "LONG" else float(event_window.high.max())
    event_stop = event_extreme - noise if side == "LONG" else event_extreme + noise
    if abs(entry - event_stop) <= 2.5 * abs(entry - stop):
        stop = event_stop
    if not (stop < entry if side == "LONG" else stop > entry):
        return None
    risk = abs(entry - stop)
    candidates = _route_candidates(frame, entry_index, entry, side, state)
    route = next((item for item in candidates if 1.0 <= item[0] / risk <= 3.5), None)
    if route is None:
        return None
    route_distance, route_price, route_kind = route
    target = entry + sign * risk
    label = _label(frame, entry_index, side, entry, stop, target, tick)
    return {
        "period": state.period,
        "state_id": state.state_id,
        "episode_id": state.episode_id,
        "symbol": state.symbol,
        "side": side,
        "event_time": event_time,
        "entry_time": frame.index[entry_index],
        "entry": entry,
        "stop": stop,
        "target": target,
        "route_objective": route_price,
        "route_kind": route_kind,
        "route_rr": route_distance / risk,
        "gross_rr": 1.0,
        "direction_probability": float(state.direction_probability),
        "move_atr": move_atr,
        "path_efficiency": efficiency,
        "activity_ratio": activity,
        "outcome": label.outcome,
        "net_r": label.net_r,
        "target_net_r": label.actual_target_r,
        "exit_time": label.exit_time,
        "hold_minutes": label.hold_minutes,
    }


def route_account(plans: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, object]]:
    plans = plans[plans.direction_probability >= 0.50].copy()
    plans = plans.sort_values(["entry_time", "direction_probability", "path_efficiency", "move_atr"], ascending=[True, False, False, False])
    chosen = []
    busy_until = pd.Timestamp.min.tz_localize("UTC")
    used: set[tuple[str, str]] = set()
    for entry_time, group in plans.groupby("entry_time", sort=True):
        entry_time = pd.Timestamp(entry_time)
        if entry_time < busy_until:
            continue
        available = group[~group.apply(lambda row: (str(row.period), str(row.episode_id)) in used, axis=1)]
        if available.empty:
            continue
        row = available.iloc[0]
        chosen.append(row)
        used.add((str(row.period), str(row.episode_id)))
        busy_until = pd.Timestamp(row.exit_time)
    trades = pd.DataFrame(chosen).reset_index(drop=True) if chosen else plans.iloc[0:0].copy()
    nav, peak, maximum_drawdown = 1.0, 1.0, 0.0
    nav_before, nav_after = [], []
    for result in trades.net_r.astype(float):
        nav_before.append(nav)
        nav *= max(1e-9, 1.0 + RISK_FRACTION * result)
        peak = max(peak, nav)
        maximum_drawdown = max(maximum_drawdown, 1.0 - nav / peak)
        nav_after.append(nav)
    trades["nav_before"] = nav_before
    trades["nav_after"] = nav_after
    wins = trades.outcome.eq("TARGET_FIRST")
    days = sum(8 for _ in trades.period.unique()) if len(trades) else 0
    summary = {
        "trades": int(len(trades)),
        "periods": int(trades.period.nunique()) if len(trades) else 0,
        "approximate_calendar_days": int(days),
        "trades_per_day": float(len(trades) / days) if days else 0.0,
        "target_first_rate": float(wins.mean()) if len(trades) else None,
        "mean_net_r": float(trades.net_r.mean()) if len(trades) else None,
        "median_hold_minutes": float(trades.hold_minutes.median()) if len(trades) else None,
        "mean_hold_minutes": float(trades.hold_minutes.mean()) if len(trades) else None,
        "ending_nav_multiplier": float(nav),
        "maximum_drawdown": float(maximum_drawdown),
        "by_period": {
            period: {
                "trades": int(len(group)),
                "target_first_rate": float(group.outcome.eq("TARGET_FIRST").mean()),
                "mean_net_r": float(group.net_r.mean()),
            }
            for period, group in trades.groupby("period")
        },
    }
    return trades, summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--raw-root", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    _, states, raw = read_universe(args.root)
    attach_external_raw(raw, args.raw_root)
    scored_states, direction_summary = destination_probabilities(states)
    common = add_common_state(raw)
    plans = []
    for _, state in scored_states.iterrows():
        period, symbol = str(state.period), str(state.symbol)
        if period not in raw or symbol not in raw[period]:
            continue
        plan = detect_rejection(state, raw[period][symbol], common[period])
        if plan is not None:
            plans.append(plan)
    plan_frame = pd.DataFrame(plans)
    trades, account = route_account(plan_frame) if not plan_frame.empty else (plan_frame, {})
    plan_frame.to_csv(args.output / "control_transfer_plans.csv", index=False)
    trades.to_csv(args.output / "account_trades.csv", index=False)
    summary = {
        "policy": "failed auction -> efficient inward initiative -> real pullback -> reacceleration -> next-open entry -> causal invalidation -> first one-R route capture before pre-existing objective",
        "direction": direction_summary,
        "candidate_plans": int(len(plan_frame)),
        "account": account,
        "risk_fraction": RISK_FRACTION,
        "single_global_position": True,
    }
    (args.output / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
