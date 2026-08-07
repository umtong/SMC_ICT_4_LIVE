#!/usr/bin/env python3
"""Diagnose causal positioning states around Candidate 11 trade plans.

This module is research-only. It does not submit orders, size positions, infer
NautilusTrader fills, or calculate account NAV. Future bars are used only to
label old, already-opened diagnostic weeks as TARGET/STOP/NO_FILL so that a
positioning hypothesis can be evaluated before it is promoted into the trading
system. All candidate features are formed from observations available no later
than plan confirmation.
"""
from __future__ import annotations

import argparse
import bisect
import csv
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from hashlib import sha256
from io import BytesIO, TextIOWrapper
import json
from math import isfinite, log, sqrt
from pathlib import Path
import sys
import time
from typing import Any, Iterable
from urllib.request import Request, urlopen
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from market_leadership import MarketLeadershipGate

UTC = timezone.utc
MINUTE_NS = 60_000_000_000
SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT")
BASE = "https://data.binance.vision/data/futures/um/daily"


@dataclass(frozen=True, slots=True)
class Bar:
    ts_ns: int
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass(frozen=True, slots=True)
class Metric:
    ts_ns: int
    open_interest: float
    open_interest_value: float
    top_count_ratio: float | None
    top_sum_ratio: float | None
    account_ratio: float | None
    taker_ratio: float | None


@dataclass(frozen=True, slots=True)
class Premium:
    ts_ns: int
    close: float


@dataclass(slots=True)
class Plan:
    week: str
    scenario_id: str
    symbol: str
    scenario: str
    direction: str
    sweep_ts_ns: int
    confirmation_ts_ns: int
    expire_ts_ns: int
    entry: float
    stop: float
    target: float
    net_r: float
    evaluation_end_ns: int
    gate_approved: bool = False
    gate_reason: str = "NOT_EVALUATED"
    outcome: str = "NOT_EVALUATED"
    fill_ts_ns: int | None = None
    exit_ts_ns: int | None = None
    features: dict[str, float | bool | str | None] | None = None


def _float(value: Any) -> float:
    result = float(value)
    if not isfinite(result):
        raise ValueError(f"non-finite numeric value: {value!r}")
    return result


def _optional_float(value: Any) -> float | None:
    text = str(value).strip()
    if not text:
        return None
    return _float(text)


def _read_csv_member(payload: bytes) -> tuple[str, list[list[str]]]:
    with ZipFile(BytesIO(payload)) as archive:
        members = archive.namelist()
        if len(members) != 1:
            raise RuntimeError(f"archive must contain exactly one member: {members}")
        with archive.open(members[0]) as raw:
            rows = list(csv.reader(TextIOWrapper(raw, encoding="utf-8-sig", newline="")))
        return members[0], rows


def _download(url: str, cache_path: Path, retries: int = 4) -> bytes:
    if cache_path.is_file() and cache_path.stat().st_size > 100:
        payload = cache_path.read_bytes()
        with ZipFile(BytesIO(payload)) as archive:
            if archive.testzip() is None:
                return payload
        cache_path.unlink(missing_ok=True)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    request = Request(url, headers={"User-Agent": "SMC-ICT-4-candidate-11-positioning"})
    last_error: Exception | None = None
    for attempt in range(retries):
        try:
            with urlopen(request, timeout=60) as response:  # noqa: S310 fixed HTTPS host
                payload = response.read()
            if len(payload) < 100:
                raise RuntimeError(f"unexpectedly small archive: {url}")
            with ZipFile(BytesIO(payload)) as archive:
                bad = archive.testzip()
                if bad is not None:
                    raise RuntimeError(f"corrupt member {bad}: {url}")
            temporary = cache_path.with_suffix(cache_path.suffix + ".tmp")
            temporary.write_bytes(payload)
            temporary.replace(cache_path)
            return payload
        except Exception as exc:
            last_error = exc
            if attempt + 1 < retries:
                time.sleep(2**attempt)
    raise RuntimeError(f"failed to download {url}: {last_error}")


