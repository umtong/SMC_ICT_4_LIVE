#!/usr/bin/env python3
"""Aggregate Candidate 15 V12B distributed-lag diagnostic artifacts.

The aggregate decides only whether a frozen causal forecast mechanism deserves
an actual NautilusTrader execution test. It does not construct trades or NAV.
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
RELATIONS = ("SPILLOVER", "SEESAW")


def _relation(row: dict[str, Any]) -> str:
    peer = float(row["peer_factor_1m"])
    if peer == 0.0:
        return "UNRESOLVED"
    peer_direction = 1 if peer > 0.0 else -1
    return "SPILLOVER" if int(row["direction"]) == peer_direction else "SEESAW"


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
            "active_intervals": 0,
            "positive_cost_intervals": 0,
            "directional_hits": 0,
            "directional_hit_rate": 0.0,
            "directional_hit_wilson_95_low": 0.0,
            "directional_hit_wilson_95_high": 0.0,
            "mean_directional_response_10m_bps": 0.0,
            "median_directional_response_10m_bps": 0.0,
            "mean_after_16bps_bps": 0.0,
            "median_after_16bps_bps": 0.0,
            "mean_after_16bps_t_stat": 0.0,
            "median_mfe_10m_bps": 0.0,
            "median_mae_10m_bps": 0.0,
            "maximum_positive_interval_share": 0.0,
        }
    directional = [float(row["directional_response_10m"]) for row in rows]
    after = [float(row["directional_response_after_16bps"]) for row in rows]
    mfe = [float(row["mfe_10m"]) for row in rows]
    mae = [float(row["mae_10m"]) for row in rows]
    hits = sum(value > 0.0 for value in directional)
    low, high = _wilson(hits, len(rows))
    interval_values: dict[str, list[float]] = {}
    for row, value in zip(rows, after, strict=True):
        interval_values.setdefault(str(row["interval"]), []).append(value)
    interval_sums = {
        interval: sum(values)
        for interval, values in interval_values.items()
    }
    positive_sums = [value for value in interval_sums.values() if value > 0.0]
    positive_total = sum(positive_sums)
    maximum_positive_share = (
        max(positive_sums) / positive_total
        if positive_total > 0.0 and positive_sums
        else 0.0
    )
    mean_after = statistics.fmean(after)
    if len(after) > 1:
        deviation = statistics.stdev(after)
        t_stat = mean_after / (deviation / sqrt(len(after))) if deviation > 0.0 else 0.0
    else:
        t_stat = 0.0
    return {
        "forecasts": len(rows),
        "active_intervals": len(interval_values),
        "positive_cost_intervals": sum(
            statistics.fmean(values) > 0.0 for values in interval_values.values()
        ),
        "directional_hits": hits,
        "directional_hit_rate": hits / len(rows),
        "directional_hit_wilson_95_low": low,
        "directional_hit_wilson_95_high": high,
        "mean_directional_response_10m_bps": statistics.fmean(directional) * 10000.0,
        "median_directional_response_10m_bps": statistics.median(directional) * 10000.0,
        "mean_after_16bps_bps": mean_after * 10000.0,
        "median_after_16bps_bps": statistics.median(after) * 10000.0,
        "mean_after_16bps_t_stat": t_stat,
        "median_mfe_10m_bps": statistics.median(mfe) * 10000.0,
        "median_mae_10m_bps": statistics.median(mae) * 10000.0,
        "maximum_positive_interval_share": maximum_positive_share,
    }


def _passes(evidence: dict[str, Any]) -> bool:
    return bool(
        evidence["forecasts"] >= 30
        and evidence["active_intervals"] >= 4
        and evidence["positive_cost_intervals"] >= 4
        and evidence["directional_hit_rate"] >= 0.56
        and evidence["directional_hit_wilson_95_low"] > 0.50
        and evidence["mean_after_16bps_bps"] > 0.0
        and evidence["median_after_16bps_bps"] > 0.0
        and evidence["mean_after_16bps_t_stat"] >= 1.645
        and evidence["median_mfe_10m_bps"] > abs(evidence["median_mae_10m_bps"])
        and evidence["maximum_positive_interval_share"] <= 0.60
    )


def aggregate(artifacts: Path, output: Path) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    interval_summaries: dict[str, Any] = {}
    for interval in INTERVALS:
        root = artifacts / f"candidate15-v12b-distributed-lag-{interval}"
        summary_path = root / "summary.json"
        forecasts_path = root / "forecasts.json"
        if not summary_path.exists() or not forecasts_path.exists():
            raise RuntimeError(f"missing V12B artifact for {interval}: {root}")
        interval_summaries[interval] = json.loads(summary_path.read_text(encoding="utf-8"))
        rows.extend(json.loads(forecasts_path.read_text(encoding="utf-8")))

    routes: dict[str, dict[str, Any]] = {"ALL": _summary(rows)}
    routes.update({
        f"SYMBOL::{symbol}": _summary(row for row in rows if row["receiver"] == symbol)
        for symbol in SYMBOLS
    })
    routes.update({
        f"RELATION::{relation}": _summary(row for row in rows if _relation(row) == relation)
        for relation in RELATIONS
    })
    eligible = [(route, evidence) for route, evidence in routes.items() if _passes(evidence)]
    eligible.sort(
        key=lambda item: (
            item[1]["mean_after_16bps_bps"],
            item[1]["mean_after_16bps_t_stat"],
            item[1]["directional_hit_rate"],
            item[1]["forecasts"],
        ),
        reverse=True,
    )
    selected = {"route": eligible[0][0], "evidence": eligible[0][1]} if eligible else None
    classification = (
        "V12B_DISTRIBUTED_LAG_SURVIVED_BUILD_NAUTILUS_EXECUTION"
        if selected is not None
        else "V12B_DISTRIBUTED_LAG_REJECTED_OR_UNDERPOWERED"
    )
    result = {
        "schema": "candidate-15-v12b-distributed-lag-aggregate-v1",
        "classification": classification,
        "diagnostic_only_not_account_backtest": True,
        "forecast_rows": len(rows),
        "routes": routes,
        "selected_for_nautilus_implementation": selected,
        "advancement_contract": {
            "minimum_forecasts": 30,
            "minimum_active_intervals": 4,
            "minimum_positive_cost_intervals": 4,
            "minimum_directional_hit_rate": 0.56,
            "minimum_wilson_95_low_exclusive": 0.50,
            "minimum_mean_after_16bps_bps_exclusive": 0.0,
            "minimum_median_after_16bps_bps_exclusive": 0.0,
            "minimum_mean_after_16bps_t_stat": 1.645,
            "require_median_mfe_gt_abs_median_mae": True,
            "maximum_positive_interval_share": 0.60,
            "eligible_route_families": ["ALL", "SYMBOL", "RELATION"],
            "selection_data": "E01_E06_EXPOSED_DEVELOPMENT_ONLY"
        },
        "interval_summaries": interval_summaries,
        "claim": "NO_TRADE_PNL_OR_NAV_CLAIM; FORECAST_MECHANISM_ONLY"
    }
    output.mkdir(parents=True, exist_ok=True)
    (output / "aggregate.json").write_text(json.dumps(result, indent=2, sort_keys=True), encoding="utf-8")
    lines = [
        "# Candidate 15 V12B distributed-lag diagnostic",
        "",
        f"**{classification}**",
        "",
        f"- globally owned cost-clearing forecasts: `{len(rows)}`",
        "",
        "## Predeclared route evidence"
    ]
    for route, evidence in routes.items():
        lines.append(
            f"- {route}: n={evidence['forecasts']}, intervals={evidence['active_intervals']}, "
            f"positive_intervals={evidence['positive_cost_intervals']}, "
            f"hit={evidence['directional_hit_rate']:.4f}, "
            f"Wilson-low={evidence['directional_hit_wilson_95_low']:.4f}, "
            f"mean_after16={evidence['mean_after_16bps_bps']:.4f} bps, "
            f"median_after16={evidence['median_after_16bps_bps']:.4f} bps, "
            f"t={evidence['mean_after_16bps_t_stat']:.3f}, "
            f"MFE/MAE={evidence['median_mfe_10m_bps']:.4f}/"
            f"{evidence['median_mae_10m_bps']:.4f} bps, "
            f"max_positive_share={evidence['maximum_positive_interval_share']:.4f}"
        )
    lines.extend([
        "",
        "This is a causal forecast diagnostic, not a Nautilus trade or continuous-account result."
    ])
    (output / "DIAGNOSTIC.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifacts", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(aggregate(args.artifacts, args.output), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
