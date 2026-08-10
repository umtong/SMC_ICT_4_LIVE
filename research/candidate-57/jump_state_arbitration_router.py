"""Peer-taker market state × side-aware arbitration for the 4h jump specialist.

The public/source jump classifier, structural stop and management are unchanged.
The wrapper exposes frozen controls and two structural compositions:

* ``source_max_z``: source maximum absolute z-score;
* ``least_qualifying_z``: least absolute already-qualified z-score;
* ``taker_conditional``: source max-z when at least 3 of 4 peer taker ratios
  already align with the proposed reversal, otherwise least-z.

An independent causal side state can keep both reversal directions or retain
only short reversals after completed upward jumps. Every metrics join is strict
as-of and no outcome, symbol exception or future path is used.
"""
from __future__ import annotations

from bisect import bisect_right
from dataclasses import replace
import json
import os
from pathlib import Path
from typing import Any, Mapping, Sequence

from router_jump_base import (
    FeatureObservation,
    JUMP_REVERSION_STATE,
    RouteConfig,
    RouteDecision,
    SMA_OFFSET_STATE,
    UNRESOLVED,
    BarObservation,
    _SYMBOL_PRIORITY,
    _unresolved,
    route_universe as _base_route_universe,
)

SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT")
_MAX_AGE_NS = 10 * 60 * 1_000_000_000
_METRICS_PATH: str | None = None
_METRICS: dict[str, list[dict[str, Any]]] = {}
_TIMES: dict[str, list[int]] = {}


def filter_mode() -> str:
    mode = os.environ.get(
        "C57_JUMP_TAKER_FILTER_MODE", "source_without_taker_filter"
    ).strip().lower()
    if mode not in {
        "source_without_taker_filter",
        "peer_taker_alignment_3of4",
    }:
        raise ValueError(f"unsupported C57_JUMP_TAKER_FILTER_MODE={mode!r}")
    return mode


def arbitration_mode() -> str:
    mode = os.environ.get(
        "C57_JUMP_ARBITRATION_MODE", "source_max_z"
    ).strip().lower()
    if mode not in {
        "source_max_z",
        "least_qualifying_z",
        "taker_conditional",
    }:
        raise ValueError(f"unsupported C57_JUMP_ARBITRATION_MODE={mode!r}")
    return mode


def side_mode() -> str:
    mode = os.environ.get("C57_JUMP_SIDE_MODE", "both").strip().lower()
    if mode not in {"both", "short_only", "long_only"}:
        raise ValueError(f"unsupported C57_JUMP_SIDE_MODE={mode!r}")
    return mode


def _side_allowed(mode: str, side: int) -> bool:
    if mode == "both":
        return True
    if mode == "short_only":
        return int(side) < 0
    if mode == "long_only":
        return int(side) > 0
    raise ValueError(mode)


