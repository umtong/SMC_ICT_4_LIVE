#!/usr/bin/env python3
"""Causal first-response router with adaptive reachable-frontier choice.

The deterministic layer defines a coherent episode and admissible action.  The
model estimates only target-before-stop reachability from information available
at the decision time.  BTC, ETH, SOL and XRP then compete for one account slot.
"""
from __future__ import annotations

import argparse
import json
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence

import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, RobustScaler

RISK_FRACTION = 0.03
UNIVERSE = {"BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"}

LEAK_TOKENS = (
    "label",
    "target_first",
    "stop_first",
    "outcome",
    "result",
    "realized",
    "future",
    "forward",
    "mfe",
    "mae",
    "exit_",
    "close_time",
    "resolution",
    "holding",
    "duration",
    "pnl",
    "net_r",
    "gross_pnl",
    "fill_price",
    "exit_price",
    "stop_hit",
    "target_hit",
    "source_window_file",
)
ID_TOKENS = ("_id", "uuid", "hash", "key")
PRICE_TOKENS = ("entry_price", "stop_price", "target_price", "open", "high", "low", "close")
TIME_CANDIDATES = (
    "decision_time",
    "decision_ts",
    "signal_time",
    "confirmation_time",
    "plan_time",
    "event_time",
    "timestamp",
)
ENTRY_TIME_CANDIDATES = (
    "fill_time",
    "entry_time",
    "filled_time",
    "execution_time",
    "decision_time",
    "decision_ts",
)
EXIT_TIME_CANDIDATES = (
    "exit_time",
    "close_time",
    "resolution_time",
    "label_end_time",
    "outcome_time",
)
EPISODE_CANDIDATES = (
    "parent_episode_id",
    "causal_episode_id",
    "episode_id",
    "source_event_id",
    "event_id",
    "plan_id",
)
SYMBOL_CANDIDATES = ("symbol", "instrument", "instrument_id")
SIDE_CANDIDATES = ("side", "direction", "trade_side")
ROLE_CANDIDATES = ("role", "sample_role", "dataset_role")
PERIOD_CANDIDATES = ("period", "window", "sample_period")
TARGET_LABEL_CANDIDATES = (
    "target_first",
    "exact_target_first",
    "label_target_first",
    "target_before_stop",
    "won",
    "is_win",
)
FILL_LABEL_CANDIDATES = ("filled", "was_filled", "label_filled", "entry_filled")
NET_R_CANDIDATES = (
    "net_r",
    "realized_net_r",
    "exact_net_r",
    "pnl_r",
    "result_r",
    "trade_r",
)
WIN_R_CANDIDATES = (
    "planned_net_rr",
    "net_win_r",
    "gross_rr",
    "planned_gross_rr",
    "route_rr",
    "target_rr",
)
FAMILY_CANDIDATES = ("family", "scenario_family", "route_family")
PHASE_CANDIDATES = ("auction_phase", "phase", "episode_phase")
GEOMETRY_CANDIDATES = ("entry_geometry", "setup_kind", "route_kind", "narrative_branch")


@dataclass(frozen=True)
class Schema:
    decision_time: str
    entry_time: str
    exit_time: str
    episode: str
    symbol: str
    side: str | None
    role: str
    period: str
    target_label: str
    fill_label: str | None
    net_r: str | None
    win_r: str
    family: str | None
    phase: str | None
    geometry: tuple[str, ...]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--plans", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--risk", type=float, default=RISK_FRACTION)
    return parser.parse_args()


def first_existing(columns: Iterable[str], candidates: Sequence[str]) -> str | None:
    column_set = set(columns)
    for name in candidates:
        if name in column_set:
            return name
    lower = {str(name).lower(): str(name) for name in columns}
    for name in candidates:
        if name.lower() in lower:
            return lower[name.lower()]
    return None


def token_column(columns: Iterable[str], token_sets: Sequence[Sequence[str]]) -> str | None:
    ranked: list[tuple[int, int, str]] = []
    for column in columns:
        low = str(column).lower()
        for rank, tokens in enumerate(token_sets):
            if all(token in low for token in tokens):
                ranked.append((rank, len(low), str(column)))
                break
    return min(ranked)[2] if ranked else None


