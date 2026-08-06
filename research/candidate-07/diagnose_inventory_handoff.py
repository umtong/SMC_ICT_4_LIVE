#!/usr/bin/env python3
"""Diagnose post-liquidation inventory handoffs at causal 15-minute pools.

This candidate addresses a specific failure observed in earlier candidates:
contemporaneous OI release at a liquidity contact does not tell us whether forced
inventory is exhausted or still propagating.  The state machine therefore waits
for the *next inventory transition* before declaring a trading scenario.

Pool formation and scenario execution are separated:

- confirmed 15-minute swing highs/lows form public pools only after two completed
  right-side bars;
- completed five-minute bars provide price, aggressor flow and OI state;
- a pool is consumed on first causal contact;
- OI release at contact opens one pending liquidation episode;
- continued OI release + outside hold + same-direction displacement routes a
  liquidation continuation;
- OI release followed by opposite-side OI build + reclaim + opposite displacement
  routes a liquidation-exhaustion reversal;
- a structural stop and the next unconsumed confirmed 15-minute pool are declared
  before the future path is inspected.

The script is an alpha diagnostic, not a backtest engine.  It creates no orders,
fills, fees, cash ledger, PnL or NAV.  A route is implemented in NautilusTrader
only if this causal market-path test shows adequate density and expectancy.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date
import json
from pathlib import Path
from typing import Any, Mapping

import pandas as pd

from data_positioning import load_positioning_bundle
from diagnose_failed_flow import aggregate_flow
from diagnose_mtf_liquidity import Pool, context_bars, pool_confirmations
from diagnose_session_handoff import _align_positioning
from smc_ict_4.manifest import write_json_atomic


@dataclass(slots=True)
class LiquidationEpisode:
    scenario_id: str
    contact_index: int
    pool_id: str
    pool_side: str
    liquidity_level: float
    attack_direction: str
    reversal_direction: str
    extreme: float
    atr: float
    contact_inventory_state: str
    contact_oi_change: float
    contact_oi_rank: float
    contact_imbalance: float
    contact_flow_z: float


@dataclass(frozen=True, slots=True)
class HandoffLogic:
    contact_min_atr: float = 0.05
    contact_max_atr: float = 1.50
    attack_imbalance: float = 0.08
    flow_z: float = 0.25
    reclaim_buffer_atr: float = 0.02
    outside_buffer_atr: float = 0.02
    confirmation_body_atr: float = 0.12
    confirmation_imbalance: float = 0.02
    stop_buffer_atr: float = 0.10
    handoff_bars: int = 3
    minimum_rr: float = 1.25
    max_hold_bars: int = 24
    oi_period: int = 36
    oi_impulse_rank: float = 0.50

    def validate(self) -> None:
        if not 0.0 < self.contact_min_atr < self.contact_max_atr:
            raise ValueError("contact penetration bounds are inconsistent")
        if not 0.0 < self.attack_imbalance < 1.0:
            raise ValueError("attack_imbalance must be in (0, 1)")
        if not 0.0 <= self.confirmation_imbalance < 1.0:
            raise ValueError("confirmation_imbalance must be in [0, 1)")
        if self.handoff_bars <= 0 or self.max_hold_bars <= 0:
            raise ValueError("bar counts must be positive")
        if self.minimum_rr <= 0.0:
            raise ValueError("minimum_rr must be positive")
        if self.oi_period <= 0 or not 0.0 <= self.oi_impulse_rank <= 1.0:
            raise ValueError("OI rank configuration is inconsistent")


def _bar_payload(row: pd.Series) -> dict[str, Any]:
    return {
        "timestamp_ns": int(row["timestamp_ns"]),
        "timestamp": row["timestamp"].isoformat(),
        "open": float(row["open"]),
        "high": float(row["high"]),
        "low": float(row["low"]),
        "close": float(row["close"]),
        "volume": float(row["volume"]),
        "imbalance": float(row["imbalance"]),
        "flow_z": float(row["flow_z"]),
        "atr": None if pd.isna(row["atr"]) else float(row["atr"]),
        "positioning_valid": bool(row["positioning_valid"]),
        "open_interest": (
            None if pd.isna(row["sum_open_interest"]) else float(row["sum_open_interest"])
        ),
        "oi_change_fraction": (
            None if pd.isna(row["oi_change_fraction"]) else float(row["oi_change_fraction"])
        ),
        "oi_impulse_rank": (
            None if pd.isna(row["oi_impulse_rank"]) else float(row["oi_impulse_rank"])
        ),
        "inventory_state": str(row["inventory_state"]),
    }


def _touched_pools(
    pools: Mapping[str, Pool],
    row: pd.Series,
    previous_close: float,
    *,
    minimum_penetration: float,
) -> tuple[list[Pool], list[Pool]]:
    upper = [
        pool
        for pool in pools.values()
        if not pool.consumed
        and pool.side == "UPPER"
        and pool.level >= previous_close
        and float(row["high"]) >= pool.level + minimum_penetration
    ]
    lower = [
        pool
        for pool in pools.values()
        if not pool.consumed
        and pool.side == "LOWER"
        and pool.level <= previous_close
        and float(row["low"]) <= pool.level - minimum_penetration
    ]
    upper.sort(key=lambda item: item.level)
    lower.sort(key=lambda item: item.level, reverse=True)
    return upper, lower


def _same_direction_flow(row: pd.Series, direction: str, threshold: float) -> bool:
    return (
        float(row["imbalance"]) >= threshold
        if direction == "LONG"
        else float(row["imbalance"]) <= -threshold
    )


def _directional_body(row: pd.Series, direction: str) -> bool:
    return (
        float(row["close"]) > float(row["open"])
        if direction == "LONG"
        else float(row["close"]) < float(row["open"])
    )


def _next_structural_target(
    pools: Mapping[str, Pool],
    *,
    direction: str,
    entry: float,
    risk: float,
    minimum_rr: float,
) -> tuple[str, float, float] | None:
    side = "UPPER" if direction == "LONG" else "LOWER"
    candidates = [
        pool
        for pool in pools.values()
        if not pool.consumed
        and pool.side == side
        and (pool.level > entry if direction == "LONG" else pool.level < entry)
    ]
    candidates.sort(
        key=lambda pool: abs(pool.level - entry),
    )
    for pool in candidates:
        rr = abs(pool.level - entry) / risk
        if rr >= minimum_rr:
            return pool.pool_id, float(pool.level), float(rr)
    return None


def _path_result(
    bars: pd.DataFrame,
    *,
    start_index: int,
    direction: str,
    entry: float,
    stop: float,
    target: float,
    max_hold_bars: int,
) -> tuple[dict[str, Any], int]:
    risk = entry - stop if direction == "LONG" else stop - entry
    future = bars.iloc[start_index + 1 : start_index + 1 + max_hold_bars]
    if future.empty:
        return {
            "outcome": "TIMEOUT",
            "timestamp_ns": None,
            "mfe_r": None,
            "mae_r": None,
            "terminal_close_r": None,
        }, start_index

    if direction == "LONG":
        favorable = (future["high"] - entry) / risk
        adverse = (entry - future["low"]) / risk
        close_r = (future["close"] - entry) / risk
    else:
        favorable = (entry - future["low"]) / risk
        adverse = (future["high"] - entry) / risk
        close_r = (entry - future["close"]) / risk

    outcome = "TIMEOUT"
    event_ns: int | None = None
    block_until = int(future.index[-1])
    for index, row in future.iterrows():
        if direction == "LONG":
            stop_hit = float(row["low"]) <= stop
            target_hit = float(row["high"]) >= target
        else:
            stop_hit = float(row["high"]) >= stop
            target_hit = float(row["low"]) <= target
        if stop_hit and target_hit:
            outcome = "AMBIGUOUS_SAME_BAR"
        elif stop_hit:
            outcome = "STOP"
        elif target_hit:
            outcome = "TARGET"
        else:
            continue
        event_ns = int(row["timestamp_ns"])
        block_until = int(index)
        break

    return {
        "outcome": outcome,
        "timestamp_ns": event_ns,
        "mfe_r": float(favorable.max()),
        "mae_r": float(adverse.max()),
        "terminal_close_r": float(close_r.iloc[-1]),
    }, block_until


def _route_episode(
    bars: pd.DataFrame,
    pools: Mapping[str, Pool],
    *,
    episode: LiquidationEpisode,
    index: int,
    logic: HandoffLogic,
) -> tuple[dict[str, Any] | None, bool, int]:
    """Return (record, terminal, block_until)."""
    row = bars.loc[index]
    age = index - episode.contact_index
    if not bool(row["positioning_valid"]):
        return {
            "scenario_id": episode.scenario_id,
            "kind": "LIQUIDATION_HANDOFF",
            "outcome": "POSITIONING_GAP_INVALIDATED",
            "pool_id": episode.pool_id,
            "contact": _bar_payload(bars.loc[episode.contact_index]),
            "terminal": _bar_payload(row),
        }, True, index
    if age > logic.handoff_bars:
        return {
            "scenario_id": episode.scenario_id,
            "kind": "LIQUIDATION_HANDOFF",
            "outcome": "HANDOFF_TIMEOUT",
            "pool_id": episode.pool_id,
            "contact": _bar_payload(bars.loc[episode.contact_index]),
            "terminal": _bar_payload(row),
        }, True, index

    if episode.pool_side == "UPPER":
        episode.extreme = max(episode.extreme, float(row["high"]))
        reclaimed = float(row["close"]) < (
            episode.liquidity_level - logic.reclaim_buffer_atr * episode.atr
        )
        held_outside = float(row["close"]) > (
            episode.liquidity_level + logic.outside_buffer_atr * episode.atr
        )
    else:
        episode.extreme = min(episode.extreme, float(row["low"]))
        reclaimed = float(row["close"]) > (
            episode.liquidity_level + logic.reclaim_buffer_atr * episode.atr
        )
        held_outside = float(row["close"]) < (
            episode.liquidity_level - logic.outside_buffer_atr * episode.atr
        )

    body_ok = abs(float(row["close"]) - float(row["open"])) >= (
        logic.confirmation_body_atr * episode.atr
    )
    state = str(row["inventory_state"])
    reversal_confirmed = (
        state == "BUILD"
        and reclaimed
        and body_ok
        and _directional_body(row, episode.reversal_direction)
        and _same_direction_flow(
            row,
            episode.reversal_direction,
            logic.confirmation_imbalance,
        )
    )
    continuation_confirmed = (
        state == "RELEASE"
        and held_outside
        and body_ok
        and _directional_body(row, episode.attack_direction)
        and _same_direction_flow(
            row,
            episode.attack_direction,
            logic.confirmation_imbalance,
        )
    )
    if not reversal_confirmed and not continuation_confirmed:
        return None, False, index

    if reversal_confirmed:
        route = "RELEASE_TO_BUILD_REVERSAL"
        direction = episode.reversal_direction
        stop = (
            episode.extreme + logic.stop_buffer_atr * episode.atr
            if direction == "SHORT"
            else episode.extreme - logic.stop_buffer_atr * episode.atr
        )
    else:
        route = "CONTINUED_RELEASE_ACCEPTANCE"
        direction = episode.attack_direction
        stop = (
            episode.liquidity_level - logic.stop_buffer_atr * episode.atr
            if direction == "LONG"
            else episode.liquidity_level + logic.stop_buffer_atr * episode.atr
        )

    entry = float(row["close"])
    risk = entry - stop if direction == "LONG" else stop - entry
    base = {
        "scenario_id": episode.scenario_id,
        "kind": "LIQUIDATION_HANDOFF",
        "route": route,
        "direction": direction,
        "pool_id": episode.pool_id,
        "pool_side": episode.pool_side,
        "liquidity_level": episode.liquidity_level,
        "contact": _bar_payload(bars.loc[episode.contact_index]),
        "confirmation": _bar_payload(row),
        "entry": entry,
        "stop": stop,
        "risk": risk,
        "risk_atr": risk / episode.atr if episode.atr > 0.0 else None,
        "handoff_age_bars": age,
    }
    if risk <= 0.0:
        return {**base, "outcome": "NONPOSITIVE_RISK"}, True, index
    selected = _next_structural_target(
        pools,
        direction=direction,
        entry=entry,
        risk=risk,
        minimum_rr=logic.minimum_rr,
    )
    if selected is None:
        return {
            **base,
            "outcome": "NO_CONFIRMED_POOL_TARGET_AT_MINIMUM_RR",
        }, True, index
    target_pool_id, target, expected_rr = selected
    path, block_until = _path_result(
        bars,
        start_index=index,
        direction=direction,
        entry=entry,
        stop=stop,
        target=target,
        max_hold_bars=logic.max_hold_bars,
    )
    return {
        **base,
        "outcome": "ENTRY_READY",
        "target_pool_id": target_pool_id,
        "target": target,
        "expected_rr": expected_rr,
        "path": path,
    }, True, block_until


def diagnose(
    bars: pd.DataFrame,
    *,
    confirmations: Mapping[int, list[Pool]],
    trade_start_ns: int,
    trade_end_ns: int,
    logic: HandoffLogic,
) -> dict[str, Any]:
    logic.validate()
    pools: dict[str, Pool] = {}
    episode: LiquidationEpisode | None = None
    scenarios: list[dict[str, Any]] = []
    contacts: Counter[str] = Counter()
    block_until = -1

    index = 1
    while index < len(bars.index):
        row = bars.loc[index]
        timestamp_ns = int(row["timestamp_ns"])
        for pool in confirmations.get(timestamp_ns, []):
            pools[pool.pool_id] = pool

        atr_value = row["atr"]
        atr = float(atr_value) if not pd.isna(atr_value) else 0.0
        previous_close = float(bars.loc[index - 1]["close"])
        upper, lower = _touched_pools(
            pools,
            row,
            previous_close,
            minimum_penetration=logic.contact_min_atr * atr if atr > 0.0 else 0.0,
        )

        if index <= block_until:
            for pool in [*upper, *lower]:
                pool.consumed = True
                pool.consumed_ts_ns = timestamp_ns
            index += 1
            continue

        if episode is not None:
            record, terminal, new_block = _route_episode(
                bars,
                pools,
                episode=episode,
                index=index,
                logic=logic,
            )
            if record is not None:
                scenarios.append(record)
            if terminal:
                episode = None
                block_until = max(block_until, new_block)
                index += 1
                continue

        # Only one active episode is allowed.  Any pool touched while waiting is
        # consumed, because its first contact has occurred and cannot be replayed.
        if episode is not None:
            for pool in [*upper, *lower]:
                pool.consumed = True
                pool.consumed_ts_ns = timestamp_ns
            index += 1
            continue

        if upper and lower:
            for pool in [*upper, *lower]:
                pool.consumed = True
                pool.consumed_ts_ns = timestamp_ns
            contacts["AMBIGUOUS_BOTH_SIDES"] += 1
            index += 1
            continue
        touched = upper or lower
        if not touched:
            index += 1
            continue
        pool = touched[0]
        for crossed in touched:
            crossed.consumed = True
            crossed.consumed_ts_ns = timestamp_ns

        if not trade_start_ns <= timestamp_ns < trade_end_ns:
            contacts["OUTSIDE_TRADE_INTERVAL"] += 1
            index += 1
            continue
        if atr <= 0.0:
            contacts["NO_ATR"] += 1
            index += 1
            continue
        if not bool(row["positioning_valid"]):
            contacts["POSITIONING_INVALID"] += 1
            index += 1
            continue
        if str(row["inventory_state"]) != "RELEASE":
            contacts[f"CONTACT_{str(row['inventory_state'])}"] += 1
            index += 1
            continue

        penetration = (
            (float(row["high"]) - pool.level) / atr
            if pool.side == "UPPER"
            else (pool.level - float(row["low"])) / atr
        )
        if not logic.contact_min_atr <= penetration <= logic.contact_max_atr:
            contacts["PENETRATION_OUTSIDE_BOUNDS"] += 1
            index += 1
            continue
        attack_direction = "LONG" if pool.side == "UPPER" else "SHORT"
        if not (
            _same_direction_flow(row, attack_direction, logic.attack_imbalance)
            and float(row["flow_z"]) >= logic.flow_z
        ):
            contacts["RELEASE_WITHOUT_ATTACK_FLOW"] += 1
            index += 1
            continue

        contacts["LIQUIDATION_RELEASE_EPISODE"] += 1
        oi_change = float(row["oi_change_fraction"])
        oi_rank = float(row["oi_impulse_rank"])
        episode = LiquidationEpisode(
            scenario_id=f"c07ih-{timestamp_ns}-{pool.pool_id}",
            contact_index=index,
            pool_id=pool.pool_id,
            pool_side=pool.side,
            liquidity_level=float(pool.level),
            attack_direction=attack_direction,
            reversal_direction="SHORT" if attack_direction == "LONG" else "LONG",
            extreme=float(row["high"]) if pool.side == "UPPER" else float(row["low"]),
            atr=atr,
            contact_inventory_state="RELEASE",
            contact_oi_change=oi_change,
            contact_oi_rank=oi_rank,
            contact_imbalance=float(row["imbalance"]),
            contact_flow_z=float(row["flow_z"]),
        )
        index += 1

    if episode is not None:
        scenarios.append(
            {
                "scenario_id": episode.scenario_id,
                "kind": "LIQUIDATION_HANDOFF",
                "outcome": "END_OF_DATA_WITH_ACTIVE_EPISODE",
                "pool_id": episode.pool_id,
                "contact": _bar_payload(bars.loc[episode.contact_index]),
            }
        )

    entry_ready = [item for item in scenarios if item.get("outcome") == "ENTRY_READY"]
    path_counts = Counter(str((item.get("path") or {}).get("outcome")) for item in entry_ready)
    by_route: dict[str, Counter[str]] = defaultdict(Counter)
    active_dates: set[str] = set()
    for item in scenarios:
        route = str(item.get("route", "UNROUTED"))
        outcome = str(item.get("outcome"))
        by_route[route][outcome] += 1
        if outcome == "ENTRY_READY":
            active_dates.add(str(item["confirmation"]["timestamp"])[:10])

    mfe = [
        float(item["path"]["mfe_r"])
        for item in entry_ready
        if (item.get("path") or {}).get("mfe_r") is not None
    ]
    mae = [
        float(item["path"]["mae_r"])
        for item in entry_ready
        if (item.get("path") or {}).get("mae_r") is not None
    ]
    summary = {
        "pool_confirmations": sum(len(value) for value in confirmations.values()),
        "pools_active_or_consumed": len(pools),
        "contact_counts": dict(sorted(contacts.items())),
        "scenarios": len(scenarios),
        "entry_ready": len(entry_ready),
        "active_days": len(active_dates),
        "path_outcome_counts": dict(sorted(path_counts.items())),
        "target_minus_stop": path_counts["TARGET"] - path_counts["STOP"],
        "median_mfe_r": float(pd.Series(mfe).median()) if mfe else None,
        "median_mae_r": float(pd.Series(mae).median()) if mae else None,
        "by_route": {
            route: dict(sorted(counts.items()))
            for route, counts in sorted(by_route.items())
        },
        "diagnostic_gate": {
            "minimum_entry_ready": len(entry_ready) >= 7,
            "minimum_active_days": len(active_dates) >= 4,
            "more_targets_than_stops": path_counts["TARGET"] > path_counts["STOP"],
            "median_mfe_at_least_minimum_rr": bool(mfe) and float(pd.Series(mfe).median()) >= logic.minimum_rr,
            "median_mae_below_one_r": bool(mae) and float(pd.Series(mae).median()) < 1.0,
        },
    }
    summary["diagnostic_gate"]["passed"] = all(summary["diagnostic_gate"].values())
    return {"summary": summary, "scenarios": scenarios}


def _utc_ns(day: date) -> int:
    return int(pd.Timestamp(day, tz="UTC").value)


def run(args: argparse.Namespace) -> int:
    config = json.loads(args.config.read_text(encoding="utf-8"))
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    bundle = load_positioning_bundle(
        symbol=str(config["symbol"]),
        trade_start=args.start,
        trade_end=args.end,
        warmup_days=int(config["warmup_days"]),
        cache_root=args.data_root.resolve(),
        manifest_destination=output.with_name("inventory_handoff_data_manifest.json"),
    )
    flow_logic = dict(config["flow_logic"])
    bars = aggregate_flow(
        bundle.frame,
        int(flow_logic["signal_minutes"]),
        int(flow_logic["flow_period"]),
    )
    logic = HandoffLogic()
    aligned = _align_positioning(
        bars,
        bundle.metrics,
        oi_period=logic.oi_period,
        oi_impulse_rank=logic.oi_impulse_rank,
    )
    context = context_bars(bundle.frame)
    confirmations = pool_confirmations(context)
    result = diagnose(
        aligned,
        confirmations=confirmations,
        trade_start_ns=_utc_ns(args.start),
        trade_end_ns=_utc_ns(args.end),
        logic=logic,
    )
    payload = {
        "candidate": "candidate-07",
        "stage": args.stage,
        "hypothesis": "post-liquidation inventory handoff at confirmed 15-minute swing pools",
        "period": {"start": args.start.isoformat(), "end_exclusive": args.end.isoformat()},
        "logic": {name: getattr(logic, name) for name in logic.__dataclass_fields__},
        "data_contract": {
            "liquidity": "15-minute swing pool confirmed after two completed right-side bars",
            "execution_state": "completed five-minute price, aggressor flow and OI metrics",
            "pool_reuse": False,
            "single_pending_or_open_slot": True,
            "future_information": False,
            "orders_or_pnl": False,
        },
        **result,
    }
    write_json_atomic(output, payload)
    print(json.dumps(payload["summary"], indent=2, sort_keys=True))
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
    return parser


def main() -> int:
    return run(build_parser().parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
