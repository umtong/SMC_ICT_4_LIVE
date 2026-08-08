#!/usr/bin/env python3
"""Candidate 13 V17 causal missed-retrace rearm diagnostic.

V16 deliberately refuses to chase a confirmed FAR.  This diagnostic asks a
narrower question: after that passive FAR parent expires unfilled, can a *new*
pullback-break-retest auction leg recover an independent opportunity without
rewriting the original entry?

The state machine is causal and reuses frozen Candidate 13 structural values:

* first counter-direction bar starts a new pullback;
* direction-aligned close through the complete prior pullback boundary;
* body >= 0.20 ATR and signed taker flow >= 0.03 in the trade direction;
* post-only retest at the broken pullback boundary;
* stop beyond the pullback extreme by 0.08 ATR;
* original still-live external-liquidity target;
* maker entry/target, taker stop, and minimum costed net R of 1.25;
* 60-minute GTD entry lifetime.

Only checksum-verified Binance USD-M one-minute klines are used.  A bar is
visible at open_time + one minute.  This is exposed opportunity-level
research, not portfolio validation or a success claim.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from dataclasses import asdict
from datetime import date, timedelta
import json
import math
from pathlib import Path
from typing import Any, Iterable

import pandas as pd

from tape_reacceptance_diagnostics import RawEvidence, daterange, load_symbol_days

ATR_PERIOD = 30
DISPLACEMENT_BODY_ATR = 0.20
DISPLACEMENT_FLOW_MIN = 0.03
STOP_BUFFER_ATR = 0.08
MIN_STOP_ATR = 0.08
MIN_NET_R = 1.25
MAKER_RATE = 0.0004
TAKER_RATE = 0.0008
DEFAULT_ENTRY_EXPIRY_MINUTES = 60


def load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise TypeError(f"{path} must contain an object")
    return payload


def add_atr(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.copy()
    previous = result["close"].shift(1)
    tr = pd.concat(
        [
            result["high"] - result["low"],
            (result["high"] - previous).abs(),
            (result["low"] - previous).abs(),
        ],
        axis=1,
    ).max(axis=1)
    result["atr"] = tr.rolling(ATR_PERIOD, min_periods=ATR_PERIOD).mean()
    return result


def direction_sign(direction: str) -> float:
    if direction == "LONG":
        return 1.0
    if direction == "SHORT":
        return -1.0
    raise ValueError(f"unsupported direction: {direction}")


def plan_key(week: str, scenario_id: str) -> str:
    return f"{week}|{scenario_id}"


def expired_unfilled_plans(results_root: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Pair each unfilled parent expiry with its submitted FAR plan.

    Global one-slot ordering makes the lifecycle segmentation deterministic.
    Child orders are GTC; the sole ORDER_EXPIRED in an unfilled segment is the
    GTD parent.  A segment with GLOBAL_ENTRY_FILLED is not a missed retrace.
    """
    records: list[dict[str, Any]] = []
    accounting: dict[str, Any] = {
        "weeks": 0,
        "submitted_plans": 0,
        "far_submitted_plans": 0,
        "filled_parent_segments": 0,
        "expired_unfilled_parent_segments": 0,
        "unmatched_expiry_segments": [],
    }
    for plan_path in sorted(results_root.glob("W*/submitted_plans.json")):
        week = plan_path.parent.name
        lifecycle_path = plan_path.with_name("order_lifecycle.json")
        if not lifecycle_path.is_file():
            raise RuntimeError(f"missing lifecycle for {week}")
        plans = load_json(plan_path).get("plans", [])
        events = load_json(lifecycle_path).get("events", [])
        by_id = {str(item["scenario_id"]): item for item in plans}
        if len(by_id) != len(plans):
            raise RuntimeError(f"duplicate submitted scenario id in {week}")
        accounting["weeks"] += 1
        accounting["submitted_plans"] += len(plans)
        accounting["far_submitted_plans"] += sum(item.get("scenario") == "FAR" for item in plans)

        current: dict[str, Any] | None = None
        for event in events:
            kind = str(event.get("type"))
            if kind == "GLOBAL_ENTRY_SUBMITTED":
                if current is not None:
                    raise RuntimeError(f"overlapping global entry segments in {week}")
                current = {
                    "scenario_id": str(event["scenario_id"]),
                    "submitted_ts_ns": int(event["ts_event"]),
                    "filled": False,
                }
                continue
            if current is None:
                continue
            if kind == "GLOBAL_ENTRY_FILLED":
                current["filled"] = True
                accounting["filled_parent_segments"] += 1
                continue
            if kind == "ORDER_EXPIRED" and not current["filled"]:
                scenario_id = current["scenario_id"]
                plan = by_id.get(scenario_id)
                if plan is None:
                    accounting["unmatched_expiry_segments"].append(plan_key(week, scenario_id))
                elif plan.get("scenario") == "FAR":
                    row = dict(plan)
                    row.update(
                        {
                            "week": week,
                            "parent_submitted_ts_ns": current["submitted_ts_ns"],
                            "parent_expired_ts_ns": int(event["ts_event"]),
                        },
                    )
                    records.append(row)
                    accounting["expired_unfilled_parent_segments"] += 1
                current = None
                continue
            if kind in {"GLOBAL_POSITION_CLOSED", "ENTRY_SUBMISSION_EXCEPTION"}:
                current = None

        if current is not None and current.get("filled"):
            # A filled position can be flattened at evaluation end without a
            # GLOBAL_POSITION_CLOSED marker in malformed evidence; fail closed.
            raise RuntimeError(f"unterminated filled global segment in {week}: {current}")
    return records, accounting