def require(name: str, value: str | None, columns: Sequence[str]) -> str:
    if value is None:
        raise KeyError(f"Could not resolve {name}; columns={list(columns)!r}")
    return value


def infer_schema(frame: pd.DataFrame) -> Schema:
    columns = list(map(str, frame.columns))
    decision = first_existing(columns, TIME_CANDIDATES) or token_column(
        columns, (("decision", "time"), ("signal", "time"), ("event", "time"))
    )
    entry = first_existing(columns, ENTRY_TIME_CANDIDATES) or decision
    exit_col = first_existing(columns, EXIT_TIME_CANDIDATES) or token_column(
        columns, (("exit", "time"), ("resolution", "time"), ("outcome", "time"))
    )
    episode = first_existing(columns, EPISODE_CANDIDATES) or token_column(
        columns, (("episode", "id"), ("event", "id"), ("plan", "id"))
    )
    symbol = first_existing(columns, SYMBOL_CANDIDATES) or token_column(
        columns, (("symbol",), ("instrument",))
    )
    side = first_existing(columns, SIDE_CANDIDATES)
    role = first_existing(columns, ROLE_CANDIDATES)
    period = first_existing(columns, PERIOD_CANDIDATES)
    target = first_existing(columns, TARGET_LABEL_CANDIDATES) or token_column(
        columns, (("target", "first"), ("target", "before", "stop"))
    )
    fill = first_existing(columns, FILL_LABEL_CANDIDATES) or token_column(
        columns, (("fill",), ("filled",))
    )
    net_r = first_existing(columns, NET_R_CANDIDATES) or token_column(
        columns, (("net", "r"), ("realized", "r"), ("pnl", "r"))
    )
    win_r = first_existing(columns, WIN_R_CANDIDATES) or token_column(
        columns, (("gross", "rr"), ("route", "rr"), ("target", "rr"))
    )
    family = first_existing(columns, FAMILY_CANDIDATES)
    phase = first_existing(columns, PHASE_CANDIDATES)
    geometry = tuple(name for name in GEOMETRY_CANDIDATES if name in columns)
    return Schema(
        decision_time=require("decision time", decision, columns),
        entry_time=require("entry time", entry, columns),
        exit_time=require("exit time", exit_col, columns),
        episode=require("episode id", episode, columns),
        symbol=require("symbol", symbol, columns),
        side=side,
        role=require("role", role, columns),
        period=require("period", period, columns),
        target_label=require("target-first label", target, columns),
        fill_label=fill,
        net_r=net_r,
        win_r=require("predeclared win R", win_r, columns),
        family=family,
        phase=phase,
        geometry=geometry,
    )


def to_bool(series: pd.Series) -> pd.Series:
    if pd.api.types.is_bool_dtype(series):
        return series.fillna(False).astype(bool)
    numeric = pd.to_numeric(series, errors="coerce")
    if numeric.notna().mean() > 0.8:
        return numeric.fillna(0.0).ne(0.0)
    truth = {"1", "true", "t", "yes", "y", "win", "target", "target_first", "filled"}
    return series.astype(str).str.strip().str.lower().isin(truth)


def parse_time(series: pd.Series) -> pd.Series:
    numeric = pd.to_numeric(series, errors="coerce")
    if numeric.notna().mean() > 0.9:
        median = float(numeric.dropna().abs().median()) if numeric.notna().any() else 0.0
        unit = "ns" if median > 1e17 else "us" if median > 1e14 else "ms" if median > 1e11 else "s"
        return pd.to_datetime(numeric, unit=unit, utc=True, errors="coerce")
    return pd.to_datetime(series, utc=True, errors="coerce")


def normalize_symbol(value: Any) -> str:
    text = str(value).upper().replace("-", "").replace("/", "").replace(".", "")
    for symbol in UNIVERSE:
        if symbol in text:
            return symbol
    return text


def text_stack(frame: pd.DataFrame, columns: Sequence[str | None]) -> pd.Series:
    present = [name for name in columns if name and name in frame.columns]
    if not present:
        return pd.Series("", index=frame.index, dtype="object")
    return frame[present].fillna("").astype(str).agg(" ".join, axis=1).str.upper()