def _load_price_bars(week_dir: Path, symbol: str) -> list[Bar]:
    rows: list[Bar] = []
    for path in sorted((week_dir / "data" / symbol).glob(f"{symbol}-1m-*.zip")):
        _, raw_rows = _read_csv_member(path.read_bytes())
        if raw_rows and raw_rows[0] and raw_rows[0][0] == "open_time":
            raw_rows = raw_rows[1:]
        for raw in raw_rows:
            if len(raw) < 6:
                continue
            try:
                open_time = int(raw[0])
            except ValueError:
                continue
            # Binance archive OHLC becomes visible only after the minute closes.
            ts_ns = (open_time + 60_000) * 1_000_000
            rows.append(Bar(
                ts_ns=ts_ns,
                open=_float(raw[1]),
                high=_float(raw[2]),
                low=_float(raw[3]),
                close=_float(raw[4]),
                volume=_float(raw[5]),
            ))
    unique = {bar.ts_ns: bar for bar in rows}
    result = [unique[key] for key in sorted(unique)]
    if not result:
        raise RuntimeError(f"no price bars: {week_dir.name} {symbol}")
    return result


def _load_events(path: Path) -> dict[str, list[dict[str, Any]]]:
    chains: dict[str, list[dict[str, Any]]] = defaultdict(list)
    with path.open("r", encoding="utf-8") as stream:
        for line in stream:
            if not line.strip():
                continue
            event = json.loads(line)
            scenario_id = event.get("scenario_id")
            if isinstance(scenario_id, str):
                chains[scenario_id].append(event)
    for chain in chains.values():
        chain.sort(key=lambda event: (
            int(event.get("observed_time_ns", -1)),
            int(event.get("event_time_ns", -1)),
            str(event.get("event_type", "")),
        ))
    return chains


def _extract_plans(
    root: Path,
    config: dict[str, Any],
) -> tuple[list[Plan], dict[str, dict[str, list[Bar]]]]:
    plans: list[Plan] = []
    bars_by_week: dict[str, dict[str, list[Bar]]] = {}
    for week_number in range(1, 10):
        week = f"W{week_number}"
        week_dir = root / "results" / f"LEADERSHIP_{week}"
        event_path = week_dir / "scenario_events.jsonl"
        if not event_path.is_file():
            continue
        interval = config["selection"]["weeks"].get(week)
        if interval is None:
            continue
        start_ns = int(datetime.fromisoformat(interval["start"]).replace(tzinfo=UTC).timestamp() * 1e9)
        end_ns = int(datetime.fromisoformat(interval["end_exclusive"]).replace(tzinfo=UTC).timestamp() * 1e9)
        week_bars = {symbol: _load_price_bars(week_dir, symbol) for symbol in SYMBOLS}
        bars_by_week[week] = week_bars
        chains = _load_events(event_path)
        for scenario_id, chain in chains.items():
            plan_events = [event for event in chain if event.get("event_type") == "TRADE_PLAN_CONFIRMED"]
            if not plan_events:
                continue
            sweep_events = [event for event in chain if event.get("event_type") == "LIQUIDITY_SWEEP"]
            if not sweep_events:
                continue
            sweep_ts_ns = min(int(event["event_time_ns"]) for event in sweep_events)
            for event in plan_events:
                confirmation_ts_ns = int(event["observed_time_ns"])
                if confirmation_ts_ns < start_ns or confirmation_ts_ns >= end_ns:
                    continue
                details = event.get("details") or {}
                instrument_id = str(event.get("instrument_id", ""))
                symbol = instrument_id.split("-PERP", 1)[0]
                if symbol not in SYMBOLS:
                    continue
                plans.append(Plan(
                    week=week,
                    scenario_id=scenario_id,
                    symbol=symbol,
                    scenario=str(details["scenario"]),
                    direction=str(details["direction"]),
                    sweep_ts_ns=sweep_ts_ns,
                    confirmation_ts_ns=confirmation_ts_ns,
                    expire_ts_ns=int(details["expire_ts_ns"]),
                    entry=_float(event["reference_price"]),
                    stop=_float(details["stop"]),
                    target=_float(details["target"]),
                    net_r=_float(details["net_r"]),
                    evaluation_end_ns=end_ns,
                ))
    plans.sort(key=lambda plan: (plan.week, plan.confirmation_ts_ns, plan.scenario_id))
    return plans, bars_by_week