def terminal_touched(row: pd.Series, direction: str, stop: float, target: float) -> str | None:
    if direction == "LONG":
        stop_hit = float(row["low"]) <= stop
        target_hit = float(row["high"]) >= target
    else:
        stop_hit = float(row["high"]) >= stop
        target_hit = float(row["low"]) <= target
    if stop_hit and target_hit:
        return "BOTH_ORIGINAL_TERMINALS_SAME_BAR"
    if stop_hit:
        return "ORIGINAL_STRUCTURAL_STOP_TOUCHED"
    if target_hit:
        return "ORIGINAL_TARGET_CONSUMED"
    return None


def costed_geometry(
    *,
    direction: str,
    entry: float,
    stop: float,
    target: float,
    atr: float,
) -> dict[str, Any]:
    sign = direction_sign(direction)
    risk = sign * (entry - stop)
    gain = sign * (target - entry)
    loss = risk + entry * MAKER_RATE + stop * TAKER_RATE
    net_gain = gain - entry * MAKER_RATE - target * MAKER_RATE
    net_r = net_gain / loss if loss > 0 else float("-inf")
    return {
        "risk": risk,
        "risk_atr": risk / atr if atr > 0 else None,
        "gross_gain": gain,
        "loss_per_unit": loss,
        "gain_per_unit": net_gain,
        "net_r": net_r,
        "feasible": (
            risk > 0
            and gain > 0
            and atr > 0
            and risk / atr >= MIN_STOP_ATR
            and net_gain > 0
            and net_r >= MIN_NET_R
        ),
    }