def eligible_mask(frame: pd.DataFrame, schema: Schema) -> tuple[pd.Series, dict[str, int]]:
    descriptors = text_stack(frame, (schema.family, schema.phase, *schema.geometry))
    first_response = descriptors.str.contains(
        r"FIRST[_ -]?(?:RETEST|RETURN|TOUCH|MITIGATION)|RETEST[_ -]?FORMING",
        regex=True,
    )
    continuation = descriptors.str.contains(r"ACCEPT|CONTINUATION|EXPANSION", regex=True)
    reversal = descriptors.str.contains(r"FAIL|REVERS|RECLAIM|REJECTION|FAKE|TRAP", regex=True)

    acceptance = pd.to_numeric(frame.get("auction_acceptance_strength"), errors="coerce")
    failure = pd.to_numeric(frame.get("auction_failure_pressure"), errors="coerce")
    if acceptance is None or isinstance(acceptance, np.ndarray):
        acceptance = pd.Series(np.nan, index=frame.index)
    if failure is None or isinstance(failure, np.ndarray):
        failure = pd.Series(np.nan, index=frame.index)
    numeric_transfer = acceptance.notna() & failure.notna()
    continuation_control = continuation & first_response & (
        ~numeric_transfer | acceptance.ge(failure)
    )
    reversal_control = reversal & (
        first_response | (~numeric_transfer) | failure.gt(acceptance)
    )
    eligible = continuation_control | reversal_control
    if not bool(eligible.any()):
        raise RuntimeError(
            "No causal first-response plan was recognized; refusing to replace the policy with an all-plan fallback"
        )
    diagnostics = {
        "first_response": int(first_response.sum()),
        "continuation_control": int(continuation_control.sum()),
        "reversal_control": int(reversal_control.sum()),
        "eligible": int(eligible.sum()),
    }
    return eligible, diagnostics


def prepare(frame: pd.DataFrame, schema: Schema) -> pd.DataFrame:
    data = frame.copy()
    data["_decision_time"] = parse_time(data[schema.decision_time])
    data["_entry_time"] = parse_time(data[schema.entry_time])
    data["_exit_time"] = parse_time(data[schema.exit_time])
    data["_symbol"] = data[schema.symbol].map(normalize_symbol)
    data["_target_first"] = to_bool(data[schema.target_label])
    data["_filled"] = to_bool(data[schema.fill_label]) if schema.fill_label else True
    data["_planned_win_r"] = pd.to_numeric(data[schema.win_r], errors="coerce")
    if schema.net_r:
        data["_net_r"] = pd.to_numeric(data[schema.net_r], errors="coerce")
    else:
        data["_net_r"] = np.where(data["_target_first"], data["_planned_win_r"], -1.0)
    data["_period"] = data[schema.period].astype(str).str.lower()
    data["_role"] = data[schema.role].astype(str).str.lower()
    data["_episode"] = data[schema.episode].astype(str)
    if schema.side:
        data["_side"] = data[schema.side].astype(str).str.upper()
    else:
        data["_side"] = ""

    if "target_fraction" in data.columns:
        data["_target_fraction"] = pd.to_numeric(data["target_fraction"], errors="coerce")
    elif "route_target_fraction" in data.columns:
        data["_target_fraction"] = pd.to_numeric(data["route_target_fraction"], errors="coerce")
    else:
        extracted = data.get("source_window_file", "").astype(str).str.extract(
            r"fraction[-_/]?(\d{3})", expand=False
        )
        data["_target_fraction"] = pd.to_numeric(extracted, errors="coerce") / 100.0

    valid = (
        data["_filled"]
        & data["_symbol"].isin(UNIVERSE)
        & data["_decision_time"].notna()
        & data["_entry_time"].notna()
        & data["_exit_time"].notna()
        & data["_target_first"].notna()
        & data["_planned_win_r"].ge(1.0)
        & data["_net_r"].notna()
    )
    data = data.loc[valid].copy()
    data = data.loc[data["_exit_time"].ge(data["_entry_time"])].copy()
    eligible, diagnostics = eligible_mask(data, schema)
    data = data.loc[eligible].copy()
    data.attrs["eligibility_diagnostics"] = diagnostics
    return data.reset_index(drop=True)


