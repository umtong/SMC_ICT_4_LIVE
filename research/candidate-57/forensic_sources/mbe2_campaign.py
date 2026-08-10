#!/usr/bin/env python3
"""Candidate 57 MBE2 strategy-anatomy campaign.

The campaign does not reduce a strategy to a pass/fail gate.  It executes the
source policy and predeclared component ablations on two distinct intervals,
then preserves continuous-account outcomes together with trade-level entry,
risk, management, state, cost and collision anatomy.  Promotion candidates are
a role-balanced Pareto set, not a single metric winner.
"""
from __future__ import annotations

from collections import defaultdict
import copy
import csv
from dataclasses import asdict, dataclass
from datetime import date, datetime, time, timedelta, timezone
import json
import math
import os
from pathlib import Path
import re
import shutil
import statistics
import subprocess
import sys
import traceback
from typing import Any, Callable, Iterable, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
REUSED = ROOT / "research" / "candidate-51"
BASE_CONFIG = REUSED / "config.json"
WORK = ROOT / ".work" / "candidate-57-mbe2-anatomy-v2"
ARTIFACTS = ROOT / "artifacts" / "candidate-57-mbe2-anatomy-v2"
EVIDENCE = HERE / "evidence" / "mbe2-anatomy-v2"
CACHE = ROOT / ".cache" / "candidate-57-mbe2-anatomy-v2"

_NUMBER = re.compile(r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?")
SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT")
WARMUP_DAYS = 2


@dataclass(frozen=True)
class Stage:
    key: str
    name: str
    start: date
    end: date

    @property
    def days(self) -> int:
        return (self.end - self.start).days + 1


@dataclass(frozen=True)
class Variant:
    name: str
    side: str
    leverage: float
    management: str
    roi_114: float
    component_role: str
    source_faithful: bool


STAGES = (
    Stage("development", "development-7d", date(2026, 7, 22), date(2026, 7, 28)),
    Stage("confirmation", "untouched-confirmation-7d", date(2025, 2, 10), date(2025, 2, 16)),
)

VARIANTS = (
    Variant(
        "both_avg646_source", "both", 6.46, "source", 0.11,
        "source control: both directions at reported average leverage", True,
    ),
    Variant(
        "long_avg646_source", "long", 6.46, "source", 0.11,
        "directional anatomy: long source leg", True,
    ),
    Variant(
        "short_avg646_source", "short", 6.46, "source", 0.11,
        "directional anatomy: short source leg", True,
    ),
    Variant(
        "both_cap10_source", "both", 10.0, "source", 0.11,
        "risk/cost geometry sensitivity at source leverage cap", True,
    ),
    Variant(
        "both_avg646_roi_only", "both", 6.46, "roi_only", 0.11,
        "management anatomy: ROI ladder without trailing", False,
    ),
    Variant(
        "both_avg646_trail_only", "both", 6.46, "trail_only", 0.11,
        "management anatomy: trailing without ROI ladder", False,
    ),
    Variant(
        "both_avg646_roi114_011", "both", 6.46, "source", 0.011,
        "structural repair: remove the 41m-to-114m ROI threshold jump", False,
    ),
)
VARIANT_BY_NAME = {item.name: item for item in VARIANTS}

LEGACY_KEYS = {
    "sma_offset_low",
    "sma_offset_high",
    "sma_stop_min_fraction",
    "sma_stop_max_fraction",
    "sma_stop_atr_buffer",
}
EXIT_EVENT_EXCLUSIONS = {
    "ENTRY_SUBMITTED",
    "POSITION_OPENED",
    "POSITION_CLOSED",
    "PUBLIC_MBE2_TRAILING_ACTIVATED",
}


def dump(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True, allow_nan=False, default=str) + "\n",
        encoding="utf-8",
    )


def load_json(path: Path) -> Any:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def finite_number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def parse_money(value: Any) -> float | None:
    if value is None:
        return None
    match = _NUMBER.search(str(value).replace(",", "").replace("_", ""))
    if match is None:
        return None
    return finite_number(match.group(0))


def parse_commissions(value: Any) -> float:
    text = str(value).replace(",", "").replace("_", "")
    values = [float(item) for item in _NUMBER.findall(text)]
    return sum(item for item in values if math.isfinite(item))


def evaluation_start_ns(day: date) -> int:
    moment = datetime.combine(day, time.min, tzinfo=timezone.utc)
    return int(moment.timestamp()) * 1_000_000_000


def evaluation_flatten_ns(day: date) -> int:
    next_midnight = datetime.combine(
        day + timedelta(days=1), time.min, tzinfo=timezone.utc
    )
    # The close intent is sent two complete minutes before the data endpoint.
    return int(next_midnight.timestamp()) * 1_000_000_000 - 120_000_000_001


def build_config(stage: Stage, variant: Variant) -> Path:
    source = json.loads(BASE_CONFIG.read_text(encoding="utf-8"))
    config = copy.deepcopy(source)
    strategy = config.setdefault("strategy", {})
    for key in LEGACY_KEYS:
        strategy.pop(key, None)
    leverage_label = "cap10" if abs(variant.leverage - 10.0) < 1e-12 else "avg646"
    strategy.update(
        {
            "cooldown_minutes": 0,
            "max_hold_minutes": 10080,
            "funding_flatten_minute": 60,
            "funding_blackout_before_minutes": -1,
            "funding_blackout_after_minutes": -1,
            "mbe_variant": f"{variant.side}_{leverage_label}",
            "mbe_management_mode": variant.management,
            "mbe_evaluation_start_ns": evaluation_start_ns(stage.start),
            "mbe_evaluation_end_ns": evaluation_flatten_ns(stage.end),
            "mbe_startup_5m_candles": 140,
            "mbe_tema_period": 9,
            "mbe_bb_period": 20,
            "mbe_rsi_period": 14,
            "mbe_source_leverage": variant.leverage,
            "mbe_source_stoploss": 0.22,
            "mbe_trailing_positive": 0.015,
            "mbe_trailing_offset": 0.025,
            "mbe_roi_0": 0.079,
            "mbe_roi_15": 0.047,
            "mbe_roi_41": 0.032,
            "mbe_roi_114": variant.roi_114,
            "mbe_roi_180": 0.007,
            "mbe_roi_420": 0.001,
            "mbe_emergency_target_fraction": 0.50,
        }
    )
    path = WORK / "configs" / stage.key / f"{variant.name}.json"
    dump(path, config)
    return path