def confirmation_from_expiry(
    plan: dict[str, Any],
    bars: pd.DataFrame,
    evaluation_end: pd.Timestamp,
) -> dict[str, Any]:
    direction = str(plan["direction"])
    sign = direction_sign(direction)
    stop = float(plan["stop"])
    target = float(plan["target"])
    expiry = pd.to_datetime(int(plan["parent_expired_ts_ns"]), unit="ns", utc=True)
    post = bars.loc[(bars["observed_time"] > expiry) & (bars["observed_time"] < evaluation_end)].copy()
    base = {
        "row_key": plan_key(str(plan["week"]), str(plan["scenario_id"])),
        "week": plan["week"],
        "symbol": plan["symbol"],
        "original_scenario_id": plan["scenario_id"],
        "direction": direction,
        "original_entry": float(plan["entry"]),
        "original_stop": stop,
        "original_target": target,
        "original_role": (plan.get("details") or {}).get("post_leadership_role"),
        "parent_expired_time": expiry.isoformat(),
        "evaluation_end": evaluation_end.isoformat(),
    }
    if post.empty:
        return {**base, "state": "NO_POST_EXPIRY_DATA"}

    pullback_started = False
    pullback_boundary: float | None = None
    pullback_extreme: float | None = None
    pullback_start: pd.Timestamp | None = None
    pullback_bars = 0
    for _, row in post.iterrows():
        terminal = terminal_touched(row, direction, stop, target)
        if terminal is not None:
            return {
                **base,
                "state": terminal,
                "terminal_time": row["observed_time"].isoformat(),
                "pullback_started": pullback_started,
            }
        atr = float(row["atr"]) if pd.notna(row["atr"]) else float("nan")
        counter = sign * (float(row["close"]) - float(row["open"])) < 0.0
        if not pullback_started:
            if not counter or not math.isfinite(atr) or atr <= 0:
                continue
            pullback_started = True
            pullback_start = row["observed_time"]
            pullback_bars = 1
            if direction == "LONG":
                pullback_boundary = float(row["high"])
                pullback_extreme = float(row["low"])
            else:
                pullback_boundary = float(row["low"])
                pullback_extreme = float(row["high"])
            continue

        assert pullback_boundary is not None and pullback_extreme is not None and pullback_start is not None
        body = abs(float(row["close"]) - float(row["open"]))
        flow = sign * float(row["signed_flow"])
        if direction == "LONG":
            broke = float(row["close"]) > pullback_boundary
            aligned = float(row["close"]) > float(row["open"])
        else:
            broke = float(row["close"]) < pullback_boundary
            aligned = float(row["close"]) < float(row["open"])
        confirmed = (
            broke
            and aligned
            and math.isfinite(atr)
            and atr > 0
            and body >= DISPLACEMENT_BODY_ATR * atr
            and flow >= DISPLACEMENT_FLOW_MIN
        )
        if confirmed:
            entry = pullback_boundary
            stop_price = (
                pullback_extreme - STOP_BUFFER_ATR * atr
                if direction == "LONG"
                else pullback_extreme + STOP_BUFFER_ATR * atr
            )
            geometry = costed_geometry(
                direction=direction,
                entry=entry,
                stop=stop_price,
                target=target,
                atr=atr,
            )
            return {
                **base,
                "state": "REARM_CONFIRMATION_FOUND" if geometry["feasible"] else "REARM_GEOMETRY_REJECTED",
                "pullback_start": pullback_start.isoformat(),
                "pullback_bars_before_confirmation": pullback_bars,
                "confirmation_time": row["observed_time"].isoformat(),
                "confirmation_close": float(row["close"]),
                "confirmation_body_atr": body / atr,
                "confirmation_directional_flow": flow,
                "entry": entry,
                "stop": stop_price,
                "target": target,
                "atr": atr,
                "entry_expiry_minutes": int(
                    (plan.get("details") or {}).get(
                        "entry_expiry_structure_minutes",
                        DEFAULT_ENTRY_EXPIRY_MINUTES,
                    ),
                ),
                **geometry,
            }

        pullback_bars += 1
        if direction == "LONG":
            pullback_boundary = max(pullback_boundary, float(row["high"]))
            pullback_extreme = min(pullback_extreme, float(row["low"]))
        else:
            pullback_boundary = min(pullback_boundary, float(row["low"]))
            pullback_extreme = max(pullback_extreme, float(row["high"]))

    return {
        **base,
        "state": "NO_REARM_CONFIRMATION_BEFORE_EVALUATION_END",
        "pullback_started": pullback_started,
        "pullback_start": None if pullback_start is None else pullback_start.isoformat(),
        "pullback_bars": pullback_bars,
    }


