#!/usr/bin/env python3
"""Candidate 15 V25 session-range delivery mechanism screen.

The fixed scenario is:
preceding-session range -> midpoint-selected liquidity objective -> 15-minute
opening balance -> cross-market state -> first completed five-minute breakout
-> first one-minute retest -> opposite opening-range invalidation -> preceding
session extreme target.

This is an economic and geometry screen. A passing integrated family still
requires NautilusTrader orders, exact 3% current-NAV sizing and continuous NAV.
"""
from __future__ import annotations

import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, time, timedelta, timezone
import json
import math
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

import diagnose_v23_quarter_hour_10s as base

SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT")
ROUTE = "SESSION_PRIOR_RANGE_DELIVERY"


def load_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise TypeError(f"{path} must contain a JSON object")
    return value


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def safe_div(numerator: pd.Series, denominator: pd.Series) -> pd.Series:
    return numerator / denominator.replace(0.0, np.nan)


def add_minute_features(frame: pd.DataFrame) -> pd.DataFrame:
    output = frame.copy()
    quote = output["quote_volume"].astype(float)
    taker = output["taker_buy_quote_volume"].astype(float)
    output["signed_taker_pressure"] = (
        2.0 * safe_div(taker, quote) - 1.0
    ).clip(-1.0, 1.0)
    return output.replace([np.inf, -np.inf], np.nan)


def parse_hhmm(value: str) -> time:
    hour, minute = (int(part) for part in value.split(":"))
    return time(hour=hour, minute=minute)


def utc_timestamp(day: date, clock: str) -> pd.Timestamp:
    parsed = parse_hhmm(clock)
    return pd.Timestamp(datetime.combine(day, parsed, tzinfo=timezone.utc))


def session_start(day: date, session: str, protocol: dict[str, Any]) -> pd.Timestamp:
    config = protocol["sessions"][session]
    if "start_utc" in config:
        return utc_timestamp(day, str(config["start_utc"]))
    local_clock = parse_hhmm(str(config["start_local"]))
    local = datetime.combine(
        day,
        local_clock,
        tzinfo=ZoneInfo(str(config["timezone"])),
    )
    return pd.Timestamp(local.astimezone(timezone.utc))


def preceding_bounds(
    day: date,
    session: str,
    start: pd.Timestamp,
    protocol: dict[str, Any],
) -> tuple[pd.Timestamp, pd.Timestamp]:
    config = protocol["sessions"][session]
    if session == "ASIA":
        previous = day - timedelta(days=1)
        return (
            utc_timestamp(previous, str(config["preceding_range_start_utc_previous_day"])),
            start,
        )
    return (
        utc_timestamp(day, str(config["preceding_range_start_utc"])),
        start,
    )


def bars_between(
    frame: pd.DataFrame,
    start_exclusive: pd.Timestamp,
    end_inclusive: pd.Timestamp,
) -> pd.DataFrame:
    return frame[(frame.index > start_exclusive) & (frame.index <= end_inclusive)]


def weighted_pressure(frame: pd.DataFrame) -> float:
    if frame.empty:
        return float("nan")
    weights = np.maximum(frame["quote_volume"].astype(float).to_numpy(), 1.0)
    values = frame["signed_taker_pressure"].astype(float).to_numpy()
    return float(np.average(values, weights=weights))


def bar_summary(frame: pd.DataFrame) -> dict[str, float]:
    if frame.empty:
        raise ValueError("cannot summarize empty bar window")
    return {
        "open": float(frame["open"].iloc[0]),
        "high": float(frame["high"].max()),
        "low": float(frame["low"].min()),
        "close": float(frame["close"].iloc[-1]),
        "quote_volume": float(frame["quote_volume"].sum()),
        "pressure": weighted_pressure(frame),
    }