def feature_columns(data: pd.DataFrame, schema: Schema) -> tuple[list[str], list[str], list[str]]:
    protected = {
        schema.decision_time,
        schema.entry_time,
        schema.exit_time,
        schema.episode,
        schema.symbol,
        schema.role,
        schema.period,
        schema.target_label,
        schema.win_r,
        *(name for name in (schema.side, schema.fill_label, schema.net_r) if name),
    }
    candidates: list[str] = []
    rejected: list[str] = []
    for column in map(str, data.columns):
        low = column.lower()
        if column in protected or column.startswith("_"):
            rejected.append(column)
            continue
        if any(token in low for token in LEAK_TOKENS + ID_TOKENS + PRICE_TOKENS):
            rejected.append(column)
            continue
        candidates.append(column)

    categorical: list[str] = []
    numeric: list[str] = []
    for column in candidates:
        series = data[column]
        if pd.api.types.is_numeric_dtype(series) or pd.to_numeric(series, errors="coerce").notna().mean() > 0.92:
            numeric.append(column)
        elif series.nunique(dropna=True) <= 80:
            categorical.append(column)
        else:
            rejected.append(column)
    if "_target_fraction" not in numeric:
        numeric.append("_target_fraction")
    numeric = [name for name in numeric if data[name].notna().any()]
    categorical = [name for name in categorical if data[name].notna().any()]
    if not numeric and not categorical:
        raise RuntimeError("No causal feature survived leakage exclusion")
    return sorted(set(numeric)), sorted(set(categorical)), sorted(set(rejected))


def build_model(numeric: list[str], categorical: list[str]) -> Pipeline:
    transformers: list[tuple[str, Pipeline, list[str]]] = []
    if numeric:
        transformers.append(
            (
                "numeric",
                Pipeline(
                    [
                        ("imputer", SimpleImputer(strategy="median", add_indicator=True)),
                        ("scale", RobustScaler(with_centering=False)),
                    ]
                ),
                numeric,
            )
        )
    if categorical:
        transformers.append(
            (
                "categorical",
                Pipeline(
                    [
                        ("imputer", SimpleImputer(strategy="most_frequent")),
                        (
                            "onehot",
                            OneHotEncoder(handle_unknown="ignore", min_frequency=3),
                        ),
                    ]
                ),
                categorical,
            )
        )
    return Pipeline(
        [
            ("features", ColumnTransformer(transformers, remainder="drop")),
            (
                "model",
                LogisticRegression(
                    C=0.35,
                    class_weight="balanced",
                    max_iter=1500,
                    solver="liblinear",
                    random_state=11,
                ),
            ),
        ]
    )


def fit_predict(
    train: pd.DataFrame,
    test: pd.DataFrame,
    numeric: list[str],
    categorical: list[str],
) -> tuple[np.ndarray, dict[str, Any]]:
    y = train["_target_first"].astype(int).to_numpy()
    base = float(np.mean(y)) if len(y) else 0.5
    if len(train) < 40 or len(np.unique(y)) < 2:
        return np.full(len(test), base, dtype=float), {
            "mode": "mature_history_base_rate",
            "train_rows": len(train),
            "positive_rate": base,
        }
    model = build_model(numeric, categorical)
    model.fit(train, y)
    raw = model.predict_proba(test)[:, 1]
    # Damp probability extremes in proportion to finite causal-history size.
    maturity = len(train) / (len(train) + 120.0)
    probability = base + maturity * (raw - base)
    probability = np.clip(probability, 0.01, 0.99)
    return probability, {
        "mode": "causal_logistic",
        "train_rows": len(train),
        "positive_rate": base,
        "maturity": maturity,
    }


def score_candidates(frame: pd.DataFrame, probability: np.ndarray, risk: float) -> pd.DataFrame:
    scored = frame.copy()
    scored["predicted_target_probability"] = probability
    win_r = scored["_planned_win_r"].clip(lower=1.0, upper=20.0).to_numpy(float)
    p = probability
    scored["predicted_expected_r"] = p * win_r - (1.0 - p)
    scored["predicted_log_growth"] = p * np.log1p(risk * win_r) + (1.0 - p) * math.log1p(-risk)
    return scored