def _label_outcome(plan: Plan, bars: list[Bar]) -> None:
    timestamps = [bar.ts_ns for bar in bars]
    # The plan is created after the confirmation bar closes; that same bar
    # cannot fill the newly submitted order.
    start = bisect.bisect_right(timestamps, plan.confirmation_ts_ns)
    expiry = bisect.bisect_right(timestamps, plan.expire_ts_ns)
    long = plan.direction == "LONG"
    fill_index: int | None = None
    for index in range(start, expiry):
        bar = bars[index]
        touched = bar.low <= plan.entry if long else bar.high >= plan.entry
        if touched:
            fill_index = index
            plan.fill_ts_ns = bar.ts_ns
            break
    if fill_index is None:
        plan.outcome = "NO_FILL"
        return
    for bar in bars[fill_index:]:
        if bar.ts_ns >= plan.evaluation_end_ns:
            break
        stop_hit = bar.low <= plan.stop if long else bar.high >= plan.stop
        target_hit = bar.high >= plan.target if long else bar.low <= plan.target
        if stop_hit and target_hit:
            plan.outcome = "AMBIGUOUS_STOP_FIRST"
            plan.exit_ts_ns = bar.ts_ns
            return
        if stop_hit:
            plan.outcome = "STOP"
            plan.exit_ts_ns = bar.ts_ns
            return
        if target_hit:
            plan.outcome = "TARGET"
            plan.exit_ts_ns = bar.ts_ns
            return
    plan.outcome = "OPEN_END"


def _evaluate_current_gate(
    plans: list[Plan],
    bars_by_week: dict[str, dict[str, list[Bar]]],
) -> None:
    plans_by_week: dict[str, list[Plan]] = defaultdict(list)
    for plan in plans:
        plans_by_week[plan.week].append(plan)
    for week, week_plans in plans_by_week.items():
        frames = bars_by_week[week]
        maps = {symbol: {bar.ts_ns: bar for bar in frames[symbol]} for symbol in SYMBOLS}
        common_ts = sorted(set.intersection(*(set(mapping) for mapping in maps.values())))
        plans_at: dict[int, list[Plan]] = defaultdict(list)
        for plan in week_plans:
            plans_at[plan.confirmation_ts_ns].append(plan)
        gate = MarketLeadershipGate(SYMBOLS, lookback_bars=1440)
        for ts_ns in common_ts:
            gate.observe_batch(
                ts_ns,
                {symbol: (maps[symbol][ts_ns].close, maps[symbol][ts_ns].volume) for symbol in SYMBOLS},
            )
            for plan in plans_at.get(ts_ns, ()):  # complete synchronized minute is visible
                decision = gate.decide(
                    symbol=plan.symbol,
                    scenario=plan.scenario,
                    direction=plan.direction,
                    sweep_ts_ns=plan.sweep_ts_ns,
                    confirmation_ts_ns=plan.confirmation_ts_ns,
                )
                plan.gate_approved = decision.approved
                plan.gate_reason = decision.reason


def _required_source_days(plans: Iterable[Plan]) -> set[tuple[str, date]]:
    required: set[tuple[str, date]] = set()
    for plan in plans:
        sweep_day = datetime.fromtimestamp(plan.sweep_ts_ns / 1e9, tz=UTC).date()
        confirmation_day = datetime.fromtimestamp(plan.confirmation_ts_ns / 1e9, tz=UTC).date()
        cursor = sweep_day - timedelta(days=1)
        while cursor <= confirmation_day:
            required.add((plan.symbol, cursor))
            cursor += timedelta(days=1)
    return required


