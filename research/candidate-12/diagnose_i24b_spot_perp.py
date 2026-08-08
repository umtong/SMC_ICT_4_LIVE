#!/usr/bin/env python3
"""One-minute ownership / five-minute execution diagnostic for Candidate 12.

The original I24 screen aggregated both markets to five minutes before asking
which market moved first. That erased the state variable whenever spot and
perpetual crossed in the same five-minute bucket. I24b fixes the unit mismatch:

* synchronized completed one-minute closes define market ownership and basis
  transmission;
* completed five-minute bars define acceptance, pullback, MSS, entry, stop and
  target geometry;
* one-minute perpetual bars only evaluate post-decision stop/target ordering.

This remains development evidence, not a performance claim.
"""
from __future__ import annotations

import argparse
from datetime import date, timedelta
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from data_loader import load_binance_bars
from diagnose_i24_spot_perp import (
    SESSIONS,
    SessionSpec,
    aggregate_five,
    costed_geometry,
    first_touch,
    interval_overlaps,
    load_spot_bars,
    parse_positions,
)


def join_one(perp: pd.DataFrame, spot: pd.DataFrame) -> pd.DataFrame:
    common = perp.index.intersection(spot.index)
    if common.empty:
        raise RuntimeError("no synchronized one-minute spot/perpetual bars")
    p = perp.loc[common].add_prefix("perp_")
    s = spot.loc[common].add_prefix("spot_")
    result = p.join(s, how="inner")
    result["basis_bps"] = (
        np.log(result["perp_close"] / result["spot_close"]) * 10_000.0
    )
    if not np.isfinite(result["basis_bps"]).all():
        raise RuntimeError("non-finite one-minute spot/perpetual basis")
    return result


def join_five(perp: pd.DataFrame, spot: pd.DataFrame) -> pd.DataFrame:
    p = aggregate_five(perp, "perp")
    s = aggregate_five(spot, "spot")
    result = p.join(s, how="inner")
    result["basis_bps"] = (
        np.log(result["perp_close"] / result["spot_close"]) * 10_000.0
    )
    return result


def session_ranges(
    day: date,
    session: SessionSpec,
    one: pd.DataFrame,
) -> dict[str, float] | None:
    origin = pd.Timestamp(day, tz="UTC")
    start = origin + pd.Timedelta(minutes=session.build_start)
    end = origin + pd.Timedelta(minutes=session.build_end)
    build = one[(one.index > start) & (one.index <= end)]
    if build.empty or end not in one.index:
        return None
    values = {
        "perp_high": float(build.perp_high.max()),
        "perp_low": float(build.perp_low.min()),
        "spot_high": float(build.spot_high.max()),
        "spot_low": float(build.spot_low.min()),
        "anchor_basis_bps": float(one.loc[end, "basis_bps"]),
    }
    values["perp_width"] = values["perp_high"] - values["perp_low"]
    values["spot_width"] = values["spot_high"] - values["spot_low"]
    if values["perp_width"] <= 0.0 or values["spot_width"] <= 0.0:
        return None
    return values


def is_outside(
    row: pd.Series,
    ranges: dict[str, float],
    market: str,
    side: str,
) -> bool:
    close = float(row[f"{market}_close"])
    boundary = ranges[f"{market}_{'high' if side == 'HIGH' else 'low'}"]
    return close > boundary if side == "HIGH" else close < boundary


def directional_basis(
    basis_bps: float,
    ranges: dict[str, float],
    side: str,
) -> float:
    sign = 1.0 if side == "HIGH" else -1.0
    return sign * (basis_bps - ranges["anchor_basis_bps"])


def first_outside_minute(
    trade_one: pd.DataFrame,
    ranges: dict[str, float],
    market: str,
    side: str,
) -> tuple[pd.Timestamp, pd.Series] | None:
    for ts, row in trade_one.iterrows():
        if is_outside(row, ranges, market, side):
            return ts, row
    return None


