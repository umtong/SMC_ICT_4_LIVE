#!/usr/bin/env python3
"""Discover one causal, cross-asset policy from fixed route-fraction routers.

Each fraction is produced by the same inherited candidate-4 detector, entry and
structural stop.  The only changed action is how much of the already-declared
opposing-liquidity route must complete.  The upstream strict router is trained on
development periods only.  This script then searches development data for a
small union of interpretable scenario families and fixes that union before the
fresh periods are evaluated.

Symbol identity, absolute price and any post-entry outcome field are never used
as policy conditions.  All selected families share one global position and one
causal-episode lockout.
"""
from __future__ import annotations

import argparse
import json
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd

RISK_FRACTION = 0.03
FRESH_PERIODS = {"2025-nov", "2026-jan", "2026-mar", "2026-apr"}
DEV_PERIODS = {"2024-feb", "2024-aug", "2025-feb", "2025-aug"}


def first_column(frame: pd.DataFrame, names: Iterable[str], *, contains: tuple[str, ...] = ()) -> str | None:
    lower = {str(column).lower(): str(column) for column in frame.columns}
    for name in names:
        if name.lower() in lower:
            return lower[name.lower()]
    for column in frame.columns:
        text = str(column).lower()
        if contains and all(token in text for token in contains):
            return str(column)
    return None


def parse_fraction(path: Path) -> float:
    for part in reversed(path.parts):
        match = re.fullmatch(r"f(?:rac)?[_-]?(\d+)", part.lower())
        if match:
            digits = match.group(1)
            value = int(digits)
            if value >= 100:
                return value / 1000.0
            return value / 100.0
        match = re.fullmatch(r"f(0?\d)[_-]?(\d+)", part.lower())
        if match:
            return float(f"{match.group(1)}.{match.group(2)}")
    metadata = path.parent / "route_fraction.json"
    if metadata.exists():
        return float(json.loads(metadata.read_text())["route_target_fraction"])
    raise ValueError(f"cannot infer route fraction from {path}")


def read_routers(root: Path) -> pd.DataFrame:
    files = sorted(root.rglob("selected_exact_trades.csv"))
    if not files:
        files = sorted(root.rglob("*selected*trades*.csv"))
    if not files:
        raise SystemExit(f"no strict-router selected trade files under {root}")

    pieces: list[pd.DataFrame] = []
    for file in files:
        frame = pd.read_csv(file, low_memory=False)
        if frame.empty:
            continue
        frame["route_fraction"] = parse_fraction(file)
        frame["router_source"] = str(file.relative_to(root))
        pieces.append(frame)
    if not pieces:
        raise SystemExit("all strict-router selected trade files were empty")
    return pd.concat(pieces, ignore_index=True, sort=False)


