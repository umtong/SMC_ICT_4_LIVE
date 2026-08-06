#!/usr/bin/env python3
"""Diagnose impact resilience from official USD-M aggregate trades.

This structural screen uses no unsupported futures kline interval and creates no
orders, fills, PnL, cash balance or NAV.  A five-minute pool is public only after
two right-side bars complete.  Its literal first completed one-second crossing
opens a fixed fifteen-second observation.  A route requires completed OI release,
extreme attack-side quote flow, weak price progress per unit of that flow, low
path efficiency, full pool reclaim and opposite terminal flow.  Stops use the
whole observed event extreme; targets use unconsumed, already-confirmed one-
minute then five-minute pools.  Passing routes must still be implemented and
costed by NautilusTrader.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import date
import json
from pathlib import Path
from typing import Any, Iterable, Mapping

import numpy as np
import pandas as pd

import diagnose_impact_resilience_1s as impact
from data_aggtrades_1s import load_aggtrade_1s_bundle
from diagnose_failed_flow import aggregate_flow
from diagnose_impact_resilience_1s_v2 import (
    attach_causal_context_gap_safe,
    deduplicate_contact_pools,
)
from diagnose_session_handoff import _align_positioning
from smc_ict_4.manifest import write_json_atomic


def _payload(row: pd.Series) -> dict[str, Any]:
    return {
        "timestamp_ns": int(row["timestamp_ns"]),
        "timestamp": pd.to_datetime(int(row["timestamp_ns"]), unit="ns", utc=True).isoformat(),
        "open": float(row["open"]),
        "high": float(row["high"]),
        "low": float(row["low"]),
        "close": float(row["close"]),
        "quote_volume": float(row["quote_volume"]),
        "taker_buy_quote": float(row["taker_buy_quote"]),
        "taker_sell_quote": float(row["taker_sell_quote"]),
        "signed_quote": float(row["signed_quote"]),
        "trade_count": int(row["trade_count"]),
        "first_trade_ns": int(row["first_trade_ns"]),
        "last_trade_ns": int(row["last_trade_ns"]),
        "atr": None if pd.isna(row["atr"]) else float(row["atr"]),
        "positioning_valid": bool(row.get("positioning_valid", False)),
        "inventory_state": str(row.get("inventory_state", "INVALID")),
        "open_interest": (
            None if pd.isna(row.get("sum_open_interest")) else float(row["sum_open_interest"])
        ),
        "oi_change_fraction": (
            None if pd.isna(row.get("oi_change_fraction")) else float(row["oi_change_fraction"])
        ),
        "oi_impulse_rank": (
            None if pd.isna(row.get("oi_impulse_rank")) else float(row["oi_impulse_rank"])
        ),
    }


def diagnose(
    bars: pd.DataFrame,
    *,
    source_pools: Iterable[impact.Pool],
    target_pools: Mapping[str, Iterable[impact.Pool]],
    trade_start_ns: int,
    trade_end_ns: int,
    max_hold_seconds: int,
    logic: impact.ImpactLogic,
    require_oi_release: bool = True,
) -> dict[str, Any]:
    logic.validate()
    source_pool_list = list(source_pools)
    timestamps = bars["timestamp_ns"].astype("int64").to_numpy()
    highs = bars["high"].astype(float).to_numpy()
    lows = bars["low"].astype(float).to_numpy()
    closes = bars["close"].astype(float).to_numpy()
    previous_close = np.empty_like(closes)
    previous_close[0] = closes[0]
    previous_close[1:] = closes[:-1]

    contact_candidates: list[tuple[int, impact.Pool]] = []
    for pool in source_pool_list:
        touch = impact._first_touch_index(
            pool,
            timestamps=timestamps,
            previous_close=previous_close,
            highs=highs,
            lows=lows,
        )
        if touch is not None:
            contact_candidates.append((touch, pool))
    contact_candidates.sort(key=lambda item: (item[0], item[1].pool_id))

    counters: Counter[str] = Counter()
    scenarios: list[dict[str, Any]] = []
    block_until = -1
    target_touch_cache: dict[str, int | None] = {}

    for contact_index, pool in contact_candidates:
        timestamp_ns = int(timestamps[contact_index])
        if not trade_start_ns <= timestamp_ns < trade_end_ns:
            counters["CONTACT_OUTSIDE_TRADE_INTERVAL"] += 1
            continue
        if contact_index <= block_until:
            counters["CONTACT_DURING_ACTIVE_SLOT"] += 1
            continue
        if contact_index + logic.event_seconds > len(bars.index):
            counters["INCOMPLETE_EVENT_WINDOW"] += 1
            continue

        contact = bars.iloc[contact_index]
        if pd.isna(contact["atr"]) or float(contact["atr"]) <= 0.0:
            counters["NO_CAUSAL_ATR"] += 1
            continue
        if not bool(contact.get("positioning_valid", False)):
            counters["POSITIONING_INVALID"] += 1
            continue
        inventory_state = str(contact.get("inventory_state"))
        if require_oi_release and inventory_state != "RELEASE":
            counters[f"CONTACT_{inventory_state}"] += 1
            continue
        q90 = float(contact["buy_q"] if pool.side == "UPPER" else contact["sell_q"])
        if not np.isfinite(q90) or q90 <= 0.0:
            counters["FLOW_REFERENCE_WARMUP"] += 1
            continue

        window = bars.iloc[contact_index : contact_index + logic.event_seconds]
        if len(window.index) != logic.event_seconds:
            counters["INCOMPLETE_EVENT_WINDOW"] += 1
            continue
        differences = window["timestamp_ns"].astype("int64").diff().dropna()
        if bool((differences != impact.NS_PER_SECOND).any()):
            counters["ONE_SECOND_GAP_IN_EVENT"] += 1
            continue

        atr = float(contact["atr"])
        buy_quote = float(window["taker_buy_quote"].sum())
        sell_quote = float(window["taker_sell_quote"].sum())
        total_quote = buy_quote + sell_quote
        if total_quote <= 0.0:
            counters["ZERO_EVENT_FLOW"] += 1
            continue
        signed_imbalance = (buy_quote - sell_quote) / total_quote
        attack_quote = buy_quote if pool.side == "UPPER" else sell_quote
        flow_multiple = attack_quote / q90
        attack_imbalance = signed_imbalance if pool.side == "UPPER" else -signed_imbalance

        event_open = float(window.iloc[0]["open"])
        terminal_close = float(window.iloc[-1]["close"])
        close_path = np.concatenate(([event_open], window["close"].astype(float).to_numpy()))
        path_length = float(np.abs(np.diff(close_path)).sum())
        path_efficiency = abs(terminal_close - event_open) / path_length if path_length > 0.0 else 0.0
        if pool.side == "UPPER":
            event_extreme = float(window["high"].max())
            penetration = event_extreme - pool.level
            retrace_fraction = (event_extreme - terminal_close) / max(penetration, 1e-12)
            reclaimed = terminal_close < pool.level - logic.reclaim_buffer_atr * atr
            direction = "SHORT"
        else:
            event_extreme = float(window["low"].min())
            penetration = pool.level - event_extreme
            retrace_fraction = (terminal_close - event_extreme) / max(penetration, 1e-12)
            reclaimed = terminal_close > pool.level + logic.reclaim_buffer_atr * atr
            direction = "LONG"
        penetration_atr = penetration / atr
        impact_per_flow = penetration_atr / max(flow_multiple, 1e-12)

        terminal = window.iloc[-logic.terminal_seconds :]
        terminal_buy = float(terminal["taker_buy_quote"].sum())
        terminal_sell = float(terminal["taker_sell_quote"].sum())
        terminal_total = terminal_buy + terminal_sell
        terminal_imbalance = (
            (terminal_buy - terminal_sell) / terminal_total if terminal_total > 0.0 else 0.0
        )
        terminal_body = float(terminal.iloc[-1]["close"]) - float(terminal.iloc[0]["open"])
        opposite_flow = (
            terminal_imbalance <= -logic.minimum_terminal_opposite_imbalance
            if direction == "SHORT"
            else terminal_imbalance >= logic.minimum_terminal_opposite_imbalance
        )
        opposite_body = (
            terminal_body <= -logic.minimum_terminal_body_atr * atr
            if direction == "SHORT"
            else terminal_body >= logic.minimum_terminal_body_atr * atr
        )

        conditions = {
            "flow_multiple": flow_multiple >= logic.minimum_flow_multiple,
            "attack_imbalance": attack_imbalance >= logic.minimum_attack_imbalance,
            "penetration": logic.minimum_penetration_atr <= penetration_atr <= logic.maximum_penetration_atr,
            "impact_per_flow": impact_per_flow <= logic.maximum_impact_per_flow,
            "path_efficiency": path_efficiency <= logic.maximum_path_efficiency,
            "retrace_fraction": retrace_fraction >= logic.minimum_retrace_fraction,
            "pool_reclaim": reclaimed,
            "terminal_opposite_flow": opposite_flow,
            "terminal_opposite_body": opposite_body,
        }
        failed = [name for name, passed in conditions.items() if not passed]
        diagnostic = {
            "pool_id": pool.pool_id,
            "pool_side": pool.side,
            "liquidity_level": pool.level,
            "contact": _payload(contact),
            "event_terminal": _payload(window.iloc[-1]),
            "direction": direction,
            "flow_multiple": flow_multiple,
            "attack_imbalance": attack_imbalance,
            "penetration_atr": penetration_atr,
            "impact_per_flow": impact_per_flow,
            "path_efficiency": path_efficiency,
            "retrace_fraction": retrace_fraction,
            "terminal_imbalance": terminal_imbalance,
            "terminal_body_atr": terminal_body / atr,
            "inventory_state": inventory_state,
            "conditions": conditions,
        }
        if failed:
            counters[f"REJECT_{failed[0].upper()}"] += 1
            scenarios.append(
                {
                    "scenario_id": f"c07agg-{timestamp_ns}-{pool.pool_id}",
                    "outcome": "EVENT_REJECTED",
                    "failed_conditions": failed,
                    **diagnostic,
                }
            )
            continue

        entry_index = contact_index + logic.event_seconds - 1
        entry = terminal_close
        stop = (
            event_extreme + logic.stop_buffer_atr * atr
            if direction == "SHORT"
            else event_extreme - logic.stop_buffer_atr * atr
        )
        risk = stop - entry if direction == "SHORT" else entry - stop
        if risk <= 0.0:
            counters["NONPOSITIVE_RISK"] += 1
            continue
        selected = impact._target_pool(
            target_pools,
            direction=direction,
            entry=entry,
            stop=stop,
            entry_index=entry_index,
            timestamps=timestamps,
            previous_close=previous_close,
            highs=highs,
            lows=lows,
            touch_cache=target_touch_cache,
            minimum_rr=logic.minimum_rr,
        )
        if selected is None:
            counters["NO_CAUSAL_TARGET_AT_MINIMUM_RR"] += 1
            scenarios.append(
                {
                    "scenario_id": f"c07agg-{timestamp_ns}-{pool.pool_id}",
                    "outcome": "NO_CAUSAL_TARGET_AT_MINIMUM_RR",
                    "entry": entry,
                    "stop": stop,
                    "risk": risk,
                    **diagnostic,
                }
            )
            continue
        target_pool, expected_rr = selected
        path, terminal_index = impact._path_result(
            bars,
            start_index=entry_index,
            direction=direction,
            entry=entry,
            stop=stop,
            target=target_pool.level,
            max_hold_seconds=max_hold_seconds,
        )
        block_until = max(block_until, terminal_index)
        counters["ENTRY_READY"] += 1
        scenarios.append(
            {
                "scenario_id": f"c07agg-{timestamp_ns}-{pool.pool_id}",
                "outcome": "ENTRY_READY",
                "entry": entry,
                "stop": stop,
                "risk": risk,
                "target": target_pool.level,
                "target_pool_id": target_pool.pool_id,
                "target_timeframe": target_pool.timeframe,
                "expected_rr": expected_rr,
                "path": path,
                **diagnostic,
            }
        )

    entries = [item for item in scenarios if item.get("outcome") == "ENTRY_READY"]
    outcomes = Counter(str(item["path"]["outcome"]) for item in entries)
    dates = Counter(
        pd.to_datetime(int(item["contact"]["timestamp_ns"]), unit="ns", utc=True)
        .date().isoformat()
        for item in entries
    )
    mfe = [float(item["path"]["mfe_r"]) for item in entries]
    mae = [float(item["path"]["mae_r"]) for item in entries]
    maximum_day_share = max(dates.values()) / len(entries) if entries and dates else None
    gate = {
        "minimum_entry_ready": len(entries) >= 7,
        "minimum_active_days": len(dates) >= 4,
        "more_targets_than_stops": outcomes["TARGET"] > outcomes["STOP"],
        "median_mfe_at_least_minimum_rr": bool(mfe) and float(pd.Series(mfe).median()) >= logic.minimum_rr,
        "median_mae_below_one_r": bool(mae) and float(pd.Series(mae).median()) < 1.0,
        "maximum_day_share_at_most_55pct": maximum_day_share is not None and maximum_day_share <= 0.55,
    }
    gate["passed"] = all(gate.values())
    return {
        "summary": {
            "source_pools": len(source_pool_list),
            "source_pools_touched": len(contact_candidates),
            "contact_counts": dict(sorted(counters.items())),
            "entry_ready": len(entries),
            "active_days": len(dates),
            "entries_by_day": dict(sorted(dates.items())),
            "maximum_day_share": maximum_day_share,
            "path_outcome_counts": dict(sorted(outcomes.items())),
            "target_minus_stop": outcomes["TARGET"] - outcomes["STOP"],
            "median_mfe_r": float(pd.Series(mfe).median()) if mfe else None,
            "median_mae_r": float(pd.Series(mae).median()) if mae else None,
            "diagnostic_gate": gate,
        },
        "scenarios": scenarios,
    }


def run(args: argparse.Namespace) -> int:
    config = json.loads(args.config.read_text(encoding="utf-8"))
    logic = impact.ImpactLogic()
    logic.validate()
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    expected_warmup = int(config["warmup_days"])
    if args.event_warmup_days != expected_warmup:
        raise ValueError(
            "event_warmup_days must equal config warmup_days so pre-trade pool "
            f"consumption is observable: {args.event_warmup_days} != {expected_warmup}"
        )
    bundle = load_aggtrade_1s_bundle(
        symbol=str(config["symbol"]),
        trade_start=args.start,
        trade_end=args.end,
        positioning_warmup_days=expected_warmup,
        event_warmup_days=args.event_warmup_days,
        cache_root=args.data_root.resolve(),
        manifest_destination=output.with_name("aggtrade_resilience_data_manifest.json"),
    )
    minute = impact._minute_features(
        bundle.minute_positioning.frame,
        atr_period=logic.minute_atr_period,
    )
    five = aggregate_flow(bundle.minute_positioning.frame, 5, 36)
    five = _align_positioning(
        five,
        bundle.minute_positioning.metrics,
        oi_period=logic.oi_period,
        oi_impulse_rank=logic.oi_impulse_rank,
    )
    five["timestamp_ns"] = five["timestamp_ns"].astype("int64")
    bars = attach_causal_context_gap_safe(
        bundle.seconds,
        minute,
        five,
        history_windows=logic.history_windows,
        flow_quantile=logic.flow_quantile,
    )
    one_minute_pools = impact._pool_confirmations(
        minute,
        timeframe="1M",
        radius=logic.one_minute_pivot_radius,
    )
    five_minute_pools = impact._pool_confirmations(
        five,
        timeframe="5M",
        radius=logic.five_minute_pivot_radius,
    )
    source_pools, collision_summary = deduplicate_contact_pools(
        bars,
        five_minute_pools,
    )
    result = diagnose(
        bars,
        source_pools=source_pools,
        target_pools={"1M": one_minute_pools, "5M": five_minute_pools},
        trade_start_ns=impact._utc_ns(args.start),
        trade_end_ns=impact._utc_ns(args.end),
        max_hold_seconds=int(config["max_hold_minutes"]) * 60,
        logic=logic,
        require_oi_release=not args.ablate_oi_release,
    )
    payload = {
        "candidate": "candidate-07",
        "stage": args.stage,
        "variant": "ablation_no_oi_release" if args.ablate_oi_release else "baseline",
        "hypothesis": "aggregate-trade impact resilience after liquidity attack",
        "period": {
            "start": args.start.isoformat(),
            "end_exclusive": args.end.isoformat(),
        },
        "logic": {name: getattr(logic, name) for name in logic.__dataclass_fields__},
        "data_contract": {
            "flow_and_price": "checksum-verified Binance USD-M aggTrades reduced to completed one-second bars",
            "aggressor_side": "buyer-maker false is aggressive buy; true is aggressive sell",
            "missing_second_policy": "not synthesized; candidate event requires exact one-second continuity",
            "positioning": "completed five-minute OI joined backward-as-of; gaps invalidate",
            "contact_pool": "five-minute pivot confirmed after two completed right-side bars",
            "target_hierarchy": "unconsumed, confirmed one-minute then five-minute pools",
            "pool_reuse": False,
            "single_pending_or_open_slot": True,
            "future_information": False,
            "orders_or_pnl": False,
        },
        "loader_diagnostics": bundle.diagnostics,
        "pool_collision_summary": collision_summary,
        **result,
    }
    write_json_atomic(output, payload)
    print(json.dumps({
        "variant": payload["variant"],
        "summary": payload["summary"],
        "loader_diagnostics": payload["loader_diagnostics"],
        "pool_collision_summary": payload["pool_collision_summary"],
    }, indent=2, sort_keys=True))
    return 0


def build_parser() -> argparse.ArgumentParser:
    candidate_dir = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=candidate_dir / "config.json")
    parser.add_argument("--stage", default="week-1")
    parser.add_argument("--start", type=date.fromisoformat, required=True)
    parser.add_argument("--end", type=date.fromisoformat, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--data-root", type=Path, default=Path(".research-data/candidate-07"))
    parser.add_argument("--event-warmup-days", type=int, default=3)
    parser.add_argument("--ablate-oi-release", action="store_true")
    return parser


def main() -> int:
    return run(build_parser().parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
