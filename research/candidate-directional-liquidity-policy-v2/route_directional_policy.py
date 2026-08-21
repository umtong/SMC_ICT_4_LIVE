#!/usr/bin/env python3
"""Causal market-wide arbitration and one continuous three-percent-risk account."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
from typing import Any

import numpy as np
import pandas as pd

RISK_FRACTION = 0.03
EPS = 1e-12
PERIOD_PATTERN = re.compile(r"(dev|fresh|cal|holdout|eval)-\d{4}-[a-z0-9]+", re.IGNORECASE)
RESOLVED_OUTCOMES = {
    "TARGET_FIRST",
    "STOP_FIRST",
    "AMBIGUOUS_SAME_MINUTE",
    "AMBIGUOUS_FILL_BARRIER_SAME_MINUTE",
}


def _bool_series(series: pd.Series) -> pd.Series:
    if pd.api.types.is_bool_dtype(series):
        return series.fillna(False)
    return series.astype(str).str.lower().isin({"true", "1", "yes"})


def _period_from_path(path: Path, summary: dict[str, Any]) -> str:
    if summary.get("period"):
        return str(summary["period"])
    for part in reversed(path.parts):
        match = PERIOD_PATTERN.search(part)
        if match:
            return match.group(0)
    return f"{summary.get('start', 'unknown')}__{summary.get('end', 'unknown')}"


def _role(period: str) -> str:
    return period.split("-", 1)[0] if "-" in period else "unknown"


def load_universe(root: Path) -> tuple[pd.DataFrame, dict[str, int], dict[str, dict[str, Any]]]:
    frames: list[pd.DataFrame] = []
    period_days: dict[str, int] = {}
    summaries: dict[str, dict[str, Any]] = {}
    for action_path in sorted(root.glob("**/departure_actions.csv.gz")):
        summary_path = action_path.parent / "summary.json"
        summary = json.loads(summary_path.read_text(encoding="utf-8")) if summary_path.exists() else {}
        period = _period_from_path(action_path, summary)
        frame = pd.read_csv(action_path, low_memory=False)
        if not frame.empty:
            frame["period"] = period
            frame["role"] = _role(period)
            frames.append(frame)
        start = pd.Timestamp(summary.get("start")) if summary.get("start") else None
        end = pd.Timestamp(summary.get("end")) if summary.get("end") else None
        if start is not None and end is not None:
            period_days[period] = max(1, int((end - start).days))
        summaries[period] = summary
    if not frames:
        return pd.DataFrame(), period_days, summaries
    return pd.concat(frames, ignore_index=True, sort=False), period_days, summaries


def _time(frame: pd.DataFrame, column: str) -> pd.Series:
    return pd.to_datetime(
        pd.to_numeric(frame.get(column), errors="coerce"),
        unit="ns",
        utc=True,
        errors="coerce",
    )


def assign_market_event_clusters(
    orders: pd.DataFrame,
    tolerance: pd.Timedelta = pd.Timedelta(minutes=8),
) -> pd.DataFrame:
    """Causally cluster correlated symbols from one market-wide liquidity event."""
    output = orders.copy()
    output["interaction_time"] = _time(output, "interaction_time_ns")
    output["order_time"] = _time(output, "order_time_ns")
    output = output.sort_values(
        ["interaction_time", "order_time", "symbol", "episode_id"],
        kind="mergesort",
    ).reset_index(drop=True)
    active: dict[str, list[tuple[pd.Timestamp, str]]] = {"LONG": [], "SHORT": []}
    cluster_ids: list[str] = []
    sequence = 0
    for row in output.itertuples(index=False):
        side = str(getattr(row, "side"))
        timestamp = pd.Timestamp(getattr(row, "interaction_time"))
        if side not in active or pd.isna(timestamp):
            sequence += 1
            cluster_ids.append(f"MKT:{sequence:08d}:{side}")
            continue
        alive = [item for item in active[side] if timestamp - item[0] <= tolerance]
        active[side] = alive
        if alive:
            cluster_id = alive[-1][1]
        else:
            sequence += 1
            cluster_id = f"MKT:{sequence:08d}:{side}"
            active[side].append((timestamp, cluster_id))
        cluster_ids.append(cluster_id)
    output["market_event_id"] = cluster_ids
    return output


def _score(frame: pd.DataFrame) -> pd.Series:
    supplied = pd.to_numeric(frame.get("opportunity_score"), errors="coerce")
    coherence = pd.to_numeric(frame.get("mechanism_coherence"), errors="coerce").fillna(0.0)
    rr = pd.to_numeric(frame.get("gross_rr"), errors="coerce").fillna(0.0)
    route = pd.to_numeric(frame.get("route_strength"), errors="coerce").fillna(0.0)
    fallback = coherence + 0.16 * np.minimum(rr, 4.0) + 0.10 * np.log1p(np.maximum(route, 0.0))
    return supplied.fillna(pd.Series(fallback, index=frame.index)).astype(float)


def route_account(
    order_rows: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    if order_rows.empty:
        empty = order_rows.copy()
        return empty, empty, empty, {
            "market_event_observations": 0,
            "independent_market_events": 0,
            "selected_orders": 0,
            "closed_trades": 0,
            "ending_nav_multiplier": 1.0,
            "maximum_drawdown": 0.0,
        }
    orders = assign_market_event_clusters(order_rows)
    orders["opportunity_score"] = _score(orders)
    orders["terminal_time"] = _time(orders, "order_terminal_time_ns")
    orders["resolution_time"] = _time(orders, "resolution_time_ns")
    orders["decision_minute"] = orders.order_time.dt.floor("min")
    orders = orders.sort_values(
        ["decision_minute", "opportunity_score", "gross_rr", "episode_id"],
        ascending=[True, False, False, True],
        kind="mergesort",
    )

    selected_rows: list[pd.Series] = []
    skipped_rows: list[pd.Series] = []
    used_market_events: set[str] = set()
    busy_until = pd.Timestamp.min.tz_localize("UTC")
    for minute, simultaneous in orders.groupby("decision_minute", sort=True):
        minute = pd.Timestamp(minute)
        available = simultaneous[
            ~simultaneous.market_event_id.astype(str).isin(used_market_events)
        ]
        if available.empty:
            skipped_rows.extend(row for _, row in simultaneous.iterrows())
            continue
        if pd.isna(minute) or minute < busy_until:
            skipped_rows.extend(row for _, row in available.iterrows())
            continue
        chosen = available.iloc[0].copy()
        selected_rows.append(chosen)
        used_market_events.add(str(chosen.market_event_id))
        terminal = pd.Timestamp(chosen.terminal_time)
        if pd.isna(terminal):
            terminal = minute
        busy_until = max(minute, terminal)
        for _, row in available.iloc[1:].iterrows():
            skipped_rows.append(row)

    selected = pd.DataFrame(selected_rows).reset_index(drop=True) if selected_rows else orders.iloc[:0].copy()
    skipped = pd.DataFrame(skipped_rows).reset_index(drop=True) if skipped_rows else orders.iloc[:0].copy()
    closed = selected[
        pd.to_numeric(selected.get("net_r"), errors="coerce").notna()
        & selected.get("outcome", "").astype(str).isin(RESOLVED_OUTCOMES)
    ].copy().reset_index(drop=True)
    closed["net_r"] = pd.to_numeric(closed.net_r, errors="coerce")

    nav = 1.0
    peak = 1.0
    maximum_drawdown = 0.0
    nav_before: list[float] = []
    nav_after: list[float] = []
    quantities: list[float] = []
    leverages: list[float] = []
    for row in closed.itertuples(index=False):
        entry = float(getattr(row, "entry"))
        stop = float(getattr(row, "stop"))
        price_risk = max(abs(entry - stop), EPS)
        quantity = nav * RISK_FRACTION / price_risk
        leverage = quantity * entry / max(nav, EPS)
        result = float(getattr(row, "net_r"))
        nav_before.append(nav)
        quantities.append(quantity)
        leverages.append(leverage)
        nav *= max(EPS, 1.0 + RISK_FRACTION * result)
        peak = max(peak, nav)
        maximum_drawdown = max(maximum_drawdown, 1.0 - nav / peak)
        nav_after.append(nav)
    closed["nav_before"] = nav_before
    closed["risk_cash"] = np.asarray(nav_before) * RISK_FRACTION if nav_before else []
    closed["diagnostic_quantity_before_precision"] = quantities
    closed["required_full_margin_leverage_before_precision"] = leverages
    closed["nav_after"] = nav_after
    wins = closed.outcome.astype(str).eq("TARGET_FIRST")
    return selected, closed, skipped, {
        "market_event_observations": int(len(orders)),
        "independent_market_events": int(orders.market_event_id.nunique()),
        "selected_orders": int(len(selected)),
        "closed_trades": int(len(closed)),
        "target_first": int(wins.sum()),
        "target_first_rate": float(wins.mean()) if len(closed) else None,
        "mean_net_r": float(closed.net_r.mean()) if len(closed) else None,
        "median_net_r": float(closed.net_r.median()) if len(closed) else None,
        "mean_planned_gross_rr": float(pd.to_numeric(closed.get("gross_rr"), errors="coerce").mean()) if len(closed) else None,
        "median_holding_minutes": float(pd.to_numeric(closed.get("holding_minutes"), errors="coerce").median()) if len(closed) else None,
        "ending_nav_multiplier": float(nav),
        "maximum_drawdown": float(maximum_drawdown),
        "risk_fraction": RISK_FRACTION,
    }


def _group_metrics(frame: pd.DataFrame, key: str) -> dict[str, Any]:
    if frame.empty or key not in frame:
        return {}
    output: dict[str, Any] = {}
    for value, group in frame.groupby(key, dropna=False):
        wins = group.outcome.astype(str).eq("TARGET_FIRST")
        output[str(value)] = {
            "trades": int(len(group)),
            "target_first_rate": float(wins.mean()) if len(group) else None,
            "mean_net_r": float(pd.to_numeric(group.net_r, errors="coerce").mean()) if len(group) else None,
            "mean_gross_rr": float(pd.to_numeric(group.get("gross_rr"), errors="coerce").mean()) if len(group) else None,
            "median_hold_minutes": float(pd.to_numeric(group.get("holding_minutes"), errors="coerce").median()) if len(group) else None,
        }
    return output


def no_trade_audit(episodes: pd.DataFrame) -> pd.DataFrame:
    if episodes.empty or "order_exists" not in episodes:
        return episodes.iloc[:0].copy()
    no_trade = episodes[~_bool_series(episodes.order_exists)].copy()
    if no_trade.empty:
        return no_trade
    up = pd.to_numeric(no_trade.get("future_up_atr_diagnostic"), errors="coerce").fillna(0.0)
    down = pd.to_numeric(no_trade.get("future_down_atr_diagnostic"), errors="coerce").fillna(0.0)
    no_trade["favorable_future_excursion_atr"] = np.where(
        no_trade.side.astype(str).eq("LONG"), up, down
    )
    return no_trade.sort_values(
        ["favorable_future_excursion_atr", "order_time_ns"],
        ascending=[False, True],
    ).head(500)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)

    episodes, period_days, source_summaries = load_universe(args.root)
    if episodes.empty:
        raise RuntimeError(f"No episode artifacts found under {args.root}")
    orders = episodes[_bool_series(episodes["order_exists"])].copy()
    selected, closed, skipped, account = route_account(orders)
    audit = no_trade_audit(episodes)

    calendar_days = int(sum(period_days.values()))
    account["diagnostic_calendar_days"] = calendar_days
    account["closed_trades_per_diagnostic_day"] = float(len(closed) / calendar_days) if calendar_days else 0.0
    account["by_period"] = _group_metrics(closed, "period")
    account["by_role"] = _group_metrics(closed, "role")
    account["by_family"] = _group_metrics(closed, "family")
    account["by_symbol"] = _group_metrics(closed, "symbol")

    summary = {
        "policy": (
            "causal direction and asymmetric liquidity objective -> completed event -> "
            "one family-specific first-return entry -> structural stop -> nearest fresh "
            "opposing liquidity target -> same-minute causal arbitration -> market-wide "
            "episode de-duplication -> one continuous account slot"
        ),
        "episode_rows": int(len(episodes)),
        "order_rows": int(len(orders)),
        "no_trade_rows": int((~_bool_series(episodes.order_exists)).sum()),
        "account": account,
        "period_days": period_days,
        "source_summaries": source_summaries,
        "one_plan_per_symbol_episode": True,
        "one_trade_per_market_event": True,
        "same_minute_arbitration_is_causal": True,
        "fitted_admission_model": False,
        "symbol_identity_feature": False,
        "outcome_fields_used_for_decision": False,
        "future_diagnostics_used_for_decision": False,
        "fixed_rr_target_lattice": False,
        "target_selected_before_rr": True,
        "filled_position_exit_contract": "TAKE_PROFIT_OR_STOP_LOSS_ONLY",
        "single_global_account_slot": True,
        "diagnostic_windows_are_not_a_long_continuous_backtest": True,
    }

    episodes.to_csv(args.output / "all_episodes.csv.gz", index=False, compression="gzip")
    orders.to_csv(args.output / "all_plans.csv.gz", index=False, compression="gzip")
    selected.to_csv(args.output / "selected_orders.csv", index=False)
    closed.to_csv(args.output / "closed_trades.csv", index=False)
    skipped.to_csv(args.output / "market_event_or_account_conflicts.csv", index=False)
    audit.to_csv(args.output / "no_trade_opportunity_audit.csv", index=False)
    (args.output / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True, default=str))


if __name__ == "__main__":
    main()
