#!/usr/bin/env python3
"""One-account router over complementary causal plan architectures.

The three plan sources solve different parts of the trading problem:

* rich action plans provide broad scenario coverage and detailed market state;
* local-RR plans provide cleaner structural completion targets;
* hybrid plans require both local geometry and rich causal context.

They are not summed as separate backtests.  Each plan is re-calibrated with only
mature earlier development outcomes from its architecture/mechanism, duplicate
market episodes are clustered, and one global account chooses a single order.
"""
from __future__ import annotations

import argparse
import itertools
import json
import math
from pathlib import Path
import re
from typing import Any, Sequence

import numpy as np
import pandas as pd

RISK = 0.03
EPS = 1e-12
NS_MINUTE = 60_000_000_000
RESOLVED = {
    "TARGET_FIRST", "STOP_FIRST", "AMBIGUOUS_SAME_MINUTE",
    "AMBIGUOUS_FILL_BARRIER_SAME_MINUTE",
}
ARCHITECTURES = ("RICH_ACTION", "LOCAL_RR", "HYBRID_RR")


def num(frame: pd.DataFrame, name: str, default: float = 0.0) -> pd.Series:
    if name not in frame:
        return pd.Series(default, index=frame.index, dtype=float)
    return pd.to_numeric(frame[name], errors="coerce").fillna(default)


def txt(frame: pd.DataFrame, name: str, default: str = "") -> pd.Series:
    if name not in frame:
        return pd.Series(default, index=frame.index, dtype=object)
    return frame[name].fillna(default).astype(str)


def truth(series: pd.Series) -> pd.Series:
    if pd.api.types.is_bool_dtype(series):
        return series.fillna(False)
    return series.fillna(False).astype(str).str.lower().isin({"true", "1", "yes"})


def logit(value: np.ndarray | pd.Series | float) -> np.ndarray:
    p = np.clip(np.asarray(value, dtype=float), 1e-5, 1.0 - 1e-5)
    return np.log(p / (1.0 - p))


def sigmoid(value: np.ndarray | pd.Series | float) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-np.clip(np.asarray(value, dtype=float), -30.0, 30.0)))


def find_scored(root: Path, preferred: str) -> Path:
    exact = sorted(root.rglob(preferred))
    if exact:
        return exact[0]
    candidates = sorted(root.rglob("scored*.csv.gz"))
    if not candidates:
        raise FileNotFoundError(f"No scored action table below {root}")
    return candidates[0]