def normalize(frame: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, str | None]]:
    result = frame.copy()
    period_col = first_column(result, ["period", "window", "period_name", "evaluation_period"])
    role_col = first_column(result, ["role", "sample_role", "evaluation_role"])
    net_r_col = first_column(
        result,
        ["net_r", "realized_net_r", "trade_net_r", "resolved_net_r", "outcome_r", "pnl_r", "return_r"],
        contains=("net", "r"),
    )
    start_col = first_column(
        result,
        ["entry_ts", "fill_ts", "entry_time", "filled_at", "entry_timestamp", "opened_at"],
    )
    end_col = first_column(
        result,
        ["exit_ts", "close_ts", "exit_time", "closed_at", "resolution_ts", "resolved_at"],
    )
    decision_col = first_column(
        result,
        ["decision_ts", "signal_ts", "plan_ts", "created_at", "decision_time"],
    )
    symbol_col = first_column(result, ["symbol", "instrument", "instrument_id"])
    side_col = first_column(result, ["side", "direction", "trade_side"])
    episode_col = first_column(
        result,
        ["causal_episode_id", "episode_id", "source_episode_id", "event_id", "parent_episode_id"],
    )

    if net_r_col is None:
        candidates = [str(c) for c in result.columns if str(c).lower().endswith("_r")]
        raise SystemExit(f"could not locate realized R column; R-like columns={candidates}")
    result["_net_r"] = pd.to_numeric(result[net_r_col], errors="coerce")
    result = result[np.isfinite(result["_net_r"])].copy()

    if period_col is None:
        raise SystemExit("strict router output lacks a period/window column")
    result["_period"] = result[period_col].astype(str).str.strip()

    if role_col is not None:
        raw_role = result[role_col].astype(str).str.lower()
        result["_role"] = np.where(raw_role.str.contains("fresh|fixed|eval"), "fresh", "dev")
    else:
        result["_role"] = np.where(result["_period"].isin(FRESH_PERIODS), "fresh", "dev")

    for target, source in (("_start", start_col), ("_end", end_col), ("_decision", decision_col)):
        if source is not None:
            result[target] = pd.to_datetime(result[source], utc=True, errors="coerce")
        else:
            result[target] = pd.NaT
    if result["_start"].isna().all() and not result["_decision"].isna().all():
        result["_start"] = result["_decision"]
    if result["_decision"].isna().all() and not result["_start"].isna().all():
        result["_decision"] = result["_start"]
    if result["_end"].isna().all():
        hold_col = first_column(result, ["hold_minutes", "holding_minutes", "duration_minutes"])
        if hold_col is not None:
            result["_end"] = result["_start"] + pd.to_timedelta(
                pd.to_numeric(result[hold_col], errors="coerce").fillna(1.0), unit="m"
            )
        else:
            result["_end"] = result["_start"] + pd.Timedelta(minutes=1)
    result["_end"] = result["_end"].fillna(result["_start"] + pd.Timedelta(minutes=1))

    if episode_col is not None:
        episode = result[episode_col].astype(str)
    else:
        symbol = result[symbol_col].astype(str) if symbol_col else "MARKET"
        side = result[side_col].astype(str) if side_col else "SIDE"
        minute = result["_decision"].dt.floor("min").astype(str)
        episode = symbol + "|" + side + "|" + minute
    result["_episode"] = episode

    columns = {
        "period": period_col,
        "role": role_col,
        "net_r": net_r_col,
        "start": start_col,
        "end": end_col,
        "decision": decision_col,
        "symbol": symbol_col,
        "side": side_col,
        "episode": episode_col,
    }
    return result, columns


def period_order(frame: pd.DataFrame, role: str) -> list[str]:
    present = set(frame.loc[frame["_role"] == role, "_period"].astype(str))
    desired = DEV_PERIODS if role == "dev" else FRESH_PERIODS
    ordered = [period for period in sorted(desired) if period in present]
    ordered.extend(sorted(present.difference(ordered)))
    return ordered


def arbitrate(frame: pd.DataFrame, priority_col: str = "_priority") -> pd.DataFrame:
    if frame.empty:
        return frame.copy()
    work = frame.copy()
    if priority_col not in work:
        work[priority_col] = 0.0
    work = work.sort_values(
        ["_episode", priority_col, "_decision", "route_fraction"],
        ascending=[True, False, True, True],
        kind="mergesort",
    )
    work = work.drop_duplicates("_episode", keep="first")
    work = work.sort_values(["_start", priority_col, "_decision"], ascending=[True, False, True], kind="mergesort")

    selected: list[int] = []
    busy_until = pd.Timestamp.min.tz_localize("UTC")
    for index, row in work.iterrows():
        start = row["_start"]
        end = row["_end"]
        if pd.isna(start):
            continue
        if start < busy_until:
            continue
        selected.append(index)
        busy_until = end if not pd.isna(end) and end >= start else start + pd.Timedelta(minutes=1)
    return work.loc[selected].sort_values("_start").reset_index(drop=True)


