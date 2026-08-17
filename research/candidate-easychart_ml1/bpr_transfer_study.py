#!/usr/bin/env python3
"""Complete causal liquidity-transfer narratives with BPR/IFVG entry geometry.

This study replaces generic level reactions with a market narrative:

* one physical interaction consumes every overlapping liquidity-pool alias;
* a sweep must reclaim quickly or an accepted break must hold;
* reversal actions require BPR, IFVG, or post-MSS displacement geometry;
* continuation actions require accepted displacement and first mitigation;
* invalidation remains beyond the sweep/source structure;
* the objective is unswept opposite liquidity at the same scale or higher;
* spot/perpetual price discovery, OI, positioning and cross-asset state are
  observable context, not hindsight gates;
* every alternative belongs to one episode and downstream routing may choose one.

Future paths, funding settlements and barrier outcomes are appended only after the
plan has been frozen. They are research labels and never live inputs.
"""
from __future__ import annotations

import argparse
from datetime import date, timedelta
import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from auction_transition_study import (
    SYMBOLS,
    TICKS,
    TAKER,
    MAKER,
    ENTRY_SLIPPAGE_TICKS,
    STOP_SLIPPAGE_TICKS,
    add_cross_features,
    make_features,
    snapshot_features,
)
from data_derivatives import join_metrics_causally, load_metrics_range
from data_funding import funding_return, load_funding_range
from data_re1_flow import load_range_flow
from data_spot_flow import load_spot_range_flow
from liquidity_event_graph import (
    EntryZone,
    InteractionEpisode,
    build_continuation_entry_zones,
    build_interaction_episodes,
    build_reversal_entry_zones,
    comparable_opposing_target,
    find_first_zone_retest,
    internal_pivots,
)
from spot_perp_context import (
    add_spot_perp_context,
    spot_perp_snapshot,
    venue_window_features,
)

R_TARGETS = (0.75, 1.0, 1.25, 1.5, 2.0)
MAX_HOLD_MINUTES = 480


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _metric_snapshot(
    frame: pd.DataFrame,
    ts: pd.Timestamp,
    side: int,
    prefix: str,
) -> dict[str, float]:
    before = frame.loc[frame.index <= ts]
    if before.empty:
        return {}
    row = before.iloc[-1]
    directional = (
        "metric_taker_imbalance",
        "metric_taker_imbalance_z",
        "global_account_imbalance",
        "global_account_imbalance_z",
        "top_account_imbalance",
        "top_account_imbalance_z",
        "top_position_imbalance",
        "top_position_imbalance_z",
    )
    neutral = (
        "metric_age_minutes",
        "oi_value_change_5",
        "oi_value_change_15",
        "oi_value_change_30",
        "oi_value_change_60",
        "oi_value_change_180",
        "oi_contracts_change_5",
        "oi_contracts_change_15",
        "oi_contracts_change_30",
        "oi_contracts_change_60",
        "oi_contracts_change_180",
        "oi_change_15_z",
        "oi_change_60_z",
    )
    output: dict[str, float] = {}
    for key in neutral:
        value = _finite(row.get(key))
        if value is not None:
            output[f"{prefix}_{key}"] = value
    for key in directional:
        value = _finite(row.get(key))
        if value is not None:
            output[f"{prefix}_aligned_{key}"] = side * value
    return output


def _oi_log_change(
    frame: pd.DataFrame,
    start: pd.Timestamp,
    end: pd.Timestamp,
) -> float | None:
    left = frame.loc[frame.index <= start]
    right = frame.loc[frame.index <= end]
    if left.empty or right.empty:
        return None
    first = _finite(left.iloc[-1].get("oi_value_log"))
    last = _finite(right.iloc[-1].get("oi_value_log"))
    if first is None or last is None:
        return None
    return last - first