def first_acceptance_five(
    trade_five: pd.DataFrame,
    ranges: dict[str, float],
    market: str,
    side: str,
    closes: int,
) -> tuple[int, pd.Timestamp, pd.Series] | None:
    run = 0
    for index, (ts, row) in enumerate(trade_five.iterrows()):
        run = run + 1 if is_outside(row, ranges, market, side) else 0
        if run >= closes:
            return index, ts, row
    return None


def emit(
    *,
    records: list[dict[str, Any]],
    week: str,
    day: date,
    session: SessionSpec,
    route: str,
    ownership_state: str,
    direction: str,
    observed: pd.Timestamp,
    decision: pd.Series,
    stop_raw: float,
    target_raw: float,
    ranges: dict[str, float],
    ownership_details: dict[str, Any],
    logic: dict[str, Any],
    perp_one: pd.DataFrame,
    occupied: list[tuple[pd.Timestamp, pd.Timestamp]],
) -> None:
    geometry, reason = costed_geometry(
        direction=direction,
        entry_raw=float(decision.perp_close),
        stop_raw=stop_raw,
        target_raw=target_raw,
        decision=decision,
        logic=logic,
    )
    item: dict[str, Any] = {
        "week": week,
        "day": day.isoformat(),
        "session": session.name,
        "route": route,
        "ownership_state": ownership_state,
        "direction": direction,
        "observed_ts": observed.isoformat(),
        "ranges": ranges,
        "decision": {
            "perp_open": float(decision.perp_open),
            "perp_high": float(decision.perp_high),
            "perp_low": float(decision.perp_low),
            "perp_close": float(decision.perp_close),
            "perp_atr": float(decision.perp_atr),
            "spot_close": float(decision.spot_close),
            "basis_bps": float(decision.basis_bps),
        },
        "ownership_details": ownership_details,
        "geometry": geometry,
        "geometry_reason": reason,
    }
    if geometry is None:
        item.update(
            {
                "outcome": "REJECTED_GEOMETRY",
                "terminal_ts": None,
                "overlaps_baseline": False,
            }
        )
    else:
        terminal = first_touch(
            perp_one,
            observed=observed,
            end=pd.Timestamp(day, tz="UTC")
            + pd.Timedelta(minutes=session.trade_end),
            direction=direction,
            stop=geometry["stop"],
            target=geometry["target"],
        )
        item.update(terminal)
        item["overlaps_baseline"] = interval_overlaps(
            observed,
            pd.Timestamp(terminal["terminal_ts"]),
            occupied,
        )
    records.append(item)