def opening_event(
    frame: pd.DataFrame,
    *,
    symbol: str,
    day: date,
    session: str,
    protocol: dict[str, Any],
) -> dict[str, Any] | None:
    rules = protocol["fixed_rules"]
    start = session_start(day, session, protocol)
    prior_start, prior_end = preceding_bounds(day, session, start, protocol)
    prior = bars_between(frame, prior_start, prior_end)
    opening_end = start + pd.Timedelta(minutes=int(rules["opening_range_minutes"]))
    opening = bars_between(frame, start, opening_end)
    if len(prior.index) < 120 or len(opening.index) != int(rules["opening_range_minutes"]):
        return None
    prior_high = float(prior["high"].max())
    prior_low = float(prior["low"].min())
    if not (math.isfinite(prior_high) and math.isfinite(prior_low)):
        return None
    if prior_high <= prior_low:
        return None
    start_price = float(opening["open"].iloc[0])
    midpoint = 0.5 * (prior_high + prior_low)
    direction = 1 if start_price > midpoint else -1 if start_price < midpoint else 0
    if direction == 0:
        return None
    summary = bar_summary(opening)
    opening_range = summary["high"] - summary["low"]
    if opening_range <= 0.0:
        return None
    body_fraction = direction * (summary["close"] - summary["open"]) / opening_range
    objective = prior_high if direction > 0 else prior_low
    consumed = summary["high"] >= objective if direction > 0 else summary["low"] <= objective
    return {
        "session_id": f"{day.isoformat()}-{session}",
        "date": day.isoformat(),
        "session": session,
        "symbol": symbol,
        "session_start": start,
        "opening_end": opening_end,
        "prior_start": prior_start,
        "prior_end": prior_end,
        "prior_high": prior_high,
        "prior_low": prior_low,
        "prior_midpoint": midpoint,
        "session_start_price": start_price,
        "direction_sign": direction,
        "direction": "LONG" if direction > 0 else "SHORT",
        "objective": objective,
        "opening_open": summary["open"],
        "opening_high": summary["high"],
        "opening_low": summary["low"],
        "opening_close": summary["close"],
        "opening_range": opening_range,
        "opening_body_fraction": body_fraction,
        "opening_pressure": summary["pressure"],
        "opening_return": summary["close"] / summary["open"] - 1.0,
        "objective_consumed_in_opening": bool(consumed),
    }


def build_opening_events(
    frames: dict[str, pd.DataFrame],
    protocol: dict[str, Any],
) -> pd.DataFrame:
    evaluation = protocol["evaluation"]
    start = date.fromisoformat(evaluation["development_start"])
    end = date.fromisoformat(evaluation["latest_pulse_end_exclusive"])
    records: list[dict[str, Any]] = []
    for timestamp in pd.date_range(start, end, freq="D", inclusive="left", tz="UTC"):
        day = timestamp.date()
        for session in protocol["sessions"]:
            session_records: list[dict[str, Any]] = []
            for symbol in SYMBOLS:
                record = opening_event(
                    frames[symbol],
                    symbol=symbol,
                    day=day,
                    session=session,
                    protocol=protocol,
                )
                if record is not None:
                    session_records.append(record)
            if len(session_records) < len(SYMBOLS):
                continue
            returns = {
                record["symbol"]: float(record["opening_return"])
                for record in session_records
            }
            for record in session_records:
                direction = int(record["direction_sign"])
                record["cross_market_opening_breadth"] = float(
                    sum(direction * value > 0.0 for value in returns.values())
                    / len(returns)
                )
                records.append(record)
    frame = pd.DataFrame(records)
    if frame.empty:
        return frame
    for column in ("session_start", "opening_end", "prior_start", "prior_end"):
        frame[column] = pd.to_datetime(frame[column], utc=True)
    return frame.sort_values(
        ["opening_end", "session", "symbol"],
        kind="stable",
    ).reset_index(drop=True)