def standardize(path: Path, architecture: str) -> pd.DataFrame:
    frame = pd.read_csv(path, low_memory=False)
    frame = frame.copy()
    frame["architecture"] = architecture
    frame["order_time_ns"] = pd.to_numeric(frame.order_time_ns, errors="coerce")
    frame = frame[frame.order_time_ns.notna()].copy()
    frame["order_time_ns"] = frame.order_time_ns.astype(np.int64)
    frame["order_time"] = pd.to_datetime(frame.order_time_ns, unit="ns", utc=True)
    frame["role"] = txt(frame, "role", "UNKNOWN")
    frame["period"] = txt(frame, "period", "UNKNOWN")
    frame["outcome"] = txt(frame, "outcome").str.upper()
    frame["resolved"] = frame.outcome.isin(RESOLVED)
    frame["win"] = frame.outcome.eq("TARGET_FIRST")
    frame["net_r_num"] = pd.to_numeric(
        frame["net_r_num"] if "net_r_num" in frame else frame.get("net_r"),
        errors="coerce",
    )
    frame["fill_time_ns_num"] = pd.to_numeric(frame.get("fill_time_ns"), errors="coerce")
    frame["terminal_ns"] = pd.to_numeric(
        frame.get("resolution_time_ns", frame.get("order_terminal_time_ns")),
        errors="coerce",
    )
    if "order_terminal_time_ns" in frame:
        frame["terminal_ns"] = frame.terminal_ns.fillna(
            pd.to_numeric(frame.order_terminal_time_ns, errors="coerce")
        )
    frame["terminal_ns"] = frame.terminal_ns.fillna(frame.order_time_ns)

    native = pd.to_numeric(frame.get("p_target_conservative"), errors="coerce")
    if native.isna().all() and "p_target_if_filled" in frame:
        native = pd.to_numeric(frame.p_target_if_filled, errors="coerce")
    frame["native_target_probability"] = native
    fill = pd.to_numeric(
        frame.get("p_fill_conservative", frame.get("p_fill")), errors="coerce"
    )
    frame["native_fill_probability"] = fill
    frame["native_expected_log_growth"] = pd.to_numeric(
        frame.get("expected_log_growth"), errors="coerce"
    )
    if "evidence_supported" in frame:
        eligible = truth(frame.evidence_supported)
    elif "policy_eligible" in frame:
        eligible = truth(frame.policy_eligible)
    else:
        eligible = frame.native_expected_log_growth.gt(0.0)
    frame["native_eligible"] = eligible

    family = txt(frame, "scenario_family", txt(frame, "family", "UNKNOWN"))
    phase = txt(frame, "auction_phase", "UNKNOWN").str.upper()
    geometry = txt(frame, "geometry_class", txt(frame, "entry_geometry", "UNKNOWN")).str.upper()
    route = txt(frame, "route_class", txt(frame, "route_kind", "UNKNOWN")).str.upper()
    base_family = txt(frame, "family", "UNKNOWN").str.upper()
    first_return = (
        phase.str.contains("FIRST_RETEST|MITIGATION|DEEP_RETEST", regex=True)
        | geometry.str.contains("OVERLAP|SOURCE|RETEST|MITIGATION", regex=True)
    )
    inferred = np.select(
        [
            base_family.eq("ACCEPTED_AUCTION_CONTINUATION") & first_return,
            base_family.eq("INITIATIVE_MITIGATION_CONTINUATION") & first_return,
            base_family.eq("FAILED_AUCTION_REVERSAL") & first_return,
        ],
        ["ACCEPTED_FIRST_RETEST", "INITIATIVE_FIRST_MITIGATION", "LOCALLY_OWNED_RECLAIM"],
        default=family,
    )
    frame["mechanism"] = pd.Series(inferred, index=frame.index).astype(str)
    frame["geometry_class_meta"] = geometry
    frame["route_class_meta"] = np.select(
        [
            route.str.contains("LOCAL|NEAR|OBSTACLE", regex=True),
            route.str.contains("DYNAMIC|CHANNEL|TRENDLINE", regex=True),
            route.str.contains("DAY|PDH|PDL", regex=True),
        ],
        ["LOCAL", "DYNAMIC", "DAY"],
        default="STRUCTURAL",
    )
    gross = num(frame, "gross_rr", 0.0)
    frame["rr_band_meta"] = pd.cut(
        gross, [-np.inf, 1.35, 1.80, 2.50, 4.0, np.inf],
        labels=["1.00-1.35", "1.35-1.80", "1.80-2.50", "2.50-4.00", "4.00+"],
    ).astype(str)
    frame["target_net_r_meta"] = num(
        frame, "planned_target_net_r", num(frame, "target_net_r", 0.0)
    )
    frame["global_action_id"] = architecture + ":" + txt(frame, "action_id")
    return frame.replace([np.inf, -np.inf], np.nan).reset_index(drop=True)


def posterior_table(
    train: pd.DataFrame,
    keys: Sequence[str],
) -> dict[tuple[str, ...], tuple[int, float, float]]:
    output: dict[tuple[str, ...], tuple[int, float, float]] = {}
    valid = train[train.resolved & train.net_r_num.notna()].copy()
    if valid.empty:
        return output
    global_rate = (float(valid.win.sum()) + 6.0) / (len(valid) + 12.0)
    for raw_key, group in valid.groupby(list(keys), dropna=False, sort=False):
        key = raw_key if isinstance(raw_key, tuple) else (raw_key,)
        key = tuple(str(value) for value in key)
        total = int(len(group))
        wins = float(group.win.sum())
        alpha = wins + global_rate * 12.0
        beta = total - wins + (1.0 - global_rate) * 12.0
        mean = alpha / (alpha + beta)
        variance = alpha * beta / max((alpha + beta) ** 2 * (alpha + beta + 1.0), EPS)
        period_values: list[float] = []
        for _, part in group.groupby("period", sort=False):
            if len(part) < 3:
                continue
            pa = float(part.win.sum()) + global_rate * 6.0
            pb = len(part) - float(part.win.sum()) + (1.0 - global_rate) * 6.0
            period_values.append(pa / (pa + pb))
        floor = float(np.quantile(period_values, 0.20)) if period_values else global_rate
        stable = 0.72 * mean + 0.28 * floor
        output[key] = (total, stable, math.sqrt(max(variance, 0.0)))
    return output


