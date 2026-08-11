"""Candidate 60: ZaratustraV5 entry gated by a causal clean-trend state.

The public Zaratustra adapter remains the source of entry direction, geometry,
score and cross-symbol arbitration.  The reused RAHTF classifier may only reject
an already-actionable source candidate.  It never creates a trade and it never
changes the source stop, target or management.
"""
from __future__ import annotations

import os
from typing import Mapping, Sequence

import router_rahtf_state as rahtf
import router_zaratustra_base as source
from router_picasso import _SYMBOL_PRIORITY

ZARA_STATE = source.ZARA_STATE
PICASSO_STATE = source.PICASSO_STATE
SMA_OFFSET_STATE = source.SMA_OFFSET_STATE
UNRESOLVED = source.UNRESOLVED
BarObservation = source.BarObservation
FeatureObservation = source.FeatureObservation
RouteConfig = source.RouteConfig
RouteDecision = source.RouteDecision

_CONTROL = "control"
_RAHTF_CLEAN = "rahtf_clean"
_ALLOWED_MODES = {_CONTROL, _RAHTF_CLEAN}


def state_mode() -> str:
    """Return the frozen state policy selected by the campaign environment."""
    value = os.environ.get("C60_ZARA_RAHTF_MODE", _CONTROL).strip().lower()
    if value not in _ALLOWED_MODES:
        raise ValueError(f"unsupported C60_ZARA_RAHTF_MODE={value!r}")
    return value


def _clone_with_diagnostics(
    decision: RouteDecision,
    diagnostics: Mapping[str, float | int | str],
    *extra_reasons: str,
) -> RouteDecision:
    return RouteDecision(
        symbol=decision.symbol,
        state=decision.state,
        side=decision.side,
        score=decision.score,
        entry_reference=decision.entry_reference,
        stop_reference=decision.stop_reference,
        objective_reference=decision.objective_reference,
        episode_ts=decision.episode_ts,
        reasons=(*decision.reasons, *extra_reasons),
        diagnostics=dict(diagnostics),
    )


def _apply_state_gate(
    decision: RouteDecision,
    bars: Sequence[BarObservation],
) -> RouteDecision:
    mode = state_mode()
    if not decision.actionable:
        return decision

    diagnostics = dict(decision.diagnostics)
    diagnostics.update(
        {
            "c60_source_actionable": 1,
            "c60_rahtf_mode": mode,
            "c60_rahtf_thresholds_searched": 0,
            "c60_rahtf_external_entry_logic_used": 0,
        }
    )
    if mode == _CONTROL:
        return _clone_with_diagnostics(decision, diagnostics, "C60_RAHTF_CONTROL")

    snapshot = rahtf._state_snapshot(bars)
    diagnostics.update(snapshot)
    diagnostics["c60_rahtf_clean_state_pass"] = 0
    episode_ts = int(decision.episode_ts)

    if not bool(int(snapshot["rahtf_context_ready"])):
        return source._unresolved(
            decision.symbol,
            "C60_RAHTF_CONTEXT_NOT_READY",
            episode_ts,
            diagnostics,
        )

    label = int(snapshot["rahtf_confirmed_label_code"])
    slow_eff = float(snapshot["rahtf_slow_eff"])
    if int(decision.side) > 0:
        label_ok = label == rahtf._TREND_UP_CLEAN
        drift_ok = slow_eff >= rahtf._TREND_DRIFT_CONFIRM
    else:
        label_ok = label == rahtf._TREND_DOWN_CLEAN
        drift_ok = slow_eff <= -rahtf._TREND_DRIFT_CONFIRM

    diagnostics.update(
        {
            "c60_rahtf_required_label_code": (
                rahtf._TREND_UP_CLEAN
                if int(decision.side) > 0
                else rahtf._TREND_DOWN_CLEAN
            ),
            "c60_rahtf_label_pass": int(label_ok),
            "c60_rahtf_slow_drift_pass": int(drift_ok),
            "c60_rahtf_symmetric_drift_threshold": rahtf._TREND_DRIFT_CONFIRM,
        }
    )
    if not label_ok:
        return source._unresolved(
            decision.symbol,
            "C60_RAHTF_CONFIRMED_LABEL_REJECTED",
            episode_ts,
            diagnostics,
        )
    if not drift_ok:
        return source._unresolved(
            decision.symbol,
            "C60_RAHTF_SLOW_DRIFT_REJECTED",
            episode_ts,
            diagnostics,
        )

    diagnostics["c60_rahtf_clean_state_pass"] = 1
    return _clone_with_diagnostics(
        decision,
        diagnostics,
        "C60_EXTERNAL_RAHTF_CLEAN_STATE_PASS",
    )


def route_symbol(
    symbol: str,
    bars: Sequence[BarObservation],
    feature: FeatureObservation,
    config: RouteConfig = RouteConfig(),
) -> RouteDecision:
    decision = source.route_symbol(symbol, bars, feature, config)
    return _apply_state_gate(decision, bars)


def route_universe(
    bars_by_symbol: Mapping[str, Sequence[BarObservation]],
    features_by_symbol: Mapping[str, FeatureObservation],
    config: RouteConfig = RouteConfig(),
) -> tuple[RouteDecision | None, dict[str, RouteDecision]]:
    decisions = {
        symbol: route_symbol(
            symbol,
            bars_by_symbol[symbol],
            features_by_symbol[symbol],
            config,
        )
        for symbol in bars_by_symbol
        if symbol in features_by_symbol
    }
    actionable = [decision for decision in decisions.values() if decision.actionable]
    actionable.sort(
        key=lambda decision: (
            -float(decision.score),
            _SYMBOL_PRIORITY.get(decision.symbol, 99),
            int(decision.episode_ts),
        )
    )
    return (actionable[0] if actionable else None), decisions


__all__ = [
    "BarObservation",
    "FeatureObservation",
    "PICASSO_STATE",
    "RouteConfig",
    "RouteDecision",
    "SMA_OFFSET_STATE",
    "UNRESOLVED",
    "ZARA_STATE",
    "_apply_state_gate",
    "route_symbol",
    "route_universe",
    "state_mode",
]
