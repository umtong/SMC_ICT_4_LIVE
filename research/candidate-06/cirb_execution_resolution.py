"""Parent-frozen five-second response-resolution experiment for CIRB.

The existing one-minute CIRB run remains the authority for which completed
five-minute inventory shocks were observable.  This module reads those parent
events, reconstructs only their already-known structural anchors, and asks a
single causal question: can the later response be confirmed on completed
five-second aggregate-trade bars before one-minute aggregation erodes the
cost-after-entry geometry?

No order, fill, PnL, cash or NAV is calculated here.  The output is a stream of
causal :class:`ScenarioSignal` plans consumed by a native NautilusTrader runner.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, timedelta
from hashlib import sha256
import csv
import io
import json
from math import log
from pathlib import Path
from typing import Any, Mapping, Sequence
import zipfile

import pandas as pd

from agg_trade_profile_data import _bool, _timestamp_ms, download_daily_archive
from futures_metrics_data import FuturesMetric
from lrb_types import BarObservation, ScenarioSignal
from primitives import CausalPrimitiveDetector

NS_PER_SECOND = 1_000_000_000
NS_PER_FIVE_SECONDS = 5 * NS_PER_SECOND
NS_PER_MINUTE = 60 * NS_PER_SECOND


@dataclass(frozen=True, slots=True)
class LoadedFiveSecondData:
    frame: pd.DataFrame
    source_files: tuple[Path, ...]
    quality: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class FrozenParent:
    scenario_id: str
    observed_ts_ns: int
    side: str
    crowding_branch: str
    event_drop: float
    drop_threshold: float
    event_open: float
    event_high: float
    event_low: float
    event_close: float
    event_mid: float
    event_range: float
    atr: float
    slow_mid: float | None
    upper_fast: float | None
    lower_fast: float | None
    prior_open_interest: float
    event_open_interest: float
    prior_all_account_ratio: float
    event_all_account_ratio: float
    shock_sign: float
    baseline_entry: Mapping[str, Any] | None
    baseline_rr_eroded: bool


@dataclass(frozen=True, slots=True)
class ChildPlan:
    parent_scenario_id: str
    signal: ScenarioSignal
    invalidation_ts_ns: int | None
    invalidation_reason: str | None
    response_delay_seconds: float
    entry_price_improvement_bps: float | None
    baseline_rr_eroded: bool


def _read_aggtrade_archive(path: Path) -> tuple[list[dict[str, float | int]], int]:
    buckets: dict[int, dict[str, float | int]] = {}
    raw_rows = 0
    with zipfile.ZipFile(path) as bundle:
        members = [name for name in bundle.namelist() if name.lower().endswith(".csv")]
        if len(members) != 1:
            raise ValueError(f"expected one aggTrades CSV in {path}, found {members}")
        with bundle.open(members[0]) as raw:
            reader = csv.reader(io.TextIOWrapper(raw, encoding="utf-8", newline=""))
            for row in reader:
                if not row:
                    continue
                if len(row) < 7:
                    raise ValueError(f"short aggTrades row in {path}: {row!r}")
                if not row[0].strip().lstrip("-").isdigit():
                    continue
                raw_rows += 1
                price = float(row[1])
                quantity = float(row[2])
                timestamp_ms = _timestamp_ms(row[5])
                buyer_maker = _bool(row[6])
                if price <= 0.0 or quantity <= 0.0:
                    raise ValueError(f"nonpositive aggTrade in {path}: {row[:7]!r}")
                bucket_start = (timestamp_ms // 5_000) * 5_000
                bucket_end = bucket_start + 5_000
                item = buckets.get(bucket_end)
                if item is None:
                    item = {
                        "observed_ms": bucket_end,
                        "open": price,
                        "high": price,
                        "low": price,
                        "close": price,
                        "volume": quantity,
                        "taker_buy_volume": 0.0 if buyer_maker else quantity,
                        "trades": 1,
                    }
                    buckets[bucket_end] = item
                else:
                    item["high"] = max(float(item["high"]), price)
                    item["low"] = min(float(item["low"]), price)
                    item["close"] = price
                    item["volume"] = float(item["volume"]) + quantity
                    if not buyer_maker:
                        item["taker_buy_volume"] = float(item["taker_buy_volume"]) + quantity
                    item["trades"] = int(item["trades"]) + 1
    return [buckets[key] for key in sorted(buckets)], raw_rows


def load_five_second_week(symbol: str, week_start: date, cache_root: str | Path) -> LoadedFiveSecondData:
    """Load checksum-verified aggTrades and build complete causal 5-second bars."""
    root = Path(cache_root).resolve() / symbol / "aggTrades"
    source_files: list[Path] = []
    daily_archives: list[Path] = []
    for offset in range(7):
        day = week_start + timedelta(days=offset)
        archive, checksum = download_daily_archive(symbol, day, root)
        source_files.extend((archive, checksum))
        daily_archives.append(archive)

    derived_root = root / "derived-five-second"
    derived_root.mkdir(parents=True, exist_ok=True)
    cache_path = derived_root / f"{symbol}-5s-{week_start.isoformat()}.pkl"
    quality_path = derived_root / f"{symbol}-5s-{week_start.isoformat()}.quality.json"
    if cache_path.exists() and quality_path.exists():
        frame = pd.read_pickle(cache_path)
        quality = json.loads(quality_path.read_text(encoding="utf-8"))
        if len(frame.index) != 7 * 24 * 60 * 12:
            raise RuntimeError("cached five-second row count is invalid")
        if not frame.index.is_monotonic_increasing or frame.index.has_duplicates:
            raise RuntimeError("cached five-second timestamps are invalid")
        quality = {
            **quality,
            "derived_cache_reused": True,
        }
        return LoadedFiveSecondData(
            frame=frame,
            source_files=tuple(source_files),
            quality=quality,
        )

    records: list[dict[str, float | int]] = []
    raw_rows = 0
    for archive in daily_archives:
        daily, count = _read_aggtrade_archive(archive)
        records.extend(daily)
        raw_rows += count
    if not records:
        raise RuntimeError("aggTrades produced no five-second records")

    frame = pd.DataFrame.from_records(records)
    frame["observed_time"] = pd.to_datetime(frame["observed_ms"], unit="ms", utc=True)
    frame = frame.set_index("observed_time").sort_index()
    frame = frame[~frame.index.duplicated(keep="last")]
    week_open = pd.Timestamp(week_start, tz="UTC")
    week_end = week_open + pd.Timedelta(days=7)
    expected = pd.date_range(
        week_open + pd.Timedelta(seconds=5),
        week_end,
        freq="5s",
        tz="UTC",
        name="observed_time",
    )
    frame = frame.reindex(expected)
    traded = frame["close"].notna()
    if not bool(traded.iloc[0]):
        raise RuntimeError("first five-second bucket lacks a causal trade price")
    carried = frame["close"].ffill()
    no_trade = ~traded
    for name in ("open", "high", "low", "close"):
        frame.loc[no_trade, name] = carried.loc[no_trade]
    for name in ("volume", "taker_buy_volume", "trades"):
        frame[name] = frame[name].fillna(0.0)
    frame["trades"] = frame["trades"].astype("int64")
    frame["observed_ms"] = (frame.index.asi8 // 1_000_000).astype("int64")

    if len(frame.index) != 7 * 24 * 60 * 12:
        raise RuntimeError(f"unexpected five-second row count: {len(frame.index)}")
    if bool((frame[["open", "high", "low", "close"]] <= 0.0).any().any()):
        raise RuntimeError("nonpositive five-second OHLC")
    if bool((frame["high"] < frame[["open", "close"]].max(axis=1)).any()):
        raise RuntimeError("five-second high is inconsistent")
    if bool((frame["low"] > frame[["open", "close"]].min(axis=1)).any()):
        raise RuntimeError("five-second low is inconsistent")
    if bool((frame["taker_buy_volume"] < 0.0).any()) or bool(
        (frame["taker_buy_volume"] > frame["volume"] + 1e-12).any()
    ):
        raise RuntimeError("five-second aggressor volume is inconsistent")

    quality = {
        "symbol": symbol,
        "provider": "Binance public data / USD-M aggTrades",
        "week_start_utc": week_start.isoformat(),
        "interval": "5s",
        "rows": int(len(frame.index)),
        "raw_aggregate_trade_rows": int(raw_rows),
        "trade_buckets": int(traded.sum()),
        "causal_zero_flow_buckets": int(no_trade.sum()),
        "missing_buckets": 0,
        "derived_cache_reused": False,
        "timestamp_contract": (
            "aggregate trades are bucketed by exchange event time; each bar becomes "
            "observable only at the exact five-second interval end; no-trade buckets "
            "carry only the last already-observed price and zero flow"
        ),
        "archives": [path.name for path in source_files if path.suffix == ".zip"],
    }
    temporary = cache_path.with_suffix(".pkl.tmp")
    frame.to_pickle(temporary)
    temporary.replace(cache_path)
    quality_path.write_text(
        json.dumps(quality, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return LoadedFiveSecondData(
        frame=frame,
        source_files=tuple(source_files),
        quality=quality,
    )


def _load_events(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _canonical_hash(rows: Sequence[Mapping[str, Any]]) -> str:
    payload = json.dumps(
        list(rows),
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return sha256(payload).hexdigest()


def freeze_parent_events(
    *,
    baseline_events_path: Path,
    one_minute_frame: pd.DataFrame,
    metrics: Mapping[int, FuturesMetric],
    logic_params: Mapping[str, Any],
) -> tuple[tuple[FrozenParent, ...], dict[str, Any]]:
    """Freeze parent events from the authoritative one-minute Nautilus run."""
    events = _load_events(baseline_events_path)
    detector = CausalPrimitiveDetector(logic_params)
    snapshots: dict[int, Any] = {}
    for timestamp, row in one_minute_frame.iterrows():
        ts_ns = int(timestamp.value)
        snapshots[ts_ns] = detector.observe(
            BarObservation(
                ts_ns=ts_ns,
                open=float(row["open"]),
                high=float(row["high"]),
                low=float(row["low"]),
                close=float(row["close"]),
                volume=float(row["volume"]),
                taker_buy_volume=float(row["taker_buy_volume"]),
                trades=int(row["trades"]),
            )
        )

    baseline_entries: dict[str, dict[str, Any]] = {}
    rr_eroded: set[str] = set()
    entry_rows: list[dict[str, Any]] = []
    for event in events:
        scenario_id = str(event.get("scenario_id", ""))
        if event.get("event_type") == "CIRB_ENTRY_TRANSITION" and event.get("next_state") == "ENTRY_ARMED":
            parent_id = str(event.get("details", {}).get("context_scenario_id"))
            row = {
                "parent_scenario_id": parent_id,
                "scenario_id": scenario_id,
                "observed_time_ns": int(event["observed_time_ns"]),
                "reason_code": str(event["reason_code"]),
                "reference_price": float(event["reference_price"]),
                "stop": float(event.get("details", {}).get("stop")),
                "target": float(event.get("details", {}).get("target")),
                "target_reason": event.get("details", {}).get("target_reason"),
            }
            baseline_entries[parent_id] = row
            entry_rows.append(row)
        if event.get("reason_code") == "NET_REWARD_RISK_ERODED_AFTER_DELAY":
            rr_eroded.add(scenario_id.split(":ENTRY", 1)[0])

    parent_rows: list[dict[str, Any]] = []
    parents: list[FrozenParent] = []
    semantic_drift = 0
    for event in events:
        if not (
            event.get("event_type") == "CROWDING_INVENTORY_RESPONSE_TRANSITION"
            and event.get("previous_state") == "IDLE"
            and event.get("next_state") == "DELEVERAGING_WAVE_OBSERVED"
        ):
            continue
        details = dict(event.get("details", {}))
        ts_ns = int(event["observed_time_ns"])
        snapshot = snapshots.get(ts_ns)
        metric = metrics.get(ts_ns)
        if snapshot is None or metric is None:
            semantic_drift += 1
            continue
        event_close = float(details["event_close"])
        if abs(event_close - float(snapshot.observation.close)) > 1e-7:
            semantic_drift += 1
        atr = max(float(snapshot.atr), 1e-12)
        event_high = float(details["event_high"])
        event_low = float(details["event_low"])
        event_open = float(details["event_open"])
        event_drop = float(details["open_interest_drop_fraction"])
        scenario_id = str(event["scenario_id"])
        parent = FrozenParent(
            scenario_id=scenario_id,
            observed_ts_ns=ts_ns,
            side=str(details["forced_side"]),
            crowding_branch=str(details["crowding_branch"]),
            event_drop=event_drop,
            drop_threshold=float(details["prior_only_drop_threshold"]),
            event_open=event_open,
            event_high=event_high,
            event_low=event_low,
            event_close=event_close,
            event_mid=(event_open + event_close) / 2.0,
            event_range=max(event_high - event_low, atr * 0.25),
            atr=atr,
            slow_mid=None if snapshot.slow_mid is None else float(snapshot.slow_mid),
            upper_fast=None if snapshot.upper_fast is None else float(snapshot.upper_fast),
            lower_fast=None if snapshot.lower_fast is None else float(snapshot.lower_fast),
            prior_open_interest=float(details["prior_open_interest"]),
            event_open_interest=float(details["event_open_interest"]),
            prior_all_account_ratio=float(details["prior_all_account_ratio"]),
            event_all_account_ratio=float(details["event_all_account_ratio"]),
            shock_sign=float(details["shock_sign"]),
            baseline_entry=baseline_entries.get(scenario_id),
            baseline_rr_eroded=scenario_id in rr_eroded,
        )
        parents.append(parent)
        parent_rows.append(
            {
                "scenario_id": scenario_id,
                "observed_ts_ns": ts_ns,
                "side": parent.side,
                "crowding_branch": parent.crowding_branch,
                "event_drop": event_drop,
                "drop_threshold": parent.drop_threshold,
                "event_open": event_open,
                "event_high": event_high,
                "event_low": event_low,
                "event_close": event_close,
                "event_open_interest": parent.event_open_interest,
                "event_all_account_ratio": parent.event_all_account_ratio,
            }
        )

    parents.sort(key=lambda item: (item.observed_ts_ns, item.scenario_id))
    parent_rows.sort(key=lambda item: (item["observed_ts_ns"], item["scenario_id"]))
    entry_rows.sort(key=lambda item: (item["observed_time_ns"], item["scenario_id"]))
    audit = {
        "parent_events": len(parents),
        "baseline_entry_signals": len(entry_rows),
        "baseline_rr_eroded_signals": len(rr_eroded),
        "parent_event_identity_hash": _canonical_hash(parent_rows),
        "baseline_entry_identity_hash": _canonical_hash(entry_rows),
        "semantic_drift_count": semantic_drift,
        "parent_rows": parent_rows,
        "baseline_entry_rows": entry_rows,
    }
    return tuple(parents), audit


def _flow_ratio(row: pd.Series) -> float:
    volume = float(row["volume"])
    if volume <= 0.0:
        return 0.0
    return (2.0 * float(row["taker_buy_volume"]) - volume) / volume


def _close_location(row: pd.Series) -> float:
    span = max(float(row["high"]) - float(row["low"]), 0.0)
    return (float(row["close"]) - float(row["low"])) / span if span > 0.0 else 0.5


def _reversal_target(
    parent: FrozenParent,
    *,
    direction: str,
    entry: float,
    stop: float,
    minimum_rr: float,
) -> tuple[float, str] | None:
    if direction == "LONG":
        raw = (
            (parent.event_open, "DELEVERAGING_IMPULSE_ORIGIN"),
            (parent.slow_mid, "PRE_SHOCK_DEALING_RANGE_EQUILIBRIUM"),
            (parent.upper_fast, "PRE_SHOCK_FAST_RANGE_LIQUIDITY"),
        )
        candidates = sorted(
            (float(level), reason)
            for level, reason in raw
            if level is not None and float(level) > entry
        )
    else:
        raw = (
            (parent.event_open, "DELEVERAGING_IMPULSE_ORIGIN"),
            (parent.slow_mid, "PRE_SHOCK_DEALING_RANGE_EQUILIBRIUM"),
            (parent.lower_fast, "PRE_SHOCK_FAST_RANGE_LIQUIDITY"),
        )
        candidates = sorted(
            (
                (float(level), reason)
                for level, reason in raw
                if level is not None and float(level) < entry
            ),
            reverse=True,
        )
    risk = abs(entry - stop)
    for target, reason in candidates:
        if risk > 0.0 and abs(target - entry) / risk >= minimum_rr:
            return target, reason
    return None


def _price_improvement_bps(parent: FrozenParent, direction: str, child_entry: float) -> float | None:
    baseline = parent.baseline_entry
    if baseline is None:
        return None
    reference = float(baseline["reference_price"])
    if reference <= 0.0:
        return None
    improvement = reference - child_entry if direction == "LONG" else child_entry - reference
    return improvement / reference * 10_000.0


def _scan_invalidation(
    *,
    parent: FrozenParent,
    frame: pd.DataFrame,
    signal_ts_ns: int,
    branch: str,
    extreme_at_signal: float,
    params: Mapping[str, Any],
) -> tuple[int | None, str | None]:
    floor = float(params.get("oidb_response_flow_ratio", 0.05))
    horizon = int(params.get("oidb_invalidation_observation_bars", 6)) * NS_PER_MINUTE
    end_ns = signal_ts_ns + horizon
    subset = frame[
        (frame.index.asi8 > signal_ts_ns)
        & (frame.index.asi8 <= end_ns)
    ]
    for timestamp, row in subset.iterrows():
        close = float(row["close"])
        flow = _flow_ratio(row)
        if branch == "REVERSAL":
            invalid = (
                (parent.side == "SELL" and close < extreme_at_signal and flow <= -floor)
                or (parent.side == "BUY" and close > extreme_at_signal and flow >= floor)
            )
            reason = "DELEVERAGING_REVERSAL_THESIS_INVALIDATED"
        else:
            invalid = (
                (parent.side == "SELL" and close > parent.event_mid and flow >= floor)
                or (parent.side == "BUY" and close < parent.event_mid and flow <= -floor)
            )
            reason = "DELEVERAGING_CONTINUATION_THESIS_INVALIDATED"
        if invalid:
            return int(timestamp.value), reason
    return None, None


def build_child_plans(
    *,
    parents: Sequence[FrozenParent],
    five_second_frame: pd.DataFrame,
    metrics: Mapping[int, FuturesMetric],
    logic_params: Mapping[str, Any],
    enable_discharge: bool,
    enable_counter_inventory: bool,
) -> tuple[tuple[ChildPlan, ...], dict[str, Any]]:
    """Build causal 5-second response plans from frozen parent events."""
    plans: list[ChildPlan] = []
    outcomes: dict[str, int] = {}
    delays: list[float] = []
    improvements: list[float] = []
    floor = float(logic_params.get("oidb_response_flow_ratio", 0.05))
    reclaim = float(logic_params.get("oidb_reclaim_close_location", 0.58))
    minimum_rr = float(logic_params.get("minimum_structural_rr", 0.75))
    buffer_fraction = float(logic_params.get("oidb_stop_buffer_atr", 0.08))
    extension_fraction = float(logic_params.get("oidb_extension_atr", 0.05))
    projection_fraction = float(logic_params.get("oidb_projection_fraction", 1.0))

    def count(name: str) -> None:
        outcomes[name] = int(outcomes.get(name, 0)) + 1

    eligible_parents = tuple(
        parent for parent in parents if parent.baseline_entry is not None
    )

    for parent in eligible_parents:
        assert parent.baseline_entry is not None
        branch = parent.crowding_branch
        expected_reason = str(parent.baseline_entry["reason_code"])
        expected_family = {
            "CROWD_DISCHARGE_REVERSAL_ENTRY_ARMED": "CIRB_D_R",
            "CROWD_DISCHARGE_CONTINUATION_ENTRY_ARMED": "CIRB_D_C",
            "TRAPPED_COUNTER_INVENTORY_CONTINUATION_ENTRY_ARMED": "CIRB_T_C",
        }.get(expected_reason)
        if expected_family is None:
            count("BASELINE_ENTRY_FAMILY_UNSUPPORTED")
            continue
        if branch == "DISCHARGE" and not enable_discharge:
            count("DISCHARGE_DISABLED")
            continue
        if branch == "COUNTER_INVENTORY" and not enable_counter_inventory:
            count("COUNTER_INVENTORY_DISABLED")
            continue
        if branch not in {"DISCHARGE", "COUNTER_INVENTORY"}:
            count("AMBIGUOUS_OR_LEGACY_PARENT")
            continue

        response_minutes = (
            int(logic_params.get("cirb_counter_response_bars", 15))
            if branch == "COUNTER_INVENTORY"
            else int(logic_params.get("oidb_response_bars", 6))
        )
        end_ns = parent.observed_ts_ns + response_minutes * NS_PER_MINUTE
        subset = five_second_frame[
            (five_second_frame.index.asi8 > parent.observed_ts_ns)
            & (five_second_frame.index.asi8 <= end_ns)
        ]
        extreme = parent.event_low if parent.side == "SELL" else parent.event_high
        previous_metric = metrics.get(parent.observed_ts_ns)
        signalled = False
        terminal_recorded = False
        partial_minute_index: int | None = None
        partial: dict[str, float] | None = None
        for timestamp, row in subset.iterrows():
            ts_ns = int(timestamp.value)
            response_minute_index = (
                ts_ns - parent.observed_ts_ns - 1
            ) // NS_PER_MINUTE
            if partial_minute_index != response_minute_index:
                partial_minute_index = response_minute_index
                partial = {
                    "open": float(row["open"]),
                    "high": float(row["high"]),
                    "low": float(row["low"]),
                    "close": float(row["close"]),
                    "volume": float(row["volume"]),
                    "taker_buy_volume": float(row["taker_buy_volume"]),
                }
            else:
                assert partial is not None
                partial["high"] = max(partial["high"], float(row["high"]))
                partial["low"] = min(partial["low"], float(row["low"]))
                partial["close"] = float(row["close"])
                partial["volume"] += float(row["volume"])
                partial["taker_buy_volume"] += float(row["taker_buy_volume"])
            assert partial is not None
            close = partial["close"]
            prior_extreme = extreme
            extreme = min(extreme, float(row["low"])) if parent.side == "SELL" else max(
                extreme, float(row["high"])
            )
            flow = _flow_ratio(partial)
            location = _close_location(partial)
            metric = metrics.get(ts_ns)
            signal: ScenarioSignal | None = None
            signal_branch: str | None = None

            if branch == "DISCHARGE":
                reversal = (
                    parent.side == "SELL"
                    and close >= parent.event_mid
                    and flow >= floor
                    and location >= reclaim
                ) or (
                    parent.side == "BUY"
                    and close <= parent.event_mid
                    and flow <= -floor
                    and location <= 1.0 - reclaim
                )
                if reversal:
                    direction = "LONG" if parent.side == "SELL" else "SHORT"
                    stop = extreme - buffer_fraction * parent.atr if direction == "LONG" else extreme + buffer_fraction * parent.atr
                    target = _reversal_target(
                        parent,
                        direction=direction,
                        entry=close,
                        stop=stop,
                        minimum_rr=minimum_rr,
                    )
                    if target is None:
                        count("NO_REVERSAL_OBJECTIVE_WITH_SUFFICIENT_SPACE")
                        terminal_recorded = True
                        break
                    signal_branch = "REVERSAL"
                    signal = ScenarioSignal(
                        scenario_id=f"{parent.scenario_id}:5S",
                        family="CIRB_D_R",
                        direction=direction,
                        observed_ts_ns=ts_ns,
                        reference_entry=close,
                        stop_price=stop,
                        target_price=target[0],
                        target_reason=target[1],
                        atr=parent.atr,
                        liquidity_level=extreme,
                        details={
                            "parent_scenario_id": parent.scenario_id,
                            "crowding_branch": branch,
                            "response_resolution": "CAUSAL_PARTIAL_MINUTE_FROM_COMPLETED_5S",
                            "baseline_rr_eroded": parent.baseline_rr_eroded,
                            "causal_exit_reason_codes": (
                                "DELEVERAGING_REVERSAL_THESIS_INVALIDATED",
                            ),
                            "causal_exit_open_position": True,
                        },
                    )
                elif (
                        expected_family == "CIRB_D_C"
                        and metric is not None
                        and previous_metric is not None
                        and previous_metric.open_interest > 0.0
                    ):
                    next_change = (
                        metric.open_interest - previous_metric.open_interest
                    ) / previous_metric.open_interest
                    persistence = next_change <= -parent.event_drop * float(
                        logic_params.get("oidb_persistence_fraction", 0.35)
                    )
                    extension = extension_fraction * parent.atr
                    continuation = (
                        parent.side == "SELL"
                        and persistence
                        and close <= prior_extreme - extension
                        and flow <= -floor
                        and location <= 1.0 - reclaim
                    ) or (
                        parent.side == "BUY"
                        and persistence
                        and close >= prior_extreme + extension
                        and flow >= floor
                        and location >= reclaim
                    )
                    if continuation:
                        direction = "SHORT" if parent.side == "SELL" else "LONG"
                        stop = parent.event_mid + buffer_fraction * parent.atr if direction == "SHORT" else parent.event_mid - buffer_fraction * parent.atr
                        distance = max(parent.event_range, parent.atr) * projection_fraction
                        target = close - distance if direction == "SHORT" else close + distance
                        risk = abs(close - stop)
                        if risk <= 0.0 or distance / risk < minimum_rr:
                            count("NO_CONTINUATION_OBJECTIVE_WITH_SUFFICIENT_SPACE")
                            terminal_recorded = True
                            break
                        signal_branch = "CONTINUATION"
                        signal = ScenarioSignal(
                            scenario_id=f"{parent.scenario_id}:5S",
                            family="CIRB_D_C",
                            direction=direction,
                            observed_ts_ns=ts_ns,
                            reference_entry=close,
                            stop_price=stop,
                            target_price=target,
                            target_reason="DELEVERAGING_RANGE_EXTENSION",
                            atr=parent.atr,
                            liquidity_level=extreme,
                            details={
                                "parent_scenario_id": parent.scenario_id,
                                "crowding_branch": branch,
                                "response_resolution": "CAUSAL_PARTIAL_MINUTE_FROM_COMPLETED_5S",
                                "baseline_rr_eroded": parent.baseline_rr_eroded,
                                "causal_exit_reason_codes": (
                                    "DELEVERAGING_CONTINUATION_THESIS_INVALIDATED",
                                ),
                                "causal_exit_open_position": True,
                            },
                        )
                if metric is not None:
                    previous_metric = metric
            else:
                invalid = (
                    parent.side == "SELL"
                    and close >= parent.event_mid
                    and flow >= floor
                    and location >= reclaim
                ) or (
                    parent.side == "BUY"
                    and close <= parent.event_mid
                    and flow <= -floor
                    and location <= 1.0 - reclaim
                )
                if invalid:
                    count("COUNTER_INVENTORY_INVALIDATED_BY_OPPOSITE_RECLAIM")
                    terminal_recorded = True
                    break
                if metric is not None and ts_ns > parent.observed_ts_ns:
                    rebuild = (
                        metric.open_interest - parent.event_open_interest
                    ) / parent.event_open_interest
                    required = parent.event_drop * float(
                        logic_params.get("cirb_counter_rebuild_fraction", 0.35)
                    )
                    composition_change = log(
                        metric.all_account_long_short / parent.event_all_account_ratio
                    )
                    persists = (
                        parent.shock_sign * composition_change <= 0.0
                        if bool(
                            logic_params.get(
                                "cirb_require_counter_composition_persistence",
                                True,
                            )
                        )
                        else True
                    )
                    extension = extension_fraction * parent.atr
                    price_extends = (
                        parent.side == "SELL"
                        and close <= prior_extreme - extension
                        and flow <= -floor
                        and location <= 1.0 - reclaim
                    ) or (
                        parent.side == "BUY"
                        and close >= prior_extreme + extension
                        and flow >= floor
                        and location >= reclaim
                    )
                    if rebuild >= required and persists and price_extends:
                        direction = "SHORT" if parent.side == "SELL" else "LONG"
                        stop = parent.event_mid + buffer_fraction * parent.atr if direction == "SHORT" else parent.event_mid - buffer_fraction * parent.atr
                        distance = max(parent.event_range, parent.atr) * projection_fraction
                        target = close - distance if direction == "SHORT" else close + distance
                        risk = abs(close - stop)
                        if risk <= 0.0 or distance / risk < minimum_rr:
                            count("NO_COUNTER_OBJECTIVE_WITH_SUFFICIENT_SPACE")
                            terminal_recorded = True
                            break
                        signal_branch = "CONTINUATION"
                        signal = ScenarioSignal(
                            scenario_id=f"{parent.scenario_id}:5S",
                            family="CIRB_T_C",
                            direction=direction,
                            observed_ts_ns=ts_ns,
                            reference_entry=close,
                            stop_price=stop,
                            target_price=target,
                            target_reason="TRAPPED_COUNTER_INVENTORY_RANGE_EXTENSION",
                            atr=parent.atr,
                            liquidity_level=extreme,
                            details={
                                "parent_scenario_id": parent.scenario_id,
                                "crowding_branch": branch,
                                "response_resolution": "CAUSAL_PARTIAL_MINUTE_FROM_COMPLETED_5S",
                                "counter_inventory_rebuild_fraction": rebuild,
                                "required_counter_inventory_rebuild_fraction": required,
                                "counter_composition_change_log": composition_change,
                                "baseline_rr_eroded": parent.baseline_rr_eroded,
                                "causal_exit_reason_codes": (
                                    "DELEVERAGING_CONTINUATION_THESIS_INVALIDATED",
                                ),
                                "causal_exit_open_position": True,
                            },
                        )

            if signal is not None and signal.family != expected_family:
                    raise RuntimeError(
                        f"five-second family drift: {signal.family} != {expected_family}"
                    )
                if signal is not None and signal_branch is not None:
                invalidation_ts, invalidation_reason = _scan_invalidation(
                    parent=parent,
                    frame=five_second_frame,
                    signal_ts_ns=ts_ns,
                    branch=signal_branch,
                    extreme_at_signal=extreme,
                    params=logic_params,
                )
                delay = (ts_ns - parent.observed_ts_ns) / NS_PER_SECOND
                improvement = _price_improvement_bps(parent, signal.direction, signal.reference_entry)
                plans.append(
                    ChildPlan(
                        parent_scenario_id=parent.scenario_id,
                        signal=signal,
                        invalidation_ts_ns=invalidation_ts,
                        invalidation_reason=invalidation_reason,
                        response_delay_seconds=delay,
                        entry_price_improvement_bps=improvement,
                        baseline_rr_eroded=parent.baseline_rr_eroded,
                    )
                )
                delays.append(delay)
                if improvement is not None:
                    improvements.append(improvement)
                count(f"SIGNAL_{signal.family}")
                signalled = True
                break
        if not signalled and not terminal_recorded:
            count("RESPONSE_EXPIRED_OR_NOT_CONFIRMED")

    plans.sort(key=lambda item: (item.signal.observed_ts_ns, item.signal.scenario_id))
    diagnostics = {
        "parent_events_observed": len(parents),
        "parent_signals_armed": len(eligible_parents),
        "child_5s_candidates": len(plans),
        "baseline_rr_eroded_parent_count": sum(
            parent.baseline_rr_eroded for parent in eligible_parents
        ),
        "partial_minute_aggregation": True,
        "entry_family_frozen": True,
        "rescued_by_5s_candidate_count": sum(plan.baseline_rr_eroded for plan in plans),
        "response_outcomes": dict(sorted(outcomes.items())),
        "entry_delay_seconds": delays,
        "mean_entry_delay_seconds": sum(delays) / len(delays) if delays else None,
        "entry_price_improvement_bps": improvements,
        "mean_entry_price_improvement_bps": (
            sum(improvements) / len(improvements) if improvements else None
        ),
        "same_parent_can_emit_at_most_one_child": True,
        "event_bar_can_trade": False,
    }
    return tuple(plans), diagnostics


def serialize_plans(plans: Sequence[ChildPlan]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for plan in plans:
        item = asdict(plan)
        item["signal"] = asdict(plan.signal)
        rows.append(item)
    return rows
