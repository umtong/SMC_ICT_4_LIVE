#!/usr/bin/env python3
"""Diagnose causal session-handoff liquidity auctions for candidate-07.

The diagnostic separates pattern detection from tradable scenarios.
Each completed UTC session forms two public liquidity pools (its high and low).
During the following handoff window the *first* causal contact with each pool is
classified by completed aggressor flow and open-interest state:

- liquidation/trapped-inventory reversal: aggressive penetration is rejected
  back inside the completed session range, followed by opposite displacement;
- new-inventory acceptance: aggressive displacement closes outside the range
  with open-interest build, followed by one completed outside hold.

The script creates no orders, fills, cash ledger, PnL or hypothetical NAV.  It
only records scenario counts, structural geometry and conservative path outcomes
for deciding whether implementation as a NautilusTrader strategy is warranted.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date
import json
from pathlib import Path
from typing import Any, Iterable, Mapping

import pandas as pd

from data_positioning import load_positioning_bundle
from diagnose_failed_flow import aggregate_flow
from smc_ict_4.manifest import write_json_atomic

NS_PER_MILLISECOND = 1_000_000
NS_PER_FIVE_MINUTES = 5 * 60 * 1_000_000_000


@dataclass(frozen=True, slots=True)
class SessionDefinition:
    name: str
    range_start_minute: int
    range_end_minute: int
    auction_end_minute: int

    def __post_init__(self) -> None:
        if not 0 <= self.range_start_minute < self.range_end_minute < self.auction_end_minute <= 24 * 60:
            raise ValueError(f"invalid session definition: {self}")
        if any(value % 5 for value in (
            self.range_start_minute,
            self.range_end_minute,
            self.auction_end_minute,
        )):
            raise ValueError("session boundaries must align to five-minute bars")


SESSIONS = (
    # Three recurring high-activity phases documented in crypto intraday data.
    # The ranges are completed before the handoff starts; no future range data
    # is used when a pool is contacted.
    SessionDefinition("ASIA_TO_EUROPE", 0, 6 * 60, 10 * 60),
    SessionDefinition("EUROPE_TO_US", 6 * 60, 12 * 60, 17 * 60),
    SessionDefinition("US_TO_LATE", 12 * 60, 18 * 60, 23 * 60),
)


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected object in {path}")
    return value


def _minute_of_day(timestamp: pd.Timestamp) -> int:
    return int(timestamp.hour) * 60 + int(timestamp.minute)


def _finite_positive(value: Any) -> bool:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return False
    return number == number and number > 0.0


def _align_positioning(
    bars: pd.DataFrame,
    metrics: pd.DataFrame,
    *,
    oi_period: int,
    oi_impulse_rank: float,
) -> pd.DataFrame:
    """Join a completed 5m bar to the snapshot published at its boundary."""
    if oi_period <= 0:
        raise ValueError("oi_period must be positive")
    if not 0.0 <= oi_impulse_rank <= 1.0:
        raise ValueError("oi_impulse_rank must be in [0, 1]")

    work = bars.copy()
    work["snapshot_ns"] = work["timestamp_ns"].astype("int64") + NS_PER_MILLISECOND
    selected = metrics[
        [
            "timestamp_ns",
            "sum_open_interest",
            "sum_open_interest_value",
            "count_toptrader_long_short_ratio",
            "sum_toptrader_long_short_ratio",
            "count_long_short_ratio",
            "sum_taker_long_short_vol_ratio",
        ]
    ].copy()
    selected = selected.rename(columns={"timestamp_ns": "snapshot_ns"})
    joined = work.merge(selected, how="left", on="snapshot_ns", sort=False)

    valid = (
        joined["sum_open_interest"].map(_finite_positive)
        & joined["sum_open_interest_value"].map(_finite_positive)
    )
    joined["positioning_valid"] = valid
    joined["oi_change_fraction"] = pd.NA
    joined["oi_impulse_rank"] = pd.NA
    joined["inventory_state"] = "INVALID"

    prior_changes: list[float] = []
    previous_oi: float | None = None
    previous_snapshot_ns: int | None = None
    for index, row in joined.iterrows():
        if not bool(row["positioning_valid"]):
            previous_oi = None
            previous_snapshot_ns = None
            prior_changes.clear()
            continue
        current_oi = float(row["sum_open_interest"])
        snapshot_ns = int(row["snapshot_ns"])
        contiguous = (
            previous_oi is not None
            and previous_snapshot_ns is not None
            and snapshot_ns - previous_snapshot_ns == NS_PER_FIVE_MINUTES
        )
        if not contiguous:
            previous_oi = current_oi
            previous_snapshot_ns = snapshot_ns
            prior_changes.clear()
            joined.at[index, "inventory_state"] = "WARMUP"
            continue
        change = (current_oi - previous_oi) / previous_oi
        magnitudes = [abs(value) for value in prior_changes[-oi_period:]]
        rank = (
            sum(value <= abs(change) for value in magnitudes) / len(magnitudes)
            if magnitudes
            else 0.0
        )
        joined.at[index, "oi_change_fraction"] = change
        joined.at[index, "oi_impulse_rank"] = rank
        if len(magnitudes) < oi_period:
            state = "WARMUP"
        elif rank < oi_impulse_rank or change == 0.0:
            state = "NEUTRAL"
        else:
            state = "BUILD" if change > 0.0 else "RELEASE"
        joined.at[index, "inventory_state"] = state
        prior_changes.append(change)
        previous_oi = current_oi
        previous_snapshot_ns = snapshot_ns

    joined["timestamp"] = pd.to_datetime(joined["timestamp_ns"], unit="ns", utc=True)
    joined["utc_date"] = joined["timestamp"].dt.date
    joined["minute_of_day"] = joined["timestamp"].map(_minute_of_day)
    return joined


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
        "atr": float(row["atr"]),
        "open_interest": float(row["sum_open_interest"]),
        "open_interest_value": float(row["sum_open_interest_value"]),
        "oi_change_fraction": (
            None if pd.isna(row["oi_change_fraction"]) else float(row["oi_change_fraction"])
        ),
        "oi_impulse_rank": (
            None if pd.isna(row["oi_impulse_rank"]) else float(row["oi_impulse_rank"])
        ),
        "inventory_state": str(row["inventory_state"]),
    }


def _outcome(
    future: pd.DataFrame,
    *,
    direction: str,
    stop: float,
    target: float,
) -> dict[str, Any]:
    for row in future.itertuples(index=False):
        if direction == "LONG":
            stop_hit = float(row.low) <= stop
            target_hit = float(row.high) >= target
        else:
            stop_hit = float(row.high) >= stop
            target_hit = float(row.low) <= target
        if stop_hit and target_hit:
            return {"outcome": "AMBIGUOUS_SAME_BAR", "timestamp_ns": int(row.timestamp_ns)}
        if stop_hit:
            return {"outcome": "STOP", "timestamp_ns": int(row.timestamp_ns)}
        if target_hit:
            return {"outcome": "TARGET", "timestamp_ns": int(row.timestamp_ns)}
    return {"outcome": "TIMEOUT", "timestamp_ns": None}


def _path_excursions(
    future: pd.DataFrame,
    *,
    direction: str,
    entry: float,
    risk: float,
) -> dict[str, float | None]:
    if future.empty or risk <= 0.0:
        return {"mfe_r": None, "mae_r": None, "terminal_close_r": None}
    if direction == "LONG":
        favorable = (future["high"] - entry) / risk
        adverse = (entry - future["low"]) / risk
        terminal = (float(future.iloc[-1]["close"]) - entry) / risk
    else:
        favorable = (entry - future["low"]) / risk
        adverse = (future["high"] - entry) / risk
        terminal = (entry - float(future.iloc[-1]["close"])) / risk
    return {
        "mfe_r": float(favorable.max()),
        "mae_r": float(adverse.max()),
        "terminal_close_r": float(terminal),
    }


def _select_target(
    *,
    direction: str,
    entry: float,
    risk: float,
    candidates: Iterable[tuple[str, float]],
    minimum_rr: float,
    maximum_rr: float,
) -> tuple[str, float, float, float] | None:
    ordered: list[tuple[str, float, float]] = []
    seen: set[float] = set()
    for label, price in candidates:
        price = float(price)
        if price in seen:
            continue
        seen.add(price)
        favorable = price > entry if direction == "LONG" else price < entry
        if favorable:
            ordered.append((label, price, abs(price - entry) / risk))
    ordered.sort(key=lambda item: abs(item[1] - entry))
    selected = next((item for item in ordered if item[2] >= minimum_rr), None)
    if selected is None:
        return None
    label, structural_price, uncapped_rr = selected
    target_rr = min(float(uncapped_rr), maximum_rr)
    target = entry + risk * target_rr if direction == "LONG" else entry - risk * target_rr
    return label, structural_price, uncapped_rr, target


def _classify_contact(
    bars: pd.DataFrame,
    *,
    index: int,
    side: str,
    range_high: float,
    range_low: float,
    session: SessionDefinition,
    logic: Mapping[str, Any],
    session_end_index: int,
) -> dict[str, Any] | None:
    row = bars.loc[index]
    if index + 1 > session_end_index:
        return None
    confirm = bars.loc[index + 1]
    if not bool(row["positioning_valid"]) or not bool(confirm["positioning_valid"]):
        return {
            "branch": "DATA_GAP",
            "outcome": "POSITIONING_INVALID",
            "side": side,
            "contact": _bar_payload(row),
        }
    if str(row["inventory_state"]) in {"INVALID", "WARMUP"}:
        return None
    atr = float(row["atr"])
    if atr <= 0.0 or pd.isna(atr):
        return None
    boundary = range_high if side == "UPPER" else range_low
    bar_range = max(1e-12, float(row["high"]) - float(row["low"]))
    penetration = (
        (float(row["high"]) - boundary) / atr
        if side == "UPPER"
        else (boundary - float(row["low"])) / atr
    )
    if not (
        float(logic["contact_min_atr"]) <= penetration <= float(logic["contact_max_atr"])
    ):
        return None
    attack_flow = (
        float(row["imbalance"]) >= float(logic["attack_imbalance"])
        if side == "UPPER"
        else float(row["imbalance"]) <= -float(logic["attack_imbalance"])
    )
    if not (attack_flow and float(row["flow_z"]) >= float(logic["flow_z"])):
        return None

    inventory = str(row["inventory_state"])
    if side == "UPPER":
        wick = (float(row["high"]) - max(float(row["open"]), float(row["close"]))) / bar_range
        rejected = float(row["close"]) < boundary - float(logic["reclaim_buffer_atr"]) * atr
        accepted = float(row["close"]) > boundary + float(logic["acceptance_buffer_atr"]) * atr
        acceptance_location = (float(row["close"]) - float(row["low"])) / bar_range
        reversal_direction = "SHORT"
        continuation_direction = "LONG"
        extreme = float(row["high"])
    else:
        wick = (min(float(row["open"]), float(row["close"])) - float(row["low"])) / bar_range
        rejected = float(row["close"]) > boundary + float(logic["reclaim_buffer_atr"]) * atr
        accepted = float(row["close"]) < boundary - float(logic["acceptance_buffer_atr"]) * atr
        acceptance_location = (float(row["high"]) - float(row["close"])) / bar_range
        reversal_direction = "LONG"
        continuation_direction = "SHORT"
        extreme = float(row["low"])

    if rejected and wick >= float(logic["rejection_wick_fraction"]):
        if inventory not in {"RELEASE", "BUILD"}:
            return None
        direction = reversal_direction
        opposite_flow = (
            float(confirm["imbalance"]) <= -float(logic["confirm_imbalance"])
            if direction == "SHORT"
            else float(confirm["imbalance"]) >= float(logic["confirm_imbalance"])
        )
        opposite_body = (
            float(confirm["close"]) < float(confirm["open"])
            if direction == "SHORT"
            else float(confirm["close"]) > float(confirm["open"])
        )
        displaced = abs(float(confirm["close"]) - float(confirm["open"])) >= (
            float(logic["confirm_body_atr"]) * atr
        )
        remains_inside = (
            float(confirm["close"]) < boundary
            if direction == "SHORT"
            else float(confirm["close"]) > boundary
        )
        if not (opposite_flow and opposite_body and displaced and remains_inside):
            return {
                "branch": "LIQUIDATION_REVERSAL" if inventory == "RELEASE" else "TRAPPED_INVENTORY_REVERSAL",
                "outcome": "REVERSAL_NOT_CONFIRMED",
                "side": side,
                "contact": _bar_payload(row),
                "confirmation": _bar_payload(confirm),
            }
        entry = float(confirm["close"])
        stop = (
            extreme + float(logic["stop_buffer_atr"]) * atr
            if direction == "SHORT"
            else extreme - float(logic["stop_buffer_atr"]) * atr
        )
        risk = stop - entry if direction == "SHORT" else entry - stop
        if risk <= 0.0:
            return None
        midpoint = 0.5 * (range_high + range_low)
        selected = _select_target(
            direction=direction,
            entry=entry,
            risk=risk,
            candidates=(("SESSION_MIDPOINT", midpoint), ("OPPOSITE_SESSION_BOUNDARY", range_low if direction == "SHORT" else range_high)),
            minimum_rr=float(logic["minimum_rr"]),
            maximum_rr=float(logic["maximum_rr"]),
        )
        if selected is None:
            return {
                "branch": "LIQUIDATION_REVERSAL" if inventory == "RELEASE" else "TRAPPED_INVENTORY_REVERSAL",
                "outcome": "NO_STRUCTURAL_TARGET_ABOVE_MINIMUM_RR",
                "side": side,
                "contact": _bar_payload(row),
                "confirmation": _bar_payload(confirm),
                "entry": entry,
                "stop": stop,
                "risk": risk,
            }
        label, structural_target, uncapped_rr, target = selected
        future = bars.loc[index + 2 : session_end_index]
        result = {
            "branch": "LIQUIDATION_REVERSAL" if inventory == "RELEASE" else "TRAPPED_INVENTORY_REVERSAL",
            "outcome": "ENTRY_READY",
            "side": side,
            "direction": direction,
            "contact": _bar_payload(row),
            "confirmation": _bar_payload(confirm),
            "entry": entry,
            "stop": stop,
            "risk": risk,
            "risk_atr": risk / atr,
            "target": target,
            "target_label": label,
            "structural_target": structural_target,
            "uncapped_rr": uncapped_rr,
            "expected_rr": abs(target - entry) / risk,
            "path_outcome": _outcome(future, direction=direction, stop=stop, target=target),
            **_path_excursions(future, direction=direction, entry=entry, risk=risk),
        }
        return result

    if accepted and acceptance_location >= float(logic["acceptance_close_location"]):
        if inventory != "BUILD":
            return None
        direction = continuation_direction
        directional_body = (
            float(row["close"]) > float(row["open"])
            if direction == "LONG"
            else float(row["close"]) < float(row["open"])
        )
        body_fraction = abs(float(row["close"]) - float(row["open"])) / bar_range
        held = (
            float(confirm["low"]) >= boundary - float(logic["reclaim_buffer_atr"]) * atr
            and float(confirm["close"]) > boundary
            if direction == "LONG"
            else float(confirm["high"]) <= boundary + float(logic["reclaim_buffer_atr"]) * atr
            and float(confirm["close"]) < boundary
        )
        confirm_flow = (
            float(confirm["imbalance"]) >= float(logic["confirm_imbalance"])
            if direction == "LONG"
            else float(confirm["imbalance"]) <= -float(logic["confirm_imbalance"])
        )
        confirm_body = (
            float(confirm["close"]) > float(confirm["open"])
            if direction == "LONG"
            else float(confirm["close"]) < float(confirm["open"])
        )
        if not (
            directional_body
            and body_fraction >= float(logic["acceptance_body_fraction"])
            and held
            and confirm_flow
            and confirm_body
        ):
            return {
                "branch": "NEW_INVENTORY_ACCEPTANCE",
                "outcome": "ACCEPTANCE_NOT_HELD",
                "side": side,
                "contact": _bar_payload(row),
                "confirmation": _bar_payload(confirm),
            }
        entry = float(confirm["close"])
        stop = (
            boundary - float(logic["stop_buffer_atr"]) * atr
            if direction == "LONG"
            else boundary + float(logic["stop_buffer_atr"]) * atr
        )
        risk = entry - stop if direction == "LONG" else stop - entry
        if risk <= 0.0:
            return None
        width = range_high - range_low
        selected = _select_target(
            direction=direction,
            entry=entry,
            risk=risk,
            candidates=(
                ("SESSION_EXTENSION_0_50", range_high + 0.5 * width if direction == "LONG" else range_low - 0.5 * width),
                ("SESSION_EXTENSION_1_00", range_high + width if direction == "LONG" else range_low - width),
            ),
            minimum_rr=float(logic["minimum_rr"]),
            maximum_rr=float(logic["maximum_rr"]),
        )
        if selected is None:
            return {
                "branch": "NEW_INVENTORY_ACCEPTANCE",
                "outcome": "NO_STRUCTURAL_TARGET_ABOVE_MINIMUM_RR",
                "side": side,
                "contact": _bar_payload(row),
                "confirmation": _bar_payload(confirm),
                "entry": entry,
                "stop": stop,
                "risk": risk,
            }
        label, structural_target, uncapped_rr, target = selected
        future = bars.loc[index + 2 : session_end_index]
        return {
            "branch": "NEW_INVENTORY_ACCEPTANCE",
            "outcome": "ENTRY_READY",
            "side": side,
            "direction": direction,
            "contact": _bar_payload(row),
            "confirmation": _bar_payload(confirm),
            "entry": entry,
            "stop": stop,
            "risk": risk,
            "risk_atr": risk / atr,
            "target": target,
            "target_label": label,
            "structural_target": structural_target,
            "uncapped_rr": uncapped_rr,
            "expected_rr": abs(target - entry) / risk,
            "path_outcome": _outcome(future, direction=direction, stop=stop, target=target),
            **_path_excursions(future, direction=direction, entry=entry, risk=risk),
        }
    return None


def diagnose(
    bars: pd.DataFrame,
    *,
    trade_start: date,
    trade_end: date,
    logic: Mapping[str, Any],
) -> list[dict[str, Any]]:
    scenarios: list[dict[str, Any]] = []
    for utc_date, day in bars.groupby("utc_date", sort=True):
        if not trade_start <= utc_date < trade_end:
            continue
        for session in SESSIONS:
            reference = day[
                (day["minute_of_day"] >= session.range_start_minute)
                & (day["minute_of_day"] < session.range_end_minute)
            ]
            auction = day[
                (day["minute_of_day"] >= session.range_end_minute)
                & (day["minute_of_day"] < session.auction_end_minute)
            ]
            expected_reference = (session.range_end_minute - session.range_start_minute) // 5
            expected_auction = (session.auction_end_minute - session.range_end_minute) // 5
            if len(reference.index) != expected_reference or len(auction.index) != expected_auction:
                scenarios.append(
                    {
                        "scenario_id": f"c07sh-{utc_date}-{session.name}-data",
                        "session": session.name,
                        "utc_date": utc_date.isoformat(),
                        "branch": "DATA_GAP",
                        "outcome": "INCOMPLETE_SESSION_BARS",
                        "reference_bars": int(len(reference.index)),
                        "expected_reference_bars": expected_reference,
                        "auction_bars": int(len(auction.index)),
                        "expected_auction_bars": expected_auction,
                    }
                )
                continue
            range_high = float(reference["high"].max())
            range_low = float(reference["low"].min())
            if range_high <= range_low:
                continue
            consumed = {"UPPER": False, "LOWER": False}
            session_end_index = int(auction.index[-1])
            for index in auction.index:
                row = bars.loc[index]
                if pd.isna(row["atr"]) or float(row["atr"]) <= 0.0:
                    continue
                atr = float(row["atr"])
                contacts: list[str] = []
                if not consumed["UPPER"] and float(row["high"]) >= range_high + float(logic["contact_min_atr"]) * atr:
                    contacts.append("UPPER")
                if not consumed["LOWER"] and float(row["low"]) <= range_low - float(logic["contact_min_atr"]) * atr:
                    contacts.append("LOWER")
                if len(contacts) > 1:
                    consumed["UPPER"] = True
                    consumed["LOWER"] = True
                    scenarios.append(
                        {
                            "scenario_id": f"c07sh-{utc_date}-{session.name}-{int(row['timestamp_ns'])}-both",
                            "session": session.name,
                            "utc_date": utc_date.isoformat(),
                            "branch": "AMBIGUOUS_CONTACT",
                            "outcome": "BOTH_BOUNDARIES_CONTACTED_SAME_BAR",
                            "range_high": range_high,
                            "range_low": range_low,
                            "contact": _bar_payload(row),
                        }
                    )
                    continue
                if not contacts:
                    continue
                side = contacts[0]
                consumed[side] = True
                candidate = _classify_contact(
                    bars,
                    index=int(index),
                    side=side,
                    range_high=range_high,
                    range_low=range_low,
                    session=session,
                    logic=logic,
                    session_end_index=session_end_index,
                )
                if candidate is None:
                    continue
                candidate.update(
                    {
                        "scenario_id": f"c07sh-{utc_date}-{session.name}-{int(row['timestamp_ns'])}-{side.lower()}",
                        "session": session.name,
                        "utc_date": utc_date.isoformat(),
                        "range_start_minute": session.range_start_minute,
                        "range_end_minute": session.range_end_minute,
                        "auction_end_minute": session.auction_end_minute,
                        "range_high": range_high,
                        "range_low": range_low,
                        "range_width": range_high - range_low,
                    }
                )
                scenarios.append(candidate)
    return scenarios


def _summary(scenarios: list[dict[str, Any]]) -> dict[str, Any]:
    entry_ready = [item for item in scenarios if item.get("outcome") == "ENTRY_READY"]
    path_counts = Counter(
        str((item.get("path_outcome") or {}).get("outcome"))
        for item in entry_ready
    )
    by_session: dict[str, Counter[str]] = defaultdict(Counter)
    by_branch: dict[str, Counter[str]] = defaultdict(Counter)
    for item in scenarios:
        outcome = str(item.get("outcome"))
        by_session[str(item.get("session"))][outcome] += 1
        by_branch[str(item.get("branch"))][outcome] += 1
    target = path_counts["TARGET"]
    stop = path_counts["STOP"]
    ambiguous = path_counts["AMBIGUOUS_SAME_BAR"]
    mfe_values = [float(item["mfe_r"]) for item in entry_ready if item.get("mfe_r") is not None]
    mae_values = [float(item["mae_r"]) for item in entry_ready if item.get("mae_r") is not None]
    return {
        "scenarios": len(scenarios),
        "entry_ready": len(entry_ready),
        "path_outcome_counts": dict(path_counts),
        "unambiguous_targets": target,
        "unambiguous_stops": stop,
        "ambiguous_same_bar": ambiguous,
        "target_minus_stop": target - stop,
        "median_mfe_r": float(pd.Series(mfe_values).median()) if mfe_values else None,
        "median_mae_r": float(pd.Series(mae_values).median()) if mae_values else None,
        "by_session": {key: dict(value) for key, value in by_session.items()},
        "by_branch": {key: dict(value) for key, value in by_branch.items()},
    }


def run(args: argparse.Namespace) -> int:
    config = _read_json(args.config.resolve())
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    bundle = load_positioning_bundle(
        symbol=str(config["symbol"]),
        trade_start=args.start,
        trade_end=args.end,
        warmup_days=int(config["warmup_days"]),
        cache_root=args.data_root.resolve(),
        manifest_destination=output.with_name("session_handoff_data_manifest.json"),
    )
    flow_logic = dict(config["flow_logic"])
    bars = aggregate_flow(
        bundle.frame,
        int(flow_logic["signal_minutes"]),
        int(flow_logic["flow_period"]),
    )
    diagnostic_logic = {
        "contact_min_atr": 0.05,
        "contact_max_atr": 1.50,
        "attack_imbalance": 0.08,
        "flow_z": 0.25,
        "reclaim_buffer_atr": 0.02,
        "acceptance_buffer_atr": 0.05,
        "rejection_wick_fraction": 0.20,
        "acceptance_close_location": 0.65,
        "acceptance_body_fraction": 0.12,
        "confirm_imbalance": 0.02,
        "confirm_body_atr": 0.15,
        "stop_buffer_atr": 0.10,
        "minimum_rr": 1.25,
        "maximum_rr": 3.00,
        "oi_period": 36,
        "oi_impulse_rank": 0.50,
    }
    aligned = _align_positioning(
        bars,
        bundle.metrics,
        oi_period=int(diagnostic_logic["oi_period"]),
        oi_impulse_rank=float(diagnostic_logic["oi_impulse_rank"]),
    )
    scenarios = diagnose(
        aligned,
        trade_start=args.start,
        trade_end=args.end,
        logic=diagnostic_logic,
    )
    payload = {
        "candidate": "candidate-07",
        "stage": args.stage,
        "purpose": "session-handoff structural diagnostic only; no orders or hypothetical NAV",
        "period": {"start": args.start.isoformat(), "end_exclusive": args.end.isoformat()},
        "sessions": [
            {
                "name": item.name,
                "range_start_minute": item.range_start_minute,
                "range_end_minute": item.range_end_minute,
                "auction_end_minute": item.auction_end_minute,
            }
            for item in SESSIONS
        ],
        "logic": diagnostic_logic,
        "data_contract": {
            "price_and_flow": "checksum-verified Binance USD-M one-minute bars aggregated to completed five-minute bars",
            "positioning": "completed five-minute public USD-M metrics; invalid/nonpositive snapshots break OI state",
            "future_information": False,
            "orders_or_pnl": False,
        },
        "summary": _summary(scenarios),
        "scenarios": scenarios,
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