def reliability_predictions(train: pd.DataFrame, test: pd.DataFrame) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    levels: tuple[tuple[str, ...], ...] = (
        ("architecture", "mechanism", "route_class_meta", "rr_band_meta"),
        ("architecture", "mechanism", "rr_band_meta"),
        ("architecture", "mechanism"),
        ("architecture",),
    )
    tables = [(keys, posterior_table(train, keys)) for keys in levels]
    valid = train[train.resolved & train.net_r_num.notna()]
    global_rate = (float(valid.win.sum()) + 6.0) / (len(valid) + 12.0) if len(valid) else 0.5
    means: list[float] = []
    stds: list[float] = []
    support: list[int] = []
    for _, row in test.iterrows():
        chosen: tuple[int, float, float] | None = None
        for keys, table in tables:
            key = tuple(str(row[key_name]) for key_name in keys)
            candidate = table.get(key)
            if candidate is not None and candidate[0] >= 12:
                chosen = candidate
                break
        if chosen is None:
            chosen = (int(len(valid)), global_rate, 0.15)
        support.append(chosen[0])
        means.append(chosen[1])
        stds.append(chosen[2])
    return np.asarray(means), np.asarray(stds), np.asarray(support)


def meta_score(frame: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, Any]]:
    out = frame.copy()
    out["meta_probability"] = np.nan
    out["meta_support"] = 0
    out["meta_expected_log_growth"] = np.nan
    out["meta_eligible"] = False
    diagnostics: dict[str, Any] = {}
    period_order = out.groupby("period")["order_time"].min().sort_values().index.tolist()
    for period in period_order:
        test_index = out.index[out.period.astype(str).eq(str(period))]
        if not len(test_index):
            continue
        start = out.loc[test_index, "order_time"].min()
        train = out[
            out.role.astype(str).eq("dev")
            & pd.to_datetime(out.terminal_ns, unit="ns", utc=True, errors="coerce").lt(start)
        ].copy()
        test = out.loc[test_index]
        reliability, reliability_std, support = reliability_predictions(train, test)
        native = pd.to_numeric(test.native_target_probability, errors="coerce").to_numpy(float)
        combined = sigmoid(0.64 * logit(native) + 0.36 * logit(reliability))
        conservative = np.clip(combined - 0.35 * reliability_std, 0.01, 0.99)
        target_r = pd.to_numeric(test.target_net_r_meta, errors="coerce").fillna(0.0).to_numpy(float)
        fill = pd.to_numeric(test.native_fill_probability, errors="coerce").fillna(0.0).to_numpy(float)
        win_log = np.log(np.maximum(EPS, 1.0 + RISK * target_r))
        loss_log = math.log(1.0 - RISK)
        growth = fill * (conservative * win_log + (1.0 - conservative) * loss_log)
        breakeven = np.where(win_log - loss_log > EPS, -loss_log / (win_log - loss_log), 1.0)
        eligible = (
            test.native_eligible.fillna(False).to_numpy(bool)
            & np.isfinite(native)
            & (support >= 12)
            & (conservative > 0.54)
            & (conservative - breakeven > 0.035)
            & (growth > 0.0)
            & (pd.to_numeric(test.gross_rr, errors="coerce").fillna(0.0).to_numpy(float) >= 1.0)
        )
        out.loc[test_index, "meta_probability"] = conservative
        out.loc[test_index, "meta_support"] = support
        out.loc[test_index, "meta_expected_log_growth"] = growth
        out.loc[test_index, "meta_eligible"] = eligible
        diagnostics[str(period)] = {
            "training_resolved_actions": int(len(train[train.resolved & train.net_r_num.notna()])),
            "test_actions": int(len(test)),
            "eligible_actions": int(np.sum(eligible)),
        }
    return out, diagnostics


