#!/usr/bin/env python3
"""Diagnose parent liquidity-ladder context for initiative-auction fades.

A local five-minute sweep/reclaim can be a genuine failed auction or a pullback
inside a larger initiative leg.  This module uses only completed fifteen-minute
price structure to distinguish those states before entry:

    first accepted parent swing objective
    -> second distinct same-side accepted parent swing objective
    -> parent initiative active while the first accepted boundary is not reclaimed

At most one accepted objective per side and completed parent bar counts toward a
ladder step, so a single gap through several old levels is one auction event.
The script creates no orders, fills, PnL, or NAV.  Existing outcomes are joined
only after the causal parent state at each source contact has been computed.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from datetime import date
import json
from math import prod
from pathlib import Path
from typing import Any, Iterable, Mapping

import numpy as np
import pandas as pd

from data_flow import load_flow_bundle
from diagnose_failed_flow import aggregate_flow
from smc_ict_4.manifest import write_json_atomic


NS_PER_MINUTE = 60_000_000_000


@dataclass(frozen=True, slots=True)
class ParentPool:
    pool_id: str
    side: str
    level: float
    pivot_ts_ns: int
    confirmed_ts_ns: int


@dataclass(frozen=True, slots=True)
class AcceptanceEvent:
    bar_index: int
    timestamp_ns: int
    side: str
    pool_id: str
    level: float


@dataclass(frozen=True, slots=True)
class ParentInitiativeState:
    direction: str
    anchor_level: float
    latest_level: float
    first_pool_id: str
    latest_pool_id: str
    activated_at_ns: int
    steps: int

    def __post_init__(self) -> None:
        if self.direction not in {"LONG", "SHORT"}:
            raise ValueError("direction must be LONG or SHORT")
        if self.anchor_level <= 0.0 or self.latest_level <= 0.0:
            raise ValueError("initiative levels must be positive")
        if self.steps < 2:
            raise ValueError("parent initiative requires at least two steps")

    def reclaimed(self, close: float) -> bool:
        if self.direction == "LONG":
            return close < self.anchor_level
        return close > self.anchor_level


def confirmed_parent_pools(
    bars: pd.DataFrame,
    *,
    radius: int = 2,
) -> list[ParentPool]:
    """Return strict causal swing pools after right-side confirmation."""
    if radius <= 0:
        raise ValueError("radius must be positive")
    timestamps = bars["timestamp_ns"].astype("int64").to_numpy()
    highs = bars["high"].astype(float).to_numpy()
    lows = bars["low"].astype(float).to_numpy()
    output: list[ParentPool] = []
    for center in range(radius, len(bars.index) - radius):
        left = slice(center - radius, center)
        right = slice(center + 1, center + radius + 1)
        pivot_ns = int(timestamps[center])
        confirmed_ns = int(timestamps[center + radius])
        high = float(highs[center])
        low = float(lows[center])
        if high > float(np.max(highs[left])) and high > float(np.max(highs[right])):
            output.append(
                ParentPool(
                    pool_id=f"15M-H-{pivot_ns}",
                    side="UPPER",
                    level=high,
                    pivot_ts_ns=pivot_ns,
                    confirmed_ts_ns=confirmed_ns,
                )
            )
        if low < float(np.min(lows[left])) and low < float(np.min(lows[right])):
            output.append(
                ParentPool(
                    pool_id=f"15M-L-{pivot_ns}",
                    side="LOWER",
                    level=low,
                    pivot_ts_ns=pivot_ns,
                    confirmed_ts_ns=confirmed_ns,
                )
            )
    output.sort(key=lambda item: (item.confirmed_ts_ns, item.pool_id))
    return output


def collapsed_acceptance_events(
    bars: pd.DataFrame,
    pools: Iterable[ParentPool],
) -> list[AcceptanceEvent]:
    """Return first close acceptance, collapsed to one event per bar and side."""
    timestamps = bars["timestamp_ns"].astype("int64").to_numpy()
    closes = bars["close"].astype(float).to_numpy()
    by_bar_side: dict[tuple[int, str], list[ParentPool]] = {}
    for pool in pools:
        start = int(np.searchsorted(timestamps, pool.confirmed_ts_ns, side="right"))
        if start >= len(timestamps):
            continue
        if pool.side == "UPPER":
            hits = np.flatnonzero(closes[start:] > pool.level)
        else:
            hits = np.flatnonzero(closes[start:] < pool.level)
        if len(hits) == 0:
            continue
        index = start + int(hits[0])
        by_bar_side.setdefault((index, pool.side), []).append(pool)

    output: list[AcceptanceEvent] = []
    for (index, side), touched in sorted(by_bar_side.items()):
        # One completed auction bar is one objective-delivery event even if it
        # gaps through several stale levels.  Retain the furthest delivered
        # level as the new parent boundary.
        selected = (
            max(touched, key=lambda item: item.level)
            if side == "UPPER"
            else min(touched, key=lambda item: item.level)
        )
        output.append(
            AcceptanceEvent(
                bar_index=index,
                timestamp_ns=int(timestamps[index]),
                side=side,
                pool_id=selected.pool_id,
                level=float(selected.level),
            )
        )
    return output


def parent_state_timeline(
    bars: pd.DataFrame,
    events: Iterable[AcceptanceEvent],
) -> list[ParentInitiativeState | None]:
    """Replay accepted-objective ladders on completed parent bars only."""
    events_by_index: dict[int, list[AcceptanceEvent]] = {}
    for event in events:
        events_by_index.setdefault(event.bar_index, []).append(event)

    chain_side: str | None = None
    chain: list[AcceptanceEvent] = []
    state: ParentInitiativeState | None = None
    timeline: list[ParentInitiativeState | None] = []

    for index, row in bars.iterrows():
        close = float(row["close"])
        if state is not None and state.reclaimed(close):
            state = None
            chain_side = None
            chain = []

        current = events_by_index.get(int(index), [])
        if len({item.side for item in current}) > 1:
            # A bar accepting both sides is not a directional initiative event.
            chain_side = None
            chain = []
            state = None
            timeline.append(state)
            continue

        if current:
            event = current[0]
            if event.side != chain_side:
                chain_side = event.side
                chain = [event]
                state = None
            else:
                chain.append(event)
            if len(chain) >= 2:
                first = chain[0]
                latest = chain[-1]
                state = ParentInitiativeState(
                    direction="LONG" if event.side == "UPPER" else "SHORT",
                    anchor_level=float(first.level),
                    latest_level=float(latest.level),
                    first_pool_id=first.pool_id,
                    latest_pool_id=latest.pool_id,
                    activated_at_ns=int(latest.timestamp_ns),
                    steps=len(chain),
                )
        timeline.append(state)
    return timeline


def state_before_timestamp(
    bars: pd.DataFrame,
    timeline: list[ParentInitiativeState | None],
    timestamp_ns: int,
) -> ParentInitiativeState | None:
    """Return state from the latest parent bar completed before the query."""
    timestamps = bars["timestamp_ns"].astype("int64").to_numpy()
    index = int(np.searchsorted(timestamps, int(timestamp_ns), side="left")) - 1
    if index < 0:
        return None
    return timeline[index]


def _read_events(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _contact_event(
    events: Iterable[Mapping[str, Any]],
    scenario_id: str,
) -> Mapping[str, Any]:
    accepted = {"UPPER_POOL_SWEEP_RECLAIM", "LOWER_POOL_SWEEP_RECLAIM"}
    for event in events:
        if event.get("scenario_id") == scenario_id and event.get("reason_code") in accepted:
            return event
    raise RuntimeError(f"missing source contact for {scenario_id}")


def diagnose(args: argparse.Namespace) -> int:
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    config = json.loads(args.config.read_text(encoding="utf-8"))
    bundle = load_flow_bundle(
        symbol=str(config["symbol"]),
        trade_start=args.start,
        trade_end=args.end,
        warmup_days=max(int(config["warmup_days"]), 3),
        cache_root=args.data_root.resolve(),
        manifest_destination=output / "data_manifest.json",
    )
    parent = aggregate_flow(bundle.frame, 15, 24)
    pools = confirmed_parent_pools(parent, radius=2)
    acceptance = collapsed_acceptance_events(parent, pools)
    timeline = parent_state_timeline(parent, acceptance)

    run_root = args.run_root.resolve()
    events = _read_events(run_root / "events.jsonl")
    trades = pd.read_csv(run_root / "trades.csv")
    absorption = trades[trades["kind"] == "ABSORPTION_RECLAIM"].copy()
    rows: list[dict[str, Any]] = []
    for trade in absorption.itertuples(index=False):
        scenario_id = str(trade.scenario_id)
        contact = _contact_event(events, scenario_id)
        contact_ns = int(contact["event_time_ns"])
        state = state_before_timestamp(parent, timeline, contact_ns)
        direction = str(trade.direction)
        fade_against_parent = (
            state is not None
            and (
                (state.direction == "LONG" and direction == "SHORT")
                or (state.direction == "SHORT" and direction == "LONG")
            )
        )
        rows.append(
            {
                "stage": str(args.stage),
                "scenario_id": scenario_id,
                "direction": direction,
                "contact_time_ns": contact_ns,
                "opened_ns": int(trade.opened_ns),
                "closed_ns": int(trade.closed_ns),
                "net_pnl": float(trade.net_pnl),
                "net_return_on_nav": float(trade.net_return_on_nav),
                "win": float(trade.net_pnl) > 0.0,
                "parent_state_active": state is not None,
                "parent_direction": None if state is None else state.direction,
                "parent_anchor_level": None if state is None else state.anchor_level,
                "parent_latest_level": None if state is None else state.latest_level,
                "parent_steps": 0 if state is None else state.steps,
                "parent_activated_at_ns": None if state is None else state.activated_at_ns,
                "fade_against_parent_initiative": fade_against_parent,
                "retain_under_parent_rule": not fade_against_parent,
                "parent_state": None if state is None else asdict(state),
            }
        )

    frame = pd.DataFrame(rows)
    frame.to_csv(output / "parent_initiative_features.csv", index=False)
    retained = frame[frame["retain_under_parent_rule"].astype(bool)].copy()
    blocked = frame[~frame["retain_under_parent_rule"].astype(bool)].copy()

    def subset_summary(part: pd.DataFrame) -> dict[str, Any]:
        returns = part["net_return_on_nav"].astype(float).tolist()
        return {
            "trades": int(len(part.index)),
            "wins": int(part["win"].astype(bool).sum()),
            "losses": int((~part["win"].astype(bool)).sum()),
            "win_rate": (
                float(part["win"].astype(bool).mean())
                if len(part.index)
                else 0.0
            ),
            "diagnostic_compounded_recorded_returns": (
                prod(1.0 + value for value in returns) - 1.0
                if returns
                else 0.0
            ),
        }

    summary = {
        "candidate": "candidate-07-parent-initiative-ladder-diagnostic",
        "stage": str(args.stage),
        "period": {
            "start": args.start.isoformat(),
            "end_exclusive": args.end.isoformat(),
        },
        "parent_timeframe_minutes": 15,
        "pivot_radius": 2,
        "activation": "two distinct same-side accepted parent swing objectives",
        "release": "completed parent close reclaims the first accepted boundary",
        "parent_pools": len(pools),
        "acceptance_events": len(acceptance),
        "all_absorption": subset_summary(frame),
        "retained_under_parent_rule": subset_summary(retained),
        "blocked_as_counter_initiative_fade": subset_summary(blocked),
        "causal_contract": {
            "state_source": "completed fifteen-minute bars only",
            "state_observed_strictly_before_contact": True,
            "same_bar_multiple_levels_count_once": True,
            "orders_or_pnl_created": False,
            "future_information_in_state": False,
        },
    }
    write_json_atomic(output / "parent_initiative_summary.json", summary)
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


def build_parser() -> argparse.ArgumentParser:
    candidate_dir = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=candidate_dir / "config.json")
    parser.add_argument("--stage", required=True)
    parser.add_argument("--start", type=date.fromisoformat, required=True)
    parser.add_argument("--end", type=date.fromisoformat, required=True)
    parser.add_argument("--run-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--data-root",
        type=Path,
        default=Path(".research-data/candidate-07"),
    )
    return parser


if __name__ == "__main__":
    raise SystemExit(diagnose(build_parser().parse_args()))
