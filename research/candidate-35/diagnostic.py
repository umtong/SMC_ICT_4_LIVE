#!/usr/bin/env python3
"""Seven-day, no-PnL structural diagnostic for Candidate 35.

This program deliberately does not simulate orders, positions, account equity or
strategy PnL.  Its only purpose is to answer the cheapest questions before any
execution adapter or longer backtest is built:

* Are all four same-minute observations causally aligned?
* Does the router actually reach continuation, reversal and unresolved states?
* Are selected entry/stop/objective geometries internally valid?
* Is opportunity frequency non-zero without counting every symbol separately?
* Which pre-trade gates reject the most episodes?

A positive diagnostic is not performance evidence.  It merely decides whether a
short NautilusTrader execution test is worth implementing.
"""
from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import asdict
from hashlib import sha256
import json
import math
from pathlib import Path
from statistics import median
from typing import Any

import pandas as pd

from router import (
    BarObservation,
    FeatureObservation,
    RouteConfig,
    RouteDecision,
    route_universe,
)


MINUTE_NS = 60_000_000_000
SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT")


class DiagnosticError(RuntimeError):
    """Raised when the diagnostic input cannot support a causal comparison."""


def _sha256(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        while block := stream.read(1 << 20):
            digest.update(block)
    return digest.hexdigest()


def _bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y", "t"}


def _float(value: Any, default: float = math.nan) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def _percentiles(values: list[float]) -> dict[str, float | None]:
    clean = sorted(value for value in values if math.isfinite(value))
    if not clean:
        return {"min": None, "p25": None, "median": None, "p75": None, "max": None}

    def pick(fraction: float) -> float:
        index = fraction * (len(clean) - 1)
        low = int(math.floor(index))
        high = int(math.ceil(index))
        if low == high:
            return float(clean[low])
        weight = index - low
        return float(clean[low] * (1.0 - weight) + clean[high] * weight)

    return {
        "min": float(clean[0]),
        "p25": pick(0.25),
        "median": float(median(clean)),
        "p75": pick(0.75),
        "max": float(clean[-1]),
    }


def _route_config(config: dict[str, Any]) -> RouteConfig:
    values = asdict(RouteConfig())
    for key, value in config.get("strategy", {}).items():
        if key in values:
            values[key] = value
    return RouteConfig(**values)


def _manifest_map(input_root: Path) -> dict[str, tuple[dict[str, Any], Path]]:
    result: dict[str, tuple[dict[str, Any], Path]] = {}
    for manifest_path in sorted(input_root.rglob("chunk_manifest.json")):
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        symbol = str(manifest.get("symbol", ""))
        if symbol not in SYMBOLS:
            continue
        if symbol in result:
            raise DiagnosticError(f"multiple chunks found for {symbol}")
        directory = manifest_path.parent
        files = manifest.get("files", {})
        for filename in ("klines.csv.gz", "features.csv.gz"):
            path = directory / filename
            expected = files.get(filename, {}).get("sha256")
            if not path.is_file() or not expected:
                raise DiagnosticError(f"{symbol} manifest does not own {filename}")
            actual = _sha256(path)
            if actual != expected:
                raise DiagnosticError(
                    f"{symbol} {filename} sha256 mismatch: {actual} != {expected}",
                )
        result[symbol] = (manifest, directory)
    missing = [symbol for symbol in SYMBOLS if symbol not in result]
    if missing:
        raise DiagnosticError(f"missing symbol chunks: {missing}")
    return result


def _load_symbol(
    *,
    symbol: str,
    manifest: dict[str, Any],
    directory: Path,
) -> tuple[list[BarObservation], pd.DataFrame, list[int]]:
    klines = pd.read_csv(directory / "klines.csv.gz", compression="gzip")
    features = pd.read_csv(directory / "features.csv.gz", compression="gzip")
    required_kline = {
        "close_time_dt",
        "open",
        "high",
        "low",
        "close",
        "volume",
    }
    required_feature = {"observed_time_ns", "feature_ready"}
    if not required_kline.issubset(klines.columns):
        raise DiagnosticError(
            f"{symbol} kline schema missing {sorted(required_kline - set(klines.columns))}",
        )
    if not required_feature.issubset(features.columns):
        raise DiagnosticError(
            f"{symbol} feature schema missing {sorted(required_feature - set(features.columns))}",
        )
    if len(klines) != int(manifest["rows"]) or len(features) != int(manifest["rows"]):
        raise DiagnosticError(
            f"{symbol} row count mismatch: kline={len(klines)} "
            f"feature={len(features)} manifest={manifest['rows']}",
        )

    kline_times = [
        int(pd.Timestamp(value).value)
        for value in pd.to_datetime(klines["close_time_dt"], utc=True, errors="raise")
    ]
    feature_times = (
        pd.to_numeric(features["observed_time_ns"], errors="raise")
        .astype("int64")
        .tolist()
    )
    if kline_times != feature_times:
        raise DiagnosticError(f"{symbol} kline/feature observation clocks differ")
    if any(current <= previous for previous, current in zip(kline_times, kline_times[1:])):
        raise DiagnosticError(f"{symbol} observations are not strictly monotonic")

    bars = [
        BarObservation(
            ts_event=timestamp,
            open=float(row.open),
            high=float(row.high),
            low=float(row.low),
            close=float(row.close),
            volume=float(row.volume),
        )
        for timestamp, row in zip(
            kline_times,
            klines[["open", "high", "low", "close", "volume"]].itertuples(index=False),
        )
    ]
    return bars, features.reset_index(drop=True), kline_times


def _feature_at(
    frame: pd.DataFrame,
    index: int,
    *,
    bar_ts: int,
    max_age_seconds: float,
) -> FeatureObservation:
    row = frame.iloc[index]
    observed = int(row["observed_time_ns"])
    if observed > bar_ts:
        raise DiagnosticError("future feature observation reached router")
    age_seconds = (bar_ts - observed) / 1_000_000_000
    ready = _bool(row["feature_ready"]) and 0.0 <= age_seconds <= max_age_seconds
    # Candidate 29's causal source publishes premium changes rather than a
    # pre-standardized z-score.  Use a purely backward 240-minute z-score when
    # enough past observations exist; otherwise leave the optional crowd input
    # neutral.  The current row is included because it is known at bar close.
    premium_z = math.nan
    premium_column = None
    for name in ("premium_change_15m", "premium_change_5m", "premium_index"):
        if name in frame.columns:
            premium_column = name
            break
    if premium_column is not None:
        start = max(0, index - 239)
        history = pd.to_numeric(
            frame.loc[start:index, premium_column],
            errors="coerce",
        ).dropna()
        if len(history) >= 60:
            std = float(history.std(ddof=0))
            current = _float(row.get(premium_column))
            if math.isfinite(current) and math.isfinite(std) and std > 0.0:
                premium_z = (current - float(history.mean())) / std

    return FeatureObservation(
        observed_time_ns=observed,
        ready=ready,
        flow_open_10s=_float(row.get("flow_open_10s")),
        notional_open_10s_burst=_float(row.get("notional_open_10s_burst")),
        flow_60s=_float(row.get("flow_60s")),
        efficiency_60s=_float(row.get("efficiency_60s")),
        oi_change_15m=_float(row.get("oi_change_15m")),
        premium_z=premium_z,
    )


def _geometry(decision: RouteDecision) -> tuple[bool, float]:
    if not decision.actionable:
        return True, math.nan
    side = decision.side
    risk = side * (decision.entry_reference - decision.stop_reference)
    reward = side * (decision.objective_reference - decision.entry_reference)
    valid = (
        math.isfinite(risk)
        and math.isfinite(reward)
        and risk > 0.0
        and reward > 0.0
    )
    return valid, reward / risk if valid else math.nan


def diagnose(
    *,
    input_root: Path,
    config_path: Path,
    output: Path,
) -> dict[str, Any]:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    if tuple(config.get("symbols", ())) != SYMBOLS:
        raise DiagnosticError(
            f"diagnostic requires exact universe {SYMBOLS}, got {config.get('symbols')}",
        )
    route_config = _route_config(config)
    max_age_seconds = float(config["strategy"]["feature_max_age_seconds"])

    manifests = _manifest_map(input_root)
    bars_by_symbol: dict[str, list[BarObservation]] = {}
    features_by_symbol: dict[str, pd.DataFrame] = {}
    times_by_symbol: dict[str, list[int]] = {}
    manifest_summary: dict[str, Any] = {}
    for symbol in SYMBOLS:
        manifest, directory = manifests[symbol]
        bars, features, times = _load_symbol(
            symbol=symbol,
            manifest=manifest,
            directory=directory,
        )
        bars_by_symbol[symbol] = bars
        features_by_symbol[symbol] = features
        times_by_symbol[symbol] = times
        manifest_summary[symbol] = {
            "core_start": manifest["core_start"],
            "core_end": manifest["core_end"],
            "rows": manifest["rows"],
            "feature_ready_rows": manifest["feature_ready_rows"],
        }

    reference_times = times_by_symbol["BTCUSDT"]
    for symbol in SYMBOLS[1:]:
        if times_by_symbol[symbol] != reference_times:
            raise DiagnosticError(
                f"{symbol} observation clock does not exactly match BTCUSDT",
            )

    required = (
        route_config.atr_period
        + route_config.prior_bars
        + route_config.response_bars
        + 1
    )
    episode_count = 0
    selected_count = 0
    global_ambiguity_count = 0
    selected_states: Counter[str] = Counter()
    selected_symbols: Counter[str] = Counter()
    symbol_states: Counter[str] = Counter()
    unresolved_reasons: Counter[str] = Counter()
    selected_scores: list[float] = []
    selected_rr: list[float] = []
    invalid_geometry = 0
    stale_feature_rows = 0
    decision_rows: list[dict[str, Any]] = []
    selected_timestamps: list[int] = []

    for index, timestamp in enumerate(reference_times):
        if index + 1 < required:
            continue
        moment = pd.Timestamp(timestamp, unit="ns", tz="UTC")
        # The first three completed one-minute bars of a quarter hour end at
        # minute 02/17/32/47.  Evaluate exactly once, after all four symbols are
        # available for that same completed minute.
        if moment.minute % 15 != route_config.response_bars - 1:
            continue
        episode_count += 1
        current_features: dict[str, FeatureObservation] = {}
        for symbol in SYMBOLS:
            feature = _feature_at(
                features_by_symbol[symbol],
                index,
                bar_ts=timestamp,
                max_age_seconds=max_age_seconds,
            )
            current_features[symbol] = feature
            if not feature.ready:
                stale_feature_rows += 1

        winner, decisions = route_universe(
            bars_by_symbol={
                symbol: bars_by_symbol[symbol][: index + 1]
                for symbol in SYMBOLS
            },
            features_by_symbol=current_features,
            config=route_config,
        )
        actionable_here = sum(decision.actionable for decision in decisions.values())
        if winner is None and actionable_here:
            global_ambiguity_count += 1

        for symbol, decision in decisions.items():
            symbol_states[f"{symbol}:{decision.state}"] += 1
            if decision.state == "UNRESOLVED":
                reason = decision.reasons[0] if decision.reasons else "UNSPECIFIED"
                unresolved_reasons[f"{symbol}:{reason}"] += 1
            valid, rr = _geometry(decision)
            if not valid:
                invalid_geometry += 1
            decision_rows.append(
                {
                    "episode_time_utc": moment.isoformat(),
                    "episode_ts": timestamp,
                    "symbol": symbol,
                    "state": decision.state,
                    "side": decision.side,
                    "score": decision.score,
                    "expected_target_r": decision.expected_target_r,
                    "entry_reference": decision.entry_reference,
                    "stop_reference": decision.stop_reference,
                    "objective_reference": decision.objective_reference,
                    "geometry_valid": valid,
                    "geometry_rr": rr,
                    "selected": winner is not None and winner.symbol == symbol,
                    "reason": decision.reasons[0] if decision.reasons else "",
                },
            )

        if winner is None:
            continue
        selected_count += 1
        selected_timestamps.append(timestamp)
        selected_states[winner.state] += 1
        selected_symbols[winner.symbol] += 1
        selected_scores.append(float(winner.score))
        valid, rr = _geometry(winner)
        if valid:
            selected_rr.append(rr)

    # A conservative opportunity proxy: selected quarter-hours within 60
    # minutes of a previous selection are treated as one causal cluster.  This
    # is diagnostic only and is never reported as final independent trade count.
    conservative_clusters = 0
    last_cluster_ts: int | None = None
    for timestamp in selected_timestamps:
        if last_cluster_ts is None or timestamp - last_cluster_ts >= 60 * MINUTE_NS:
            conservative_clusters += 1
            last_cluster_ts = timestamp

    output.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(decision_rows).to_csv(
        output / "router_decisions.csv",
        index=False,
    )
    result = {
        "schema": "candidate-35-short-router-diagnostic-v1",
        "claim_scope": (
            "STRUCTURAL_DIAGNOSTIC_ONLY_NO_ORDER_SIMULATION_NO_PNL_NO_NAV_CLAIM"
        ),
        "input": manifest_summary,
        "route_config": asdict(route_config),
        "episodes_evaluated": episode_count,
        "selected_universe_routes": selected_count,
        "selected_route_rate": (
            selected_count / episode_count if episode_count else 0.0
        ),
        "conservative_60m_causal_clusters": conservative_clusters,
        "global_ambiguity_rejections": global_ambiguity_count,
        "selected_states": dict(sorted(selected_states.items())),
        "selected_symbols": dict(sorted(selected_symbols.items())),
        "all_symbol_states": dict(sorted(symbol_states.items())),
        "top_unresolved_reasons": dict(unresolved_reasons.most_common(20)),
        "selected_score_distribution": _percentiles(selected_scores),
        "selected_geometry_rr_distribution": _percentiles(selected_rr),
        "invalid_geometry_decisions": invalid_geometry,
        "feature_not_ready_symbol_rows": stale_feature_rows,
        "same_minute_four_symbol_clock": True,
        "future_feature_violations": 0,
        "next_stage_assessment": (
            "DO_NOT_BUILD_EXECUTION_ROUTER_DEAD"
            if selected_count == 0
            else "DO_NOT_BUILD_EXECUTION_INVALID_GEOMETRY"
            if invalid_geometry > 0
            else "ELIGIBLE_FOR_SHORT_NAUTILUS_EXECUTION_DIAGNOSTIC"
        ),
    }
    (output / "router_diagnostic.json").write_text(
        json.dumps(result, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-root", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = diagnose(
        input_root=args.input_root.resolve(),
        config_path=args.config.resolve(),
        output=args.output.resolve(),
    )
    print(json.dumps(result, indent=2, sort_keys=True, allow_nan=False))


if __name__ == "__main__":
    main()