def simulate_retest(record: dict[str, Any], bars: pd.DataFrame, evaluation_end: pd.Timestamp) -> dict[str, Any]:
    if record.get("state") != "REARM_CONFIRMATION_FOUND":
        return record
    direction = str(record["direction"])
    entry = float(record["entry"])
    stop = float(record["stop"])
    target = float(record["target"])
    confirmation = pd.Timestamp(record["confirmation_time"])
    expiry = confirmation + pd.Timedelta(minutes=int(record["entry_expiry_minutes"]))
    post = bars.loc[
        (bars["observed_time"] > confirmation)
        & (bars["observed_time"] < evaluation_end)
    ].copy()
    filled = False
    fill_time: pd.Timestamp | None = None
    for _, row in post.iterrows():
        ts = row["observed_time"]
        if not filled and ts > expiry:
            return {**record, "outcome": "REARM_PARENT_EXPIRED_UNFILLED", "rearm_parent_expiry": expiry.isoformat()}
        if direction == "LONG":
            entry_touch = float(row["low"]) <= entry
            stop_hit = float(row["low"]) <= stop
            target_hit = float(row["high"]) >= target
        else:
            entry_touch = float(row["high"]) >= entry
            stop_hit = float(row["high"]) >= stop
            target_hit = float(row["low"]) <= target

        if not filled:
            if target_hit:
                return {**record, "outcome": "TARGET_CONSUMED_BEFORE_RETEST_FILL", "terminal_time": ts.isoformat()}
            if stop_hit:
                return {**record, "outcome": "STRUCTURAL_STOP_BEFORE_RETEST_FILL", "terminal_time": ts.isoformat()}
            if not entry_touch:
                continue
            filled = True
            fill_time = ts
            if target_hit:
                return {**record, "outcome": "AMBIGUOUS_ENTRY_AND_TARGET_SAME_BAR", "fill_time": ts.isoformat()}
            if stop_hit:
                return {
                    **record,
                    "outcome": "LOSS_STOP_SAME_FILL_BAR",
                    "fill_time": ts.isoformat(),
                    "terminal_time": ts.isoformat(),
                    "realized_r": -1.0,
                }
            continue

        if stop_hit and target_hit:
            return {
                **record,
                "outcome": "LOSS_BOTH_TERMINALS_STOP_FIRST",
                "fill_time": fill_time.isoformat() if fill_time is not None else None,
                "terminal_time": ts.isoformat(),
                "realized_r": -1.0,
            }
        if stop_hit:
            return {
                **record,
                "outcome": "LOSS_STOP",
                "fill_time": fill_time.isoformat() if fill_time is not None else None,
                "terminal_time": ts.isoformat(),
                "realized_r": -1.0,
            }
        if target_hit:
            return {
                **record,
                "outcome": "WIN_TARGET",
                "fill_time": fill_time.isoformat() if fill_time is not None else None,
                "terminal_time": ts.isoformat(),
                "realized_r": float(record["net_r"]),
            }

    if not filled:
        return {**record, "outcome": "NO_FILL_BEFORE_EVALUATION_END"}
    last = post.iloc[-1]
    close = float(last["close"])
    sign = direction_sign(direction)
    gross = sign * (close - entry)
    # Evaluation-end flatten is marketable; entry remains maker.
    net = gross - entry * MAKER_RATE - close * TAKER_RATE
    realized_r = net / float(record["loss_per_unit"])
    return {
        **record,
        "outcome": "EVALUATION_END_MARKET_FLATTEN",
        "fill_time": fill_time.isoformat() if fill_time is not None else None,
        "terminal_time": last["observed_time"].isoformat(),
        "evaluation_end_close": close,
        "realized_r": realized_r,
    }


def summarize(records: list[dict[str, Any]]) -> dict[str, Any]:
    state_counts = Counter(str(row.get("state")) for row in records)
    outcome_counts = Counter(str(row.get("outcome", "NO_EXECUTABLE_PLAN")) for row in records)
    executable = [row for row in records if row.get("state") == "REARM_CONFIRMATION_FOUND"]
    filled = [row for row in executable if row.get("fill_time")]
    wins = [row for row in filled if str(row.get("outcome", "")).startswith("WIN_")]
    losses = [row for row in filled if str(row.get("outcome", "")).startswith("LOSS_")]
    realized = [float(row["realized_r"]) for row in filled if row.get("realized_r") is not None]
    return {
        "expired_far_parents": len(records),
        "confirmation_found": len(executable),
        "filled_rearm_plans": len(filled),
        "wins": len(wins),
        "losses": len(losses),
        "resolved_win_rate": len(wins) / (len(wins) + len(losses)) if wins or losses else None,
        "sum_realized_r": sum(realized),
        "median_planned_net_r": (
            float(pd.Series([float(row["net_r"]) for row in executable]).median())
            if executable else None
        ),
        "state_counts": dict(sorted(state_counts.items())),
        "outcome_counts": dict(sorted(outcome_counts.items())),
        "filled_row_keys": [row["row_key"] for row in filled],
    }