def route(scored: pd.DataFrame, risk: float) -> tuple[pd.DataFrame, dict[str, Any]]:
    if scored.empty:
        return scored.copy(), metrics(scored, risk)
    ordered = scored.sort_values(
        ["_decision_time", "predicted_log_growth", "_planned_win_r"],
        ascending=[True, False, False],
        kind="mergesort",
    ).copy()
    ordered["_decision_bucket"] = ordered["_decision_time"].dt.floor("min")
    used_episodes: set[str] = set()
    chosen_rows: list[pd.Series] = []
    occupied_until = pd.Timestamp.min.tz_localize("UTC")

    for _, group in ordered.groupby("_decision_bucket", sort=True):
        decision_time = group["_decision_time"].min()
        if decision_time < occupied_until:
            continue
        available = group.loc[
            group["predicted_log_growth"].gt(0.0)
            & ~group["_episode"].isin(used_episodes)
        ]
        if available.empty:
            continue
        # Target fractions and symbols are arbitrated as alternate actions from the same account.
        best = available.sort_values(
            ["predicted_log_growth", "predicted_expected_r", "_planned_win_r"],
            ascending=False,
            kind="mergesort",
        ).iloc[0]
        episode = str(best["_episode"])
        chosen_rows.append(best)
        used_episodes.add(episode)
        occupied_until = best["_exit_time"]

    if not chosen_rows:
        selected = ordered.iloc[0:0].copy()
    else:
        selected = pd.DataFrame(chosen_rows).sort_values("_entry_time", kind="mergesort").reset_index(drop=True)
    nav = 1.0
    nav_path: list[float] = []
    for value in selected["_net_r"].to_numpy(float):
        nav *= 1.0 + risk * value
        nav_path.append(nav)
    selected["nav_after"] = nav_path
    return selected, metrics(selected, risk)


def metrics(trades: pd.DataFrame, risk: float) -> dict[str, Any]:
    if trades.empty:
        return {
            "trades": 0,
            "calendar_days": 0,
            "trades_per_day": 0.0,
            "win_rate": 0.0,
            "mean_net_r": 0.0,
            "reward_risk": 0.0,
            "profit_factor_r": 0.0,
            "ending_nav": 1.0,
            "maximum_drawdown": 0.0,
        }
    r = trades["_net_r"].to_numpy(float)
    wins = r[r > 0]
    losses = r[r < 0]
    nav = np.cumprod(1.0 + risk * r)
    peaks = np.maximum.accumulate(np.r_[1.0, nav])
    curve = np.r_[1.0, nav]
    drawdown = 1.0 - curve / peaks
    first_day = trades["_entry_time"].min().floor("D")
    last_day = trades["_entry_time"].max().floor("D")
    days = max(1, int((last_day - first_day).days) + 1)
    return {
        "trades": int(len(trades)),
        "calendar_days": days,
        "trades_per_day": float(len(trades) / days),
        "win_rate": float(np.mean(r > 0)),
        "mean_net_r": float(np.mean(r)),
        "median_net_r": float(np.median(r)),
        "reward_risk": float(np.mean(wins) / abs(np.mean(losses))) if len(wins) and len(losses) else 0.0,
        "profit_factor_r": float(wins.sum() / abs(losses.sum())) if len(losses) else float("inf"),
        "ending_nav": float(nav[-1]),
        "maximum_drawdown": float(drawdown.max()),
    }


def period_metrics(trades: pd.DataFrame, risk: float) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for period, group in trades.groupby("_period", sort=True):
        row = {"period": period}
        row.update(metrics(group.sort_values("_entry_time"), risk))
        rows.append(row)
    return pd.DataFrame(rows)