def read_positions(path: Path) -> tuple[list[dict[str, Any]], list[str]]:
    errors: list[str] = []
    if not path.is_file():
        return [], ["POSITIONS_MISSING"]
    try:
        with path.open(newline="", encoding="utf-8") as stream:
            rows = list(csv.DictReader(stream))
    except (OSError, csv.Error) as exc:
        return [], [f"POSITIONS_PARSE_ERROR:{type(exc).__name__}"]
    output: list[dict[str, Any]] = []
    for row_number, row in enumerate(rows, start=2):
        side_state = str(row.get("side", "")).strip().upper()
        ts_closed_text = str(row.get("ts_closed", "")).strip().lower()
        if side_state != "FLAT" or ts_closed_text in {"", "nan", "none"}:
            continue
        pnl = parse_money(row.get("realized_pnl"))
        entry_price = finite_number(row.get("avg_px_open"))
        exit_price = finite_number(row.get("avg_px_close"))
        quantity = finite_number(row.get("peak_qty"))
        duration_ns = finite_number(row.get("duration_ns"))
        closed_ns = finite_number(row.get("ts_last"))
        if None in (pnl, entry_price, exit_price, quantity, duration_ns, closed_ns):
            errors.append(f"INVALID_CLOSED_POSITION_ROW_{row_number}")
            continue
        entry_side = str(row.get("entry", "")).strip().upper()
        side = 1 if entry_side == "BUY" else -1 if entry_side == "SELL" else 0
        if side == 0:
            errors.append(f"INVALID_ENTRY_SIDE_ROW_{row_number}")
            continue
        symbol = str(row.get("instrument_id", "")).split("-")[0].split(".")[0]
        gross_pnl = side * (float(exit_price) - float(entry_price)) * float(quantity)
        commissions = parse_commissions(row.get("commissions"))
        output.append(
            {
                "symbol": symbol,
                "side": side,
                "closed_ns": int(float(closed_ns)),
                "duration_minutes": float(duration_ns) / 60_000_000_000.0,
                "avg_px_open": float(entry_price),
                "avg_px_close": float(exit_price),
                "quantity": float(quantity),
                "entry_notional": abs(float(entry_price) * float(quantity)),
                "gross_pnl_usdt": gross_pnl,
                "commissions_usdt": commissions,
                "realized_pnl_usdt": float(pnl),
                "realized_return": finite_number(row.get("realized_return")),
            }
        )
    output.sort(key=lambda item: (item["closed_ns"], item["symbol"]))
    return output, sorted(set(errors))