def metric(frame: pd.DataFrame, periods: list[str]) -> dict[str, Any]:
    trades = arbitrate(frame)
    nav = 1.0
    peak = 1.0
    max_dd = 0.0
    gross_win = 0.0
    gross_loss = 0.0
    log_growth = 0.0
    wins = 0
    for value in trades["_net_r"].astype(float):
        multiplier = max(1e-12, 1.0 + RISK_FRACTION * value)
        nav *= multiplier
        log_growth += math.log(multiplier)
        peak = max(peak, nav)
        max_dd = max(max_dd, 1.0 - nav / peak)
        if value > 0:
            gross_win += value
            wins += 1
        elif value < 0:
            gross_loss += -value

    period_rows: list[dict[str, Any]] = []
    for period in periods:
        subset = trades[trades["_period"] == period]
        plog = float(np.log(np.maximum(1e-12, 1.0 + RISK_FRACTION * subset["_net_r"].to_numpy(float))).sum())
        period_rows.append(
            {
                "period": period,
                "trades": int(len(subset)),
                "mean_net_r": float(subset["_net_r"].mean()) if len(subset) else 0.0,
                "log_growth": plog,
                "nav_multiplier": float(math.exp(plog)),
            }
        )

    if len(trades) and not trades["_start"].isna().all():
        start = trades["_start"].min()
        end = trades["_end"].max()
        days = max(1, int((end - start).total_seconds() // 86400) + 1)
    else:
        days = max(1, 7 * max(1, len(periods)))
    logs = np.array([row["log_growth"] for row in period_rows], dtype=float)
    stability_penalty = float(logs.std(ddof=0) * math.sqrt(max(1, len(logs)))) if len(logs) else 0.0
    robust_objective = float(log_growth - 0.75 * stability_penalty + 0.01 * min(2.0, len(trades) / days))

    return {
        "trades": int(len(trades)),
        "calendar_days": int(days),
        "trades_per_day": float(len(trades) / days),
        "win_rate": float(wins / len(trades)) if len(trades) else 0.0,
        "mean_net_r": float(trades["_net_r"].mean()) if len(trades) else 0.0,
        "median_net_r": float(trades["_net_r"].median()) if len(trades) else 0.0,
        "profit_factor_r": float(gross_win / gross_loss) if gross_loss > 0 else (float("inf") if gross_win > 0 else 0.0),
        "ending_nav_multiplier": float(nav),
        "maximum_drawdown": float(max_dd),
        "log_growth": float(log_growth),
        "robust_objective": robust_objective,
        "by_period": period_rows,
    }


@dataclass(frozen=True)
class Rule:
    fraction: float
    conditions: tuple[tuple[str, str], ...]
    score: float
    trades: int
    active_periods: int

    @property
    def name(self) -> str:
        if not self.conditions:
            return f"fraction={self.fraction:.3f}"
        body = ",".join(f"{column}={value}" for column, value in self.conditions)
        return f"fraction={self.fraction:.3f}|{body}"


def categorical_columns(frame: pd.DataFrame) -> list[str]:
    allowed = [
        "family",
        "auction_phase",
        "entry_geometry",
        "route_kind",
        "setup_kind",
        "location_kind",
        "narrative_branch",
        "source_pool_kind",
    ]
    lower = {str(column).lower(): str(column) for column in frame.columns}
    return [lower[name] for name in allowed if name in lower]


def mask_rule(frame: pd.DataFrame, rule: Rule) -> pd.Series:
    mask = np.isclose(frame["route_fraction"].astype(float), rule.fraction)
    for column, value in rule.conditions:
        mask &= frame[column].astype(str) == value
    return mask


def candidate_rules(frame: pd.DataFrame, dev_periods: list[str]) -> list[Rule]:
    dev = frame[frame["_role"] == "dev"].copy()
    categories = categorical_columns(dev)
    combos: list[tuple[str, ...]] = [tuple()]
    combos.extend((column,) for column in categories)
    preferred_pairs = [
        ("family", "auction_phase"),
        ("family", "entry_geometry"),
        ("family", "route_kind"),
        ("auction_phase", "entry_geometry"),
        ("auction_phase", "route_kind"),
        ("entry_geometry", "route_kind"),
    ]
    lower = {str(column).lower(): str(column) for column in categories}
    for left, right in preferred_pairs:
        if left in lower and right in lower:
            combos.append((lower[left], lower[right]))

    rules: list[Rule] = []
    for fraction in sorted(dev["route_fraction"].dropna().unique()):
        fraction_frame = dev[np.isclose(dev["route_fraction"], fraction)]
        for combo in combos:
            if not combo:
                groups = [(tuple(), fraction_frame)]
            else:
                groups = []
                for values, subset in fraction_frame.groupby(list(combo), dropna=False, sort=False):
                    if not isinstance(values, tuple):
                        values = (values,)
                    conditions = tuple((column, str(value)) for column, value in zip(combo, values))
                    groups.append((conditions, subset))
            for conditions, subset in groups:
                traded = arbitrate(subset)
                if len(traded) < 5:
                    continue
                active = int(traded["_period"].nunique())
                if active < 2:
                    continue
                counts = traded["_period"].value_counts()
                if float(counts.max() / len(traded)) > 0.78:
                    continue
                stats = metric(traded, dev_periods)
                rules.append(
                    Rule(
                        fraction=float(fraction),
                        conditions=conditions,
                        score=float(stats["robust_objective"]),
                        trades=int(stats["trades"]),
                        active_periods=active,
                    )
                )
    rules.sort(key=lambda rule: (rule.score, rule.trades), reverse=True)
    return rules[:250]


def union_for_rules(frame: pd.DataFrame, rules: list[Rule]) -> pd.DataFrame:
    pieces: list[pd.DataFrame] = []
    for rank, rule in enumerate(rules):
        subset = frame[mask_rule(frame, rule)].copy()
        subset["_rule"] = rule.name
        subset["_priority"] = rule.score + 1e-7 * (len(rules) - rank)
        pieces.append(subset)
    if not pieces:
        return frame.iloc[0:0].copy()
    return arbitrate(pd.concat(pieces, ignore_index=True, sort=False))


def greedy_policy(frame: pd.DataFrame, rules: list[Rule], dev_periods: list[str]) -> list[Rule]:
    selected: list[Rule] = []
    current_score = 0.0
    for _ in range(8):
        best_rule: Rule | None = None
        best_score = current_score
        for rule in rules:
            if rule in selected:
                continue
            candidate = selected + [rule]
            dev_union = union_for_rules(frame[frame["_role"] == "dev"], candidate)
            score = float(metric(dev_union, dev_periods)["robust_objective"])
            if score > best_score + 0.0015:
                best_rule = rule
                best_score = score
        if best_rule is None:
            break
        selected.append(best_rule)
        current_score = best_score
    return selected


def json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): json_safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [json_safe(v) for v in value]
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        value = float(value)
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)

    raw = read_routers(args.root)
    frame, schema = normalize(raw)
    dev_periods = period_order(frame, "dev")
    fresh_periods = period_order(frame, "fresh")
    if not dev_periods or not fresh_periods:
        raise SystemExit(f"need both development and fresh periods; dev={dev_periods}, fresh={fresh_periods}")

    rules = candidate_rules(frame, dev_periods)
    selected_rules = greedy_policy(frame, rules, dev_periods)

    variants: dict[str, list[Rule]] = {"greedy_scenario_fusion": selected_rules}
    fractions = sorted(frame["route_fraction"].dropna().unique())
    for fraction in fractions:
        base = next((rule for rule in rules if rule.fraction == fraction and not rule.conditions), None)
        if base is not None:
            variants[f"single_fraction_{fraction:.3f}"] = [base]

    # Mechanism-focused alternatives remain useful when the greedy union chooses
    # a broad parent rule.  Their inclusion is determined only by development
    # data and they are compared as complete one-account policies.
    for token, label in (("FIRST_RETEST", "first_retest"), ("FAILED_AUCTION", "failed_auction"), ("ACCEPTED_AUCTION", "accepted_auction")):
        subset = [
            rule
            for rule in rules
            if any(token in value.upper() for _, value in rule.conditions)
        ]
        chosen = greedy_policy(frame, subset, dev_periods)
        if chosen:
            variants[label] = chosen

    variant_rows: list[dict[str, Any]] = []
    variant_trades: dict[str, pd.DataFrame] = {}
    for name, policy_rules in variants.items():
        combined = union_for_rules(frame, policy_rules)
        dev_stats = metric(combined[combined["_role"] == "dev"], dev_periods)
        fresh_stats = metric(combined[combined["_role"] == "fresh"], fresh_periods)
        variant_trades[name] = combined
        variant_rows.append(
            {
                "variant": name,
                "development_objective": dev_stats["robust_objective"],
                "development_trades": dev_stats["trades"],
                "development_mean_net_r": dev_stats["mean_net_r"],
                "development_nav": dev_stats["ending_nav_multiplier"],
                "development_maximum_drawdown": dev_stats["maximum_drawdown"],
                "fresh_trades": fresh_stats["trades"],
                "fresh_mean_net_r": fresh_stats["mean_net_r"],
                "fresh_nav": fresh_stats["ending_nav_multiplier"],
                "fresh_maximum_drawdown": fresh_stats["maximum_drawdown"],
                "fresh_trades_per_day": fresh_stats["trades_per_day"],
                "rule_count": len(policy_rules),
            }
        )

    variant_table = pd.DataFrame(variant_rows).sort_values(
        ["development_objective", "development_trades"], ascending=[False, False]
    )
    if variant_table.empty:
        raise SystemExit("no executable development policy was discovered")
    selected_name = str(variant_table.iloc[0]["variant"])
    final_rules = variants[selected_name]
    final_trades = variant_trades[selected_name]
    development = metric(final_trades[final_trades["_role"] == "dev"], dev_periods)
    fresh = metric(final_trades[final_trades["_role"] == "fresh"], fresh_periods)

    rule_table = pd.DataFrame(
        [
            {
                "name": rule.name,
                "fraction": rule.fraction,
                "conditions": json.dumps(dict(rule.conditions), ensure_ascii=False, sort_keys=True),
                "development_rule_objective": rule.score,
                "development_rule_trades": rule.trades,
                "development_active_periods": rule.active_periods,
                "selected": rule in final_rules,
            }
            for rule in rules
        ]
    )

    summary = {
        "policy": "ML_FIRST_REACHABLE_SCENARIO_FUSION_V3",
        "selected_variant": selected_name,
        "risk_fraction_per_completed_trade": RISK_FRACTION,
        "route_fractions_researched": [float(value) for value in fractions],
        "development_periods": dev_periods,
        "fresh_periods": fresh_periods,
        "selected_rules": [
            {
                "name": rule.name,
                "route_fraction": rule.fraction,
                "conditions": dict(rule.conditions),
                "development_rule_objective": rule.score,
                "development_rule_trades": rule.trades,
            }
            for rule in final_rules
        ],
        "development": development,
        "fresh": fresh,
        "schema": schema,
        "forbidden_policy_inputs": [
            "symbol identity",
            "absolute price",
            "post-entry MFE/MAE",
            "target/stop outcome",
            "future bars",
        ],
    }

    output_columns = [column for column in final_trades.columns if not column.startswith("_", 0)]
    output_columns += ["_period", "_role", "_net_r", "_start", "_end", "_episode", "_rule"]
    output_columns = list(dict.fromkeys(column for column in output_columns if column in final_trades.columns))
    final_trades[output_columns].to_csv(args.output / "selected_fused_trades.csv", index=False)
    variant_table.to_csv(args.output / "variant_metrics.csv", index=False)
    rule_table.to_csv(args.output / "rule_catalog.csv", index=False)
    pd.DataFrame(development["by_period"] + fresh["by_period"]).to_csv(
        args.output / "period_metrics.csv", index=False
    )
    (args.output / "summary.json").write_text(
        json.dumps(json_safe(summary), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(json_safe(summary), ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
