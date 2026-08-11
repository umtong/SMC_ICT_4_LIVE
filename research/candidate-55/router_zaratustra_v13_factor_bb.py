"""Broad-factor acceptance gate for the public ``ZaratustraV13`` short BB edge.

This module reuses the source-faithful V13 detector and changes only the state
ownership decision. A downside Bollinger edge is actionable only when the same
completed minute shows a broad 30-minute repricing across the four-symbol
universe: at least three assets must be down and the cross-asset median return
must be negative. The gate is deliberately sign/breadth based rather than an
optimized magnitude threshold.
"""
from __future__ import annotations

from dataclasses import replace
import importlib.util
import math
from pathlib import Path
import statistics
import sys
from typing import Mapping, Sequence

_BASE_PATH = Path(__file__).resolve().with_name("router_zaratustra_v13.py")
_SPEC = importlib.util.spec_from_file_location(
    "candidate55_zaratustra_v13_factor_base", _BASE_PATH
)
if _SPEC is None or _SPEC.loader is None:
    raise RuntimeError(f"cannot load reused ZaratustraV13 router: {_BASE_PATH}")
_BASE = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = _BASE
_SPEC.loader.exec_module(_BASE)

BarObservation = _BASE.BarObservation
FeatureObservation = _BASE.FeatureObservation
RouteConfig = _BASE.RouteConfig
RouteDecision = _BASE.RouteDecision
UNRESOLVED = _BASE.UNRESOLVED
ZARATUSTRA_STATE = "PUBLIC_ZARATUSTRA_V13_FACTOR_ACCEPTED_BB_SHORT"
PICASSO_STATE = ZARATUSTRA_STATE
SMA_OFFSET_STATE = ZARATUSTRA_STATE
_SYMBOL_PRIORITY = {"BTCUSDT": 0, "ETHUSDT": 1, "SOLUSDT": 2, "XRPUSDT": 3}
_FACTOR_PREFIX = "factor_"
_FACTOR_LOOKBACK_MINUTES = 30
_FACTOR_MIN_DOWN_ASSETS = 3
_MINUTE_NS = 60_000_000_000

# Re-export source helpers for contract tests and provenance checks.
_adx_dx = _BASE._adx_dx
_aggregate_complete = _BASE._aggregate_complete
_bollinger = _BASE._bollinger
_directional_indicators = _BASE._directional_indicators
source_entry_flags = _BASE.source_entry_flags


def _decode_factor_mode(mode: str) -> tuple[bool, str]:
    normalized = str(mode).strip().lower().replace("-", "_")
    if normalized.startswith(_FACTOR_PREFIX):
        return True, normalized[len(_FACTOR_PREFIX) :]
    return False, normalized


def _base_config(config: RouteConfig) -> tuple[bool, RouteConfig]:
    enabled, base_mode = _decode_factor_mode(config.picasso_precedence_mode)
    return enabled, replace(config, picasso_precedence_mode=base_mode)


def _price_at_or_before(
    bars: Sequence[BarObservation], cutoff_ns: int
) -> float | None:
    for bar in reversed(bars):
        if int(bar.ts_event) <= cutoff_ns:
            price = float(bar.close)
            if math.isfinite(price) and price > 0.0:
                return price
    return None


def _factor_snapshot(
    bars_by_symbol: Mapping[str, Sequence[BarObservation]],
    lookback_minutes: int = _FACTOR_LOOKBACK_MINUTES,
    min_down_assets: int = _FACTOR_MIN_DOWN_ASSETS,
) -> tuple[bool, dict[str, float | int | str]]:
    """Return broad-downside acceptance using completed bars only."""
    if lookback_minutes <= 0:
        raise ValueError("factor lookback must be positive")
    returns: dict[str, float] = {}
    latest_times: list[int] = []
    for symbol, bars in bars_by_symbol.items():
        if not bars:
            continue
        current = bars[-1]
        current_ts = int(current.ts_event)
        current_price = float(current.close)
        prior_price = _price_at_or_before(
            bars, current_ts - lookback_minutes * _MINUTE_NS
        )
        if (
            prior_price is None
            or not math.isfinite(current_price)
            or current_price <= 0.0
        ):
            continue
        returns[symbol] = (current_price / prior_price - 1.0) * 10_000.0
        latest_times.append(current_ts)

    diagnostics: dict[str, float | int | str] = {
        "factor_lookback_minutes": int(lookback_minutes),
        "factor_min_down_assets": int(min_down_assets),
        "factor_assets_ready": len(returns),
    }
    for symbol, value in sorted(returns.items()):
        diagnostics[f"factor_return_{symbol}_bps"] = float(value)
    if len(returns) != 4:
        diagnostics.update(
            {
                "factor_down_assets": sum(
                    value < 0.0 for value in returns.values()
                ),
                "factor_common_return_bps": math.nan,
                "factor_accepted": 0,
                "factor_reason": "INCOMPLETE_FOUR_ASSET_FACTOR_STATE",
            }
        )
        return False, diagnostics

    values = list(returns.values())
    down_assets = sum(value < 0.0 for value in values)
    common_return = float(statistics.median(values))
    synchronized = max(latest_times) - min(latest_times) <= _MINUTE_NS
    accepted = (
        synchronized
        and down_assets >= min_down_assets
        and common_return < 0.0
    )
    diagnostics.update(
        {
            "factor_down_assets": int(down_assets),
            "factor_common_return_bps": common_return,
            "factor_synchronized": int(synchronized),
            "factor_accepted": int(accepted),
            "factor_reason": (
                "BROAD_30M_DOWNSIDE_ACCEPTED"
                if accepted
                else "BROAD_30M_DOWNSIDE_NOT_ACCEPTED"
            ),
        }
    )
    return accepted, diagnostics