def screen_failure(
    *,
    week: str,
    day: date,
    session: SessionSpec,
    trade_one: pd.DataFrame,
    trade_five: pd.DataFrame,
    ranges: dict[str, float],
    logic: dict[str, Any],
    perp_one: pd.DataFrame,
    occupied: list[tuple[pd.Timestamp, pd.Timestamp]],
    records: list[dict[str, Any]],
    states: list[dict[str, Any]],
) -> None:
    five_rows = list(trade_five.iterrows())
    for side, direction in (("HIGH", "SHORT"), ("LOW", "LONG")):
        perp_lead = first_outside_minute(trade_one, ranges, "perp", side)
        spot_lead = first_outside_minute(trade_one, ranges, "spot", side)
        if perp_lead is None:
            continue
        perp_ts, perp_row = perp_lead
        if spot_lead is not None and spot_lead[0] <= perp_ts:
            continue
        lead_basis = directional_basis(
            float(perp_row.basis_bps),
            ranges,
            side,
        )
        state = {
            "day": day.isoformat(),
            "session": session.name,
            "side": side,
            "family": "PERP_LED_DISLOCATION",
            "perp_first_outside": perp_ts.isoformat(),
            "spot_first_outside": None if spot_lead is None else spot_lead[0].isoformat(),
            "lead_directional_basis_bps": lead_basis,
            "terminal_state": "UNRESOLVED",
        }
        states.append(state)
        if lead_basis <= 0.0:
            state["terminal_state"] = "PERP_LEAD_WITHOUT_DIRECTIONAL_BASIS"
            continue

        start_five_index = next(
            (
                index
                for index, (ts, _) in enumerate(five_rows)
                if ts >= perp_ts.ceil("5min")
            ),
            None,
        )
        if start_five_index is None:
            continue
        extreme = (
            float(perp_row.perp_high)
            if side == "HIGH"
            else float(perp_row.perp_low)
        )
        max_basis = lead_basis
        reclaim_index: int | None = None
        reclaim_ts: pd.Timestamp | None = None
        reclaim_row: pd.Series | None = None
        for index in range(
            start_five_index,
            min(
                len(five_rows),
                start_five_index + int(logic["reclaim_max_bars"]) + 1,
            ),
        ):
            ts, row = five_rows[index]
            extreme = (
                max(extreme, float(row.perp_high))
                if side == "HIGH"
                else min(extreme, float(row.perp_low))
            )
            max_basis = max(
                max_basis,
                directional_basis(float(row.basis_bps), ranges, side),
            )
            if spot_lead is not None and spot_lead[0] <= ts:
                state["terminal_state"] = "SPOT_CONFIRMED_BEFORE_RECLAIM"
                break
            if not is_outside(row, ranges, "perp", side):
                reclaim_basis = directional_basis(
                    float(row.basis_bps),
                    ranges,
                    side,
                )
                if reclaim_basis >= max_basis:
                    state["terminal_state"] = "RECLAIM_WITHOUT_BASIS_UNWIND"
                    break
                reclaim_index = index
                reclaim_ts = ts
                reclaim_row = row
                state["terminal_state"] = "PERP_RECLAIMED_SPOT_UNCONFIRMED"
                state["reclaim_directional_basis_bps"] = reclaim_basis
                state["maximum_directional_basis_bps"] = max_basis
                break
        if reclaim_index is None or reclaim_ts is None or reclaim_row is None:
            continue

        for index in range(
            reclaim_index + 1,
            min(len(five_rows), reclaim_index + 4),
        ):
            ts, row = five_rows[index]
            mss = (
                float(row.perp_close) < float(reclaim_row.perp_low)
                if direction == "SHORT"
                else float(row.perp_close) > float(reclaim_row.perp_high)
            )
            if not mss:
                continue
            if is_outside(row, ranges, "perp", side) or is_outside(
                row,
                ranges,
                "spot",
                side,
            ):
                state["terminal_state"] = "FAILED_STATE_INVALIDATED_BEFORE_MSS"
                break
            stop_raw = (
                extreme
                + float(logic["fvg_stop_buffer_atr"]) * float(row.perp_atr)
                if direction == "SHORT"
                else extreme
                - float(logic["fvg_stop_buffer_atr"]) * float(row.perp_atr)
            )
            emit(
                records=records,
                week=week,
                day=day,
                session=session,
                route=f"SPOT_PERP_1M_{side}_DISLOCATION_FAILURE",
                ownership_state="PERP_LED_SPOT_UNCONFIRMED_BASIS_UNWIND",
                direction=direction,
                observed=ts,
                decision=row,
                stop_raw=stop_raw,
                target_raw=(
                    ranges["perp_low"]
                    if direction == "SHORT"
                    else ranges["perp_high"]
                ),
                ranges=ranges,
                ownership_details={
                    **state,
                    "reclaim_ts": reclaim_ts.isoformat(),
                    "excursion_extreme": extreme,
                    "local_mss": True,
                    "target_semantics": "OPPOSITE_COMPLETED_PERP_SESSION_BOUNDARY",
                },
                logic=logic,
                perp_one=perp_one,
                occupied=occupied,
            )
            state["terminal_state"] = "TRADE_CANDIDATE"
            break