def _load_metrics() -> None:
    global _METRICS_PATH, _METRICS, _TIMES
    raw_path = os.environ.get("C57_JUMP_TAKER_METRICS_PATH", "").strip()
    if not raw_path:
        raise RuntimeError("C57_JUMP_TAKER_METRICS_PATH is required")
    if _METRICS_PATH == raw_path and _METRICS:
        return
    path = Path(raw_path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    metrics = {
        symbol: sorted(
            [
                {
                    **row,
                    "ts_event": int(row["ts_event"]),
                    "sum_taker_long_short_vol_ratio": float(
                        row["sum_taker_long_short_vol_ratio"]
                    ),
                }
                for row in (payload.get("symbols") or {}).get(symbol, [])
            ],
            key=lambda row: int(row["ts_event"]),
        )
        for symbol in SYMBOLS
    }
    if any(not metrics[symbol] for symbol in SYMBOLS):
        raise RuntimeError(f"metrics sidecar lacks one or more symbols: {path}")
    _METRICS_PATH = raw_path
    _METRICS = metrics
    _TIMES = {
        symbol: [int(row["ts_event"]) for row in rows]
        for symbol, rows in metrics.items()
    }


def _asof(symbol: str, ts_event: int) -> dict[str, Any] | None:
    _load_metrics()
    times = _TIMES[symbol]
    index = bisect_right(times, int(ts_event)) - 1
    if index < 0:
        return None
    row = _METRICS[symbol][index]
    age_ns = int(ts_event) - int(row["ts_event"])
    if age_ns < 0 or age_ns > _MAX_AGE_NS:
        return None
    return {**row, "age_minutes": age_ns / 60_000_000_000.0}


def _alignment(side: int, ts_event: int) -> dict[str, Any]:
    snapshots: dict[str, Any] = {}
    aligned = 0
    for symbol in SYMBOLS:
        row = _asof(symbol, ts_event)
        if row is None:
            snapshots[symbol] = None
            continue
        ratio = float(row["sum_taker_long_short_vol_ratio"])
        is_aligned = ratio > 1.0 if side > 0 else ratio < 1.0
        aligned += int(is_aligned)
        snapshots[symbol] = {
            "ts_event": int(row["ts_event"]),
            "age_minutes": float(row["age_minutes"]),
            "sum_taker_long_short_vol_ratio": ratio,
            "aligned_with_proposed_reversal": bool(is_aligned),
            "sum_open_interest": row.get("sum_open_interest"),
            "sum_open_interest_value": row.get("sum_open_interest_value"),
            "count_long_short_ratio": row.get("count_long_short_ratio"),
            "count_toptrader_long_short_ratio": row.get(
                "count_toptrader_long_short_ratio"
            ),
            "sum_toptrader_long_short_ratio": row.get(
                "sum_toptrader_long_short_ratio"
            ),
        }
    return {
        "available_peers": sum(value is not None for value in snapshots.values()),
        "aligned_peers": aligned,
        "required_aligned_peers": 3,
        "peer_snapshots_json": json.dumps(
            snapshots, sort_keys=True, separators=(",", ":")
        ),
    }


def _effective_arbitration(
    requested_mode: str,
    source_score: float,
    absolute_z: float,
    available_peers: int,
    aligned_peers: int,
) -> tuple[str, float] | None:
    if requested_mode == "source_max_z":
        return "source_max_z", float(source_score)
    if requested_mode == "least_qualifying_z":
        return "least_qualifying_z", -float(absolute_z)
    if requested_mode != "taker_conditional":
        raise ValueError(requested_mode)
    if available_peers < 4:
        return None
    if aligned_peers >= 3:
        return "source_max_z", float(source_score)
    return "least_qualifying_z", -float(absolute_z)


def route_universe(
    bars_by_symbol: Mapping[str, Sequence[BarObservation]],
    features_by_symbol: Mapping[str, FeatureObservation],
    config: RouteConfig = RouteConfig(),
) -> tuple[RouteDecision | None, dict[str, RouteDecision]]:
    taker_mode = filter_mode()
    requested_selection_mode = arbitration_mode()
    requested_side_mode = side_mode()
    source_config = replace(config, jump_selection_mode="source")
    _, raw = _base_route_universe(
        bars_by_symbol=bars_by_symbol,
        features_by_symbol=features_by_symbol,
        config=source_config,
    )
    actionable_raw = [decision for decision in raw.values() if decision.actionable]
    snapshot = []
    for decision in actionable_raw:
        diagnostics = decision.diagnostics or {}
        snapshot.append(
            {
                "symbol": decision.symbol,
                "side": int(decision.side),
                "absolute_z": abs(float(diagnostics.get("causal_zscore", 0.0))),
                "source_score": float(decision.score),
                "absolute_return": float(diagnostics.get("absolute_return", 0.0)),
                "residual_z": float(
                    diagnostics.get("cross_sectional_residual_z", 0.0)
                ),
                "stop_fraction": float(diagnostics.get("stop_fraction", 0.0)),
            }
        )
    snapshot.sort(key=lambda row: _SYMBOL_PRIORITY.get(str(row["symbol"]), 99))
    snapshot_json = json.dumps(snapshot, sort_keys=True, separators=(",", ":"))

    decisions: dict[str, RouteDecision] = {}
    for symbol, decision in raw.items():
        if not decision.actionable:
            decisions[symbol] = decision
            continue

        diagnostics = dict(decision.diagnostics or {})
        diagnostics.update(
            {
                "jump_requested_side_mode": requested_side_mode,
                "jump_boundary_candidate_count": len(actionable_raw),
                "jump_boundary_candidate_set_json": snapshot_json,
            }
        )
        if not _side_allowed(requested_side_mode, int(decision.side)):
            decisions[symbol] = _unresolved(
                symbol,
                "JUMP_SIDE_REJECTED",
                int(decision.episode_ts),
                diagnostics,
            )
            continue

        state = _alignment(int(decision.side), int(decision.episode_ts))
        absolute_z = abs(float(diagnostics.get("causal_zscore", 0.0)))
        effective = _effective_arbitration(
            requested_mode=requested_selection_mode,
            source_score=float(decision.score),
            absolute_z=absolute_z,
            available_peers=int(state["available_peers"]),
            aligned_peers=int(state["aligned_peers"]),
        )
        diagnostics.update(
            {
                "jump_taker_filter_mode": taker_mode,
                "jump_taker_available_peers": state["available_peers"],
                "jump_taker_aligned_peers": state["aligned_peers"],
                "jump_taker_required_aligned_peers": 3,
                "jump_taker_peer_snapshots_json": state["peer_snapshots_json"],
                "jump_requested_arbitration_mode": requested_selection_mode,
                "jump_source_score": float(decision.score),
                "jump_absolute_z": absolute_z,
            }
        )
        if effective is None:
            decisions[symbol] = _unresolved(
                symbol,
                "JUMP_TAKER_METRICS_UNRESOLVED",
                int(decision.episode_ts),
                diagnostics,
            )
            continue
        effective_mode, effective_score = effective
        diagnostics.update(
            {
                "jump_effective_arbitration_mode": effective_mode,
                "jump_effective_arbitration_score": effective_score,
                "jump_taker_conditional_aligned_regime": int(
                    int(state["aligned_peers"]) >= 3
                ),
            }
        )

        if taker_mode == "peer_taker_alignment_3of4":
            if int(state["available_peers"]) < 4:
                decisions[symbol] = _unresolved(
                    symbol,
                    "JUMP_TAKER_METRICS_UNRESOLVED",
                    int(decision.episode_ts),
                    diagnostics,
                )
                continue
            if int(state["aligned_peers"]) < 3:
                decisions[symbol] = _unresolved(
                    symbol,
                    "JUMP_TAKER_ALIGNMENT_REJECTED",
                    int(decision.episode_ts),
                    diagnostics,
                )
                continue

        diagnostics["jump_combined_policy_accepted"] = 1
        decisions[symbol] = replace(
            decision,
            score=effective_score,
            diagnostics=diagnostics,
        )

    actionable = [decision for decision in decisions.values() if decision.actionable]
    actionable.sort(
        key=lambda item: (
            -float(item.score),
            _SYMBOL_PRIORITY.get(item.symbol, 99),
            int(item.episode_ts),
        )
    )
    if not actionable:
        return None, decisions
    selected = actionable[0]
    selected_diagnostics = dict(selected.diagnostics or {})
    selected_diagnostics.update(
        {
            "jump_selected_after_state_filter": 1,
            "jump_selected_by_effective_arbitration": 1,
        }
    )
    selected = replace(selected, diagnostics=selected_diagnostics)
    decisions[selected.symbol] = selected
    return selected, decisions


__all__ = [
    "BarObservation",
    "FeatureObservation",
    "JUMP_REVERSION_STATE",
    "RouteConfig",
    "RouteDecision",
    "SMA_OFFSET_STATE",
    "UNRESOLVED",
    "arbitration_mode",
    "filter_mode",
    "side_mode",
    "route_universe",
]