def _unresolved_from_signal(
    decision: RouteDecision,
    reason: str,
    diagnostics: Mapping[str, float | int | str],
) -> RouteDecision:
    merged = dict(decision.diagnostics)
    merged.update(diagnostics)
    merged.update(
        {
            "factor_rejected_source_state": decision.state,
            "factor_rejected_source_side": int(decision.side),
            "factor_rejected_source_score": float(decision.score),
        }
    )
    return RouteDecision(
        symbol=decision.symbol,
        state=UNRESOLVED,
        side=0,
        score=0.0,
        entry_reference=math.nan,
        stop_reference=math.nan,
        objective_reference=math.nan,
        episode_ts=int(decision.episode_ts),
        reasons=(reason,),
        diagnostics=merged,
    )


def classify_symbol(
    symbol: str,
    bars: Sequence[BarObservation],
    feature: FeatureObservation,
    config: RouteConfig = RouteConfig(),
) -> RouteDecision:
    """Fail closed for factor modes because ownership is universe-relative."""
    enabled, base_config = _base_config(config)
    if enabled:
        return RouteDecision(
            symbol=symbol,
            state=UNRESOLVED,
            side=0,
            score=0.0,
            entry_reference=math.nan,
            stop_reference=math.nan,
            objective_reference=math.nan,
            episode_ts=int(bars[-1].ts_event) if bars else 0,
            reasons=("FACTOR_MODE_REQUIRES_ROUTE_UNIVERSE",),
            diagnostics={"factor_gate_enabled": 1},
        )
    return _BASE.classify_symbol(symbol, bars, feature, base_config)


def route_universe(
    bars_by_symbol: Mapping[str, Sequence[BarObservation]],
    features_by_symbol: Mapping[str, FeatureObservation],
    config: RouteConfig = RouteConfig(),
) -> tuple[RouteDecision | None, dict[str, RouteDecision]]:
    enabled, base_config = _base_config(config)
    decisions = {
        symbol: _BASE.classify_symbol(
            symbol,
            bars,
            features_by_symbol.get(
                symbol,
                FeatureObservation(
                    bars[-1].ts_event if bars else 0, ready=True
                ),
            ),
            base_config,
        )
        for symbol, bars in bars_by_symbol.items()
    }

    if enabled:
        accepted, factor = _factor_snapshot(bars_by_symbol)
        gated: dict[str, RouteDecision] = {}
        for symbol, decision in decisions.items():
            if not decision.actionable:
                merged = dict(decision.diagnostics)
                merged.update(factor)
                gated[symbol] = replace(decision, diagnostics=merged)
                continue
            if decision.side != -1:
                gated[symbol] = _unresolved_from_signal(
                    decision,
                    "FACTOR_BB_POLICY_REJECTED_NON_SHORT_SOURCE_SIGNAL",
                    factor,
                )
                continue
            if not accepted:
                gated[symbol] = _unresolved_from_signal(
                    decision,
                    "FACTOR_BB_BROAD_30M_DOWNSIDE_NOT_ACCEPTED",
                    factor,
                )
                continue
            merged = dict(decision.diagnostics)
            merged.update(factor)
            merged.update(
                {
                    "source_state_before_factor_gate": decision.state,
                    "factor_gate_enabled": 1,
                }
            )
            gated[symbol] = replace(
                decision,
                state=ZARATUSTRA_STATE,
                reasons=decision.reasons
                + ("BROAD_30M_DOWNSIDE_ACCEPTS_BB_SHORT",),
                diagnostics=merged,
            )
        decisions = gated

    actionable = [
        decision for decision in decisions.values() if decision.actionable
    ]
    actionable.sort(
        key=lambda item: (
            -float(item.score),
            _SYMBOL_PRIORITY.get(item.symbol, 99),
            int(item.episode_ts),
        )
    )
    return (actionable[0] if actionable else None), decisions


classify_sma_offset = classify_symbol

__all__ = [
    "BarObservation",
    "FeatureObservation",
    "PICASSO_STATE",
    "RouteConfig",
    "RouteDecision",
    "SMA_OFFSET_STATE",
    "UNRESOLVED",
    "ZARATUSTRA_STATE",
    "_decode_factor_mode",
    "_factor_snapshot",
    "classify_symbol",
    "route_universe",
    "source_entry_flags",
]
