#!/usr/bin/env python3
"""Development-only OI lifecycle anatomy for the 4h jump specialist.

External liquidation strategies commonly distinguish leveraged position build-up
from position closure.  For an already-completed jump, this script asks whether
the target contract's Binance USD-M open interest fell across the same four-hour
impulse.  Falling OI is treated only as a descriptive liquidation/covering state;
no threshold is fitted and no fresh interval is consumed here.
"""
from __future__ import annotations

from bisect import bisect_right
from collections import defaultdict
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import subprocess
import sys
from typing import Any, Callable

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
ROWS_PATH = (
    HERE
    / "evidence"
    / "jump-taker-alignment-fresh-v1"
    / "source_without_taker_filter"
    / "episode_rows.json"
)
WORK = ROOT / ".work" / "candidate-57-jump-oi-lifecycle-development-v1"
CACHE = ROOT / ".cache" / "candidate-57-jump-oi-lifecycle-development-v1"
METRICS = WORK / "binance_metrics_2026-03-28_2026-04-14.json"
OUT = HERE / "evidence" / "jump-oi-lifecycle-development-v1"
SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT")
MAX_AGE_NS = 10 * 60 * 1_000_000_000
FOUR_HOURS_NS = 4 * 60 * 60 * 1_000_000_000


def dump(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True, allow_nan=False, default=str)
        + "\n",
        encoding="utf-8",
    )


def stats(rows: list[dict[str, Any]]) -> dict[str, Any]:
    values = [
        float(row["exit_net_r"])
        for row in rows
        if row.get("exit_net_r") is not None
        and math.isfinite(float(row["exit_net_r"]))
    ]
    positive = [value for value in values if value > 0.0]
    negative = [value for value in values if value < 0.0]
    return {
        "rows": len(rows),
        "resolved": len(values),
        "wins": len(positive),
        "losses": len(negative),
        "win_rate": len(positive) / len(values) if values else None,
        "sum_r": sum(values),
        "mean_r": sum(values) / len(values) if values else None,
        "profit_factor_r": sum(positive) / -sum(negative) if negative else None,
    }


def download() -> int:
    command = [
        sys.executable,
        str(HERE / "download_binance_metrics_sidecar.py"),
        "--start",
        "2026-03-28",
        "--end",
        "2026-04-14",
        "--output",
        str(METRICS),
        "--cache",
        str(CACHE / "metrics"),
    ]
    return subprocess.run(command, cwd=ROOT, check=False).returncode


def load_metrics() -> tuple[dict[str, list[dict[str, Any]]], dict[str, list[int]]]:
    payload = json.loads(METRICS.read_text(encoding="utf-8"))
    metrics: dict[str, list[dict[str, Any]]] = {}
    times: dict[str, list[int]] = {}
    for symbol in SYMBOLS:
        rows = sorted(
            [
                {
                    **row,
                    "ts_event": int(row["ts_event"]),
                    "sum_open_interest": float(row["sum_open_interest"]),
                }
                for row in (payload.get("symbols") or {}).get(symbol, [])
            ],
            key=lambda row: int(row["ts_event"]),
        )
        if not rows:
            raise RuntimeError(f"missing Binance metrics for {symbol}")
        metrics[symbol] = rows
        times[symbol] = [int(row["ts_event"]) for row in rows]
    return metrics, times


def asof(
    metrics: dict[str, list[dict[str, Any]]],
    times: dict[str, list[int]],
    symbol: str,
    ts_event: int,
) -> dict[str, Any] | None:
    index = bisect_right(times[symbol], int(ts_event)) - 1
    if index < 0:
        return None
    row = metrics[symbol][index]
    age_ns = int(ts_event) - int(row["ts_event"])
    if age_ns < 0 or age_ns > MAX_AGE_NS:
        return None
    return {**row, "age_minutes": age_ns / 60_000_000_000.0}