def read_events(path: Path) -> tuple[dict[str, list[dict[str, Any]]], list[str]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    errors: list[str] = []
    if not path.is_file():
        return grouped, ["SCENARIO_EVENTS_MISSING"]
    try:
        with path.open(encoding="utf-8") as stream:
            for line_number, line in enumerate(stream, start=1):
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    errors.append(f"INVALID_EVENT_JSON_LINE_{line_number}")
                    continue
                scenario_id = row.get("scenario_id")
                if scenario_id:
                    grouped[str(scenario_id)].append(row)
    except OSError as exc:
        errors.append(f"EVENT_READ_ERROR:{type(exc).__name__}")
    return grouped, sorted(set(errors))


def infer_exit_reason(
    scenario: Mapping[str, Any],
    position: Mapping[str, Any],
    events: Sequence[Mapping[str, Any]],
) -> str:
    exit_events = [
        str(item.get("event_type", ""))
        for item in events
        if str(item.get("event_type", "")) not in EXIT_EVENT_EXCLUSIONS
    ]
    if exit_events:
        return exit_events[-1]
    side = int(scenario.get("side") or position.get("side") or 0)
    close = finite_number(position.get("avg_px_close"))
    stop = finite_number(scenario.get("stop"))
    target = finite_number(scenario.get("target"))
    if side in (-1, 1) and close is not None:
        distances: list[tuple[str, float]] = []
        if stop is not None and stop > 0.0:
            distances.append(("BRACKET_STOP_INFERRED", abs(close - stop) / stop))
        if target is not None and target > 0.0:
            distances.append(("BRACKET_TARGET_INFERRED", abs(close - target) / target))
        if distances:
            label, distance = min(distances, key=lambda item: item[1])
            if distance <= 0.003:
                return label
    return "ENGINE_OR_BRACKET_EXIT_UNRESOLVED"


def match_positions(
    scenarios: Sequence[Mapping[str, Any]], positions: Sequence[Mapping[str, Any]]
) -> tuple[list[tuple[Mapping[str, Any], Mapping[str, Any]]], list[str]]:
    unused = set(range(len(positions)))
    matches: list[tuple[Mapping[str, Any], Mapping[str, Any]]] = []
    errors: list[str] = []
    for scenario in sorted(
        scenarios,
        key=lambda item: int(item.get("ts_event") or item.get("closed_ts_event") or 0),
    ):
        symbol = str(scenario.get("symbol", ""))
        closed_ns = int(scenario.get("ts_event") or scenario.get("closed_ts_event") or 0)
        candidates = [
            index
            for index in unused
            if positions[index].get("symbol") == symbol
        ]
        if not candidates:
            errors.append(f"NO_POSITION_FOR_SCENARIO:{scenario.get('scenario_id')}")
            continue
        index = min(candidates, key=lambda item: abs(int(positions[item]["closed_ns"]) - closed_ns))
        delta = abs(int(positions[index]["closed_ns"]) - closed_ns)
        if delta > 180_000_000_000:
            errors.append(f"POSITION_TIME_MISMATCH:{scenario.get('scenario_id')}:{delta}")
            continue
        unused.remove(index)
        matches.append((scenario, positions[index]))
    for index in sorted(unused):
        errors.append(
            f"UNMATCHED_CLOSED_POSITION:{positions[index].get('symbol')}:{positions[index].get('closed_ns')}"
        )
    return matches, sorted(set(errors))


def safe_ratio(numerator: float, denominator: float) -> float | None:
    if not (math.isfinite(numerator) and math.isfinite(denominator)) or abs(denominator) <= 1e-15:
        return None
    return numerator / denominator


def timestamp_parts(ts_ns: int) -> tuple[str, int]:
    moment = datetime.fromtimestamp(ts_ns / 1_000_000_000, tz=timezone.utc)
    return moment.date().isoformat(), moment.hour


def build_trade_records(
    output: Path,
) -> tuple[list[dict[str, Any]], list[str]]:
    scenarios_value = load_json(output / "closed_scenarios.json")
    scenarios = scenarios_value if isinstance(scenarios_value, list) else []
    errors: list[str] = [] if isinstance(scenarios_value, list) else ["CLOSED_SCENARIOS_MISSING_OR_INVALID"]
    positions, position_errors = read_positions(output / "positions.csv")
    events_by_scenario, event_errors = read_events(output / "scenario_events.jsonl")
    errors.extend(position_errors)
    errors.extend(event_errors)
    matches, match_errors = match_positions(scenarios, positions)
    errors.extend(match_errors)
    records: list[dict[str, Any]] = []
    for scenario, position in matches:
        scenario_id = str(scenario.get("scenario_id", ""))
        diagnostics = scenario.get("diagnostics")
        diagnostics = diagnostics if isinstance(diagnostics, dict) else {}
        side = int(scenario.get("side") or position.get("side") or 0)
        risk_budget = finite_number(scenario.get("risk_budget")) or 0.0
        planned_loss = finite_number(scenario.get("planned_account_loss")) or 0.0
        pnl = float(position["realized_pnl_usdt"])
        gross_pnl = float(position["gross_pnl_usdt"])
        commission = float(position["commissions_usdt"])
        entry = float(position["avg_px_open"])
        close = float(position["avg_px_close"])
        episode_ts = int(scenario.get("episode_ts") or 0)
        episode_date, episode_hour = timestamp_parts(episode_ts or int(position["closed_ns"]))
        ema_gap = finite_number(diagnostics.get("ema_2h_to_8h_bps"))
        return_4h = finite_number(diagnostics.get("return_4h_bps"))
        record = {
            "scenario_id": scenario_id,
            "episode_key": f"{scenario.get('symbol')}:{scenario.get('state')}:{episode_ts}",
            "episode_ts": episode_ts,
            "episode_date": episode_date,
            "episode_hour_utc": episode_hour,
            "symbol": str(scenario.get("symbol") or position.get("symbol")),
            "side": side,
            "side_label": "LONG" if side > 0 else "SHORT",
            "score": finite_number(scenario.get("score")),
            "entry_reference": finite_number(scenario.get("entry_reference")),
            "stop": finite_number(scenario.get("stop")),
            "target": finite_number(scenario.get("target")),
            "avg_px_open": entry,
            "avg_px_close": close,
            "quantity": float(position["quantity"]),
            "entry_notional_usdt": float(position["entry_notional"]),
            "duration_minutes": float(position["duration_minutes"]),
            "realized_pnl_usdt": pnl,
            "gross_pnl_usdt": gross_pnl,
            "commissions_usdt": commission,
            "net_r": safe_ratio(pnl, risk_budget),
            "gross_r": safe_ratio(gross_pnl, risk_budget),
            "commission_r": safe_ratio(commission, risk_budget),
            "gross_move_bps": side * (close / entry - 1.0) * 10_000.0 if entry > 0.0 else None,
            "cost_bps_on_entry_notional": safe_ratio(commission * 10_000.0, float(position["entry_notional"])),
            "risk_budget_usdt": risk_budget,
            "planned_account_loss_usdt": planned_loss,
            "planned_loss_to_budget": safe_ratio(planned_loss, risk_budget),
            "stop_distance_bps": (
                abs((finite_number(scenario.get("entry_reference")) or entry) - (finite_number(scenario.get("stop")) or entry))
                / max(abs(finite_number(scenario.get("entry_reference")) or entry), 1e-12)
                * 10_000.0
            ),
            "target_distance_bps": (
                abs((finite_number(scenario.get("target")) or entry) - (finite_number(scenario.get("entry_reference")) or entry))
                / max(abs(finite_number(scenario.get("entry_reference")) or entry), 1e-12)
                * 10_000.0
            ),
            "exit_reason": infer_exit_reason(
                scenario, position, events_by_scenario.get(scenario_id, [])
            ),
            "management_mode": str(scenario.get("mbe_management_mode", "source")),
            "collision_competitors": int(scenario.get("mbe_collision_competitors") or 0),
            "winner_score_gap": finite_number(scenario.get("mbe_winner_score_gap")),
            "mfe_underlying_fraction": finite_number(scenario.get("mbe_mfe_underlying_fraction")),
            "mae_underlying_fraction": finite_number(scenario.get("mbe_mae_underlying_fraction")),
            "mfe_source_profit_ratio": finite_number(scenario.get("mbe_mfe_source_profit_ratio")),
            "mae_source_profit_ratio": finite_number(scenario.get("mbe_mae_source_profit_ratio")),
            "rsi": finite_number(diagnostics.get("rsi")),
            "previous_rsi": finite_number(diagnostics.get("previous_rsi")),
            "rsi_cross_magnitude": finite_number(diagnostics.get("rsi_cross_magnitude")),
            "tema_to_middle_bps": finite_number(diagnostics.get("tema_to_middle_bps")),
            "tema_slope_bps": finite_number(diagnostics.get("tema_slope_bps")),
            "bb_width_bps": finite_number(diagnostics.get("bb_width_bps")),
            "volume_ratio_20": finite_number(diagnostics.get("volume_ratio_20")),
            "return_1h_bps": finite_number(diagnostics.get("return_1h_bps")),
            "return_4h_bps": return_4h,
            "return_8h_bps": finite_number(diagnostics.get("return_8h_bps")),
            "ema_2h_to_8h_bps": ema_gap,
            "realized_vol_1h_bps": finite_number(diagnostics.get("realized_vol_1h_bps")),
            "range_1h_bps": finite_number(diagnostics.get("range_1h_bps")),
            "trend_alignment_bps": side * ema_gap if ema_gap is not None else None,
            "momentum_4h_alignment_bps": side * return_4h if return_4h is not None else None,
        }
        records.append(record)
    records.sort(key=lambda item: (item["episode_ts"], item["scenario_id"]))
    duplicate_count = len(records) - len({item["episode_key"] for item in records})
    if duplicate_count:
        errors.append(f"DUPLICATE_INDEPENDENT_EPISODES:{duplicate_count}")
    return records, sorted(set(errors))


def median(values: Iterable[float]) -> float | None:
    clean = [float(item) for item in values if item is not None and math.isfinite(float(item))]
    return statistics.median(clean) if clean else None


def mean(values: Iterable[float]) -> float | None:
    clean = [float(item) for item in values if item is not None and math.isfinite(float(item))]
    return sum(clean) / len(clean) if clean else None


def trade_stats(records: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    pnls = [float(item["realized_pnl_usdt"]) for item in records]
    gross = [float(item["gross_pnl_usdt"]) for item in records]
    costs = [float(item["commissions_usdt"]) for item in records]
    net_r = [float(item["net_r"]) for item in records if item.get("net_r") is not None]
    wins = [value for value in pnls if value > 0.0]
    losses = [-value for value in pnls if value < 0.0]
    gross_profit = sum(wins)
    gross_loss = sum(losses)
    unique_days = len({str(item["episode_date"]) for item in records})
    symbol_counts = defaultdict(int)
    for item in records:
        symbol_counts[str(item["symbol"])] += 1
    count = len(records)
    hhi = (
        sum((value / count) ** 2 for value in symbol_counts.values())
        if count else 0.0
    )
    risk_ratios = [
        float(item["planned_loss_to_budget"])
        for item in records
        if item.get("planned_loss_to_budget") is not None
    ]
    total_notional = sum(float(item["entry_notional_usdt"]) for item in records)
    cost_bps = safe_ratio(sum(costs) * 10_000.0, total_notional)
    positive_sorted = sorted(wins, reverse=True)
    negative_sorted = sorted(losses, reverse=True)
    return {
        "completed_positions": count,
        "wins": len(wins),
        "losses": len(losses),
        "flat": count - len(wins) - len(losses),
        "win_rate": len(wins) / count if count else 0.0,
        "net_pnl_usdt": sum(pnls),
        "gross_price_pnl_usdt": sum(gross),
        "commissions_usdt": sum(costs),
        "average_pnl_usdt": mean(pnls),
        "median_pnl_usdt": median(pnls),
        "expectancy_r": mean(net_r),
        "median_r": median(net_r),
        "gross_profit_usdt": gross_profit,
        "gross_loss_usdt": gross_loss,
        "profit_factor": (
            gross_profit / gross_loss
            if gross_loss > 0.0
            else (None if gross_profit > 0.0 else 0.0)
        ),
        "payoff_ratio": (
            (mean(wins) or 0.0) / (mean(losses) or math.inf)
            if wins and losses else None
        ),
        "average_hold_minutes": mean(float(item["duration_minutes"]) for item in records),
        "median_hold_minutes": median(float(item["duration_minutes"]) for item in records),
        "average_roundtrip_cost_bps": cost_bps,
        "cost_to_absolute_gross_pnl": safe_ratio(sum(costs), sum(abs(value) for value in gross)),
        "cost_to_net_pnl": safe_ratio(sum(costs), sum(pnls)),
        "largest_winner_share_of_gross_profit": (
            positive_sorted[0] / gross_profit if positive_sorted and gross_profit > 0.0 else None
        ),
        "largest_loss_share_of_gross_loss": (
            negative_sorted[0] / gross_loss if negative_sorted and gross_loss > 0.0 else None
        ),
        "top3_winner_share_of_gross_profit": (
            sum(positive_sorted[:3]) / gross_profit if gross_profit > 0.0 else None
        ),
        "independent_episode_days": unique_days,
        "completed_per_active_day": count / unique_days if unique_days else 0.0,
        "duplicate_episode_count": count - len({str(item["episode_key"]) for item in records}),
        "symbol_trade_counts": dict(sorted(symbol_counts.items())),
        "symbol_count_hhi": hhi,
        "planned_loss_to_budget_min": min(risk_ratios) if risk_ratios else None,
        "planned_loss_to_budget_max": max(risk_ratios) if risk_ratios else None,
        "average_mfe_underlying_fraction": mean(item.get("mfe_underlying_fraction") for item in records),
        "average_mae_underlying_fraction": mean(item.get("mae_underlying_fraction") for item in records),
        "average_mfe_source_profit_ratio": mean(item.get("mfe_source_profit_ratio") for item in records),
        "average_mae_source_profit_ratio": mean(item.get("mae_source_profit_ratio") for item in records),
        "average_stop_distance_bps": mean(float(item["stop_distance_bps"]) for item in records),
        "average_collision_competitors": mean(float(item["collision_competitors"]) for item in records),
    }


def compact_group_stats(records: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    stats = trade_stats(records)
    keys = (
        "completed_positions", "win_rate", "net_pnl_usdt", "expectancy_r",
        "profit_factor", "average_hold_minutes", "average_roundtrip_cost_bps",
        "average_mfe_source_profit_ratio", "average_mae_source_profit_ratio",
    )
    return {key: stats.get(key) for key in keys}


def hold_bucket(item: Mapping[str, Any]) -> str:
    value = float(item["duration_minutes"])
    if value < 15: return "00_lt_15m"
    if value < 41: return "01_15_40m"
    if value < 114: return "02_41_113m"
    if value < 180: return "03_114_179m"
    if value < 420: return "04_180_419m"
    return "05_ge_420m"


def session_bucket(item: Mapping[str, Any]) -> str:
    hour = int(item["episode_hour_utc"])
    if hour < 8: return "ASIA_00_08_UTC"
    if hour < 16: return "EUROPE_08_16_UTC"
    return "AMERICAS_16_24_UTC"


def score_bucket(item: Mapping[str, Any]) -> str:
    value = finite_number(item.get("score"))
    if value is None: return "MISSING"
    if value < 3: return "00_lt_3"
    if value < 5: return "01_3_5"
    if value < 7: return "02_5_7"
    return "03_ge_7"


def cross_bucket(item: Mapping[str, Any]) -> str:
    value = finite_number(item.get("rsi_cross_magnitude"))
    if value is None: return "MISSING"
    if value < 1: return "00_lt_1"
    if value < 2: return "01_1_2"
    if value < 4: return "02_2_4"
    return "03_ge_4"


def tema_gap_bucket(item: Mapping[str, Any]) -> str:
    value = finite_number(item.get("tema_to_middle_bps"))
    if value is None: return "MISSING"
    value = abs(value)
    if value < 5: return "00_lt_5bp"
    if value < 15: return "01_5_15bp"
    if value < 30: return "02_15_30bp"
    return "03_ge_30bp"


def aligned_bucket(item: Mapping[str, Any], field: str, neutral: float) -> str:
    value = finite_number(item.get(field))
    if value is None: return "MISSING"
    if value > neutral: return "ALIGNED"
    if value < -neutral: return "COUNTER"
    return "NEUTRAL"


def vol_bucket(item: Mapping[str, Any]) -> str:
    value = finite_number(item.get("realized_vol_1h_bps"))
    if value is None: return "MISSING"
    if value < 20: return "LOW_LT_20BP"
    if value < 50: return "MID_20_50BP"
    return "HIGH_GE_50BP"


def collision_bucket(item: Mapping[str, Any]) -> str:
    return "COLLISION_WINNER" if int(item.get("collision_competitors") or 0) > 0 else "SINGLE_SIGNAL"


def grouped_stats(
    records: Sequence[Mapping[str, Any]], key: Callable[[Mapping[str, Any]], str]
) -> dict[str, Any]:
    groups: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for item in records:
        groups[key(item)].append(item)
    return {name: compact_group_stats(rows) for name, rows in sorted(groups.items())}


def anatomy(records: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    return {
        "overall": trade_stats(records),
        "by_symbol": grouped_stats(records, lambda item: str(item["symbol"])),
        "by_side": grouped_stats(records, lambda item: str(item["side_label"])),
        "by_exit_reason": grouped_stats(records, lambda item: str(item["exit_reason"])),
        "by_hold_bucket": grouped_stats(records, hold_bucket),
        "by_utc_session": grouped_stats(records, session_bucket),
        "by_router_score_bucket": grouped_stats(records, score_bucket),
        "by_rsi_cross_bucket": grouped_stats(records, cross_bucket),
        "by_tema_middle_gap_bucket": grouped_stats(records, tema_gap_bucket),
        "by_trend_alignment": grouped_stats(
            records, lambda item: aligned_bucket(item, "trend_alignment_bps", 5.0)
        ),
        "by_4h_momentum_alignment": grouped_stats(
            records, lambda item: aligned_bucket(item, "momentum_4h_alignment_bps", 10.0)
        ),
        "by_realized_volatility": grouped_stats(records, vol_bucket),
        "by_signal_collision": grouped_stats(records, collision_bucket),
    }


def continuous_metrics(raw: Mapping[str, Any], stage: Stage) -> dict[str, Any]:
    starting = finite_number(raw.get("starting_nav")) or 0.0
    ending = finite_number(raw.get("ending_nav")) or 0.0
    geometric = (
        (ending / starting) ** (1.0 / stage.days) - 1.0
        if starting > 0.0 and ending > 0.0 else None
    )
    return {
        "evaluation_start": str(stage.start),
        "evaluation_end": str(stage.end),
        "calendar_days": stage.days,
        "warmup_days_not_scored": WARMUP_DAYS,
        "starting_nav": starting,
        "ending_nav": ending,
        "total_return": ending / starting - 1.0 if starting > 0.0 else None,
        "geometric_daily_growth": geometric,
        "max_drawdown": finite_number(raw.get("max_drawdown")),
        "min_equity": finite_number(raw.get("min_equity")),
        "runner_reported_trades": raw.get("trades"),
    }


def mechanics_reasons(
    returncode: int,
    raw: Mapping[str, Any] | None,
    diagnostics: Mapping[str, Any] | None,
    records: Sequence[Mapping[str, Any]],
    parse_errors: Sequence[str],
) -> list[str]:
    reasons = list(parse_errors)
    if returncode != 0:
        reasons.append(f"RUN_RETURN_CODE_{returncode}")
    if raw is None:
        reasons.append("METRICS_MISSING_OR_INVALID")
    if diagnostics is None:
        reasons.append("DIAGNOSTICS_MISSING_OR_INVALID")
    if raw is None or diagnostics is None:
        return sorted(set(reasons))
    if int(diagnostics.get("order_rejections") or 0) > 0:
        reasons.append("ORDER_REJECTION")
    if int(diagnostics.get("global_position_violations") or 0) > 0:
        reasons.append("GLOBAL_POSITION_VIOLATION")
    if int(diagnostics.get("max_open_positions_observed") or 0) > 1:
        reasons.append("MULTIPLE_OPEN_POSITIONS")
    if int(diagnostics.get("max_simultaneous_entry_intents") or 0) > 1:
        reasons.append("MULTIPLE_ENTRY_INTENTS")
    unresolved = diagnostics.get("unresolved_reason_counts")
    if isinstance(unresolved, dict) and int(unresolved.get("FUTURE_FEATURE_REJECTED") or 0) > 0:
        reasons.append("FUTURE_FEATURE_REJECTED")
    if int(diagnostics.get("real_binance_ohlc_execution") or 0) != 1:
        reasons.append("REAL_OHLC_CONTRACT_MISSING")
    if int(diagnostics.get("one_minute_trailing_detail") or 0) != 1:
        reasons.append("ONE_MINUTE_TRAILING_CONTRACT_MISSING")
    if int(diagnostics.get("same_minute_trail_activation_and_hit_allowed") or 0) != 0:
        reasons.append("SAME_MINUTE_TRAILING_HINDSIGHT")
    if int(diagnostics.get("project_independent_episode_mode") or 0) != 1:
        reasons.append("INDEPENDENT_EPISODE_CONTRACT_MISSING")
    ending = finite_number(raw.get("ending_nav"))
    minimum = finite_number(raw.get("min_equity"))
    if ending is None or ending <= 0.0:
        reasons.append("NON_POSITIVE_OR_NONFINITE_ENDING_NAV")
    if minimum is None or minimum <= 0.0:
        reasons.append("NON_POSITIVE_OR_NONFINITE_MIN_EQUITY")
    if len(records) != len({str(item["episode_key"]) for item in records}):
        reasons.append("DUPLICATE_INDEPENDENT_EPISODE")
    for item in records:
        ratio = finite_number(item.get("planned_loss_to_budget"))
        if ratio is None or ratio <= 0.0 or ratio > 1.000001:
            reasons.append("PLANNED_LOSS_BUDGET_CONTRACT")
            break
    return sorted(set(reasons))


def observations(
    continuous: Mapping[str, Any] | None,
    records: Sequence[Mapping[str, Any]],
    diagnostics: Mapping[str, Any] | None,
) -> list[str]:
    notes: list[str] = []
    stats = trade_stats(records)
    if diagnostics is not None:
        signals = int(diagnostics.get("source_signals_before_execution_filters") or 0)
        entries = int(diagnostics.get("entry_submissions") or 0)
        if signals == 0:
            notes.append("NO_SOURCE_SIGNAL_IN_INTERVAL")
        elif entries == 0:
            notes.append("SOURCE_SIGNALS_WITHOUT_ENTRY")
    if not records:
        notes.append("NO_COMPLETED_POSITION")
    else:
        if float(stats.get("net_pnl_usdt") or 0.0) <= 0.0:
            notes.append("NON_POSITIVE_COMPLETED_NET_PNL")
        if float(stats.get("expectancy_r") or 0.0) <= 0.0:
            notes.append("NON_POSITIVE_EXPECTANCY_R")
        cost_to_abs = finite_number(stats.get("cost_to_absolute_gross_pnl"))
        if cost_to_abs is not None and cost_to_abs >= 0.50:
            notes.append("COST_CONSUMES_AT_LEAST_HALF_ABSOLUTE_GROSS_MOVE_PNL")
        largest = finite_number(stats.get("largest_winner_share_of_gross_profit"))
        if largest is not None and largest >= 0.50:
            notes.append("GROSS_PROFIT_CONCENTRATED_IN_ONE_WINNER")
    if continuous is not None:
        growth = finite_number(continuous.get("geometric_daily_growth"))
        drawdown = finite_number(continuous.get("max_drawdown"))
        if growth is not None and growth <= 0.0:
            notes.append("NON_POSITIVE_CONTINUOUS_GROWTH")
        if drawdown is not None and drawdown > 0.20:
            notes.append("MAX_DRAWDOWN_GT_20PCT")
    return sorted(set(notes))


def selected_diagnostics(diagnostics: Mapping[str, Any] | None) -> dict[str, Any]:
    if diagnostics is None:
        return {}
    keys = (
        "external_source", "external_source_blob", "mbe_variant", "mbe_management_mode",
        "source_signals_before_execution_filters", "entry_submissions", "entry_expirations",
        "order_rejections", "global_position_violations", "max_simultaneous_entry_intents",
        "max_open_positions_observed", "used_episode_rejections", "cooldown_rejections",
        "funding_runway_rejections", "mbe_trailing_activations", "mbe_trailing_exits",
        "mbe_roi_exits", "mbe_collision_minutes", "mbe_competing_candidates",
        "mbe_collision_rejected_symbols", "mbe_collision_score_gap_count",
        "mbe_collision_score_gap_sum", "mbe_collision_score_gap_min",
        "mbe_collision_score_gap_max", "selected_symbols", "route_counts",
        "actionable_family_counts", "unresolved_reason_counts", "complete_5m_candles_only",
        "one_minute_trailing_detail", "same_minute_trail_activation_and_hit_allowed",
        "real_binance_ohlc_execution", "project_independent_episode_mode",
        "warmup_trade_block_start_ns", "forced_flat_cutoff_ns", "exchange_max_quantity_bounds",
    )
    return {key: diagnostics.get(key) for key in keys if key in diagnostics}


def run_case(stage: Stage, variant: Variant) -> dict[str, Any]:
    case_id = f"{stage.key}-{variant.name}"
    output = ARTIFACTS / case_id
    workspace = WORK / "workspace" / case_id
    log_path = WORK / "logs" / f"{case_id}.log"
    for path in (output, workspace):
        if path.exists():
            shutil.rmtree(path)
        path.mkdir(parents=True, exist_ok=True)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    CACHE.mkdir(parents=True, exist_ok=True)
    config_path = build_config(stage, variant)
    command = [
        sys.executable, str(REUSED / "launch.py"),
        "--config", str(config_path),
        "--start", str(stage.start - timedelta(days=WARMUP_DAYS)),
        "--end", str(stage.end),
        "--cache", str(CACHE),
        "--output", str(output),
        "--workspace", str(workspace),
    ]
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(REUSED)
    with log_path.open("w", encoding="utf-8") as log:
        log.write("COMMAND " + " ".join(command) + "\n")
        log.flush()
        completed = subprocess.run(
            command, cwd=ROOT, env=environment, stdout=log,
            stderr=subprocess.STDOUT, check=False,
        )
    raw_value = load_json(output / "metrics.json")
    diagnostics_value = load_json(output / "strategy_diagnostics.json")
    raw = raw_value if isinstance(raw_value, dict) else None
    diagnostics = diagnostics_value if isinstance(diagnostics_value, dict) else None
    records, parse_errors = build_trade_records(output)
    continuous = continuous_metrics(raw, stage) if raw is not None else None
    mechanical = mechanics_reasons(
        int(completed.returncode), raw, diagnostics, records, parse_errors
    )
    result = {
        "candidate": "candidate-57",
        "family": "public_mbe2",
        "case_id": case_id,
        "stage": asdict(stage),
        "variant": asdict(variant),
        "returncode": int(completed.returncode),
        "mechanics_ok": not mechanical,
        "mechanical_reasons": mechanical,
        "descriptive_observations": observations(continuous, records, diagnostics),
        "continuous_account": continuous,
        "trade_anatomy": anatomy(records),
        "trade_records": records,
        "diagnostics": selected_diagnostics(diagnostics),
    }
    if completed.returncode != 0 and log_path.is_file():
        result["failure_log_tail"] = log_path.read_text(
            encoding="utf-8", errors="replace"
        ).splitlines()[-200:]
    for name in ("run.json", "data_manifest.json"):
        value = load_json(output / name)
        if isinstance(value, dict):
            result[name.removesuffix(".json")] = value
    dump(EVIDENCE / "cases" / f"{case_id}.json", result)
    summary = {
        "case_id": case_id,
        "mechanics_ok": result["mechanics_ok"],
        "continuous_account": continuous,
        "overall": result["trade_anatomy"]["overall"],
        "observations": result["descriptive_observations"],
    }
    print(json.dumps(summary, indent=2, sort_keys=True), flush=True)
    return result


def pf_value(value: Any) -> float:
    number = finite_number(value)
    if number is None:
        return 1_000_000.0 if value is None else -1_000_000.0
    return number


def cross_period(rows: Mapping[str, Mapping[str, Mapping[str, Any]]]) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for variant in VARIANTS:
        cases = [rows[stage.key][variant.name] for stage in STAGES]
        records = [item for case in cases for item in case.get("trade_records", [])]
        combined = trade_stats(records)
        growths = [
            finite_number(case.get("continuous_account", {}).get("geometric_daily_growth"))
            for case in cases
        ]
        drawdowns = [
            finite_number(case.get("continuous_account", {}).get("max_drawdown"))
            for case in cases
        ]
        output[variant.name] = {
            "variant": asdict(variant),
            "mechanics_ok_both_periods": all(bool(case.get("mechanics_ok")) for case in cases),
            "period_mechanics": {case["stage"]["key"]: bool(case.get("mechanics_ok")) for case in cases},
            "period_continuous": {case["stage"]["key"]: case.get("continuous_account") for case in cases},
            "period_trade_stats": {
                case["stage"]["key"]: case.get("trade_anatomy", {}).get("overall")
                for case in cases
            },
            "combined_trade_stats_diagnostic_only": combined,
            "robust_growth_floor": min(value for value in growths if value is not None) if any(value is not None for value in growths) else None,
            "average_period_growth": mean(value for value in growths if value is not None),
            "worst_period_drawdown": max(value for value in drawdowns if value is not None) if any(value is not None for value in drawdowns) else None,
            "growth_sign_consistent": (
                None if any(value is None for value in growths)
                else all(value > 0.0 for value in growths) or all(value <= 0.0 for value in growths)
            ),
        }
    return output


def dominates(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
    left_stats = left["combined_trade_stats_diagnostic_only"]
    right_stats = right["combined_trade_stats_diagnostic_only"]
    left_vector = (
        finite_number(left.get("robust_growth_floor")) or -1e9,
        finite_number(left_stats.get("expectancy_r")) or -1e9,
        pf_value(left_stats.get("profit_factor")),
        float(left_stats.get("completed_positions") or 0),
        -(finite_number(left.get("worst_period_drawdown")) or 1e9),
        -(finite_number(left_stats.get("average_roundtrip_cost_bps")) or 1e9),
    )
    right_vector = (
        finite_number(right.get("robust_growth_floor")) or -1e9,
        finite_number(right_stats.get("expectancy_r")) or -1e9,
        pf_value(right_stats.get("profit_factor")),
        float(right_stats.get("completed_positions") or 0),
        -(finite_number(right.get("worst_period_drawdown")) or 1e9),
        -(finite_number(right_stats.get("average_roundtrip_cost_bps")) or 1e9),
    )
    return all(a >= b - 1e-15 for a, b in zip(left_vector, right_vector)) and any(
        a > b + 1e-15 for a, b in zip(left_vector, right_vector)
    )


def pareto_frontier(summary: Mapping[str, Mapping[str, Any]]) -> list[str]:
    eligible = [
        name for name, row in summary.items()
        if bool(row.get("mechanics_ok_both_periods"))
    ]
    return sorted(
        name for name in eligible
        if not any(
            other != name and dominates(summary[other], summary[name])
            for other in eligible
        )
    )


def metric_delta(current: Any, baseline: Any) -> float | None:
    current_number = finite_number(current)
    baseline_number = finite_number(baseline)
    if current_number is None or baseline_number is None:
        return None
    return current_number - baseline_number


def component_deltas(
    rows: Mapping[str, Mapping[str, Mapping[str, Any]]]
) -> dict[str, Any]:
    baseline_name = "both_avg646_source"
    output: dict[str, Any] = {}
    for variant in VARIANTS:
        if variant.name == baseline_name:
            continue
        stage_rows: dict[str, Any] = {}
        for stage in STAGES:
            base = rows[stage.key][baseline_name]
            current = rows[stage.key][variant.name]
            base_trade = base["trade_anatomy"]["overall"]
            current_trade = current["trade_anatomy"]["overall"]
            stage_rows[stage.key] = {
                "delta_geometric_daily_growth": metric_delta(
                    current.get("continuous_account", {}).get("geometric_daily_growth"),
                    base.get("continuous_account", {}).get("geometric_daily_growth"),
                ),
                "delta_completed_positions": int(current_trade.get("completed_positions") or 0) - int(base_trade.get("completed_positions") or 0),
                "delta_expectancy_r": metric_delta(current_trade.get("expectancy_r"), base_trade.get("expectancy_r")),
                "delta_profit_factor": metric_delta(current_trade.get("profit_factor"), base_trade.get("profit_factor")),
                "delta_win_rate": metric_delta(current_trade.get("win_rate"), base_trade.get("win_rate")),
                "delta_average_hold_minutes": metric_delta(current_trade.get("average_hold_minutes"), base_trade.get("average_hold_minutes")),
                "delta_roundtrip_cost_bps": metric_delta(current_trade.get("average_roundtrip_cost_bps"), base_trade.get("average_roundtrip_cost_bps")),
                "delta_max_drawdown": metric_delta(
                    current.get("continuous_account", {}).get("max_drawdown"),
                    base.get("continuous_account", {}).get("max_drawdown"),
                ),
            }
        output[variant.name] = {
            "component_role": variant.component_role,
            "relative_to": baseline_name,
            "by_period": stage_rows,
        }
    return output


def argmax_name(
    names: Sequence[str], summary: Mapping[str, Mapping[str, Any]], key: Callable[[Mapping[str, Any]], float]
) -> str | None:
    eligible = [name for name in names if summary[name].get("mechanics_ok_both_periods")]
    return max(eligible, key=lambda name: (key(summary[name]), name)) if eligible else None


def roles(summary: Mapping[str, Mapping[str, Any]], frontier: Sequence[str]) -> dict[str, Any]:
    names = list(summary)
    quality = argmax_name(
        names, summary,
        lambda row: finite_number(row["combined_trade_stats_diagnostic_only"].get("expectancy_r")) or -1e9,
    )
    growth = argmax_name(
        names, summary,
        lambda row: finite_number(row.get("robust_growth_floor")) or -1e9,
    )
    frequency = argmax_name(
        names, summary,
        lambda row: float(row["combined_trade_stats_diagnostic_only"].get("completed_positions") or 0),
    )
    sorted_by_frequency = sorted(
        [name for name in names if summary[name].get("mechanics_ok_both_periods")],
        key=lambda name: int(summary[name]["combined_trade_stats_diagnostic_only"].get("completed_positions") or 0),
    )
    low_half = sorted_by_frequency[: max(1, (len(sorted_by_frequency) + 1) // 2)]
    low_frequency_quality = argmax_name(
        low_half, summary,
        lambda row: finite_number(row["combined_trade_stats_diagnostic_only"].get("expectancy_r")) or -1e9,
    )
    cost_efficiency = argmax_name(
        names, summary,
        lambda row: (
            safe_ratio(
                float(row["combined_trade_stats_diagnostic_only"].get("net_pnl_usdt") or 0.0),
                float(row["combined_trade_stats_diagnostic_only"].get("commissions_usdt") or 0.0),
            ) or -1e9
        ),
    )
    role_names = []
    for name in (quality, growth, low_frequency_quality, cost_efficiency):
        if name and name not in role_names:
            role_names.append(name)
    for name in frontier:
        if name not in role_names:
            role_names.append(name)
    return {
        "quality_anchor": quality,
        "growth_robustness_anchor": growth,
        "frequency_reference_not_automatic_endorsement": frequency,
        "low_frequency_quality_anchor": low_frequency_quality,
        "cost_efficiency_anchor": cost_efficiency,
        "pareto_frontier": list(frontier),
        "role_balanced_intermediate_shortlist_max_4": role_names[:4],
        "selection_note": (
            "The shortlist intentionally preserves distinct roles.  Frequency alone cannot promote a weak policy, "
            "and low frequency cannot eliminate a high-quality component."
        ),
    }


def markdown(synthesis: Mapping[str, Any]) -> str:
    summary = synthesis["cross_period_summary"]
    lines = [
        "# Candidate 57 — public MBE2 strategy anatomy",
        "",
        "This campaign is an evidence map, not a binary gate. Every source leg and predeclared management/risk-geometry ablation was replayed on both seven-day intervals under the same continuous four-symbol, one-position NautilusTrader account.",
        "",
        "| variant | trades | win rate | expectancy R | PF | robust daily growth | worst DD | avg cost bps |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for variant in VARIANTS:
        row = summary[variant.name]
        stats = row["combined_trade_stats_diagnostic_only"]
        def fmt(value: Any, pct: bool = False) -> str:
            number = finite_number(value)
            if number is None: return "n/a"
            return f"{number * 100:.3f}%" if pct else f"{number:.4f}"
        lines.append(
            "| " + " | ".join(
                [
                    variant.name,
                    str(stats.get("completed_positions", 0)),
                    fmt(stats.get("win_rate"), True),
                    fmt(stats.get("expectancy_r")),
                    fmt(stats.get("profit_factor")),
                    fmt(row.get("robust_growth_floor"), True),
                    fmt(row.get("worst_period_drawdown"), True),
                    fmt(stats.get("average_roundtrip_cost_bps")),
                ]
            ) + " |"
        )
    role_data = synthesis["roles"]
    lines.extend(
        [
            "",
            "## Role-preserving interpretation",
            "",
            f"- Pareto frontier: `{', '.join(role_data['pareto_frontier']) or 'none'}`",
            f"- Quality anchor: `{role_data['quality_anchor']}`",
            f"- Low-frequency quality anchor: `{role_data['low_frequency_quality_anchor']}`",
            f"- Growth/robustness anchor: `{role_data['growth_robustness_anchor']}`",
            f"- Frequency reference (not automatic endorsement): `{role_data['frequency_reference_not_automatic_endorsement']}`",
            f"- Role-balanced next-stage shortlist: `{', '.join(role_data['role_balanced_intermediate_shortlist_max_4']) or 'none'}`",
            "",
            "Detailed symbol, direction, exit, hold-time, session, router-score, RSI-cross, TEMA-gap, trend, momentum, volatility, collision, MFE/MAE, cost and risk-budget slices are stored in each case JSON. Component deltas are measured against the source-faithful both-side 6.46x control.",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    for path in (WORK, ARTIFACTS, EVIDENCE, CACHE):
        path.mkdir(parents=True, exist_ok=True)
    manifest = {
        "candidate": "candidate-57",
        "family": "public_mbe2",
        "research_policy": {
            "binary_strategy_gate": False,
            "all_predeclared_components_run_on_both_intervals": True,
            "low_frequency_quality_preserved": True,
            "high_frequency_weak_return_not_privileged": True,
            "promotion_basis": "role-balanced Pareto evidence plus strategy anatomy",
        },
        "source": {
            "repository": "remiotore/ccxt-freqtrade",
            "path": "strategies/myshortingstrategiembe2.py",
            "blob_sha": "d312e07abc99ffd5631a992fc67a4e97a8768c0a",
            "timeframe": "5m",
            "startup_candle_count": 140,
        },
        "project_contract": {
            "engine": "NautilusTrader BacktestNode",
            "universe": list(SYMBOLS),
            "global_pending_or_position_limit": 1,
            "risk_fraction": 0.03,
            "warmup_days_not_scored": WARMUP_DAYS,
            "real_1m_ohlc_execution": True,
            "same_minute_trailing_hindsight": False,
            "independent_rsi_cross_episodes": True,
        },
        "stages": [asdict(stage) | {"days": stage.days} for stage in STAGES],
        "variants": [asdict(variant) for variant in VARIANTS],
    }
    dump(EVIDENCE / "manifest.json", manifest)

    rows: dict[str, dict[str, dict[str, Any]]] = {}
    for stage in STAGES:
        rows[stage.key] = {}
        for variant in VARIANTS:
            rows[stage.key][variant.name] = run_case(stage, variant)

    summary = cross_period(rows)
    frontier = pareto_frontier(summary)
    synthesis = {
        "candidate": "candidate-57",
        "family": "public_mbe2",
        "status": "ANATOMY_COMPLETE" if all(
            case.get("mechanics_ok")
            for stage_rows in rows.values()
            for case in stage_rows.values()
        ) else "ANATOMY_COMPLETE_WITH_MECHANICAL_FAILURES",
        "cross_period_summary": summary,
        "component_deltas_vs_source_control": component_deltas(rows),
        "roles": roles(summary, frontier),
        "mechanical_failures": {
            stage_key: {
                name: row.get("mechanical_reasons")
                for name, row in stage_rows.items()
                if not row.get("mechanics_ok")
            }
            for stage_key, stage_rows in rows.items()
        },
        "interpretation_contract": (
            "No variant is killed or promoted solely by trade count, win rate, take-profit count, or one threshold. "
            "The next stage must preserve high-quality low-frequency components and only use frequency contributors "
            "whose after-cost contribution is complementary under the shared one-position account."
        ),
    }
    dump(EVIDENCE / "synthesis.json", synthesis)
    (EVIDENCE / "RESULT.md").write_text(markdown(synthesis), encoding="utf-8")
    print(json.dumps({
        "status": synthesis["status"],
        "roles": synthesis["roles"],
        "mechanical_failures": synthesis["mechanical_failures"],
    }, indent=2, sort_keys=True), flush=True)
    return 0 if synthesis["status"] == "ANATOMY_COMPLETE" else 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception as exc:
        EVIDENCE.mkdir(parents=True, exist_ok=True)
        failure = {
            "candidate": "candidate-57",
            "family": "public_mbe2",
            "status": "CAMPAIGN_EXCEPTION",
            "exception_type": type(exc).__name__,
            "exception": str(exc),
            "traceback": traceback.format_exc(),
        }
        dump(EVIDENCE / "campaign_failure.json", failure)
        print(json.dumps(failure, indent=2, sort_keys=True), file=sys.stderr)
        raise
