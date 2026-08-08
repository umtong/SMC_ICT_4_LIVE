#!/usr/bin/env python3
"""Diagnose preemptive continuation inside an accepted parent auction.

A local absorption/reclaim which points against an active parent initiative is
not entered immediately.  The local reversal's immutable stop and target become
competing observable barriers while no position is open.  If the local target
is delivered first, responsive activity succeeded and no continuation exists.
If the stop is touched within the original five-minute signal shock and one of
the next three completed minutes closes beyond that stop before reclaiming the
source pool, parent initiative is re-accepted and a continuation plan is formed
with the existing failed-absorption geometry.

This is a structural diagnostic only.  It creates no orders, fills, PnL, cash,
or NAV.  Exact execution is eligible only if this route shows sufficient target
frequency and active-day density before a fresh NautilusTrader replay.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import date
import json
from pathlib import Path
from typing import Any, Iterable, Mapping

import pandas as pd

from data_flow import load_flow_bundle
from diagnose_parent_auction_state import (
    build_parent_state_history,
    parent_state_strictly_before,
    prepare_complete_auctions,
    reversal_context,
)
from failed_continuation import AcceptanceOutcome, FailedAbsorptionAcceptance
from model import Direction
from smc_ict_4.manifest import write_json_atomic


NS_PER_MINUTE = 60_000_000_000


@dataclass(frozen=True, slots=True)
class BarrierObservation:
    outcome: str
    index: int | None
    timestamp_ns: int | None


def prepare_minutes(frame: pd.DataFrame) -> pd.DataFrame:
    required = {"open", "high", "low", "close", "volume"}
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ValueError(f"minute frame missing columns: {missing}")
    work = frame.copy()
    work["timestamp_ns"] = [int(value.value) for value in work.index]
    work = work.sort_values("timestamp_ns", kind="stable").reset_index(drop=True)
    gaps = work["timestamp_ns"].astype("int64").diff().dropna()
    if bool((gaps != NS_PER_MINUTE).any()):
        raise RuntimeError("minute frame is not contiguous")
    return work


def first_reversal_barrier(
    minutes: pd.DataFrame,
    *,
    direction: str,
    opened_ns: int,
    stop: float,
    target: float,
    signal_minutes: int,
) -> BarrierObservation:
    """Return the first immutable stop/target event in the original shock."""
    if direction not in {"LONG", "SHORT"}:
        raise ValueError(f"unsupported direction: {direction}")
    if signal_minutes <= 0:
        raise ValueError("signal_minutes must be positive")
    end_ns = int(opened_ns) + signal_minutes * NS_PER_MINUTE
    path = minutes[
        (minutes["timestamp_ns"] >= int(opened_ns))
        & (minutes["timestamp_ns"] <= end_ns)
    ]
    for index, row in path.iterrows():
        if direction == "LONG":
            stop_hit = float(row["low"]) <= stop
            target_hit = float(row["high"]) >= target
        else:
            stop_hit = float(row["high"]) >= stop
            target_hit = float(row["low"]) <= target
        if stop_hit and target_hit:
            return BarrierObservation("AMBIGUOUS_SAME_MINUTE", int(index), int(row["timestamp_ns"]))
        if target_hit:
            return BarrierObservation("REVERSAL_TARGET_FIRST", int(index), int(row["timestamp_ns"]))
        if stop_hit:
            return BarrierObservation("REVERSAL_STOP_FIRST", int(index), int(row["timestamp_ns"]))
    return BarrierObservation("NO_BARRIER_IN_SIGNAL_SHOCK", None, None)


def continuation_acceptance(
    minutes: pd.DataFrame,
    *,
    stop_index: int,
    source_scenario_id: str,
    direction: Direction,
    liquidity_level: float,
    acceptance_level: float,
    atr: float,
    timeout_bars: int,
) -> tuple[str, int | None, int | None, int]:
    """Observe only bars completed after the stop-touch minute."""
    state = FailedAbsorptionAcceptance(
        source_scenario_id=source_scenario_id,
        direction=direction,
        liquidity_level=liquidity_level,
        acceptance_level=acceptance_level,
        atr=atr,
        armed_at_ns=int(minutes.iloc[stop_index]["timestamp_ns"]),
        timeout_bars=timeout_bars,
    )
    end = min(len(minutes.index), stop_index + 1 + timeout_bars)
    for index in range(stop_index + 1, end):
        row = minutes.iloc[index]
        observation = state.observe(float(row["close"]))
        if observation.outcome is AcceptanceOutcome.WAITING:
            continue
        return (
            observation.outcome.value,
            index,
            int(row["timestamp_ns"]),
            observation.bars_seen,
        )
    return ("INCOMPLETE_ACCEPTANCE_WINDOW", None, None, state.bars_seen)


def continuation_geometry(
    *,
    direction: Direction,
    confirmation_entry: float,
    liquidity_level: float,
    atr: float,
    stop_buffer_atr: float,
    minimum_stop_atr: float,
    maximum_stop_atr: float,
    target_rr: float,
) -> tuple[float, float] | None:
    buffer = stop_buffer_atr * atr
    if direction is Direction.LONG:
        raw_stop = liquidity_level - buffer
        minimum_stop = confirmation_entry - minimum_stop_atr * atr
        stop = min(raw_stop, minimum_stop)
        risk = confirmation_entry - stop
        target = confirmation_entry + risk * target_rr
    else:
        raw_stop = liquidity_level + buffer
        minimum_stop = confirmation_entry + minimum_stop_atr * atr
        stop = max(raw_stop, minimum_stop)
        risk = stop - confirmation_entry
        target = confirmation_entry - risk * target_rr
    if risk <= 0.0 or risk > maximum_stop_atr * atr:
        return None
    return stop, target


def path_outcome(
    minutes: pd.DataFrame,
    *,
    entry_index: int,
    direction: str,
    entry: float,
    stop: float,
    target: float,
    maximum_hold_minutes: int,
) -> dict[str, Any]:
    risk = entry - stop if direction == "LONG" else stop - entry
    if risk <= 0.0:
        raise ValueError("continuation risk must be positive")
    end = min(len(minutes.index), entry_index + 1 + maximum_hold_minutes)
    terminal_r = 0.0
    for index in range(entry_index + 1, end):
        row = minutes.iloc[index]
        if direction == "LONG":
            stop_hit = float(row["low"]) <= stop
            target_hit = float(row["high"]) >= target
        else:
            stop_hit = float(row["high"]) >= stop
            target_hit = float(row["low"]) <= target
        if stop_hit and target_hit:
            return {
                "outcome": "AMBIGUOUS_SAME_MINUTE",
                "terminal_index": index,
                "terminal_ns": int(row["timestamp_ns"]),
                "terminal_r": None,
            }
        if stop_hit:
            return {
                "outcome": "STOP",
                "terminal_index": index,
                "terminal_ns": int(row["timestamp_ns"]),
                "terminal_r": -1.0,
            }
        if target_hit:
            target_r = abs(target - entry) / risk
            return {
                "outcome": "TARGET",
                "terminal_index": index,
                "terminal_ns": int(row["timestamp_ns"]),
                "terminal_r": target_r,
            }
    if end > entry_index + 1:
        close = float(minutes.iloc[end - 1]["close"])
        terminal_r = (
            (close - entry) / risk
            if direction == "LONG"
            else (entry - close) / risk
        )
        terminal_ns = int(minutes.iloc[end - 1]["timestamp_ns"])
    else:
        terminal_ns = None
    return {
        "outcome": "TIMEOUT",
        "terminal_index": None if terminal_ns is None else end - 1,
        "terminal_ns": terminal_ns,
        "terminal_r": terminal_r,
    }


def _events(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line]


def _event(
    events: Iterable[Mapping[str, Any]],
    scenario_id: str,
    reason: str,
) -> Mapping[str, Any]:
    for item in events:
        if item.get("scenario_id") == scenario_id and item.get("reason_code") == reason:
            return item
    raise RuntimeError(f"missing {reason} for {scenario_id}")


def _contact(
    events: Iterable[Mapping[str, Any]],
    scenario_id: str,
) -> Mapping[str, Any]:
    for reason in ("UPPER_POOL_SWEEP_RECLAIM", "LOWER_POOL_SWEEP_RECLAIM"):
        try:
            return _event(events, scenario_id, reason)
        except RuntimeError:
            pass
    raise RuntimeError(f"missing contact for {scenario_id}")


def diagnose(args: argparse.Namespace) -> int:
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    config = json.loads(args.config.read_text())
    logic = dict(config["logic"])
    bundle = load_flow_bundle(
        symbol=str(config["symbol"]),
        trade_start=args.start,
        trade_end=args.end,
        warmup_days=int(config["warmup_days"]),
        cache_root=args.data_root.resolve(),
        manifest_destination=output / "data_manifest.json",
    )
    minutes = prepare_minutes(bundle.frame)
    parent_history = build_parent_state_history(prepare_complete_auctions(bundle.frame))
    events = _events(args.run_root.resolve() / "events.jsonl")
    trades = pd.read_csv(args.run_root.resolve() / "trades.csv")
    absorption = trades[trades["kind"] == "ABSORPTION_RECLAIM"].copy()

    rows: list[dict[str, Any]] = []
    for trade in absorption.itertuples(index=False):
        scenario_id = str(trade.scenario_id)
        contact = _contact(events, scenario_id)
        contact_ns = int(contact["event_time_ns"])
        parent = parent_state_strictly_before(parent_history, contact_ns)
        context = reversal_context(str(trade.direction), parent)
        if context != "COUNTER_INITIATIVE":
            continue
        route = _event(events, scenario_id, "CAUSAL_ROUTE_READY")
        mss = _event(events, scenario_id, "OPPOSITE_DISPLACEMENT_MSS")
        details = dict(route.get("details") or {})
        stop = float(details["stop"])
        target = float(details["target"])
        opened_ns = int(trade.opened_ns)
        barrier = first_reversal_barrier(
            minutes,
            direction=str(trade.direction),
            opened_ns=opened_ns,
            stop=stop,
            target=target,
            signal_minutes=int(logic["signal_minutes"]),
        )
        continuation_direction = (
            Direction.LONG if str(trade.direction) == "SHORT" else Direction.SHORT
        )
        row: dict[str, Any] = {
            "stage": str(args.stage),
            "source_scenario_id": scenario_id,
            "source_reversal_direction": str(trade.direction),
            "parent_direction": None if parent is None else parent.direction,
            "contact_ns": contact_ns,
            "opened_ns": opened_ns,
            "source_liquidity_level": float(contact["reference_price"]),
            "source_stop": stop,
            "source_target": target,
            "source_trade_win": float(trade.net_pnl) > 0.0,
            "source_net_return_on_nav": float(trade.net_return_on_nav),
            "barrier_outcome": barrier.outcome,
            "barrier_ns": barrier.timestamp_ns,
            "continuation_direction": continuation_direction.value,
            "acceptance_outcome": None,
            "acceptance_ns": None,
            "acceptance_bars": 0,
            "entry_ns": None,
            "entry": None,
            "stop": None,
            "target": None,
            "actual_rr": None,
            "continuation_outcome": "NO_ROUTE",
            "continuation_terminal_r": None,
        }
        if barrier.outcome != "REVERSAL_STOP_FIRST" or barrier.index is None:
            rows.append(row)
            continue

        acceptance, acceptance_index, acceptance_ns, bars_seen = continuation_acceptance(
            minutes,
            stop_index=barrier.index,
            source_scenario_id=scenario_id,
            direction=continuation_direction,
            liquidity_level=float(contact["reference_price"]),
            acceptance_level=stop,
            atr=float((mss.get("details") or {})["atr"]),
            timeout_bars=3,
        )
        row.update(
            {
                "acceptance_outcome": acceptance,
                "acceptance_ns": acceptance_ns,
                "acceptance_bars": bars_seen,
            }
        )
        if acceptance != AcceptanceOutcome.CONFIRMED.value or acceptance_index is None:
            rows.append(row)
            continue

        confirmation_close = float(minutes.iloc[acceptance_index]["close"])
        geometry = continuation_geometry(
            direction=continuation_direction,
            confirmation_entry=confirmation_close,
            liquidity_level=float(contact["reference_price"]),
            atr=float((mss.get("details") or {})["atr"]),
            stop_buffer_atr=float(logic["stop_buffer_atr"]),
            minimum_stop_atr=float(logic["minimum_stop_atr"]),
            maximum_stop_atr=float(logic["maximum_stop_atr"]),
            target_rr=float(logic["continuation_target_rr"]),
        )
        if geometry is None or acceptance_index + 1 >= len(minutes.index):
            row["continuation_outcome"] = "GEOMETRY_REJECTED"
            rows.append(row)
            continue
        continuation_stop, continuation_target = geometry
        entry_index = acceptance_index + 1
        entry = float(minutes.iloc[entry_index]["close"])
        risk = (
            entry - continuation_stop
            if continuation_direction is Direction.LONG
            else continuation_stop - entry
        )
        reward = (
            continuation_target - entry
            if continuation_direction is Direction.LONG
            else entry - continuation_target
        )
        if risk <= 0.0 or reward <= 0.0:
            row["continuation_outcome"] = "DELAYED_ENTRY_GEOMETRY_INVALID"
            rows.append(row)
            continue
        actual_rr = reward / risk
        if actual_rr < float(logic["minimum_rr"]):
            row["continuation_outcome"] = "DELAYED_ENTRY_RR_ERODED"
            rows.append(row)
            continue
        outcome = path_outcome(
            minutes,
            entry_index=entry_index,
            direction=continuation_direction.value,
            entry=entry,
            stop=continuation_stop,
            target=continuation_target,
            maximum_hold_minutes=int(config["max_hold_minutes"]),
        )
        row.update(
            {
                "entry_ns": int(minutes.iloc[entry_index]["timestamp_ns"]),
                "entry": entry,
                "stop": continuation_stop,
                "target": continuation_target,
                "actual_rr": actual_rr,
                "continuation_outcome": outcome["outcome"],
                "continuation_terminal_r": outcome["terminal_r"],
                "continuation_terminal_ns": outcome["terminal_ns"],
            }
        )
        rows.append(row)

    frame = pd.DataFrame(rows)
    frame.to_csv(output / "parent_continuation_paths.csv", index=False)
    routed = frame[frame["entry_ns"].notna()] if not frame.empty else frame
    counts = (
        routed["continuation_outcome"].value_counts().sort_index().to_dict()
        if not routed.empty
        else {}
    )
    active_days = (
        pd.to_datetime(routed["entry_ns"].astype("int64"), unit="ns", utc=True)
        .dt.date.astype(str).nunique()
        if not routed.empty
        else 0
    )
    summary = {
        "candidate": "candidate-07-preemptive-parent-initiative-continuation",
        "stage": str(args.stage),
        "counter_initiative_sources": int(len(frame.index)),
        "source_reversal_winners": int(frame["source_trade_win"].sum()) if not frame.empty else 0,
        "source_reversal_losses": int((~frame["source_trade_win"].astype(bool)).sum()) if not frame.empty else 0,
        "barrier_counts": frame["barrier_outcome"].value_counts().sort_index().to_dict() if not frame.empty else {},
        "acceptance_counts": frame["acceptance_outcome"].fillna("NOT_ARMED").value_counts().sort_index().to_dict() if not frame.empty else {},
        "entry_ready": int(len(routed.index)),
        "active_days": int(active_days),
        "continuation_outcomes": counts,
        "targets": int(counts.get("TARGET", 0)),
        "stops": int(counts.get("STOP", 0)),
        "timeouts": int(counts.get("TIMEOUT", 0)),
        "ambiguous": int(counts.get("AMBIGUOUS_SAME_MINUTE", 0)),
        "gross_structural_r": float(routed["continuation_terminal_r"].dropna().sum()) if not routed.empty else 0.0,
        "causal_contract": {
            "source_population": "counter-initiative absorption/reclaim plans only",
            "position_before_route": False,
            "first_competing_event": "immutable local reversal stop versus target",
            "stop_touch_window": "existing five-minute signal shock",
            "acceptance": "existing three completed-bar FailedAbsorptionAcceptance state",
            "geometry": "existing failed-continuation stop and 2.2R target",
            "entry_delay": "next completed minute after acceptance confirmation",
            "orders_or_pnl_created": False,
            "future_information_in_route": False,
        },
    }
    write_json_atomic(output / "parent_continuation_summary.json", summary)
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
    parser.add_argument("--data-root", type=Path, default=Path(".research-data/candidate-07"))
    return parser


if __name__ == "__main__":
    raise SystemExit(diagnose(build_parser().parse_args()))