def cluster_market(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.sort_values(["period", "order_time_ns", "global_action_id"]).copy()
    ids: dict[int, str] = {}
    for period, group in out.groupby("period", sort=True):
        last: dict[str, int] = {}
        current: dict[str, int] = {}
        for index, row in group.iterrows():
            side = str(row.get("side", "UNKNOWN"))
            now = int(row.order_time_ns)
            if side not in last or now - last[side] > 4 * NS_MINUTE:
                current[side] = current.get(side, 0) + 1
                last[side] = now
            ids[index] = f"{period}:{side}:MKT{current[side]}"
    out["market_episode_id"] = pd.Series(ids)
    return out


def route(frame: pd.DataFrame, architectures: set[str]) -> tuple[pd.DataFrame, pd.DataFrame]:
    candidates = frame[
        frame.meta_eligible.fillna(False)
        & frame.architecture.astype(str).isin(architectures)
    ].copy()
    if candidates.empty:
        return candidates, candidates
    candidates = cluster_market(candidates)
    candidates = candidates.sort_values(
        ["period", "order_time_ns", "meta_expected_log_growth", "meta_probability", "gross_rr", "global_action_id"],
        ascending=[True, True, False, False, True, True],
    )
    # A market episode may be represented by several architectures.  Keep the
    # strongest immutable plan before account arbitration.
    candidates = candidates.drop_duplicates(
        ["period", "market_episode_id"], keep="first"
    ).sort_values(
        ["period", "order_time_ns", "meta_expected_log_growth"],
        ascending=[True, True, False],
    )
    selected: list[pd.Series] = []
    for _, group in candidates.groupby("period", sort=True):
        active: pd.Series | None = None
        used: set[str] = set()
        for timestamp, simultaneous in group.groupby("order_time_ns", sort=True):
            timestamp = int(timestamp)
            pool = simultaneous[~simultaneous.market_episode_id.astype(str).isin(used)]
            if pool.empty:
                continue
            candidate = pool.iloc[0]
            if active is not None:
                terminal = int(finite(active.get("terminal_ns"), timestamp))
                fill = finite(active.get("fill_time_ns_num"), np.inf)
                if timestamp >= terminal:
                    selected.append(active)
                    used.add(str(active.market_episode_id))
                    active = None
                elif fill <= timestamp:
                    continue
                else:
                    stronger = float(candidate.meta_expected_log_growth) > float(active.meta_expected_log_growth) + EPS
                    independent = str(candidate.market_episode_id) != str(active.market_episode_id)
                    if stronger and independent:
                        used.add(str(active.market_episode_id))
                        active = candidate
                    continue
            if active is None:
                active = candidate
        if active is not None:
            selected.append(active)
    orders = pd.DataFrame(selected).reset_index(drop=True) if selected else candidates.iloc[:0]
    trades = orders[orders.resolved & orders.net_r_num.notna()].copy().reset_index(drop=True)
    return orders, trades


def metrics(frame: pd.DataFrame) -> dict[str, Any]:
    work = frame[frame.net_r_num.notna()].copy()
    nav = peak = 1.0
    drawdown = 0.0
    for value in work.sort_values(["order_time_ns", "global_action_id"]).net_r_num.astype(float):
        nav *= max(EPS, 1.0 + RISK * value)
        peak = max(peak, nav)
        drawdown = max(drawdown, 1.0 - nav / peak)
    days = 0
    for _, group in work.groupby("period", sort=False):
        if len(group):
            days += max(1, int(math.ceil((group.terminal_ns.max() - group.order_time_ns.min()) / (1440 * NS_MINUTE))))
    return {
        "closed_trades": int(len(work)),
        "calendar_days": int(days),
        "trades_per_day": float(len(work) / max(days, 1)),
        "target_first_rate": float(work.win.mean()) if len(work) else None,
        "mean_net_r": float(work.net_r_num.mean()) if len(work) else None,
        "mean_gross_rr": float(num(work, "gross_rr", np.nan).mean()) if len(work) else None,
        "ending_nav_multiplier": float(nav),
        "maximum_drawdown": float(drawdown),
    }


def grouped(frame: pd.DataFrame, key: str) -> list[dict[str, Any]]:
    if frame.empty or key not in frame:
        return []
    return [{key: str(value), **metrics(group)} for value, group in frame.groupby(key, dropna=False)]


def development_rank(trades: pd.DataFrame) -> tuple[float, ...]:
    rows = [metrics(group) for _, group in trades.groupby("period", sort=True) if len(group)]
    if len(rows) < 2:
        return (-1e9, -1e9, -1e9, -1e9)
    means = [float(row["mean_net_r"]) for row in rows]
    rates = [float(row["target_first_rate"]) for row in rows]
    overall = metrics(trades)
    return (
        float(np.quantile(means, 0.20)),
        float(np.median(means)),
        float(np.median(rates)),
        float(overall["trades_per_day"]),
    )


def run(rich_root: Path, local_root: Path, hybrid_root: Path, output: Path) -> dict[str, Any]:
    frames = [
        standardize(find_scored(rich_root, "scored_actions.csv.gz"), "RICH_ACTION"),
        standardize(find_scored(local_root, "scored_orders.csv.gz"), "LOCAL_RR"),
        standardize(find_scored(hybrid_root, "scored_actions.csv.gz"), "HYBRID_RR"),
    ]
    combined = pd.concat(frames, ignore_index=True, sort=False)
    combined, diagnostics = meta_score(combined)
    development = combined[combined.role.astype(str).eq("dev")]
    fresh = combined[combined.role.astype(str).eq("fresh")]

    subset_rows: list[dict[str, Any]] = []
    ranked: list[tuple[tuple[float, ...], set[str]]] = []
    for size in range(1, len(ARCHITECTURES) + 1):
        for combo in itertools.combinations(ARCHITECTURES, size):
            architectures = set(combo)
            _, trades = route(development, architectures)
            rank = development_rank(trades)
            subset_rows.append({
                "architectures": sorted(architectures),
                "development": metrics(trades),
                "development_by_period": grouped(trades, "period"),
                "rank": list(rank),
            })
            ranked.append((rank, architectures))
    ranked.sort(key=lambda item: item[0], reverse=True)
    selected_architectures = ranked[0][1] if ranked else set()
    dev_orders, dev_trades = route(development, selected_architectures)
    fresh_orders, fresh_trades = route(fresh, selected_architectures)

    summary = {
        "policy": "ML_K_ONE_ACCOUNT_INTEGRATED_ARCHITECTURES",
        "selection_uses_fresh_outcomes": False,
        "selected_architectures": sorted(selected_architectures),
        "risk_fraction": RISK,
        "one_global_pending_or_position_slot": True,
        "development": metrics(dev_trades),
        "final_fresh": metrics(fresh_trades),
        "development_by_period": grouped(dev_trades, "period"),
        "final_fresh_by_period": grouped(fresh_trades, "period"),
        "final_fresh_by_architecture": grouped(fresh_trades, "architecture"),
        "final_fresh_by_mechanism": grouped(fresh_trades, "mechanism"),
        "final_fresh_by_symbol": grouped(fresh_trades, "symbol"),
        "meta_model_diagnostics": diagnostics,
        "development_architecture_subsets": subset_rows,
    }
    output.mkdir(parents=True, exist_ok=True)
    combined.to_csv(output / "meta_scored_actions.csv.gz", index=False, compression="gzip")
    pd.concat([dev_orders, fresh_orders], ignore_index=True, sort=False).to_csv(output / "selected_orders.csv", index=False)
    pd.concat([dev_trades, fresh_trades], ignore_index=True, sort=False).to_csv(output / "closed_trades.csv", index=False)
    fresh_trades.to_csv(output / "final_fresh_closed_trades.csv", index=False)
    (output / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True, default=str))
    return summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rich-root", type=Path, required=True)
    parser.add_argument("--local-root", type=Path, required=True)
    parser.add_argument("--hybrid-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    run(args.rich_root, args.local_root, args.hybrid_root, args.output)


if __name__ == "__main__":
    main()