def expanding_development(
    dev: pd.DataFrame,
    numeric: list[str],
    categorical: list[str],
    risk: float,
) -> tuple[pd.DataFrame, list[dict[str, Any]]]:
    periods = sorted(dev["_period"].unique())
    selected: list[pd.DataFrame] = []
    history: list[dict[str, Any]] = []
    for index in range(1, len(periods)):
        train_periods = periods[:index]
        validation_period = periods[index]
        train = dev.loc[dev["_period"].isin(train_periods)].copy()
        validation = dev.loc[dev["_period"].eq(validation_period)].copy()
        probability, fit_info = fit_predict(train, validation, numeric, categorical)
        scored = score_candidates(validation, probability, risk)
        routed, routed_metrics = route(scored, risk)
        selected.append(routed)
        history.append(
            {
                "train_periods": train_periods,
                "validation_period": validation_period,
                "fit": fit_info,
                "metrics": routed_metrics,
            }
        )
    combined = pd.concat(selected, ignore_index=True, sort=False) if selected else dev.iloc[0:0].copy()
    return combined.sort_values("_entry_time", kind="mergesort"), history


def serializable_schema(schema: Schema) -> dict[str, Any]:
    return {field: getattr(schema, field) for field in schema.__dataclass_fields__}


def main() -> None:
    args = parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    raw = pd.read_csv(args.plans, low_memory=False)
    schema = infer_schema(raw)
    data = prepare(raw, schema)
    numeric, categorical, rejected = feature_columns(data, schema)

    dev = data.loc[data["_role"].str.contains("dev")].copy()
    fresh = data.loc[data["_role"].str.contains("fresh")].copy()
    if dev.empty or fresh.empty:
        raise RuntimeError(f"Both dev and fresh data are required; dev={len(dev)}, fresh={len(fresh)}")

    dev_selected, history = expanding_development(dev, numeric, categorical, args.risk)
    probability, final_fit = fit_predict(dev, fresh, numeric, categorical)
    fresh_scored = score_candidates(fresh, probability, args.risk)
    fresh_selected, fresh_summary = route(fresh_scored, args.risk)

    dev_summary = metrics(dev_selected, args.risk)
    integrated = pd.concat([dev_selected.assign(_evaluation_role="development_oof"), fresh_selected.assign(_evaluation_role="fresh")], ignore_index=True, sort=False)
    integrated = integrated.sort_values("_entry_time", kind="mergesort").reset_index(drop=True)
    integrated_summary = metrics(integrated, args.risk)

    export_columns = [
        name
        for name in (
            "_decision_time",
            "_entry_time",
            "_exit_time",
            "_period",
            "_role",
            "_evaluation_role",
            "_symbol",
            "_side",
            "_episode",
            "_target_fraction",
            "_planned_win_r",
            "_target_first",
            "_net_r",
            "predicted_target_probability",
            "predicted_expected_r",
            "predicted_log_growth",
            "nav_after",
            schema.family,
            schema.phase,
            *schema.geometry,
        )
        if name and name in integrated.columns
    ]
    integrated[export_columns].to_csv(output / "selected_trades.csv", index=False)
    period_metrics(fresh_selected, args.risk).to_csv(output / "fresh_period_metrics.csv", index=False)
    period_metrics(dev_selected, args.risk).to_csv(output / "development_period_metrics.csv", index=False)

    feature_audit = {
        "schema": serializable_schema(schema),
        "numeric": numeric,
        "categorical": categorical,
        "rejected": rejected,
        "eligibility": data.attrs.get("eligibility_diagnostics", {}),
    }
    (output / "feature_audit.json").write_text(
        json.dumps(feature_audit, indent=2, ensure_ascii=False, default=str) + "\n",
        encoding="utf-8",
    )
    summary = {
        "policy": "CAUSAL_FIRST_RESPONSE_ADAPTIVE_REACHABLE_FRONTIER",
        "risk_fraction": args.risk,
        "development_oof": dev_summary,
        "development_history": history,
        "fresh": fresh_summary,
        "fresh_by_period": period_metrics(fresh_selected, args.risk).to_dict("records"),
        "integrated_diagnostic": integrated_summary,
        "final_fit": final_fit,
        "eligible_rows": int(len(data)),
        "development_rows": int(len(dev)),
        "fresh_rows": int(len(fresh)),
        "target_fraction_usage_fresh": {
            str(key): int(value)
            for key, value in fresh_selected["_target_fraction"].value_counts(dropna=False).sort_index().to_dict().items()
        },
    }
    (output / "summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False, default=str) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