def screen_acceptance(
    *,
    week: str,
    day: date,
    session: SessionSpec,
    trade_one: pd.DataFrame,
    trade_five: pd.DataFrame,
    ranges: dict[str, float],
    logic: dict[str, Any],
    perp_one: pd.DataFrame,
    occupied: list[tuple[pd.Timestamp, pd.Timestamp]],
    records: list[dict[str, Any]],
    states: list[dict[str, Any]],
) -> None:
    five_rows = list(trade_five.iterrows())
    for side, direction in (("HIGH", "LONG"), ("LOW", "SHORT")):
        spot_first = first_outside_minute(trade_one, ranges, "spot", side)
        perp_first = first_outside_minute(trade_one, ranges, "perp", side)
        if spot_first is None or perp_first is None or spot_first[0] == perp_first[0]:
            continue
        spot_accept = first_acceptance_five(
            trade_five,
            ranges,
            "spot",
            side,
            int(logic["acceptance_closes"]),
        )
        perp_accept = first_acceptance_five(
            trade_five,
            ranges,
            "perp",
            side,
            int(logic["acceptance_closes"]),
        )
        if spot_accept is None or perp_accept is None:
            continue
        spot_index, spot_accept_ts, spot_accept_row = spot_accept
        perp_index, perp_accept_ts, perp_accept_row = perp_accept
        joint_index = max(spot_index, perp_index)
        joint_ts, joint_row = five_rows[joint_index]
        if not is_outside(joint_row, ranges, "spot", side) or not is_outside(
            joint_row,
            ranges,
            "perp",
            side,
        ):
            continue

        if spot_first[0] < perp_first[0]:
            ownership = "SPOT_LED_THEN_PERP_ACCEPTED"
            lead_basis = directional_basis(
                float(spot_first[1].basis_bps),
                ranges,
                side,
            )
            joint_basis = directional_basis(
                float(joint_row.basis_bps),
                ranges,
                side,
            )
            transmitted = lead_basis < 0.0 and joint_basis > lead_basis
        else:
            ownership = "PERP_LED_THEN_SPOT_CONFIRMED"
            lead_basis = directional_basis(
                float(perp_first[1].basis_bps),
                ranges,
                side,
            )
            joint_basis = directional_basis(
                float(joint_row.basis_bps),
                ranges,
                side,
            )
            transmitted = lead_basis > 0.0 and joint_basis < lead_basis

        state = {
            "day": day.isoformat(),
            "session": session.name,
            "side": side,
            "family": "JOINT_ACCEPTANCE",
            "ownership": ownership,
            "spot_first_outside": spot_first[0].isoformat(),
            "perp_first_outside": perp_first[0].isoformat(),
            "spot_accept_ts": spot_accept_ts.isoformat(),
            "perp_accept_ts": perp_accept_ts.isoformat(),
            "joint_accept_ts": joint_ts.isoformat(),
            "lead_directional_basis_bps": lead_basis,
            "joint_directional_basis_bps": joint_basis,
            "terminal_state": "TRANSMITTED" if transmitted else "OWNERSHIP_PATH_NOT_TRANSMITTED",
        }
        states.append(state)
        if not transmitted:
            continue

        pullback_index: int | None = None
        pullback_row: pd.Series | None = None
        for index in range(
            joint_index + 1,
            min(
                len(five_rows),
                joint_index + int(logic["acceptance_retest_expiry_bars"]) + 1,
            ),
        ):
            ts, row = five_rows[index]
            if not is_outside(row, ranges, "spot", side) or not is_outside(
                row,
                ranges,
                "perp",
                side,
            ):
                state["terminal_state"] = "JOINT_ACCEPTANCE_FAILED_BACK_INSIDE"
                break
            boundary_distance = (
                float(row.perp_low) - ranges["perp_high"]
                if side == "HIGH"
                else ranges["perp_low"] - float(row.perp_high)
            )
            near_boundary = boundary_distance <= (
                float(logic["fvg_boundary_tolerance_atr"])
                * float(row.perp_atr)
            )
            if pullback_index is None and near_boundary:
                pullback_index = index
                pullback_row = row
                state["terminal_state"] = "JOINT_ACCEPTANCE_PULLBACK"
                continue
            if pullback_index is None or pullback_row is None:
                continue
            if index - pullback_index > int(logic["reclaim_max_bars"]) + 1:
                state["terminal_state"] = "PULLBACK_LACKED_NEW_LEG"
                break
            local_break = (
                float(row.perp_close) > float(pullback_row.perp_high)
                if direction == "LONG"
                else float(row.perp_close) < float(pullback_row.perp_low)
            )
            if not local_break:
                continue
            stop_raw = (
                float(pullback_row.perp_low)
                - float(logic["fvg_stop_buffer_atr"]) * float(row.perp_atr)
                if direction == "LONG"
                else float(pullback_row.perp_high)
                + float(logic["fvg_stop_buffer_atr"]) * float(row.perp_atr)
            )
            target_raw = (
                ranges["perp_high"] + ranges["perp_width"]
                if direction == "LONG"
                else ranges["perp_low"] - ranges["perp_width"]
            )
            emit(
                records=records,
                week=week,
                day=day,
                session=session,
                route=(
                    f"SPOT_PERP_1M_{side}_SPOT_LED_ACCEPTANCE"
                    if ownership == "SPOT_LED_THEN_PERP_ACCEPTED"
                    else f"SPOT_PERP_1M_{side}_PERP_LED_CONFIRMED_ACCEPTANCE"
                ),
                ownership_state=ownership,
                direction=direction,
                observed=ts,
                decision=row,
                stop_raw=stop_raw,
                target_raw=target_raw,
                ranges=ranges,
                ownership_details={
                    **state,
                    "pullback_high": float(pullback_row.perp_high),
                    "pullback_low": float(pullback_row.perp_low),
                    "local_break": True,
                    "target_semantics": "ONE_COMPLETED_PERP_SESSION_RANGE_PROJECTION",
                },
                logic=logic,
                perp_one=perp_one,
                occupied=occupied,
            )
            state["terminal_state"] = "TRADE_CANDIDATE"
            break


