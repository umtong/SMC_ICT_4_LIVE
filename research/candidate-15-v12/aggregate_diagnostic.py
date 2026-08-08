#!/usr/bin/env python3
"""Aggregate Candidate 15 V12 causal response artifacts.

This remains a mechanism diagnostic. It never converts labeled future returns
into a synthetic account or a success claim. A route may advance only to an
actual NautilusTrader execution test.
"""
from __future__ import annotations

import argparse
import json
from math import sqrt
from pathlib import Path
import statistics
from typing import Any, Iterable

SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT")
INTERVALS = ("E01", "E02", "E03", "E04", "E05", "E06")
REGIMES = ("DIFFUSE", "CONCENTRATED")
MECHANISMS = ("SPILLOVER_POSITIVE_BETA", "SEESAW_NEGATIVE_BETA")


def _mechanism(row: dict[str, Any]) -> str:
    factor_sign = 1 if float(row["factor"]) > 0.0 else -1
    return (
        "SPILLOVER_POSITIVE_BETA"
        if int(row["direction"]) == factor_sign
        else "SEESAW_NEGATIVE_BETA"
    )


def _wilson(successes: int, total: int, z: float = 1.959963984540054) -> tuple[float, float]:
    if total <= 0:
        return 0.0, 0.0
    p = successes / total
    denominator = 1.0 + z * z / total
    center = (p + z * z / (2.0 * total)) / denominator
    margin = z * sqrt((p * (1.0 - p) / total) + z * z / (4.0 * total * total)) / denominator
    return max(0.0, center - margin), min(1.0, center + margin)


def _summary(items: Iterable[dict[str, Any]]) -> dict[str, Any]:
    rows = list(items)
    if not rows:
        return {
            "forecasts": 0,
            "directional_hits": 0,
            "directional_hit_rate": 0.0,
            "directional_hit_wilson_95_low": 0.0,
            "directional_hit_wilson_95_high": 0.0,
            "mean_directional_response_10m_bps": 0.0,
            "median_directional_response_10m_bps": 0.0,
            "mean_after_8bps_bps": 0.0,
            "mean_after_12bps_bps": 0.0,
            "mean_after_16bps_bps": 0.0,
            "positive_after_8bps_rate": 0.0,
            "positive_after_12bps_rate": 0.0,
            "positive_after_16bps_rate": 0.0,
            "median_mfe_10m_bps": 0.0,
            "median_mae_10m_bps": 0.0,
            "active_intervals": 0,
        }
    directional = [float(row["directional_response_10m"]) for row in rows]
    mfe = [float(row["mfe_10m"]) for row in rows]
    mae = [float(row["mae_10m"]) for row in rows]
    hits = sum(value > 0.0 for value in directional)
    low, high = _wilson(hits, len(rows))
    intervals = {str(row["interval"]) for row in rows}

    def after_cost(rate: float) -> list[float]:
        return [value - rate for value in directional]

    after8 = after_cost(0.0008)
    after12 = after_cost(0.0012)
    after16 = after_cost(0.0016)
    return {
        "forecasts": len(rows),
        "directional_hits": hits,
        "directional_hit_rate": hits / len(rows),
        "directional_hit_wilson_95_low": low,
        "directional_hit_wilson_95_high": high,
        "mean_directional_response_10m_bps": statistics.fmean(directional) * 10000.0,
        "median_directional_response_10m_bps": statistics.median(directional) * 10000.0,
        "mean_after_8bps_bps": statistics.fmean(after8) * 10000.0,
        "mean_after_12bps_bps": statistics.fmean(after12) * 10000.0,
        "mean_after_16bps_bps": statistics.fmean(after16) * 10000.0,
        "positive_after_8bps_rate": sum(value > 0.0 for value in after8) / len(rows),
        "positive_after_12bps_rate": sum(value > 0.0 for value in after12) / len(rows),
        "positive_after_16bps_rate": sum(value > 0.0 for value in after16) / len(rows),
        "median_mfe_10m_bps": statistics.median(mfe) * 10000.0,
        "median_mae_10m_bps": statistics.median(mae) * 10000.0,
        "active_intervals": len(intervals),
    }