def _simulate_plan(
    frame: pd.DataFrame,
    funding: pd.DataFrame,
    side: int,
    entry_i: int,
    entry: float,
    stop: float,
    target: float,
    tick: float,
) -> dict[str, Any]:
    """Conservative minute-bar first passage with costs, funding and timeout."""
    entry_ts = frame.index[entry_i]
    entry_fill = entry + side * ENTRY_SLIPPAGE_TICKS * tick
    stop_fill = stop - side * STOP_SLIPPAGE_TICKS * tick
    target_fill = target
    planned_stop_return = (
        side * (stop_fill - entry_fill) / entry_fill - 2.0 * TAKER
    )
    planned_target_return = (
        side * (target_fill - entry_fill) / entry_fill - TAKER - MAKER
    )
    risk_fraction = -planned_stop_return
    if risk_fraction <= 0.0:
        raise RuntimeError(f"non-positive planned risk: {risk_fraction}")

    end_i = min(entry_i + MAX_HOLD_MINUTES - 1, len(frame) - 1)
    outcome = "TIMEOUT"
    exit_i = end_i
    exit_fill = float(frame.iloc[end_i].close) - side * ENTRY_SLIPPAGE_TICKS * tick
    exit_fee = TAKER
    for i in range(entry_i, end_i + 1):
        bar = frame.iloc[i]
        stop_hit = (
            float(bar.low) <= stop
            if side > 0
            else float(bar.high) >= stop
        )
        target_hit = (
            float(bar.high) >= target
            if side > 0
            else float(bar.low) <= target
        )
        if stop_hit:
            outcome = "AMBIGUOUS_SAME_MINUTE" if target_hit else "STOP_FIRST"
            exit_i = i
            exit_fill = stop_fill
            exit_fee = TAKER
            break
        if target_hit:
            outcome = "TARGET_FIRST"
            exit_i = i
            exit_fill = target_fill
            exit_fee = MAKER
            break
    exit_ts = frame.index[exit_i]
    gross_return = side * (exit_fill - entry_fill) / entry_fill
    fees = TAKER + exit_fee
    funding_component = funding_return(funding, side, entry_ts, exit_ts)
    net_return = gross_return - fees + funding_component
    realized_r = net_return / risk_fraction
    return {
        "outcome": outcome,
        "exit_ts": exit_ts.isoformat(),
        "exit_price": exit_fill,
        "duration_minutes": int(exit_i - entry_i + 1),
        "gross_return": gross_return,
        "fee_return": -fees,
        "funding_return": funding_component,
        "net_return": net_return,
        "planned_loss_return": planned_stop_return,
        "planned_target_return": planned_target_return,
        "risk_fraction_of_price": risk_fraction,
        "planned_target_r": planned_target_return / risk_fraction,
        "realized_r": realized_r,
        "target_first": int(outcome == "TARGET_FIRST"),
        "stopped": int(outcome in {"STOP_FIRST", "AMBIGUOUS_SAME_MINUTE"}),
        "timed_out": int(outcome == "TIMEOUT"),
    }


def _path_labels(
    frame: pd.DataFrame,
    side: int,
    entry_i: int,
    entry: float,
    risk_price: float,
) -> dict[str, float]:
    output: dict[str, float] = {}
    for horizon in (5, 10, 15, 30, 60, 120, 240, 480):
        future = frame.iloc[entry_i : min(entry_i + horizon, len(frame))]
        if future.empty:
            continue
        if side > 0:
            mfe = (float(future.high.max()) - entry) / risk_price
            mae = (entry - float(future.low.min())) / risk_price
        else:
            mfe = (entry - float(future.low.min())) / risk_price
            mae = (float(future.high.max()) - entry) / risk_price
        output[f"mfe_r_{horizon}"] = mfe
        output[f"mae_r_{horizon}"] = mae
    return output


def _source_features(episode: InteractionEpisode) -> dict[str, Any]:
    pool = episode.primary_pool
    return {
        "source_pool_id": pool.pool_id,
        "source_pool_side": pool.side,
        "source_primary_timeframe": pool.timeframe,
        "source_primary_span": pool.span,
        "source_primary_equality_atr": pool.equality_atr,
        "source_primary_strength": pool.strength,
        "source_primary_separation_bars": pool.separation_bars,
        "source_pool_count": episode.source_pool_count,
        "source_min_timeframe": episode.source_min_timeframe,
        "source_max_timeframe": episode.source_max_timeframe,
        "source_timeframe_count": len(episode.source_timeframes),
        "source_strength_max": episode.source_strength_max,
        "source_strength_mean": episode.source_strength_mean,
        "source_equality_min": episode.source_equality_min,
        "source_equality_mean": episode.source_equality_mean,
        "source_age_minutes": (
            episode.interaction_ts - pool.observed_ts
        ) / pd.Timedelta(minutes=1),
        "source_alias_ids": "|".join(episode.touched_pool_ids),
    }


