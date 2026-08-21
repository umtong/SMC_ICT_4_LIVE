#!/usr/bin/env python3
"""Blocked-period learning and one-account execution for directional liquidity.

The route model answers whether the direction implied by a completed liquidity
auction reaches its frozen route target before route invalidation.  The entry
model answers whether the immutable first-return plan reaches its target before
its stop after realistic costs.  The models do not create direction, levels,
entries, stops or targets; those belong to the causal market narrative.

Every evaluation period is scored by models that did not train on that period.
A separate 2024 -> 2025 view is produced without changing the policy.  The only
trade admission boundary is positive expected account log-growth after combining
directional and entry evidence.  Four symbols compete chronologically for one
position and one continuous NAV within each actual period.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence
import argparse
import json
import math
import pickle

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier


RISK_FRACTION = 0.03
MODEL_SEEDS = (17011, 29021, 41047, 53069, 71081)
ALLOWED_PRETRADE_R_COLUMNS = {"target_net_r", "stop_net_r"}
IDENTITY_COLUMNS = {
    "symbol",
    "period",
    "route_id",
    "action_id",
    "episode_id",
    "source_level_id",
    "objective_id",
}
FUTURE_TOKENS = (
    "outcome",
    "resolution",
    "holding",
    "mfe",
    "mae",
    "fill_state",
    "fill_time",
    "entry_wait",
    "route_label",
)
DIAGNOSTIC_TOKENS = ("diagnostic_", "_price", "interaction_time_ns", "emission_time_ns")


@dataclass
class Encoder:
    numeric: list[str]
    categorical: list[str]
    medians: dict[str, float]
    lower: dict[str, float]
    upper: dict[str, float]
    categories: dict[str, list[str]]

    @classmethod
    def fit(cls, frame: pd.DataFrame, columns: Sequence[str]) -> "Encoder":
        numeric: list[str] = []
        categorical: list[str] = []
        medians: dict[str, float] = {}
        lower: dict[str, float] = {}
        upper: dict[str, float] = {}
        categories: dict[str, list[str]] = {}
        for column in columns:
            series = frame[column]
            converted = pd.to_numeric(series, errors="coerce")
            if converted.notna().mean() >= 0.9:
                numeric.append(column)
                finite = converted.replace([np.inf, -np.inf], np.nan)
                medians[column] = float(finite.median()) if finite.notna().any() else 0.0
                lower[column] = float(finite.quantile(0.005)) if finite.notna().any() else 0.0
                upper[column] = float(finite.quantile(0.995)) if finite.notna().any() else 0.0
                if not math.isfinite(lower[column]):
                    lower[column] = medians[column]
                if not math.isfinite(upper[column]):
                    upper[column] = medians[column]
                if upper[column] < lower[column]:
                    lower[column], upper[column] = upper[column], lower[column]
            else:
                categorical.append(column)
                values = series.fillna("__MISSING__").astype(str)
                counts = values.value_counts()
                # Categories are mechanism vocabulary, not date/symbol identity.
                categories[column] = sorted(counts.index[:32].tolist())
        return cls(numeric, categorical, medians, lower, upper, categories)

    def transform(self, frame: pd.DataFrame) -> np.ndarray:
        blocks: list[np.ndarray] = []
        if self.numeric:
            numeric = np.empty((len(frame), len(self.numeric)), dtype=np.float64)
            for index, column in enumerate(self.numeric):
                series = pd.to_numeric(frame.get(column, pd.Series(index=frame.index, dtype=float)), errors="coerce")
                values = series.replace([np.inf, -np.inf], np.nan).fillna(self.medians[column]).to_numpy(float)
                numeric[:, index] = np.clip(values, self.lower[column], self.upper[column])
            blocks.append(numeric)
        for column in self.categorical:
            values = frame.get(column, pd.Series("__MISSING__", index=frame.index)).fillna("__MISSING__").astype(str)
            vocabulary = self.categories[column]
            block = np.zeros((len(frame), len(vocabulary) + 1), dtype=np.float64)
            mapping = {value: index for index, value in enumerate(vocabulary)}
            for row, value in enumerate(values):
                block[row, mapping.get(value, len(vocabulary))] = 1.0
            blocks.append(block)
        if not blocks:
            raise RuntimeError("no causal feature columns")
        return np.concatenate(blocks, axis=1)

    @property
    def dimension(self) -> int:
        return len(self.numeric) + sum(len(values) + 1 for values in self.categories.values())


@dataclass
class Ensemble:
    encoder: Encoder
    models: list[HistGradientBoostingClassifier]
    feature_columns: list[str]

    @classmethod
    def fit(cls, frame: pd.DataFrame, labels: np.ndarray, columns: Sequence[str]) -> "Ensemble":
        if len(frame) < 60:
            raise RuntimeError(f"insufficient resolved causal examples: {len(frame)}")
        unique = np.unique(labels)
        if set(unique.tolist()) != {0, 1}:
            raise RuntimeError(f"both outcomes are required, got {unique}")
        encoder = Encoder.fit(frame, columns)
        matrix = encoder.transform(frame)
        models: list[HistGradientBoostingClassifier] = []
        for seed in MODEL_SEEDS:
            model = HistGradientBoostingClassifier(
                learning_rate=0.045,
                max_iter=240,
                max_leaf_nodes=15,
                min_samples_leaf=12,
                l2_regularization=3.0,
                max_bins=127,
                early_stopping=True,
                validation_fraction=0.15,
                n_iter_no_change=25,
                random_state=seed,
            )
            model.fit(matrix, labels)
            models.append(model)
        return cls(encoder, models, list(columns))

    def predict(self, frame: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
        matrix = self.encoder.transform(frame)
        probabilities = np.vstack([model.predict_proba(matrix)[:, 1] for model in self.models])
        return np.median(probabilities, axis=0), np.std(probabilities, axis=0)


def _period_from_path(path: Path) -> str:
    for part in reversed(path.parts):
        if part.startswith("dev-"):
            return part
    return path.parent.name


def _load(root: Path, filename: str) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    for path in root.rglob(filename):
        frame = pd.read_csv(path)
        if frame.empty:
            continue
        frame["period"] = _period_from_path(path)
        frames.append(frame)
    if not frames:
        raise RuntimeError(f"no non-empty {filename} beneath {root}")
    return pd.concat(frames, ignore_index=True, sort=False)


def _is_feature(column: str, *, route: bool) -> bool:
    if column in IDENTITY_COLUMNS or column == "side":
        return False
    lower = column.lower()
    if column in ALLOWED_PRETRADE_R_COLUMNS:
        return not route
    if any(token in lower for token in FUTURE_TOKENS):
        return False
    if any(token in lower for token in DIAGNOSTIC_TOKENS):
        return False
    if lower in {
        "entry",
        "stop",
        "target",
        "source_price",
        "source_lower",
        "source_upper",
        "route_source_price",
        "route_target_price",
        "route_invalidation_price",
        "emission_index",
        "interaction_index",
        "route_decision_index",
        "fill_index",
        "resolution_index",
    }:
        return False
    if route:
        return lower.startswith((
            "route_",
            "liquidity_",
            "structure_",
            "pre_balance_",
            "profile_",
            "direction_",
            "minute_",
            "macro_",
            "distance_to_",
            "quarter_",
            "hour_utc_",
        ))
    return lower.startswith((
        "route_",
        "liquidity_",
        "structure_",
        "pre_balance_",
        "profile_",
        "direction_",
        "entry_",
        "response_",
        "minute_",
        "macro_",
        "distance_to_",
        "quarter_",
        "hour_utc_",
    )) or column in ALLOWED_PRETRADE_R_COLUMNS or lower in {"gross_rr", "risk_bps", "target_bps", "post_cost_reward_risk", "post_cost_break_even_probability", "round_trip_cost_r_target"}


def _feature_columns(frame: pd.DataFrame, *, route: bool) -> list[str]:
    columns = sorted(column for column in frame.columns if _is_feature(str(column), route=route))
    forbidden = [column for column in columns if any(token in column.lower() for token in FUTURE_TOKENS)]
    if forbidden:
        raise RuntimeError(f"future fields entered feature contract: {forbidden}")
    if not columns:
        raise RuntimeError("empty feature contract")
    return columns


def _resolved_routes(routes: pd.DataFrame) -> pd.DataFrame:
    frame = routes[routes["route_label_outcome"].isin(["ROUTE_TARGET_FIRST", "ROUTE_INVALIDATION_FIRST"])].copy()
    frame["label"] = frame["route_label_outcome"].eq("ROUTE_TARGET_FIRST").astype(np.int8)
    return frame


def _resolved_actions(actions: pd.DataFrame) -> pd.DataFrame:
    outcomes = ["TARGET_FIRST", "STOP_FIRST", "AMBIGUOUS_SAME_MINUTE"]
    frame = actions[actions["outcome"].isin(outcomes)].copy()
    frame["label"] = frame["outcome"].eq("TARGET_FIRST").astype(np.int8)
    return frame


def _logit(probability: np.ndarray) -> np.ndarray:
    value = np.clip(probability, 1e-5, 1.0 - 1e-5)
    return np.log(value / (1.0 - value))


def _sigmoid(value: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-np.clip(value, -30.0, 30.0)))


def _score_actions(
    actions: pd.DataFrame,
    route_probability: np.ndarray,
    route_disagreement: np.ndarray,
    entry_probability: np.ndarray,
    entry_disagreement: np.ndarray,
) -> pd.DataFrame:
    frame = actions.copy()
    # Route evidence is a directional prior.  Entry evidence is conditional on
    # that route and owns more weight, avoiding double-counting the same event.
    combined = _sigmoid(_logit(entry_probability) + 0.5 * _logit(route_probability))
    win_r = pd.to_numeric(frame["target_net_r"], errors="coerce").to_numpy(float) / np.maximum(
        np.abs(pd.to_numeric(frame["stop_net_r"], errors="coerce").to_numpy(float)), 1e-12
    )
    log_win = np.log1p(RISK_FRACTION * win_r)
    log_loss = math.log1p(-RISK_FRACTION)
    expected = combined * log_win + (1.0 - combined) * log_loss
    frame["route_probability"] = route_probability
    frame["route_model_disagreement"] = route_disagreement
    frame["entry_probability"] = entry_probability
    frame["entry_model_disagreement"] = entry_disagreement
    frame["combined_probability"] = combined
    frame["expected_account_log_growth"] = expected
    frame["probability_break_even"] = -log_loss / np.maximum(log_win - log_loss, 1e-12)
    return frame


def _fit_score_split(
    routes: pd.DataFrame,
    actions: pd.DataFrame,
    *,
    train_periods: Sequence[str],
    evaluation_period: str,
    route_columns: Sequence[str],
    action_columns: Sequence[str],
) -> pd.DataFrame:
    route_train = routes[routes.period.isin(train_periods)]
    action_train = actions[actions.period.isin(train_periods)]
    route_eval = routes[routes.period.eq(evaluation_period)]
    action_eval = actions[actions.period.eq(evaluation_period)].copy()
    if action_eval.empty:
        return action_eval
    route_model = Ensemble.fit(route_train, route_train.label.to_numpy(np.int8), route_columns)
    entry_model = Ensemble.fit(action_train, action_train.label.to_numpy(np.int8), action_columns)
    route_lookup = route_eval.set_index("route_id")
    aligned_routes = route_lookup.loc[action_eval.episode_id]
    route_p, route_std = route_model.predict(aligned_routes)
    entry_p, entry_std = entry_model.predict(action_eval)
    return _score_actions(action_eval, route_p, route_std, entry_p, entry_std)


def _account(frame: pd.DataFrame, period: str, output: Path) -> dict[str, Any]:
    if frame.empty:
        empty = pd.DataFrame()
        empty.to_csv(output / f"account_{period}.csv", index=False)
        return {"period": period, "trades": 0, "ending_nav": 100_000.0}
    candidates = frame[frame.expected_account_log_growth > 0.0].copy()
    candidates = candidates.sort_values(
        ["fill_time_ns", "expected_account_log_growth", "route_probability", "action_id"],
        ascending=[True, False, False, True],
    )
    nav = 100_000.0
    peak = nav
    max_drawdown = 0.0
    busy_until = -1
    used_episodes: set[str] = set()
    records: list[dict[str, Any]] = []
    # At one timestamp every symbol and mechanism competes before chronology is
    # advanced.  The account takes one action, never a post-hoc sum of families.
    for fill_time, group in candidates.groupby("fill_time_ns", sort=True):
        if int(fill_time) <= busy_until:
            continue
        available = group[~group.episode_id.astype(str).isin(used_episodes)]
        if available.empty:
            continue
        row = available.sort_values(
            ["expected_account_log_growth", "route_probability", "entry_probability", "action_id"],
            ascending=[False, False, False, True],
        ).iloc[0]
        if pd.isna(row.resolution_time_ns):
            continue
        stop_r = abs(float(row.stop_net_r))
        account_r = float(row.net_r) / max(stop_r, 1e-12)
        nav_before = nav
        nav *= 1.0 + RISK_FRACTION * account_r
        peak = max(peak, nav)
        max_drawdown = max(max_drawdown, 1.0 - nav / peak)
        busy_until = int(row.resolution_time_ns)
        used_episodes.add(str(row.episode_id))
        records.append({
            **row.to_dict(),
            "nav_before": nav_before,
            "account_r": account_r,
            "risk_cash": nav_before * RISK_FRACTION,
            "nav_after": nav,
            "drawdown": 1.0 - nav / peak,
        })
    ledger = pd.DataFrame(records)
    ledger.to_csv(output / f"account_{period}.csv", index=False)
    if ledger.empty:
        return {"period": period, "trades": 0, "ending_nav": nav}
    wins = int((ledger.account_r > 0.0).sum())
    return {
        "period": period,
        "trades": int(len(ledger)),
        "wins": wins,
        "win_rate": wins / len(ledger),
        "mean_account_r": float(ledger.account_r.mean()),
        "median_account_r": float(ledger.account_r.median()),
        "mean_planned_gross_rr": float(ledger.gross_rr.mean()),
        "median_holding_minutes": float(ledger.holding_minutes.median()),
        "ending_nav": float(nav),
        "return": float(nav / 100_000.0 - 1.0),
        "max_drawdown": float(max_drawdown),
        "active_days": int(pd.to_datetime(ledger.fill_time_ns, unit="ns", utc=True).dt.date.nunique()),
        "by_event": ledger.groupby("route_event_kind").account_r.agg(["count", "mean"]).to_dict("index"),
        "by_location": ledger.groupby("entry_location_kind").account_r.agg(["count", "mean"]).to_dict("index"),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    routes_all = _load(args.input_root, "routes.csv")
    actions_all = _load(args.input_root, "actions.csv")
    routes = _resolved_routes(routes_all)
    actions = _resolved_actions(actions_all)
    route_columns = _feature_columns(routes, route=True)
    action_columns = _feature_columns(actions, route=False)

    periods = sorted(set(routes.period) & set(actions.period))
    if len(periods) < 4:
        raise RuntimeError(f"at least four separated periods are required, got {periods}")
    scored_frames: list[pd.DataFrame] = []
    account_summaries: list[dict[str, Any]] = []
    for period in periods:
        training = [item for item in periods if item != period]
        scored = _fit_score_split(
            routes,
            actions,
            train_periods=training,
            evaluation_period=period,
            route_columns=route_columns,
            action_columns=action_columns,
        )
        scored["score_mode"] = "LEAVE_PERIOD_OUT"
        scored.to_csv(args.output / f"scored_{period}.csv", index=False)
        scored_frames.append(scored)
        account_summaries.append(_account(scored, period, args.output))

    # A fixed temporal transfer view: every 2025 period is predicted only from
    # 2024 periods.  This is direct evidence against date memorization, not a
    # separate promotion or scoring framework.
    train_2024 = [period for period in periods if "dev-2024-" in period]
    forward_summaries: list[dict[str, Any]] = []
    for period in [item for item in periods if "dev-2025-" in item]:
        if not train_2024:
            break
        scored = _fit_score_split(
            routes,
            actions,
            train_periods=train_2024,
            evaluation_period=period,
            route_columns=route_columns,
            action_columns=action_columns,
        )
        scored["score_mode"] = "TRAIN_2024_FORWARD_2025"
        scored.to_csv(args.output / f"forward_scored_{period}.csv", index=False)
        forward_summaries.append(_account(scored, f"forward_{period}", args.output))

    combined_scored = pd.concat(scored_frames, ignore_index=True, sort=False)
    combined_scored.to_csv(args.output / "all_oof_scored_actions.csv", index=False)

    final_route_model = Ensemble.fit(routes, routes.label.to_numpy(np.int8), route_columns)
    final_entry_model = Ensemble.fit(actions, actions.label.to_numpy(np.int8), action_columns)
    with (args.output / "policy_models.pkl").open("wb") as stream:
        pickle.dump({
            "route_model": final_route_model,
            "entry_model": final_entry_model,
            "route_feature_columns": route_columns,
            "action_feature_columns": action_columns,
            "risk_fraction": RISK_FRACTION,
            "symbol_identity_used": False,
            "period_identity_used": False,
        }, stream, protocol=pickle.HIGHEST_PROTOCOL)

    summary = {
        "periods": periods,
        "resolved_routes": int(len(routes)),
        "resolved_actions": int(len(actions)),
        "route_feature_count": len(route_columns),
        "action_feature_count": len(action_columns),
        "route_encoder_dimension": final_route_model.encoder.dimension,
        "action_encoder_dimension": final_entry_model.encoder.dimension,
        "leave_period_out_accounts": account_summaries,
        "train_2024_forward_2025_accounts": forward_summaries,
        "risk_fraction": RISK_FRACTION,
        "admission": "POSITIVE_EXPECTED_ACCOUNT_LOG_GROWTH",
        "one_global_position": True,
        "symbol_identity_used": False,
        "period_identity_used": False,
        "future_information_used_by_models": False,
    }
    (args.output / "policy_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