def markdown(result: dict[str, Any]) -> str:
    summary = result["summary"]
    lines = [
        "# Candidate 13 V17 — missed passive retrace rearm diagnostic",
        "",
        "**EXPOSED_DEVELOPMENT_DIAGNOSTIC_ONLY — success_claim: false**",
        "",
        "The original V16 FAR is never chased or rewritten.  This diagnostic begins only after a real unfilled parent expiry and requires a new pullback-break-retest leg.",
        "",
        "## Summary",
        "",
        f"- expired FAR parents: `{summary['expired_far_parents']}`",
        f"- feasible rearm confirmations: `{summary['confirmation_found']}`",
        f"- filled rearm plans: `{summary['filled_rearm_plans']}`",
        f"- wins / losses: `{summary['wins']} / {summary['losses']}`",
        f"- resolved win rate: `{summary['resolved_win_rate']}`",
        f"- sum realized R: `{summary['sum_realized_r']:.6f}`",
        "",
        "## Per expired FAR",
        "",
        "| Week | Symbol | Role | State | Outcome | Planned R | Realized R |",
        "|---|---|---|---|---|---:|---:|",
    ]
    for row in result["records"]:
        lines.append(
            "| {week} | {symbol} | {role} | {state} | {outcome} | {planned} | {realized} |".format(
                week=row["week"],
                symbol=row["symbol"],
                role=row.get("original_role"),
                state=row.get("state"),
                outcome=row.get("outcome", "—"),
                planned="—" if row.get("net_r") is None else f"{float(row['net_r']):.3f}",
                realized="—" if row.get("realized_r") is None else f"{float(row['realized_r']):.3f}",
            ),
        )
    lines.extend(
        [
            "",
            "## Causal contract",
            "",
            "- Signal search begins strictly after the Nautilus parent expiry event.",
            "- The breakout bar is not included in the prior pullback boundary.",
            "- The retest limit cannot fill on the confirmation bar.",
            "- Original target/stop consumption invalidates rearming.",
            "- Same-bar entry/target ambiguity is not credited as a win.",
            "- Portfolio overlap and final fills must be verified in NautilusTrader before adoption.",
        ],
    )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--results-root", type=Path, required=True)
    parser.add_argument("--protocol", type=Path, required=True)
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--output-md", type=Path, required=True)
    parser.add_argument("--evidence-json", type=Path, required=True)
    args = parser.parse_args()

    protocol = load_json(args.protocol)
    holdouts = protocol["selection"]["holdouts"]
    expired, accounting = expired_unfilled_plans(args.results_root)
    required_days: dict[str, set[date]] = defaultdict(set)
    for plan in expired:
        week = str(plan["week"])
        end = date.fromisoformat(holdouts[week]["end_exclusive"])
        expiry = pd.to_datetime(int(plan["parent_expired_ts_ns"]), unit="ns", utc=True)
        required_days[str(plan["symbol"])].update(
            daterange(expiry.date() - timedelta(days=1), end - timedelta(days=1)),
        )

    frames: dict[str, pd.DataFrame] = {}
    evidence: list[RawEvidence] = []
    for symbol, days in sorted(required_days.items()):
        raw, items = load_symbol_days(symbol, days, args.cache)
        frames[symbol] = add_atr(raw)
        evidence.extend(items)

    records: list[dict[str, Any]] = []
    for plan in expired:
        week = str(plan["week"])
        evaluation_end = pd.Timestamp(holdouts[week]["end_exclusive"], tz="UTC")
        found = confirmation_from_expiry(plan, frames[str(plan["symbol"])], evaluation_end)
        records.append(simulate_retest(found, frames[str(plan["symbol"])], evaluation_end))

    if len({row["row_key"] for row in records}) != len(records):
        raise RuntimeError("duplicate expired FAR row key")
    result = {
        "schema": "candidate-13-v17-missed-retrace-rearm-diagnostic-v1",
        "evaluation_role": "EXPOSED_DEVELOPMENT_DIAGNOSTIC_ONLY",
        "success_claim": False,
        "causal_contract": {
            "market_data": "checksum-verified official Binance USD-M one-minute klines",
            "bar_observed_time": "open_time + one minute",
            "search_begins_after_real_parent_expiry": True,
            "post_confirmation_same_bar_fill": False,
            "original_far_rewritten": False,
            "portfolio_overlap_modeled": False,
        },
        "constants": {
            "atr_period": ATR_PERIOD,
            "displacement_body_atr": DISPLACEMENT_BODY_ATR,
            "displacement_flow_min": DISPLACEMENT_FLOW_MIN,
            "stop_buffer_atr": STOP_BUFFER_ATR,
            "min_stop_atr": MIN_STOP_ATR,
            "min_net_r": MIN_NET_R,
            "maker_rate": MAKER_RATE,
            "taker_rate": TAKER_RATE,
        },
        "accounting": accounting,
        "summary": summarize(records),
        "records": records,
    }
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(json.dumps(result, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")
    args.output_md.write_text(markdown(result), encoding="utf-8")
    args.evidence_json.write_text(
        json.dumps(
            {"schema": "candidate-13-v17-raw-kline-evidence-v1", "archives": [asdict(item) for item in evidence]},
            indent=2,
            sort_keys=True,
        ) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(result["summary"], indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