def summarize(
    records: list[dict[str, Any]],
    states: list[dict[str, Any]],
) -> dict[str, Any]:
    costed = [record for record in records if record["geometry"] is not None]
    additive = [record for record in costed if not record["overlaps_baseline"]]
    outcomes: dict[str, int] = {}
    routes: dict[str, dict[str, int]] = {}
    geometry_reasons: dict[str, int] = {}
    state_reasons: dict[str, int] = {}
    for state in states:
        name = str(state["terminal_state"])
        state_reasons[name] = state_reasons.get(name, 0) + 1
    for record in records:
        name = str(record["geometry_reason"])
        geometry_reasons[name] = geometry_reasons.get(name, 0) + 1
    for record in additive:
        outcome = str(record["outcome"])
        outcomes[outcome] = outcomes.get(outcome, 0) + 1
        bucket = routes.setdefault(str(record["route"]), {})
        bucket[outcome] = bucket.get(outcome, 0) + 1
    return {
        "ownership_states": len(states),
        "state_terminal_counts": state_reasons,
        "raw_candidates": len(records),
        "costed_candidates": len(costed),
        "additive_costed_candidates": len(additive),
        "additive_outcomes": outcomes,
        "route_outcomes": routes,
        "geometry_reasons": geometry_reasons,
        "additive_targets": [
            {
                "day": record["day"],
                "session": record["session"],
                "route": record["route"],
                "direction": record["direction"],
                "observed_ts": record["observed_ts"],
                "net_r": record["geometry"]["net_r"],
                "terminal_ts": record["terminal_ts"],
            }
            for record in additive
            if record["outcome"] == "TARGET"
        ],
        "additive_stops": [
            {
                "day": record["day"],
                "session": record["session"],
                "route": record["route"],
                "direction": record["direction"],
                "observed_ts": record["observed_ts"],
                "net_r": record["geometry"]["net_r"],
                "terminal_ts": record["terminal_ts"],
            }
            for record in additive
            if record["outcome"] == "STOP"
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--config",
        type=Path,
        default=Path(__file__).with_name("config.json"),
    )
    parser.add_argument("--weeks", nargs="+", default=["W1", "W12"])
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--baseline-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    config = json.loads(args.config.read_text(encoding="utf-8"))
    logic = dict(config["logic"])
    logic["price_increment"] = float(
        config["symbols"]["BTCUSDT"]["price_increment"]
    )
    result: dict[str, Any] = {
        "schema": "candidate-12-i24b-spot-perp-ownership-v2",
        "candidate_source": config["candidate"],
        "not_performance_evidence": True,
        "policy": (
            "one-minute spot/perpetual market ordering and basis transmission "
            "define ownership; later five-minute BTC-perpetual transition defines execution"
        ),
        "weeks": {},
    }

    for week in args.weeks:
        spec = config["selection"]["weeks"][week]
        evaluation_start = date.fromisoformat(spec["start"])
        evaluation_end = date.fromisoformat(spec["end_exclusive"])
        warmup = evaluation_start - timedelta(
            days=int(config["selection"]["warmup_days"])
        )
        perp_one, perp_manifest = load_binance_bars(
            "BTCUSDT",
            warmup,
            evaluation_end,
            args.data_dir / "perpetual",
        )
        spot_one, spot_manifest = load_spot_bars(
            "BTCUSDT",
            warmup,
            evaluation_end,
            args.data_dir,
        )
        one = join_one(perp_one, spot_one)
        five = join_five(perp_one, spot_one)
        occupied = parse_positions(
            args.baseline_root / f"BTCUSDT-{week}" / "positions.csv"
        )
        records: list[dict[str, Any]] = []
        states: list[dict[str, Any]] = []
        cursor = evaluation_start
        while cursor < evaluation_end:
            if cursor.weekday() < 5:
                for session in SESSIONS:
                    ranges = session_ranges(cursor, session, one)
                    if ranges is None:
                        continue
                    origin = pd.Timestamp(cursor, tz="UTC")
                    start = origin + pd.Timedelta(minutes=session.build_end)
                    end = origin + pd.Timedelta(minutes=session.trade_end)
                    trade_one = one[(one.index > start) & (one.index <= end)]
                    trade_five = five[(five.index > start) & (five.index <= end)]
                    if trade_one.empty or trade_five.empty:
                        continue
                    screen_failure(
                        week=week,
                        day=cursor,
                        session=session,
                        trade_one=trade_one,
                        trade_five=trade_five,
                        ranges=ranges,
                        logic=logic,
                        perp_one=perp_one,
                        occupied=occupied,
                        records=records,
                        states=states,
                    )
                    screen_acceptance(
                        week=week,
                        day=cursor,
                        session=session,
                        trade_one=trade_one,
                        trade_five=trade_five,
                        ranges=ranges,
                        logic=logic,
                        perp_one=perp_one,
                        occupied=occupied,
                        records=records,
                        states=states,
                    )
            cursor += timedelta(days=1)
        unique: dict[tuple[str, str, str], dict[str, Any]] = {}
        for record in records:
            key = (record["observed_ts"], record["direction"], record["session"])
            unique.setdefault(key, record)
        records = list(unique.values())
        result["weeks"][week] = {
            "evaluation_start": evaluation_start.isoformat(),
            "evaluation_end_exclusive": evaluation_end.isoformat(),
            "perpetual_manifest": perp_manifest,
            "spot_manifest": spot_manifest,
            "baseline_positions": [
                {"opened": start.isoformat(), "closed": end.isoformat()}
                for start, end in occupied
            ],
            "summary": summarize(records, states),
            "ownership_states": states,
            "records": records,
        }

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {week: value["summary"] for week, value in result["weeks"].items()},
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
