#!/usr/bin/env python3
"""Diagnose episode-level forced-inventory continuation at causal liquidity pools.

This is an independent successor to the discarded inventory-handoff candidate,
not a threshold sweep.  It retains only the part with plausible favorable
excursion and changes the state representation and execution geometry:

- contact source: first aggressive OI-release contact with a confirmed 15-minute
  external swing pool;
- episode state: cumulative OI pressure relative to contact, rather than a new
  rank-qualified release label on every five-minute bar;
- route: continuation only; no liquidation-reversal branch;
- confirmation: completed outside hold plus same-direction displacement while OI
  remains at or below contact OI;
- invalidation: beyond the contact/confirmation auction structure;
- target hierarchy: nearest active confirmed five-minute internal pool, then
  nearest active confirmed fifteen-minute external pool.

The script evaluates causal paths only.  It creates no orders, fills, fees, PnL,
cash ledger or NAV and therefore is not a replacement backtest engine.
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
from diagnose_inventory_handoff import HandoffLogic, _same_direction_flow, _directional_body
from diagnose_inventory_handoff_exit_safe import _exit_safe_path_result
from diagnose_mtf_liquidity import Pool, context_bars, pool_confirmations
from diagnose_session_handoff import _align_positioning
from smc_ict_4.manifest import write_json_atomic


@dataclass(slots=True)
class PressureEpisode:
    scenario_id: str
    contact_index: int
    pool_id: str
    pool_side: str
    liquidity_level: float
    direction: str
    contact_oi: float
    contact_low: float
    contact_high: float
    atr: float
    contact_oi_change: float
    contact_oi_rank: float
    contact_imbalance: float
    contact_flow_z: float


@dataclass(frozen=True, slots=True)
class PressureLogic:
    contact_min_atr: float = 0.05
    contact_max_atr: float = 1.50
    attack_imbalance: float = 0.08
    flow_z: float = 0.25
    outside_buffer_atr: float = 0.02
    reclaim_buffer_atr: float = 0.02
    confirmation_body_atr: float = 0.12
    confirmation_imbalance: float = 0.02
    stop_buffer_atr: float = 0.10
    confirmation_bars: int = 3
    minimum_rr: float = 1.25
    max_hold_bars: int = 24
    oi_period: int = 36
    oi_impulse_rank: float = 0.50
    internal_pivot_radius: int = 2

    def validate(self) -> None:
        if not 0.0 < self.contact_min_atr < self.contact_max_atr:
            raise ValueError("contact penetration bounds are inconsistent")
        if not 0.0 < self.attack_imbalance < 1.0:
            raise ValueError("attack_imbalance must be in (0, 1)")
        if not 0.0 <= self.confirmation_imbalance < 1.0:
            raise ValueError("confirmation_imbalance must be in [0, 1)")
        if self.confirmation_bars <= 0 or self.max_hold_bars <= 0:
            raise ValueError("bar counts must be positive")
        if self.minimum_rr <= 0.0:
            raise ValueError("minimum_rr must be positive")
        if self.oi_period <= 0 or not 0.0 <= self.oi_impulse_rank <= 1.0:
            raise ValueError("OI rank configuration is inconsistent")
        if self.internal_pivot_radius <= 0:
            raise ValueError("internal_pivot_radius must be positive")


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


def five_minute_pool_confirmations(
    bars: pd.DataFrame,
    *,
    radius: int,
) -> dict[int, list[Pool]]:
    events: dict[int, list[Pool]] = defaultdict(list)
    for center in range(radius, len(bars.index) - radius):
        row = bars.loc[center]
        left = bars.iloc[center - radius : center]
        right = bars.iloc[center + 1 : center + radius + 1]
        confirmation_index = center + radius
        confirmation_ns = int(bars.loc[confirmation_index]["timestamp_ns"])
        pivot_ns = int(row["timestamp_ns"])
        high = float(row["high"])
        low = float(row["low"])
        if high > float(left["high"].max()) and high > float(right["high"].max()):
            events[confirmation_ns].append(
                Pool(
                    pool_id=f"5H-{pivot_ns}",
                    side="UPPER",
                    level=high,
                    pivot_ts_ns=pivot_ns,
                    confirmed_ts_ns=confirmation_ns,
                )
            )
        if low < float(left["low"].min()) and low < float(right["low"].min()):
            events[confirmation_ns].append(
                Pool(
                    pool_id=f"5L-{pivot_ns}",
                    side="LOWER",
                    level=low,
                    pivot_ts_ns=pivot_ns,
                    confirmed_ts_ns=confirmation_ns,
                )
            )
    return events


def _copy_pool(pool: Pool, prefix: str) -> Pool:
    return Pool(
        pool_id=f"{prefix}:{pool.pool_id}",
        side=pool.side,
        level=float(pool.level),
        pivot_ts_ns=int(pool.pivot_ts_ns),
        confirmed_ts_ns=int(pool.confirmed_ts_ns),
    )


def _consume_crossed(
    pools: Mapping[str, Pool],
    row: pd.Series,
    previous_close: float,
    timestamp_ns: int,
) -> None:
    for pool in pools.values():
        if pool.consumed:
            continue
        crossed = (
            pool.side == "UPPER"
            and previous_close <= pool.level
            and float(row["high"]) >= pool.level
        ) or (
            pool.side == "LOWER"
            and previous_close >= pool.level
            and float(row["low"]) <= pool.level
        )
        if crossed:
            pool.consumed = True
            pool.consumed_ts_ns = timestamp_ns


def _contacted_external(
    pools: Mapping[str, Pool],
    row: pd.Series,
    previous_close: float,
    *,
    minimum_penetration: float,
) -> tuple[list[Pool], list[Pool]]:
    upper = [
        pool for pool in pools.values()
        if not pool.consumed
        and pool.side == "UPPER"
        and previous_close <= pool.level
        and float(row["high"]) >= pool.level + minimum_penetration
    ]
    lower = [
        pool for pool in pools.values()
        if not pool.consumed
        and pool.side == "LOWER"
        and previous_close >= pool.level
        and float(row["low"]) <= pool.level - minimum_penetration
    ]
    upper.sort(key=lambda pool: pool.level)
    lower.sort(key=lambda pool: pool.level, reverse=True)
    return upper, lower


def _target(
    internal: Mapping[str, Pool],
    external: Mapping[str, Pool],
    *,
    direction: str,
    entry: float,
    risk: float,
    minimum_rr: float,
) -> tuple[str, str, float, float] | None:
    side = "UPPER" if direction == "LONG" else "LOWER"
    for label, registry in (("INTERNAL_5M", internal), ("EXTERNAL_15M", external)):
        candidates = [
            pool for pool in registry.values()
            if not pool.consumed
            and pool.side == side
            and (pool.level > entry if direction == "LONG" else pool.level < entry)
        ]
        candidates.sort(key=lambda pool: abs(pool.level - entry))
        for pool in candidates:
            rr = abs(pool.level - entry) / risk
            if rr >= minimum_rr:
                return label, pool.pool_id, float(pool.level), float(rr)
    return None


def _advance(
    bars: pd.DataFrame,
    internal_targets: Mapping[str, Pool],
    external_targets: Mapping[str, Pool],
    *,
    episode: PressureEpisode,
    index: int,
    logic: PressureLogic,
) -> tuple[dict[str, Any] | None, bool, int]:
    row = bars.loc[index]
    age = index - episode.contact_index
    if not bool(row["positioning_valid"]):
        return {
            "scenario_id": episode.scenario_id,
            "outcome": "POSITIONING_GAP_INVALIDATED",
            "contact": _bar_payload(bars.loc[episode.contact_index]),
            "terminal": _bar_payload(row),
        }, True, index
    if age > logic.confirmation_bars:
        return {
            "scenario_id": episode.scenario_id,
            "outcome": "PRESSURE_CONFIRMATION_TIMEOUT",
            "contact": _bar_payload(bars.loc[episode.contact_index]),
            "terminal": _bar_payload(row),
        }, True, index

    current_oi = float(row["sum_open_interest"])
    cumulative_oi_change = (current_oi - episode.contact_oi) / episode.contact_oi
    if episode.direction == "LONG":
        outside = float(row["close"]) > (
            episode.liquidity_level + logic.outside_buffer_atr * episode.atr
        )
        reclaimed = float(row["close"]) < (
            episode.liquidity_level - logic.reclaim_buffer_atr * episode.atr
        )
    else:
        outside = float(row["close"]) < (
            episode.liquidity_level - logic.outside_buffer_atr * episode.atr
        )
        reclaimed = float(row["close"]) > (
            episode.liquidity_level + logic.reclaim_buffer_atr * episode.atr
        )
    if reclaimed:
        return {
            "scenario_id": episode.scenario_id,
            "outcome": "BROKEN_POOL_RECLAIMED",
            "cumulative_oi_change": cumulative_oi_change,
            "contact": _bar_payload(bars.loc[episode.contact_index]),
            "terminal": _bar_payload(row),
        }, True, index

    body_ok = abs(float(row["close"]) - float(row["open"])) >= (
        logic.confirmation_body_atr * episode.atr
    )
    confirmed = (
        current_oi <= episode.contact_oi
        and outside
        and body_ok
        and _directional_body(row, episode.direction)
        and _same_direction_flow(row, episode.direction, logic.confirmation_imbalance)
    )
    if not confirmed:
        return None, False, index

    contact = bars.loc[episode.contact_index]
    entry = float(row["close"])
    if episode.direction == "LONG":
        structural_low = min(
            float(contact["low"]),
            float(row["low"]),
            episode.liquidity_level,
        )
        stop = structural_low - logic.stop_buffer_atr * episode.atr
        risk = entry - stop
    else:
        structural_high = max(
            float(contact["high"]),
            float(row["high"]),
            episode.liquidity_level,
        )
        stop = structural_high + logic.stop_buffer_atr * episode.atr
        risk = stop - entry
    base = {
        "scenario_id": episode.scenario_id,
        "route": "EPISODE_INVENTORY_PRESSURE_CONTINUATION",
        "direction": episode.direction,
        "pool_id": episode.pool_id,
        "pool_side": episode.pool_side,
        "liquidity_level": episode.liquidity_level,
        "contact": _bar_payload(contact),
        "confirmation": _bar_payload(row),
        "cumulative_oi_change": cumulative_oi_change,
        "entry": entry,
        "stop": stop,
        "risk": risk,
        "risk_atr": risk / episode.atr if episode.atr > 0.0 else None,
        "confirmation_age_bars": age,
    }
    if risk <= 0.0:
        return {**base, "outcome": "NONPOSITIVE_RISK"}, True, index
    selected = _target(
        internal_targets,
        external_targets,
        direction=episode.direction,
        entry=entry,
        risk=risk,
        minimum_rr=logic.minimum_rr,
    )
    if selected is None:
        return {**base, "outcome": "NO_CAUSAL_LIQUIDITY_TARGET_AT_MINIMUM_RR"}, True, index
    target_class, target_pool_id, target_price, expected_rr = selected
    path, block_until = _exit_safe_path_result(
        bars,
        start_index=index,
        direction=episode.direction,
        entry=entry,
        stop=stop,
        target=target_price,
        max_hold_bars=logic.max_hold_bars,
    )
    return {
        **base,
        "outcome": "ENTRY_READY",
        "target_class": target_class,
        "target_pool_id": target_pool_id,
        "target": target_price,
        "expected_rr": expected_rr,
        "path": path,
    }, True, block_until


def diagnose(
    bars: pd.DataFrame,
    *,
    contact_confirmations: Mapping[int, list[Pool]],
    internal_confirmations: Mapping[int, list[Pool]],
    trade_start_ns: int,
    trade_end_ns: int,
    logic: PressureLogic,
) -> dict[str, Any]:
    logic.validate()
    external_contacts: dict[str, Pool] = {}
    internal_targets: dict[str, Pool] = {}
    external_targets: dict[str, Pool] = {}
    episode: PressureEpisode | None = None
    block_until = -1
    scenarios: list[dict[str, Any]] = []
    contact_counts: Counter[str] = Counter()

    index = 1
    while index < len(bars.index):
        row = bars.loc[index]
        timestamp_ns = int(row["timestamp_ns"])
        for pool in contact_confirmations.get(timestamp_ns, []):
            external_contacts[pool.pool_id] = pool
            copied = _copy_pool(pool, "T15")
            external_targets[copied.pool_id] = copied
        for pool in internal_confirmations.get(timestamp_ns, []):
            copied = _copy_pool(pool, "T5")
            internal_targets[copied.pool_id] = copied

        previous_close = float(bars.loc[index - 1]["close"])
        # A target which has already traded is no longer outstanding liquidity.
        _consume_crossed(internal_targets, row, previous_close, timestamp_ns)
        _consume_crossed(external_targets, row, previous_close, timestamp_ns)

        atr_value = row["atr"]
        atr = float(atr_value) if not pd.isna(atr_value) else 0.0
        upper, lower = _contacted_external(
            external_contacts,
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
            record, terminal, new_block = _advance(
                bars,
                internal_targets,
                external_targets,
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
            contact_counts["AMBIGUOUS_BOTH_SIDES"] += 1
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
            contact_counts["OUTSIDE_TRADE_INTERVAL"] += 1
            index += 1
            continue
        if atr <= 0.0:
            contact_counts["NO_ATR"] += 1
            index += 1
            continue
        if not bool(row["positioning_valid"]):
            contact_counts["POSITIONING_INVALID"] += 1
            index += 1
            continue
        if str(row["inventory_state"]) != "RELEASE":
            contact_counts[f"CONTACT_{str(row['inventory_state'])}"] += 1
            index += 1
            continue

        penetration = (
            (float(row["high"]) - pool.level) / atr
            if pool.side == "UPPER"
            else (pool.level - float(row["low"])) / atr
        )
        if not logic.contact_min_atr <= penetration <= logic.contact_max_atr:
            contact_counts["PENETRATION_OUTSIDE_BOUNDS"] += 1
            index += 1
            continue
        direction = "LONG" if pool.side == "UPPER" else "SHORT"
        if not (
            _same_direction_flow(row, direction, logic.attack_imbalance)
            and float(row["flow_z"]) >= logic.flow_z
        ):
            contact_counts["RELEASE_WITHOUT_ATTACK_FLOW"] += 1
            index += 1
            continue

        contact_counts["PRESSURE_EPISODE"] += 1
        episode = PressureEpisode(
            scenario_id=f"c07ipc-{timestamp_ns}-{pool.pool_id}",
            contact_index=index,
            pool_id=pool.pool_id,
            pool_side=pool.side,
            liquidity_level=float(pool.level),
            direction=direction,
            contact_oi=float(row["sum_open_interest"]),
            contact_low=float(row["low"]),
            contact_high=float(row["high"]),
            atr=atr,
            contact_oi_change=float(row["oi_change_fraction"]),
            contact_oi_rank=float(row["oi_impulse_rank"]),
            contact_imbalance=float(row["imbalance"]),
            contact_flow_z=float(row["flow_z"]),
        )
        index += 1

    if episode is not None:
        scenarios.append(
            {
                "scenario_id": episode.scenario_id,
                "outcome": "END_OF_DATA_WITH_ACTIVE_EPISODE",
                "contact": _bar_payload(bars.loc[episode.contact_index]),
            }
        )

    entry = [item for item in scenarios if item.get("outcome") == "ENTRY_READY"]
    outcomes = Counter(str((item.get("path") or {}).get("outcome")) for item in entry)
    active_dates = {str(item["confirmation"]["timestamp"])[:10] for item in entry}
    target_classes = Counter(str(item.get("target_class")) for item in entry)
    mfe = [float(item["path"]["mfe_r"]) for item in entry if item["path"].get("mfe_r") is not None]
    mae = [float(item["path"]["mae_r"]) for item in entry if item["path"].get("mae_r") is not None]
    gate = {
        "minimum_entry_ready": len(entry) >= 7,
        "minimum_active_days": len(active_dates) >= 4,
        "more_targets_than_stops": outcomes["TARGET"] > outcomes["STOP"],
        "median_mfe_at_least_minimum_rr": bool(mfe) and float(pd.Series(mfe).median()) >= logic.minimum_rr,
        "median_mae_below_one_r": bool(mae) and float(pd.Series(mae).median()) < 1.0,
    }
    gate["passed"] = all(gate.values())
    return {
        "summary": {
            "external_pool_confirmations": sum(len(value) for value in contact_confirmations.values()),
            "internal_pool_confirmations": sum(len(value) for value in internal_confirmations.values()),
            "contact_counts": dict(sorted(contact_counts.items())),
            "scenarios": len(scenarios),
            "entry_ready": len(entry),
            "active_days": len(active_dates),
            "path_outcome_counts": dict(sorted(outcomes.items())),
            "target_minus_stop": outcomes["TARGET"] - outcomes["STOP"],
            "target_class_counts": dict(sorted(target_classes.items())),
            "median_mfe_r": float(pd.Series(mfe).median()) if mfe else None,
            "median_mae_r": float(pd.Series(mae).median()) if mae else None,
            "diagnostic_gate": gate,
        },
        "scenarios": scenarios,
    }


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
        manifest_destination=output.with_name("inventory_pressure_data_manifest.json"),
    )
    flow_logic = dict(config["flow_logic"])
    bars = aggregate_flow(
        bundle.frame,
        int(flow_logic["signal_minutes"]),
        int(flow_logic["flow_period"]),
    )
    logic = PressureLogic()
    aligned = _align_positioning(
        bars,
        bundle.metrics,
        oi_period=logic.oi_period,
        oi_impulse_rank=logic.oi_impulse_rank,
    )
    external = pool_confirmations(context_bars(bundle.frame))
    internal = five_minute_pool_confirmations(
        aligned,
        radius=logic.internal_pivot_radius,
    )
    result = diagnose(
        aligned,
        contact_confirmations=external,
        internal_confirmations=internal,
        trade_start_ns=_utc_ns(args.start),
        trade_end_ns=_utc_ns(args.end),
        logic=logic,
    )
    payload = {
        "candidate": "candidate-07",
        "stage": args.stage,
        "hypothesis": "episode inventory-pressure continuation with internal-to-external liquidity targets",
        "period": {"start": args.start.isoformat(), "end_exclusive": args.end.isoformat()},
        "logic": {name: getattr(logic, name) for name in logic.__dataclass_fields__},
        "data_contract": {
            "contact_pool": "15-minute swing confirmed after two completed right-side bars",
            "internal_target": "five-minute swing confirmed after two completed right-side bars",
            "state": "completed five-minute aggressor flow and OI; cumulative OI is measured from contact",
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