def _load_positioning_sources(
    plans: list[Plan],
    cache_dir: Path,
) -> tuple[dict[str, list[Metric]], dict[str, list[Premium]], list[dict[str, Any]]]:
    metrics: dict[str, list[Metric]] = defaultdict(list)
    premiums: dict[str, list[Premium]] = defaultdict(list)
    manifest: list[dict[str, Any]] = []
    for symbol, day in sorted(_required_source_days(plans)):
        iso = day.isoformat()
        sources = {
            "metrics": f"{BASE}/metrics/{symbol}/{symbol}-metrics-{iso}.zip",
            "premium": f"{BASE}/premiumIndexKlines/{symbol}/1m/{symbol}-1m-{iso}.zip",
        }
        for kind, url in sources.items():
            path = cache_dir / kind / symbol / Path(url).name
            payload = _download(url, path)
            member, rows = _read_csv_member(payload)
            manifest.append({
                "kind": kind,
                "symbol": symbol,
                "date": iso,
                "url": url,
                "member": member,
                "bytes": len(payload),
                "sha256": sha256(payload).hexdigest(),
                "rows": max(0, len(rows) - 1),
            })
            if kind == "metrics":
                if not rows:
                    continue
                header = rows[0]
                expected = [
                    "create_time", "symbol", "sum_open_interest",
                    "sum_open_interest_value", "count_toptrader_long_short_ratio",
                    "sum_toptrader_long_short_ratio", "count_long_short_ratio",
                    "sum_taker_long_short_vol_ratio",
                ]
                if header != expected:
                    raise RuntimeError(f"unexpected metrics schema {symbol} {iso}: {header}")
                for raw in rows[1:]:
                    if len(raw) != len(expected):
                        continue
                    created = datetime.strptime(raw[0], "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC)
                    # Conservative visibility: a row stamped at the start of a
                    # five-minute interval is consumed only after that interval ends.
                    ts_ns = int((created + timedelta(minutes=5)).timestamp() * 1e9)
                    metrics[symbol].append(Metric(
                        ts_ns=ts_ns,
                        open_interest=_float(raw[2]),
                        open_interest_value=_float(raw[3]),
                        top_count_ratio=_optional_float(raw[4]),
                        top_sum_ratio=_optional_float(raw[5]),
                        account_ratio=_optional_float(raw[6]),
                        taker_ratio=_optional_float(raw[7]),
                    ))
            else:
                if rows and rows[0] and rows[0][0] == "open_time":
                    rows = rows[1:]
                for raw in rows:
                    if len(raw) < 5:
                        continue
                    try:
                        open_time = int(raw[0])
                    except ValueError:
                        continue
                    premiums[symbol].append(Premium(
                        ts_ns=(open_time + 60_000) * 1_000_000,
                        close=_float(raw[4]),
                    ))
    for symbol in SYMBOLS:
        metrics[symbol] = sorted({item.ts_ns: item for item in metrics[symbol]}.values(), key=lambda item: item.ts_ns)
        premiums[symbol] = sorted({item.ts_ns: item for item in premiums[symbol]}.values(), key=lambda item: item.ts_ns)
    return metrics, premiums, manifest


def _at_or_before(items: list[Any], ts_ns: int) -> Any | None:
    timestamps = [item.ts_ns for item in items]
    index = bisect.bisect_right(timestamps, ts_ns) - 1
    return None if index < 0 else items[index]


def _between(items: list[Any], start_ns: int, end_ns: int) -> list[Any]:
    timestamps = [item.ts_ns for item in items]
    left = bisect.bisect_left(timestamps, start_ns)
    right = bisect.bisect_right(timestamps, end_ns)
    return items[left:right]


def _log_change(after: float, before: float) -> float | None:
    if after <= 0 or before <= 0:
        return None
    return log(after / before)


def _feature_plan(
    plan: Plan,
    metrics: dict[str, list[Metric]],
    premiums: dict[str, list[Premium]],
) -> None:
    series = metrics[plan.symbol]
    premium_series = premiums[plan.symbol]
    before_sweep = _at_or_before(series, plan.sweep_ts_ns - 30 * MINUTE_NS)
    at_sweep = _at_or_before(series, plan.sweep_ts_ns)
    at_confirmation = _at_or_before(series, plan.confirmation_ts_ns)
    before_confirmation = _at_or_before(series, plan.confirmation_ts_ns - 60 * MINUTE_NS)
    premium_sweep = _at_or_before(premium_series, plan.sweep_ts_ns)
    premium_confirmation = _at_or_before(premium_series, plan.confirmation_ts_ns)
    trailing_premium = _between(
        premium_series,
        plan.confirmation_ts_ns - 360 * MINUTE_NS,
        plan.confirmation_ts_ns,
    )
    if at_sweep is None or at_confirmation is None:
        plan.features = {"source_complete": False, "positioning_state": "MISSING_POSITIONING_DATA"}
        return

    sign = 1.0 if plan.direction == "LONG" else -1.0
    oi_into_sweep = None if before_sweep is None else _log_change(
        at_sweep.open_interest,
        before_sweep.open_interest,
    )
    oi_event = _log_change(at_confirmation.open_interest, at_sweep.open_interest)
    oi_hour = None if before_confirmation is None else _log_change(
        at_confirmation.open_interest,
        before_confirmation.open_interest,
    )
    oi_value_event = _log_change(at_confirmation.open_interest_value, at_sweep.open_interest_value)
    signed_taker = None
    if at_confirmation.taker_ratio is not None and at_confirmation.taker_ratio > 0:
        signed_taker = sign * log(at_confirmation.taker_ratio)
    signed_account_crowding = None
    if at_confirmation.account_ratio is not None and at_confirmation.account_ratio > 0:
        signed_account_crowding = sign * log(at_confirmation.account_ratio)
    signed_top_position = None
    if at_confirmation.top_sum_ratio is not None and at_confirmation.top_sum_ratio > 0:
        signed_top_position = sign * log(at_confirmation.top_sum_ratio)

    premium_at_confirmation = None if premium_confirmation is None else premium_confirmation.close
    signed_premium = None if premium_at_confirmation is None else sign * premium_at_confirmation
    premium_event = None
    if premium_sweep is not None and premium_confirmation is not None:
        premium_event = sign * (premium_confirmation.close - premium_sweep.close)
    premium_z = None
    if premium_at_confirmation is not None and len(trailing_premium) >= 30:
        values = [item.close for item in trailing_premium[:-1]]
        if values:
            mean = sum(values) / len(values)
            variance = sum((value - mean) ** 2 for value in values) / len(values)
            premium_z = sign * (premium_at_confirmation - mean) / max(sqrt(variance), 1e-12)

    # Mechanism labels use only signs and the exchange-native ratio pivot of one.
    # They are hypotheses, not fitted scores.
    closure_driven = (
        oi_event is not None
        and oi_event <= 0.0
        and (oi_into_sweep is None or oi_into_sweep <= 0.0)
    )
    new_position_driven = (
        oi_event is not None
        and oi_event > 0.0
        and signed_taker is not None
        and signed_taker > 0.0
    )
    premium_not_adverse = signed_premium is None or signed_premium >= 0.0
    if plan.scenario == "FAR":
        positioning_confirmed = closure_driven or (
            new_position_driven and premium_not_adverse
        )
    else:
        positioning_confirmed = new_position_driven and premium_not_adverse
    if closure_driven:
        state = "POSITION_CLOSURE_TRANSFER"
    elif new_position_driven and premium_not_adverse:
        state = "NEW_POSITION_ACCEPTANCE"
    elif new_position_driven:
        state = "NEW_POSITION_WITH_ADVERSE_PREMIUM"
    else:
        state = "UNCONFIRMED_POSITIONING"

    plan.features = {
        "source_complete": True,
        "oi_into_sweep_log_change_30m": oi_into_sweep,
        "oi_event_log_change": oi_event,
        "oi_confirmation_log_change_60m": oi_hour,
        "oi_value_event_log_change": oi_value_event,
        "signed_taker_ratio_log": signed_taker,
        "signed_account_crowding_log": signed_account_crowding,
        "signed_top_position_ratio_log": signed_top_position,
        "signed_premium_close": signed_premium,
        "signed_premium_event_change": premium_event,
        "signed_premium_z_6h": premium_z,
        "closure_driven": closure_driven,
        "new_position_driven": new_position_driven,
        "premium_not_adverse": premium_not_adverse,
        "positioning_confirmed": positioning_confirmed,
        "positioning_state": state,
    }


def _contingency(plans: list[Plan]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    dimensions = {
        "all": lambda plan: "ALL",
        "week": lambda plan: plan.week,
        "scenario": lambda plan: plan.scenario,
        "current_gate": lambda plan: "APPROVED" if plan.gate_approved else "REJECTED",
        "gate_reason": lambda plan: plan.gate_reason,
        "positioning_state": lambda plan: str((plan.features or {}).get("positioning_state")),
        "current_gate_x_positioning": lambda plan: (
            ("APPROVED" if plan.gate_approved else "REJECTED")
            + ":"
            + str((plan.features or {}).get("positioning_state"))
        ),
    }
    for name, selector in dimensions.items():
        groups: dict[str, Counter[str]] = defaultdict(Counter)
        for plan in plans:
            groups[selector(plan)][plan.outcome] += 1
        result[name] = {
            key: dict(sorted(counter.items()))
            for key, counter in sorted(groups.items())
        }
    return result


def _rule_audit(plans: list[Plan]) -> dict[str, Any]:
    audits: dict[str, dict[str, Any]] = {}
    rules = {
        "current_price_discovery_gate": lambda plan: plan.gate_approved,
        "positioning_mechanism_only": lambda plan: bool((plan.features or {}).get("positioning_confirmed")),
        "current_gate_and_positioning": lambda plan: (
            plan.gate_approved and bool((plan.features or {}).get("positioning_confirmed"))
        ),
        "current_gate_or_positioning_rescue": lambda plan: (
            plan.gate_approved or bool((plan.features or {}).get("positioning_confirmed"))
        ),
    }
    for name, rule in rules.items():
        selected = [plan for plan in plans if rule(plan)]
        outcomes = Counter(plan.outcome for plan in selected)
        decisive = outcomes["TARGET"] + outcomes["STOP"] + outcomes["AMBIGUOUS_STOP_FIRST"]
        audits[name] = {
            "selected": len(selected),
            "outcomes": dict(sorted(outcomes.items())),
            "decisive_win_rate": None if decisive == 0 else outcomes["TARGET"] / decisive,
            "weeks_with_selected_plans": sorted({plan.week for plan in selected}),
        }
    rescued = [
        plan for plan in plans
        if not plan.gate_approved and bool((plan.features or {}).get("positioning_confirmed"))
    ]
    audits["positioning_rescue_only"] = {
        "selected": len(rescued),
        "outcomes": dict(sorted(Counter(plan.outcome for plan in rescued).items())),
        "by_week": {
            week: dict(sorted(Counter(plan.outcome for plan in rescued if plan.week == week).items()))
            for week in sorted({plan.week for plan in rescued})
        },
        "records": [
            {
                "week": plan.week,
                "scenario_id": plan.scenario_id,
                "symbol": plan.symbol,
                "scenario": plan.scenario,
                "direction": plan.direction,
                "outcome": plan.outcome,
                "gate_reason": plan.gate_reason,
                "positioning_state": (plan.features or {}).get("positioning_state"),
                "features": plan.features,
            }
            for plan in rescued
        ],
    }
    return audits


def _serialize_plan(plan: Plan) -> dict[str, Any]:
    return {
        "week": plan.week,
        "scenario_id": plan.scenario_id,
        "symbol": plan.symbol,
        "scenario": plan.scenario,
        "direction": plan.direction,
        "sweep_ts_ns": plan.sweep_ts_ns,
        "confirmation_ts_ns": plan.confirmation_ts_ns,
        "expire_ts_ns": plan.expire_ts_ns,
        "entry": plan.entry,
        "stop": plan.stop,
        "target": plan.target,
        "net_r": plan.net_r,
        "evaluation_end_ns": plan.evaluation_end_ns,
        "gate_approved": plan.gate_approved,
        "gate_reason": plan.gate_reason,
        "outcome": plan.outcome,
        "fill_ts_ns": plan.fill_ts_ns,
        "exit_ts_ns": plan.exit_ts_ns,
        "features": plan.features,
    }


def run(root: Path, output: Path, cache_dir: Path, skip_download: bool) -> dict[str, Any]:
    config = json.loads((root / "config.json").read_text(encoding="utf-8"))
    plans, bars_by_week = _extract_plans(root, config)
    for plan in plans:
        _label_outcome(plan, bars_by_week[plan.week][plan.symbol])
    _evaluate_current_gate(plans, bars_by_week)
    manifest: list[dict[str, Any]] = []
    if skip_download:
        for plan in plans:
            plan.features = {"source_complete": False, "positioning_state": "SKIPPED"}
    else:
        metrics, premiums, manifest = _load_positioning_sources(plans, cache_dir)
        for plan in plans:
            _feature_plan(plan, metrics, premiums)

    payload = {
        "schema": "candidate-11-positioning-hypothesis-diagnostic-v1",
        "evidence_class": "RESEARCH_DIAGNOSTIC_NOT_NAUTILUS_PERFORMANCE",
        "warning": (
            "W1-W9 outcomes are used only to decide whether a positioning mechanism "
            "deserves implementation. This file cannot establish out-of-sample success."
        ),
        "causal_contract": {
            "metrics_visibility": "create_time plus five minutes",
            "premium_visibility": "archive open_time plus one minute",
            "feature_cutoff": "no observation after plan confirmation",
            "outcome_usage": "future bars used only as old-week diagnostic labels",
        },
        "plans": len(plans),
        "outcomes": dict(sorted(Counter(plan.outcome for plan in plans).items())),
        "current_gate": dict(sorted(Counter(
            "APPROVED" if plan.gate_approved else "REJECTED" for plan in plans
        ).items())),
        "contingency": _contingency(plans),
        "rule_audit": _rule_audit(plans),
        "records": [_serialize_plan(plan) for plan in plans],
        "data_manifest": manifest,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(output)
    return payload


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--output", type=Path, default=ROOT / "positioning_hypotheses.json")
    parser.add_argument("--cache", type=Path, default=Path("/tmp/candidate-11-positioning-cache"))
    parser.add_argument("--skip-download", action="store_true")
    args = parser.parse_args()
    payload = run(args.root.resolve(), args.output.resolve(), args.cache.resolve(), args.skip_download)
    print(json.dumps({
        "plans": payload["plans"],
        "outcomes": payload["outcomes"],
        "current_gate": payload["current_gate"],
        "rule_audit": payload["rule_audit"],
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