def aggregate(artifacts: Path, output: Path) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    interval_summaries: dict[str, Any] = {}
    for interval in INTERVALS:
        root = artifacts / f"candidate15-v12-diagnostic-{interval}"
        summary_path = root / "summary.json"
        forecasts_path = root / "forecasts.json"
        if not summary_path.exists() or not forecasts_path.exists():
            raise RuntimeError(f"missing complete diagnostic artifact for {interval}: {root}")
        interval_summaries[interval] = json.loads(summary_path.read_text(encoding="utf-8"))
        rows.extend(json.loads(forecasts_path.read_text(encoding="utf-8")))

    overall = _summary(rows)
    by_interval = {
        interval: _summary(row for row in rows if row["interval"] == interval)
        for interval in INTERVALS
    }
    by_regime = {
        regime: _summary(row for row in rows if row["regime"] == regime)
        for regime in REGIMES
    }
    by_mechanism = {
        mechanism: _summary(row for row in rows if _mechanism(row) == mechanism)
        for mechanism in MECHANISMS
    }
    by_regime_mechanism: dict[str, dict[str, Any]] = {}
    for regime in REGIMES:
        for mechanism in MECHANISMS:
            key = f"{regime}::{mechanism}"
            by_regime_mechanism[key] = _summary(
                row
                for row in rows
                if row["regime"] == regime and _mechanism(row) == mechanism
            )
    by_symbol = {
        symbol: _summary(row for row in rows if row["receiver"] == symbol)
        for symbol in SYMBOLS
    }

    eligible: list[tuple[str, dict[str, Any]]] = []
    for key, evidence in by_regime_mechanism.items():
        if (
            evidence["forecasts"] >= 24
            and evidence["active_intervals"] >= 4
            and evidence["directional_hit_rate"] >= 0.55
            and evidence["mean_after_16bps_bps"] > 0.0
            and evidence["median_mfe_10m_bps"] > abs(evidence["median_mae_10m_bps"])
        ):
            eligible.append((key, evidence))
    eligible.sort(
        key=lambda item: (
            item[1]["mean_after_16bps_bps"],
            item[1]["directional_hit_rate"],
            item[1]["active_intervals"],
            item[1]["forecasts"],
        ),
        reverse=True,
    )
    selected = (
        {"route": eligible[0][0], "evidence": eligible[0][1]}
        if eligible
        else None
    )
    classification = (
        "V12_MECHANISM_SURVIVED_BUILD_NAUTILUS_EXECUTION"
        if selected is not None
        else "V12_MECHANISM_REJECTED_OR_UNDERPOWERED"
    )
    aggregate_result = {
        "schema": "candidate-15-v12-causal-cross-predictive-aggregate-v2",
        "classification": classification,
        "diagnostic_only_not_account_backtest": True,
        "forecast_rows": len(rows),
        "overall": overall,
        "by_interval": by_interval,
        "by_regime": by_regime,
        "by_mechanism": by_mechanism,
        "by_regime_mechanism": by_regime_mechanism,
        "by_symbol": by_symbol,
        "selected_for_nautilus_implementation": selected,
        "advancement_contract": {
            "minimum_forecasts": 24,
            "minimum_active_intervals": 4,
            "minimum_directional_hit_rate": 0.55,
            "minimum_mean_after_16bps_bps_exclusive": 0.0,
            "require_median_mfe_greater_than_absolute_median_mae": True,
            "selection_data": "E01-E06_EXPOSED_DEVELOPMENT_ONLY",
        },
        "interval_summaries": interval_summaries,
        "claim": "NO_PNL_OR_SUCCESS_CLAIM; CONDITIONAL_RESPONSE_MECHANISM_ONLY",
    }
    output.mkdir(parents=True, exist_ok=True)
    (output / "aggregate.json").write_text(
        json.dumps(aggregate_result, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    lines = [
        "# Candidate 15 V12 causal cross-predictive diagnostic",
        "",
        f"**{classification}**",
        "",
        f"- forecasts: `{len(rows)}`",
        f"- directional hit rate: `{overall['directional_hit_rate']:.6f}`",
        f"- mean 10m directional response: `{overall['mean_directional_response_10m_bps']:.4f}` bps",
        f"- mean after 16 bps urgent round trip: `{overall['mean_after_16bps_bps']:.4f}` bps",
        "",
        "## Regime × learned response sign",
    ]
    for key, evidence in by_regime_mechanism.items():
        lines.append(
            f"- {key}: n={evidence['forecasts']}, intervals={evidence['active_intervals']}, "
            f"hit={evidence['directional_hit_rate']:.4f}, "
            f"Wilson-low={evidence['directional_hit_wilson_95_low']:.4f}, "
            f"after16={evidence['mean_after_16bps_bps']:.4f} bps, "
            f"MFE/MAE={evidence['median_mfe_10m_bps']:.4f}/"
            f"{evidence['median_mae_10m_bps']:.4f} bps"
        )
    lines.extend(
        [
            "",
            "This is a causal future-response diagnostic, not a trade, portfolio, or NAV result.",
        ]
    )
    (output / "DIAGNOSTIC.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return aggregate_result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifacts", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = aggregate(args.artifacts, args.output)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
