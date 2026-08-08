#!/usr/bin/env python3
"""Audit V3 failed-attack reacceptance paths on exposed development data.

This is a structural path audit, not an account backtest or success claim.  It
uses the exact checksum-verified kline archives already owned by the V3
Nautilus replay and the emitted setup/transition events.  Its sole purpose is
to distinguish an implementation error from a different latent state:

    leveraged attack -> true failure inside value -> later same-side reacceptance

Only events whose completed response actually closed back inside the old range
are retained.  Wick-retention failure while price remains outside is explicitly
excluded.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import math
from pathlib import Path
from typing import Any

import pandas as pd

COLUMNS = (
    "open_time", "open", "high", "low", "close", "volume", "close_time",
    "quote_volume", "trades", "taker_buy_volume", "taker_buy_quote_volume", "ignore",
)
MINUTE_NS = 60_000_000_000
HORIZONS = (15, 30, 60, 120, 240)


def _epoch(values: pd.Series) -> pd.Series:
    numeric = pd.to_numeric(values, errors="raise").astype("int64")
    first = abs(int(numeric.iloc[0]))
    if first >= 10**17:
        unit = "ns"
    elif first >= 10**14:
        unit = "us"
    elif first >= 10**11:
        unit = "ms"
    else:
        unit = "s"
    return pd.to_datetime(numeric, unit=unit, utc=True)


def _read_kline(path: Path) -> pd.DataFrame:
    raw = pd.read_csv(path, compression="zip", header=None)
    if raw.shape[1] != len(COLUMNS):
        raw = pd.read_csv(path, compression="zip")
        if not set(COLUMNS).issubset(raw.columns):
            raise RuntimeError(f"unexpected kline schema: {path}: {list(raw.columns)}")
        raw = raw.loc[:, COLUMNS]
    else:
        raw.columns = COLUMNS
        first = str(raw.iloc[0]["open_time"])
        if not first.lstrip("-").isdigit():
            raw = raw.iloc[1:].copy()
    for name in ("open", "high", "low", "close", "volume"):
        raw[name] = pd.to_numeric(raw[name], errors="raise")
    raw["time"] = _epoch(raw["close_time"]).dt.floor("min")
    return raw.set_index("time").sort_index()


def _events(path: Path) -> tuple[dict[tuple[str, int], dict[str, Any]], list[dict[str, Any]]]:
    setups: dict[tuple[str, int], dict[str, Any]] = {}
    transitions: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        item = json.loads(line)
        kind = item.get("event_type")
        details = item.get("details") or {}
        if kind == "TRAPPED_BUILD_SETUP_DETECTED":
            setups[(str(details["symbol"]), int(item["ts_event"]))] = item
        elif kind == "TRAPPED_BUILD_STATE_TRANSITION":
            transitions.append(item)
    return setups, transitions


def _feature_row(source_root: Path, symbol: str, ts: int) -> dict[str, float]:
    path = source_root / symbol / "features.csv.gz"
    frame = pd.read_csv(path, compression="infer")
    times = pd.to_numeric(frame["observed_time_ns"], errors="raise").astype("int64")
    selected = frame.loc[times == ts]
    if len(selected) != 1:
        raise RuntimeError(f"expected one feature row for {symbol} {ts}, got {len(selected)}")
    row = selected.iloc[0]
    result: dict[str, float] = {}
    for name in (
        "flow_open_10s", "flow_15s", "flow_60s", "flow_3m",
        "efficiency_60s", "absorption_60s", "oi_change_5m",
        "oi_change_15m", "oi_change_30m", "premium_index",
        "depth_imbalance_1", "depth_imbalance_2",
    ):
        value = float(row.get(name, math.nan))
        result[name] = value if math.isfinite(value) else math.nan
    return result


def audit(*, events: Path, cache: Path, source_root: Path, output: Path) -> dict[str, Any]:
    setups, transitions = _events(events)
    rows: list[dict[str, Any]] = []
    price_cache: dict[tuple[str, str], pd.DataFrame] = {}

    for transition in transitions:
        details = transition["details"]
        symbol = str(details["symbol"])
        setup_ts = int(details["setup_detected_ts"])
        setup_item = setups[(symbol, setup_ts)]
        setup = setup_item["details"]
        diagnostic = setup["diagnostics"]

        response_hold = float(diagnostic["response_close_from_boundary_atr"])
        # The defining repair: prior value was actually re-entered. A weak wick
        # close which remains outside is not a failed attack.
        if response_hold >= 0.0:
            continue

        transition_ts = int(transition["ts_event"])
        transition_minute = pd.Timestamp(transition_ts, unit="ns", tz="UTC").floor("min")
        day = transition_minute.date().isoformat()
        key = (symbol, day)
        if key not in price_cache:
            archive = cache / "klines" / f"{symbol}-1m-{day}.zip"
            if not archive.is_file():
                raise RuntimeError(f"missing owned kline archive: {archive}")
            price_cache[key] = _read_kline(archive)
        frame = price_cache[key]
        if transition_minute not in frame.index:
            raise RuntimeError(f"transition minute absent: {symbol} {transition_minute}")

        detected_minute = pd.Timestamp(setup_ts, unit="ns", tz="UTC").floor("min")
        leg = frame.loc[detected_minute:transition_minute]
        current = frame.loc[transition_minute]
        side = int(setup["attack_side"])
        boundary = float(setup["boundary"])
        attack_extreme = float(setup["attack_extreme"])
        atr = float(diagnostic["atr"])
        feature = _feature_row(source_root, symbol, transition_ts)
        current_flow = side * feature["flow_60s"]
        reaccepted_boundary = side * (float(current["close"]) - boundary) > 0.0
        reaccepted_extreme = side * (float(current["close"]) - attack_extreme) >= 0.0

        # A new continuation leg is anchored to the last completed micro-balance
        # before the reacceptance close.  Entry, invalidation and target are all
        # measured from this new leg; the old broad sweep is not reused as stop.
        prior = leg.iloc[:-1].tail(2)
        if prior.empty:
            continue
        micro_boundary = (
            float(prior["high"].max()) if side > 0 else float(prior["low"].min())
        )
        local_extreme = (
            float(leg["low"].min()) if side > 0 else float(leg["high"].max())
        )
        stop = (
            min(local_extreme, boundary - 0.10 * atr)
            if side > 0
            else max(local_extreme, boundary + 0.10 * atr)
        )
        context_range = float(setup["context_high"]) - float(setup["context_low"])
        objective = boundary + side * context_range
        risk = side * (micro_boundary - stop)
        reward = side * (objective - micro_boundary)
        gross_r_space = reward / risk if risk > 0.0 and reward > 0.0 else math.nan

        future = frame.loc[transition_minute:].iloc[1:241]
        record: dict[str, Any] = {
            "symbol": symbol,
            "setup_time_utc": pd.Timestamp(setup_ts, unit="ns", tz="UTC").isoformat(),
            "transition_time_utc": pd.Timestamp(transition_ts, unit="ns", tz="UTC").isoformat(),
            "attack_side": side,
            "boundary": boundary,
            "attack_extreme": attack_extreme,
            "response_hold_atr": response_hold,
            "transition_close": float(current["close"]),
            "reaccepted_boundary": reaccepted_boundary,
            "reaccepted_attack_extreme": reaccepted_extreme,
            "current_flow_alignment": current_flow,
            "current_oi_change_15m": feature["oi_change_15m"],
            "current_absorption_60s": feature["absorption_60s"],
            "micro_boundary": micro_boundary,
            "local_stop": stop,
            "natural_objective": objective,
            "gross_structural_r_space": gross_r_space,
            "same_leg_geometry_valid": bool(risk > 0.0 and reward > 0.0),
        }
        for horizon in HORIZONS:
            selected = future.iloc[:horizon]
            if selected.empty:
                record[f"close_return_{horizon}m"] = math.nan
                record[f"mfe_{horizon}m"] = math.nan
                record[f"mae_{horizon}m"] = math.nan
                continue
            final = float(selected.iloc[-1]["close"])
            record[f"close_return_{horizon}m"] = side * (final / micro_boundary - 1.0)
            if side > 0:
                record[f"mfe_{horizon}m"] = float(selected["high"].max()) / micro_boundary - 1.0
                record[f"mae_{horizon}m"] = float(selected["low"].min()) / micro_boundary - 1.0
            else:
                record[f"mfe_{horizon}m"] = 1.0 - float(selected["low"].min()) / micro_boundary
                record[f"mae_{horizon}m"] = 1.0 - float(selected["high"].max()) / micro_boundary
        rows.append(record)

    output.mkdir(parents=True, exist_ok=True)
    frame = pd.DataFrame(rows)
    frame.to_csv(output / "strict_failed_attack_reacceptance.csv", index=False)
    result = {
        "schema": "candidate-39-v3-strict-failure-reacceptance-audit-v1",
        "claim_scope": "EXPOSED_DEVELOPMENT_STRUCTURAL_PATH_AUDIT_NO_ACCOUNT_CLAIM",
        "strict_failed_attack_events": len(rows),
        "boundary_reaccepted": sum(bool(row["reaccepted_boundary"]) for row in rows),
        "attack_extreme_reaccepted": sum(bool(row["reaccepted_attack_extreme"]) for row in rows),
        "flow_aligned_at_reacceptance": sum(float(row["current_flow_alignment"]) > 0.0 for row in rows),
        "same_leg_geometry_valid": sum(bool(row["same_leg_geometry_valid"]) for row in rows),
        "events": rows,
        "decision": (
            "PROMOTE_REACCEPTANCE_AS_DISTINCT_DEVELOPMENT_HYPOTHESIS"
            if rows and any(
                row["reaccepted_boundary"]
                and row["current_flow_alignment"] > 0.0
                and row["same_leg_geometry_valid"]
                for row in rows
            )
            else "DISCARD_TRAPPED_BUILD_LINEAGE"
        ),
        "success_claim": False,
    }
    (output / "summary.json").write_text(
        json.dumps(result, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--events", type=Path, required=True)
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(audit(
        events=args.events,
        cache=args.cache,
        source_root=args.source_root,
        output=args.output,
    ), indent=2, sort_keys=True, allow_nan=False))


if __name__ == "__main__":
    main()
