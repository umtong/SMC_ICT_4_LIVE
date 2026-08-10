"""Frozen Binance peer-taker state filter for the 4h jump specialist."""
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
    return {
        **row,
        "age_minutes": age_ns / 60_000_000_000.0,
    }


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


def route_universe(
    bars_by_symbol: Mapping[str, Sequence[BarObservation]],
    features_by_symbol: Mapping[str, FeatureObservation],
    config: RouteConfig = RouteConfig(),
) -> tuple[RouteDecision | None, dict[str, RouteDecision]]:
    mode = filter_mode()
    source_config = replace(config, jump_selection_mode="source")
    _, raw = _base_route_universe(
        bars_by_symbol=bars_by_symbol,
        features_by_symbol=features_by_symbol,
        config=source_config,
    )
    decisions: dict[str, RouteDecision] = {}
    for symbol, decision in raw.items():
        if not decision.actionable:
            decisions[symbol] = decision
            continue
        state = _alignment(int(decision.side), int(decision.episode_ts))
        diagnostics = dict(decision.diagnostics or {})
        diagnostics.update(
            {
                "jump_taker_filter_mode": mode,
                "jump_taker_available_peers": state["available_peers"],
                "jump_taker_aligned_peers": state["aligned_peers"],
                "jump_taker_required_aligned_peers": 3,
                "jump_taker_peer_snapshots_json": state["peer_snapshots_json"],
            }
        )
        if mode == "peer_taker_alignment_3of4":
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
        decisions[symbol] = replace(decision, diagnostics=diagnostics)

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
    selected_diagnostics["jump_taker_selected_after_filter"] = 1
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
    "filter_mode",
    "route_universe",
]