def first_breakout(
    frame: pd.DataFrame,
    event: pd.Series,
    rules: dict[str, Any],
) -> dict[str, Any] | None:
    direction = int(event["direction_sign"])
    opening_end = pd.Timestamp(event["opening_end"])
    end = opening_end + pd.Timedelta(minutes=int(rules["breakout_window_minutes"]))
    step = int(rules["breakout_bar_minutes"])
    cursor = opening_end
    while cursor < end:
        window_end = min(cursor + pd.Timedelta(minutes=step), end)
        window = bars_between(frame, cursor, window_end)
        if len(window.index) != int((window_end - cursor).total_seconds() // 60):
            cursor = window_end
            continue
        summary = bar_summary(window)
        boundary = float(event["opening_high"]) if direction > 0 else float(event["opening_low"])
        closed_outside = summary["close"] > boundary if direction > 0 else summary["close"] < boundary
        pressure_aligned = direction * summary["pressure"] > float(rules["minimum_breakout_directional_taker_pressure"])
        if closed_outside and pressure_aligned:
            return {
                "breakout_start": cursor,
                "breakout_end": window_end,
                "breakout_open": summary["open"],
                "breakout_high": summary["high"],
                "breakout_low": summary["low"],
                "breakout_close": summary["close"],
                "breakout_pressure": summary["pressure"],
                "broken_boundary": boundary,
            }
        cursor = window_end
    return None


def first_retest(
    frame: pd.DataFrame,
    event: pd.Series,
    breakout: dict[str, Any],
    rules: dict[str, Any],
) -> dict[str, Any] | None:
    direction = int(event["direction_sign"])
    start = pd.Timestamp(breakout["breakout_end"])
    end = start + pd.Timedelta(minutes=int(rules["retest_window_minutes"]))
    window = bars_between(frame, start, end)
    boundary = float(breakout["broken_boundary"])
    for timestamp, bar in window.iterrows():
        pressure = float(bar["signed_taker_pressure"])
        if direction > 0:
            touched = float(bar["low"]) <= boundary
            held = float(bar["close"]) > boundary
        else:
            touched = float(bar["high"]) >= boundary
            held = float(bar["close"]) < boundary
        pressure_valid = direction * pressure >= float(rules["minimum_retest_directional_taker_pressure"])
        if touched and held and pressure_valid:
            return {
                "entry_ts": pd.Timestamp(timestamp),
                "entry_price": float(bar["close"]),
                "retest_open": float(bar["open"]),
                "retest_high": float(bar["high"]),
                "retest_low": float(bar["low"]),
                "retest_close": float(bar["close"]),
                "retest_pressure": pressure,
            }
    return None


def evaluate_trade(
    frame: pd.DataFrame,
    event: pd.Series,
    entry: dict[str, Any],
    *,
    stop: float,
    target: float,
    protocol: dict[str, Any],
) -> dict[str, Any] | None:
    direction = int(event["direction_sign"])
    session = str(event["session"])
    horizon = int(protocol["sessions"][session]["evaluation_minutes"])
    terminal = pd.Timestamp(event["session_start"]) + pd.Timedelta(minutes=horizon)
    entry_ts = pd.Timestamp(entry["entry_ts"])
    if terminal <= entry_ts:
        return None
    path = bars_between(frame, entry_ts, terminal)
    if path.empty:
        return None
    exit_price: float | None = None
    exit_ts: pd.Timestamp | None = None
    exit_reason = "SESSION_TIMEOUT"
    ambiguous = False
    for timestamp, bar in path.iterrows():
        if direction > 0:
            stop_hit = float(bar["low"]) <= stop
            target_hit = float(bar["high"]) >= target
        else:
            stop_hit = float(bar["high"]) >= stop
            target_hit = float(bar["low"]) <= target
        if stop_hit and target_hit:
            ambiguous = True
            exit_price = stop
            exit_ts = pd.Timestamp(timestamp)
            exit_reason = "STOP_FIRST_SAME_BAR_AMBIGUITY"
            break
        if stop_hit:
            exit_price = stop
            exit_ts = pd.Timestamp(timestamp)
            exit_reason = "STRUCTURAL_STOP"
            break
        if target_hit:
            exit_price = target
            exit_ts = pd.Timestamp(timestamp)
            exit_reason = "PRIOR_SESSION_OBJECTIVE"
            break
    if exit_price is None:
        exit_price = float(path["close"].iloc[-1])
        exit_ts = pd.Timestamp(path.index[-1])
    gross_return = direction * (exit_price / float(entry["entry_price"]) - 1.0)
    cost_return = (
        float(protocol["fixed_rules"]["execution_round_trip_cost_bps"])
        + float(protocol["fixed_rules"]["funding_and_unmodeled_impact_reserve_bps"])
    ) / 10_000.0
    return {
        "exit_ts": exit_ts,
        "exit_price": exit_price,
        "exit_reason": exit_reason,
        "same_bar_ambiguous": ambiguous,
        "gross_return": gross_return,
        "cost_return": cost_return,
        "net_return": gross_return - cost_return,
    }


def executable_candidates(
    openings: pd.DataFrame,
    frames: dict[str, pd.DataFrame],
    protocol: dict[str, Any],
) -> tuple[pd.DataFrame, Counter[str]]:
    if openings.empty:
        return pd.DataFrame(), Counter()
    rules = protocol["fixed_rules"]
    rejections: Counter[str] = Counter()
    records: list[dict[str, Any]] = []
    for _, event in openings.iterrows():
        direction = int(event["direction_sign"])
        if bool(event["objective_consumed_in_opening"]):
            rejections["OBJECTIVE_CONSUMED_IN_OPENING"] += 1
            continue
        if float(event["opening_body_fraction"]) < float(rules["minimum_opening_range_directional_body_fraction"]):
            rejections["OPENING_BODY_NOT_DIRECTIONAL"] += 1
            continue
        if direction * float(event["opening_pressure"]) <= float(rules["minimum_opening_range_directional_taker_pressure"]):
            rejections["OPENING_FLOW_NOT_DIRECTIONAL"] += 1
            continue
        if float(event["cross_market_opening_breadth"]) < float(rules["minimum_cross_market_opening_range_breadth"]):
            rejections["CROSS_MARKET_BREADTH_UNRESOLVED"] += 1
            continue
        frame = frames[str(event["symbol"])]
        breakout = first_breakout(frame, event, rules)
        if breakout is None:
            rejections["NO_ACCEPTED_BREAKOUT"] += 1
            continue
        retest = first_retest(frame, event, breakout, rules)
        if retest is None:
            rejections["NO_FIRST_RETEST_HOLD"] += 1
            continue
        entry_price = float(retest["entry_price"])
        stop = float(event["opening_low"]) if direction > 0 else float(event["opening_high"])
        target = float(event["objective"])
        valid_geometry = stop < entry_price < target if direction > 0 else target < entry_price < stop
        if not valid_geometry:
            rejections["INVALID_GEOMETRY"] += 1
            continue
        cost_return = (
            float(rules["execution_round_trip_cost_bps"])
            + float(rules["funding_and_unmodeled_impact_reserve_bps"])
        ) / 10_000.0
        loss_fraction = abs(entry_price - stop) / entry_price + cost_return
        reward_fraction = abs(target - entry_price) / entry_price - cost_return
        structural_r = reward_fraction / max(loss_fraction, 1e-12)
        if structural_r < float(rules["minimum_net_structural_r"]):
            rejections["INSUFFICIENT_NET_STRUCTURAL_R"] += 1
            continue
        outcome = evaluate_trade(frame, event, retest, stop=stop, target=target, protocol=protocol)
        if outcome is None:
            rejections["NO_COMPLETE_OUTCOME_PATH"] += 1
            continue
        record = {
            **{key: event[key] for key in (
                "session_id", "date", "session", "symbol", "session_start",
                "opening_end", "prior_start", "prior_end", "prior_high",
                "prior_low", "prior_midpoint", "session_start_price", "direction",
                "direction_sign", "objective", "opening_open", "opening_high",
                "opening_low", "opening_close", "opening_range",
                "opening_body_fraction", "opening_pressure",
                "cross_market_opening_breadth",
            )},
            **breakout,
            **retest,
            "route": ROUTE,
            "stop_price": stop,
            "target_price": target,
            "loss_fraction_with_cost": loss_fraction,
            "reward_fraction_after_cost": reward_fraction,
            "net_structural_r": structural_r,
            **outcome,
        }
        records.append(record)
    frame = pd.DataFrame(records)
    if frame.empty:
        return frame, rejections
    for column in (
        "session_start", "opening_end", "prior_start", "prior_end",
        "breakout_start", "breakout_end", "entry_ts", "exit_ts",
    ):
        frame[column] = pd.to_datetime(frame[column], utc=True)
    frame = frame.sort_values(
        ["entry_ts", "net_structural_r", "breakout_pressure", "symbol"],
        ascending=[True, False, False, True],
        kind="stable",
    ).reset_index(drop=True)
    return frame, rejections


def arbitrate(candidates: pd.DataFrame) -> tuple[pd.DataFrame, Counter[str]]:
    if candidates.empty:
        return candidates.copy(), Counter()
    selected: list[pd.Series] = []
    skips: Counter[str] = Counter()
    free_at = pd.Timestamp.min.tz_localize("UTC")
    consumed_sessions: set[str] = set()
    for timestamp, batch in candidates.groupby("entry_ts", sort=True):
        available = batch[~batch["session_id"].astype(str).isin(consumed_sessions)]
        if available.empty:
            skips["SESSION_ALREADY_CONSUMED"] += len(batch.index)
            continue
        winner = available.iloc[0]
        same_session_losers = available[available["session_id"] == winner["session_id"]]
        skips["SAME_SESSION_LOSER"] += max(0, len(same_session_losers.index) - 1)
        session_id = str(winner["session_id"])
        consumed_sessions.add(session_id)
        if pd.Timestamp(timestamp) < free_at:
            skips["GLOBAL_POSITION_OCCUPIED"] += 1
            continue
        selected.append(winner)
        free_at = pd.Timestamp(winner["exit_ts"])
    if not selected:
        return candidates.iloc[0:0].copy(), skips
    return pd.DataFrame(selected).reset_index(drop=True), skips


def t_stat(values: pd.Series) -> float | None:
    if len(values.index) < 2:
        return None
    standard = float(values.std(ddof=1))
    if not math.isfinite(standard) or standard <= 0.0:
        return None
    return float(values.mean() / (standard / math.sqrt(len(values.index))))


def payoff(values: pd.Series) -> float | None:
    wins = values[values > 0.0]
    losses = values[values < 0.0]
    if wins.empty or losses.empty:
        return None
    return float(wins.mean() / abs(losses.mean()))


def split_summary(frame: pd.DataFrame, start: str, end_exclusive: str) -> dict[str, Any]:
    lower = pd.Timestamp(start, tz="UTC")
    upper = pd.Timestamp(end_exclusive, tz="UTC")
    section = frame[(frame["entry_ts"] >= lower) & (frame["entry_ts"] < upper)].copy()
    calendar_days = int((upper - lower).total_seconds() // 86_400)
    net = section["net_return"] if not section.empty else pd.Series(dtype=float)
    if not section.empty:
        monthly = section.assign(month=section["entry_ts"].dt.to_period("M").astype(str)).groupby("month")["net_return"].sum()
    else:
        monthly = pd.Series(dtype=float)
    return {
        "start": start,
        "end_exclusive": end_exclusive,
        "calendar_days": calendar_days,
        "trades": len(section.index),
        "trades_per_calendar_day": len(section.index) / max(calendar_days, 1),
        "mean_gross_bps": None if section.empty else float(section["gross_return"].mean() * 10_000.0),
        "mean_net_bps": None if section.empty else float(net.mean() * 10_000.0),
        "net_t_stat": t_stat(net),
        "win_rate": None if section.empty else float((net > 0.0).mean()),
        "payoff_ratio": payoff(net),
        "mean_net_structural_r": None if section.empty else float(section["net_structural_r"].mean()),
        "positive_months": int((monthly > 0.0).sum()),
        "active_months": len(monthly.index),
        "positive_month_share": float((monthly > 0.0).mean()) if len(monthly.index) else 0.0,
        "symbol_counts": dict(Counter(section["symbol"])) if not section.empty else {},
        "session_counts": dict(Counter(section["session"])) if not section.empty else {},
        "exit_reason_counts": dict(Counter(section["exit_reason"])) if not section.empty else {},
        "same_bar_ambiguities": int(section["same_bar_ambiguous"].sum()) if not section.empty else 0,
    }


def execute(protocol_path: Path, output: Path) -> dict[str, Any]:
    protocol = load_object(protocol_path)
    if protocol["schema"] != "candidate-15-v25-session-range-delivery-v1":
        raise RuntimeError("unexpected Candidate 15 V25 protocol")
    output.mkdir(parents=True, exist_ok=True)
    data_dir = output / "data"
    frames: dict[str, pd.DataFrame] = {}
    manifest: list[dict[str, Any]] = []

    def load_symbol(symbol: str) -> tuple[str, pd.DataFrame, list[dict[str, Any]]]:
        frame, records = base.load_monthly_one_minute(symbol, protocol, data_dir)
        return symbol, add_minute_features(frame), records

    with ThreadPoolExecutor(max_workers=4) as pool:
        jobs = [pool.submit(load_symbol, symbol) for symbol in SYMBOLS]
        for job in as_completed(jobs):
            symbol, frame, records = job.result()
            frames[symbol] = frame
            manifest.extend(records)
    write_json(
        output / "data_manifest.json",
        {
            "schema": "candidate-15-v25-data-manifest-v1",
            "protocol": protocol["schema"],
            "files": sorted(manifest, key=lambda item: (item.get("symbol", ""), item.get("token", item.get("month", "")))),
        },
    )

    openings = build_opening_events(frames, protocol)
    candidates, rejections = executable_candidates(openings, frames, protocol)
    selected, arbitration = arbitrate(candidates)
    openings.to_csv(output / "opening_events.csv", index=False)
    candidates.to_csv(output / "all_executable_candidates.csv", index=False)
    selected.to_csv(output / "selected_trades.csv", index=False)

    evaluation = protocol["evaluation"]
    summaries = {
        name: split_summary(selected, evaluation[f"{name}_start"], evaluation[f"{name}_end_exclusive"])
        for name in ("development", "stability", "july_confirmation", "latest_pulse")
    }
    development = summaries["development"]
    stability = summaries["stability"]
    july = summaries["july_confirmation"]
    pulse = summaries["latest_pulse"]
    gate = protocol["advance_gate"]
    max_symbol_share = max(stability["symbol_counts"].values()) / max(stability["trades"], 1) if stability["symbol_counts"] else 1.0
    max_session_share = max(stability["session_counts"].values()) / max(stability["trades"], 1) if stability["session_counts"] else 1.0
    checks = {
        "positive_development_mean_net": development["mean_net_bps"] is not None and development["mean_net_bps"] > 0.0,
        "positive_stability_mean_net": stability["mean_net_bps"] is not None and stability["mean_net_bps"] >= float(gate["minimum_stability_mean_net_bps"]),
        "stability_net_t_stat": stability["net_t_stat"] is not None and stability["net_t_stat"] >= float(gate["minimum_stability_net_t_stat"]),
        "stability_positive_month_share": stability["positive_month_share"] >= float(gate["minimum_stability_positive_month_share"]),
        "stability_frequency": stability["trades_per_calendar_day"] >= float(gate["minimum_stability_trades_per_calendar_day"]),
        "positive_july_confirmation_mean_net": july["mean_net_bps"] is not None and july["mean_net_bps"] > 0.0,
        "july_confirmation_trade_count": july["trades"] >= int(gate["minimum_july_confirmation_trades"]),
        "positive_latest_pulse_mean_net": pulse["mean_net_bps"] is not None and pulse["mean_net_bps"] >= float(gate["minimum_latest_pulse_mean_net_bps"]),
        "latest_pulse_trade_count": pulse["trades"] >= int(gate["minimum_latest_pulse_trades"]),
        "symbol_concentration": max_symbol_share <= float(gate["maximum_single_symbol_share"]),
        "session_concentration": max_session_share <= float(gate["maximum_single_session_share"]),
    }
    advance = all(checks.values())
    classification = "V25_SESSION_RANGE_DELIVERY_ADVANCE_TO_NAUTILUS" if advance else "V25_SESSION_RANGE_DELIVERY_REJECTED_OR_UNDERPOWERED"
    decision = (
        "Freeze this exact session family and implement NautilusTrader bracket execution with current-NAV 3% sizing."
        if advance
        else "Do not tune the session family after these declared results; preserve only reusable geometry and move to another mechanism."
    )
    summary = {
        "schema": "candidate-15-v25-summary-v1",
        "classification": classification,
        "advance_to_nautilus": advance,
        "opening_events": len(openings.index),
        "executable_candidates": len(candidates.index),
        "selected_trades": len(selected.index),
        "logic_rejections": dict(rejections),
        "arbitration_skips": dict(arbitration),
        "development": development,
        "stability": stability,
        "july_confirmation": july,
        "latest_pulse": pulse,
        "maximum_stability_symbol_share": max_symbol_share,
        "maximum_stability_session_share": max_session_share,
        "advance_checks": checks,
        "decision": decision,
    }
    write_json(output / "summary.json", summary)

    lines = [
        "# Candidate 15 V25 — Session opening-range delivery diagnostic",
        "",
        f"**{classification}**",
        "",
        "The screen uses three fixed liquidity handoffs, one attempt per session, a 15-minute opening balance, cross-market state, a completed five-minute breakout and the first one-minute retest. Stop and target are structural levels from the same session auction.",
        "",
    ]
    for title, record in (
        ("Development", development),
        ("Year-long stability", stability),
        ("July 2026 confirmation", july),
        ("Latest August 1-7 pulse", pulse),
    ):
        lines.extend([
            f"## {title}",
            f"- interval: `{record['start']} -> {record['end_exclusive']}`",
            f"- trades / day: `{record['trades']} / {record['trades_per_calendar_day']}`",
            f"- gross / net mean: `{record['mean_gross_bps']} / {record['mean_net_bps']}` bp",
            f"- win rate / payoff: `{record['win_rate']} / {record['payoff_ratio']}`",
            f"- net t-stat: `{record['net_t_stat']}`",
            f"- mean net structural R: `{record['mean_net_structural_r']}`",
            f"- positive months: `{record['positive_months']} / {record['active_months']}`",
            f"- symbol counts: `{record['symbol_counts']}`",
            f"- session counts: `{record['session_counts']}`",
            f"- exit reasons: `{record['exit_reason_counts']}`",
            f"- same-bar conservative stops: `{record['same_bar_ambiguities']}`",
            "",
        ])
    lines.extend([
        "## Advance checks",
        *[f"- {key}: `{value}`" for key, value in checks.items()],
        "",
        "## Logic rejections",
        f"`{dict(rejections)}`",
        "",
        "## Decision",
        decision,
        "",
        "This is not synthetic account success. A passing result still requires frozen NautilusTrader orders, exact current-NAV 3% risk, one global slot and continuous-account validation.",
    ])
    (output / "RESULT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2, sort_keys=True, default=str))
    return summary


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--protocol", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    execute(args.protocol.resolve(), args.output.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