def _zone_features(
    zone: EntryZone,
    mitigation: pd.Series,
    side: int,
    tick: float,
) -> dict[str, Any]:
    width = max(zone.upper - zone.lower, tick)
    return {
        "action_family": zone.action_family,
        "action_id": zone.action_id,
        "zone_formed_ts": zone.formed_ts.isoformat(),
        "zone_lower": zone.lower,
        "zone_upper": zone.upper,
        "zone_width_bps": width / float(mitigation.close) * 1e4,
        "adverse_gap_id": zone.adverse_gap_id,
        "aligned_gap_id": zone.aligned_gap_id,
        "mss_ts": None if zone.mss_ts is None else zone.mss_ts.isoformat(),
        "mss_level": zone.mss_level,
        "bpr_overlap_fraction": zone.bpr_overlap_fraction,
        "ifvg_close_through_sigma": zone.ifvg_close_through_sigma,
        "mitigation_penetration": (
            max(zone.upper - float(mitigation.low), 0.0) / width
            if side > 0
            else max(float(mitigation.high) - zone.lower, 0.0) / width
        ),
        "mitigation_close_location_aligned": (
            float(mitigation.close_location)
            if side > 0
            else 1.0 - float(mitigation.close_location)
        ),
        "mitigation_delta_aligned": side * float(mitigation.delta_share_1),
        "mitigation_range_ratio": float(mitigation.range_ratio),
        "mitigation_activity_ratio": float(mitigation.activity_ratio),
    }


def build_action_row(
    symbol: str,
    period: str,
    frame: pd.DataFrame,
    funding: pd.DataFrame,
    pools: list,
    pivots: list,
    episode: InteractionEpisode,
    zone: EntryZone,
    start_ts: pd.Timestamp,
    end_ts: pd.Timestamp,
) -> dict[str, Any] | None:
    if episode.interaction_ts < start_ts or episode.interaction_ts > end_ts:
        return None
    tick = TICKS[symbol]
    pool = episode.primary_pool
    buffer = max(2.0 * tick, 0.04 * pool.atr)
    if episode.state == "SWEEP_RECLAIM":
        hard_invalidation = (
            episode.sweep_extreme - buffer
            if episode.side > 0
            else episode.sweep_extreme + buffer
        )
    else:
        hard_invalidation = (
            pool.inner - buffer
            if episode.side > 0
            else pool.inner + buffer
        )
    mitigation_i = find_first_zone_retest(
        frame,
        episode.side,
        zone,
        hard_invalidation,
        tick,
        max_minutes=120,
    )
    if mitigation_i is None or mitigation_i >= len(frame) - 1:
        return None
    decision_ts = frame.index[mitigation_i]
    entry_i = mitigation_i + 1
    entry_ts = frame.index[entry_i]
    entry = float(frame.iloc[entry_i].open)
    mitigation = frame.iloc[mitigation_i]
    stop = hard_invalidation
    risk_price = abs(entry - stop)
    if (
        entry <= 0.0
        or risk_price <= tick
        or (episode.side > 0 and stop >= entry)
        or (episode.side < 0 and stop <= entry)
    ):
        return None
    target_info = comparable_opposing_target(
        frame,
        pools,
        pivots,
        episode,
        entry,
        decision_ts,
    )
    if target_info is None:
        return None
    target, target_id, target_timeframe, target_kind = target_info
    if (
        (episode.side > 0 and target <= entry)
        or (episode.side < 0 and target >= entry)
    ):
        return None

    prior_sigma = max(float(frame.loc[decision_ts, "prior_sigma"]), 1e-12)
    interaction_depth = (
        (pool.outer - episode.sweep_extreme) / entry / prior_sigma
        if pool.side == "LOW"
        else (episode.sweep_extreme - pool.outer) / entry / prior_sigma
    )
    row: dict[str, Any] = {
        "period": period,
        "plan_id": f"BPR:{episode.episode_id}:{zone.action_id}",
        "episode_id": episode.episode_id,
        "symbol": symbol,
        "state": episode.state,
        "side": "LONG" if episode.side > 0 else "SHORT",
        "side_sign": episode.side,
        "interaction_ts": episode.interaction_ts.isoformat(),
        "confirm_ts": episode.confirm_ts.isoformat(),
        "decision_ts": decision_ts.isoformat(),
        "entry_ts": entry_ts.isoformat(),
        "entry": entry,
        "stop": stop,
        "structural_target": target,
        "target_liquidity_id": target_id,
        "target_timeframe": target_timeframe,
        "target_kind": target_kind,
        "structural_gross_rr": abs(target - entry) / risk_price,
        "risk_bps": risk_price / entry * 1e4,
        "risk_sigma": risk_price / entry / prior_sigma,
        "interaction_depth_sigma": interaction_depth,
        "confirmation_delay_minutes": (
            episode.confirm_ts - episode.interaction_ts
        ) / pd.Timedelta(minutes=1),
        "zone_formation_delay_minutes": (
            zone.formed_ts - episode.interaction_ts
        ) / pd.Timedelta(minutes=1),
        "mitigation_delay_minutes": (
            decision_ts - zone.formed_ts
        ) / pd.Timedelta(minutes=1),
    }
    row.update(_source_features(episode))
    row.update(_zone_features(zone, mitigation, episode.side, tick))
    row.update(snapshot_features(frame, decision_ts, episode.side))
    row.update(spot_perp_snapshot(frame, episode.interaction_ts, episode.side))
    row.update(
        {
            f"decision_{key}": value
            for key, value in spot_perp_snapshot(
                frame,
                decision_ts,
                episode.side,
            ).items()
        }
    )
    row.update(
        venue_window_features(
            frame,
            episode.interaction_ts - pd.Timedelta(minutes=4),
            episode.confirm_ts,
            episode.side,
            "event_venue",
        )
    )
    row.update(
        _metric_snapshot(
            frame,
            episode.interaction_ts,
            episode.side,
            "interaction",
        )
    )
    row.update(
        _metric_snapshot(
            frame,
            episode.confirm_ts,
            episode.side,
            "confirm",
        )
    )
    row.update(
        _metric_snapshot(
            frame,
            decision_ts,
            episode.side,
            "decision",
        )
    )
    event_oi = _oi_log_change(
        frame,
        episode.interaction_ts - pd.Timedelta(minutes=15),
        episode.confirm_ts,
    )
    if event_oi is not None:
        row["event_oi_value_change"] = event_oi
        row["aligned_event_oi_price_interaction"] = episode.side * event_oi
        row["deleveraging_intensity"] = -event_oi
    post_oi = _oi_log_change(frame, episode.confirm_ts, decision_ts)
    if post_oi is not None:
        row["post_confirm_oi_value_change"] = post_oi
        row["aligned_post_confirm_oi_change"] = episode.side * post_oi
    row.update(_path_labels(frame, episode.side, entry_i, entry, risk_price))

    row.update(
        {
            f"structural_{key}": value
            for key, value in _simulate_plan(
                frame,
                funding,
                episode.side,
                entry_i,
                entry,
                stop,
                target,
                tick,
            ).items()
        }
    )
    for r in R_TARGETS:
        tag = str(r).replace(".", "p")
        objective = entry + episode.side * risk_price * r
        row[f"r_{tag}_target"] = objective
        labelled = _simulate_plan(
            frame,
            funding,
            episode.side,
            entry_i,
            entry,
            stop,
            objective,
            tick,
        )
        for key, value in labelled.items():
            row[f"r_{tag}_{key}"] = value
    return row