def enrich(
    source_rows: list[dict[str, Any]],
    metrics: dict[str, list[dict[str, Any]]],
    times: dict[str, list[int]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    output: list[dict[str, Any]] = []
    unresolved: list[dict[str, Any]] = []
    for source in source_rows:
        row = dict(source)
        ts_event = int(row["episode_ts"])
        start_ts = ts_event - FOUR_HOURS_NS
        peer_states: dict[str, Any] = {}
        for symbol in SYMBOLS:
            before = asof(metrics, times, symbol, start_ts)
            after = asof(metrics, times, symbol, ts_event)
            if before is None or after is None:
                peer_states[symbol] = None
                continue
            before_oi = float(before["sum_open_interest"])
            after_oi = float(after["sum_open_interest"])
            change = (after_oi / before_oi - 1.0) if before_oi > 0.0 else None
            peer_states[symbol] = {
                "start_ts": int(before["ts_event"]),
                "end_ts": int(after["ts_event"]),
                "start_age_minutes": float(before["age_minutes"]),
                "end_age_minutes": float(after["age_minutes"]),
                "start_open_interest": before_oi,
                "end_open_interest": after_oi,
                "open_interest_change_fraction": change,
                "open_interest_unwind": bool(change is not None and change < 0.0),
            }
        if any(peer_states[symbol] is None for symbol in SYMBOLS):
            unresolved.append(
                {
                    "candidate_id": row.get("candidate_id"),
                    "symbol": row.get("symbol"),
                    "episode_ts": ts_event,
                    "reason": "STRICT_ASOF_OI_UNRESOLVED",
                }
            )
            continue
        target = peer_states[str(row["symbol"])]
        diagnostics = row.get("diagnostics") or {}
        row.update(
            {
                "event_time_utc": datetime.fromtimestamp(
                    ts_event / 1_000_000_000, tz=timezone.utc
                ).isoformat(),
                "target_oi_change_fraction": target[
                    "open_interest_change_fraction"
                ],
                "target_oi_unwind": bool(target["open_interest_unwind"]),
                "peer_oi_unwind_count": sum(
                    int(peer_states[symbol]["open_interest_unwind"])
                    for symbol in SYMBOLS
                ),
                "peer_oi_states": peer_states,
                "taker_3of4": int(
                    diagnostics.get("jump_taker_aligned_peers", 0) or 0
                )
                >= 3,
                "absolute_z": abs(
                    float(diagnostics.get("causal_zscore", 0.0) or 0.0)
                ),
            }
        )
        output.append(row)
    return output, unresolved


def choose(
    rows: list[dict[str, Any]],
    eligible: Callable[[dict[str, Any]], bool],
    mode: str,
) -> list[dict[str, Any]]:
    grouped: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if eligible(row):
            grouped[int(row["episode_ts"])].append(row)
    selected: list[dict[str, Any]] = []
    for boundary in sorted(grouped):
        candidates = grouped[boundary]
        if mode == "max_z":
            candidates.sort(key=lambda row: (-float(row["absolute_z"]), str(row["symbol"])))
        elif mode == "least_z":
            candidates.sort(key=lambda row: (float(row["absolute_z"]), str(row["symbol"])))
        else:
            raise ValueError(mode)
        selected.append(candidates[0])
    return selected


def main() -> int:
    if not ROWS_PATH.is_file():
        raise RuntimeError(f"source jump anatomy is missing: {ROWS_PATH}")
    OUT.mkdir(parents=True, exist_ok=True)
    WORK.mkdir(parents=True, exist_ok=True)
    CACHE.mkdir(parents=True, exist_ok=True)
    status = download()
    if status != 0:
        return status
    source_rows = json.loads(ROWS_PATH.read_text(encoding="utf-8"))
    metrics, times = load_metrics()
    rows, unresolved = enrich(source_rows, metrics, times)

    groups = {
        "all": rows,
        "target_oi_unwind": [row for row in rows if row["target_oi_unwind"]],
        "target_oi_build_or_flat": [row for row in rows if not row["target_oi_unwind"]],
        "taker_3of4": [row for row in rows if row["taker_3of4"]],
        "oi_unwind_and_taker_3of4": [
            row for row in rows if row["target_oi_unwind"] and row["taker_3of4"]
        ],
        "oi_build_or_flat_and_taker_3of4": [
            row for row in rows if not row["target_oi_unwind"] and row["taker_3of4"]
        ],
    }
    by_side = {
        side: {
            name: stats([row for row in group if int(row["side"]) == sign])
            for name, group in groups.items()
        }
        for side, sign in (("long_reversal", 1), ("short_reversal", -1))
    }
    by_peer_unwind_count = {
        str(count): stats(
            [row for row in rows if int(row["peer_oi_unwind_count"]) == count]
        )
        for count in range(5)
    }
    policies = {
        "source_max_z": choose(rows, lambda row: True, "max_z"),
        "least_z": choose(rows, lambda row: True, "least_z"),
        "oi_unwind_max_z": choose(
            rows, lambda row: bool(row["target_oi_unwind"]), "max_z"
        ),
        "oi_unwind_least_z": choose(
            rows, lambda row: bool(row["target_oi_unwind"]), "least_z"
        ),
        "oi_unwind_taker_least_z": choose(
            rows,
            lambda row: bool(row["target_oi_unwind"] and row["taker_3of4"]),
            "least_z",
        ),
    }
    result = {
        "experiment": "candidate-57-jump-oi-lifecycle-development-v1",
        "development_only": True,
        "source_interval": ["2026-04-01", "2026-04-14"],
        "external_mechanism": (
            "open-interest fall across the completed 4h price impulse as a "
            "position-closure/liquidation state"
        ),
        "state_definition": {
            "target_oi_unwind": "end sum_open_interest < start sum_open_interest",
            "lookback_minutes": 240,
            "strict_asof_max_age_minutes": 10,
            "threshold_fitted": False,
        },
        "rows": len(rows),
        "unresolved": unresolved,
        "candidate_groups": {name: stats(group) for name, group in groups.items()},
        "candidate_groups_by_side": by_side,
        "candidate_groups_by_peer_unwind_count": by_peer_unwind_count,
        "one_candidate_per_boundary_shadow_policies": {
            name: {
                **stats(selected),
                "independent_boundaries": len(selected),
                "selected": [
                    {
                        "candidate_id": row.get("candidate_id"),
                        "symbol": row.get("symbol"),
                        "episode_ts": row.get("episode_ts"),
                        "side": row.get("side"),
                        "absolute_z": row.get("absolute_z"),
                        "target_oi_change_fraction": row.get(
                            "target_oi_change_fraction"
                        ),
                        "peer_oi_unwind_count": row.get("peer_oi_unwind_count"),
                        "taker_3of4": row.get("taker_3of4"),
                        "exit_net_r": row.get("exit_net_r"),
                        "outcome": row.get("outcome"),
                    }
                    for row in selected
                ],
            }
            for name, selected in policies.items()
        },
    }
    dump(OUT / "comparison.json", result)
    dump(OUT / "enriched_candidates.json", rows)

    lines = [
        "# Jump OI lifecycle development anatomy",
        "",
        "This is a development-only causal diagnostic. Falling target open "
        "interest is defined by the sign of the change across the same completed "
        "four-hour impulse; no magnitude threshold was fitted.",
        "",
        "| group | rows | win rate | mean R | PF(R) | sum R |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for name, group in groups.items():
        summary = stats(group)
        lines.append(
            "| "
            + " | ".join(
                [
                    name,
                    str(summary["resolved"]),
                    str(summary["win_rate"]),
                    str(summary["mean_r"]),
                    str(summary["profit_factor_r"]),
                    str(summary["sum_r"]),
                ]
            )
            + " |"
        )
    lines.extend(
        [
            "",
            "## One candidate per independent boundary (shadow only)",
            "",
            "| policy | boundaries | win rate | mean R | PF(R) | sum R |",
            "|---|---:|---:|---:|---:|---:|",
        ]
    )
    for name, selected in policies.items():
        summary = stats(selected)
        lines.append(
            "| "
            + " | ".join(
                [
                    name,
                    str(len(selected)),
                    str(summary["win_rate"]),
                    str(summary["mean_r"]),
                    str(summary["profit_factor_r"]),
                    str(summary["sum_r"]),
                ]
            )
            + " |"
        )
    (OUT / "RESULT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps(result["candidate_groups"], indent=2, sort_keys=True))
    return 0 if not unresolved else 2


if __name__ == "__main__":
    raise SystemExit(main())