def harvest_symbol(
    symbol: str,
    period: str,
    frame: pd.DataFrame,
    funding: pd.DataFrame,
    start_ts: pd.Timestamp,
    end_ts: pd.Timestamp,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    episodes, pools, pivots = build_interaction_episodes(symbol, frame)
    pivots1, pivots5 = internal_pivots(frame)
    rows: list[dict[str, Any]] = []
    diagnostics: dict[str, Any] = {
        "episodes": len(episodes),
        "sweep_reclaims": sum(
            item.state == "SWEEP_RECLAIM" for item in episodes
        ),
        "accepted_breaks": sum(
            item.state == "ACCEPTED_BREAK" for item in episodes
        ),
        "entry_zones": 0,
        "plans": 0,
        "by_action": {},
    }
    for episode in episodes:
        if episode.state == "SWEEP_RECLAIM":
            zones = build_reversal_entry_zones(
                frame,
                episode,
                TICKS[symbol],
                pivots1,
                pivots5,
            )
        else:
            zones = build_continuation_entry_zones(
                frame,
                episode,
                TICKS[symbol],
            )
        diagnostics["entry_zones"] += len(zones)
        for zone in zones:
            diagnostics["by_action"][zone.action_family] = (
                diagnostics["by_action"].get(zone.action_family, 0) + 1
            )
            row = build_action_row(
                symbol,
                period,
                frame,
                funding,
                pools,
                pivots,
                episode,
                zone,
                start_ts,
                end_ts,
            )
            if row is not None:
                rows.append(row)
                diagnostics["plans"] += 1
    return rows, diagnostics


def _objective_summary(
    group: pd.DataFrame,
    prefix: str,
) -> dict[str, Any]:
    realized = pd.to_numeric(
        group[f"{prefix}_realized_r"],
        errors="coerce",
    )
    return {
        "rows": int(len(group)),
        "target_first_rate": float(
            pd.to_numeric(group[f"{prefix}_target_first"]).mean()
        ),
        "stop_rate": float(
            pd.to_numeric(group[f"{prefix}_stopped"]).mean()
        ),
        "timeout_rate": float(
            pd.to_numeric(group[f"{prefix}_timed_out"]).mean()
        ),
        "mean_realized_r": float(realized.mean()),
        "median_realized_r": float(realized.median()),
        "mean_planned_target_r": float(
            pd.to_numeric(group[f"{prefix}_planned_target_r"]).mean()
        ),
        "mean_duration_minutes": float(
            pd.to_numeric(group[f"{prefix}_duration_minutes"]).mean()
        ),
    }


def _summary(
    events: pd.DataFrame,
    start: date,
    end: date,
    diagnostics: dict[str, Any],
) -> dict[str, Any]:
    days = (end - start).days + 1
    output: dict[str, Any] = {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "action_rows": int(len(events)),
        "independent_episodes": (
            int(events.episode_id.nunique()) if not events.empty else 0
        ),
        "episodes_per_day": (
            float(events.episode_id.nunique() / days)
            if not events.empty
            else 0.0
        ),
        "diagnostics": diagnostics,
        "causality": (
            "POOL_ALIASES_COLLAPSED_THEN_FROZEN_ACTION_NEXT_OPEN_THEN_FUTURE_LABEL"
        ),
    }
    if events.empty:
        return output
    output["by_action"] = {}
    for action, group in events.groupby("action_family"):
        output["by_action"][action] = {
            "rows": int(len(group)),
            "episodes": int(group.episode_id.nunique()),
            "risk_bps_median": float(group.risk_bps.median()),
            "r_1p0": _objective_summary(group, "r_1p0"),
            "r_1p25": _objective_summary(group, "r_1p25"),
            "r_1p5": _objective_summary(group, "r_1p5"),
            "r_2p0": _objective_summary(group, "r_2p0"),
            "structural": _objective_summary(group, "structural"),
        }
    return output


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--period", required=True)
    parser.add_argument("--start", type=date.fromisoformat, required=True)
    parser.add_argument("--end", type=date.fromisoformat, required=True)
    parser.add_argument("--warmup-days", type=int, default=28)
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    warmup_start = args.start - timedelta(days=args.warmup_days)
    start_ts = pd.Timestamp(args.start, tz="UTC")
    end_ts = (
        pd.Timestamp(args.end + timedelta(days=1), tz="UTC")
        - pd.Timedelta(minutes=1)
    )

    futures_raw = {
        symbol: load_range_flow(
            symbol,
            warmup_start,
            args.end,
            args.cache / "futures",
        )
        for symbol in SYMBOLS
    }
    spot_raw = {
        symbol: load_spot_range_flow(
            symbol,
            warmup_start,
            args.end,
            args.cache,
        )
        for symbol in SYMBOLS
    }
    frames = {
        symbol: make_features(symbol, raw)
        for symbol, raw in futures_raw.items()
    }
    frames = add_cross_features(frames)
    funding: dict[str, pd.DataFrame] = {}
    for symbol in SYMBOLS:
        frames[symbol] = add_spot_perp_context(
            frames[symbol],
            symbol,
            spot_raw[symbol],
        )
        metrics = load_metrics_range(
            symbol,
            warmup_start,
            args.end,
            args.cache,
        )
        frames[symbol] = join_metrics_causally(frames[symbol], metrics)
        funding[symbol] = load_funding_range(
            symbol,
            warmup_start,
            args.end,
            args.cache,
        )

    rows: list[dict[str, Any]] = []
    diagnostics: dict[str, Any] = {}
    for symbol in SYMBOLS:
        symbol_rows, symbol_diagnostics = harvest_symbol(
            symbol,
            args.period,
            frames[symbol],
            funding[symbol],
            start_ts,
            end_ts,
        )
        rows.extend(symbol_rows)
        diagnostics[symbol] = symbol_diagnostics
    events = pd.DataFrame(rows)
    if not events.empty:
        events = events.sort_values(
            ["entry_ts", "episode_id", "action_family", "plan_id"]
        ).reset_index(drop=True)
        if events.plan_id.duplicated().any():
            duplicate = events.loc[
                events.plan_id.duplicated(),
                "plan_id",
            ].head().tolist()
            raise RuntimeError(f"duplicate plan identities: {duplicate}")
        alternatives = events.groupby("episode_id").size()
        events["episode_alternative_count"] = (
            events.episode_id.map(alternatives).astype(int)
        )
        events["episode_weight"] = 1.0 / events.episode_alternative_count

    args.output.mkdir(parents=True, exist_ok=True)
    events.to_csv(args.output / "events.csv", index=False)
    (args.output / "summary.json").write_text(
        json.dumps(
            _summary(events, args.start, args.end, diagnostics),
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
