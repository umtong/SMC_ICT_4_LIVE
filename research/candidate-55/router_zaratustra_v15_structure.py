"""Causal structural repair for the public ZaratustraV15 short engine.

The source short policy contains two economically different opportunity engines:

* DI state edges: a directional state transition.  The profitable subset in the
  observed diagnostic periods was not a late chase; it was a local pullback
  inside an already negative broader auction, followed by renewed short state.
* Bollinger edges: an outer-band expansion.  The profitable subset was a clean
  downside impulse shared by most of the four-asset universe, not an isolated
  one-symbol band touch.

The source entry, source 10x-normalized stop, source trailing management, exact
3% current-NAV risk sizing, one global slot and NautilusTrader execution are
unchanged.  This module replaces only the market-state interpretation that
allowed the source engine to consume low-quality episodes.
"""
from __future__ import annotations

import importlib.util
import math
from pathlib import Path
import sys
from typing import Mapping, Sequence

_BASE_PATH = Path(__file__).resolve().with_name("router_zaratustra_v15_short.py")
_SPEC = importlib.util.spec_from_file_location(
    "candidate55_zaratustra_v15_structure_base", _BASE_PATH
)
if _SPEC is None or _SPEC.loader is None:
    raise RuntimeError(f"cannot load frozen V15 short router: {_BASE_PATH}")
_BASE = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = _BASE
_SPEC.loader.exec_module(_BASE)

BarObservation = _BASE.BarObservation
FeatureObservation = _BASE.FeatureObservation
RouteConfig = _BASE.RouteConfig
RouteDecision = _BASE.RouteDecision
UNRESOLVED = _BASE.UNRESOLVED

ZARATUSTRA_STATE = "PUBLIC_ZARATUSTRA_V15_STRUCTURAL_SHORT"
PICASSO_STATE = ZARATUSTRA_STATE
SMA_OFFSET_STATE = ZARATUSTRA_STATE

_SYMBOL_PRIORITY = {"BTCUSDT": 0, "ETHUSDT": 1, "SOLUSDT": 2, "XRPUSDT": 3}
_NS_PER_MINUTE = 60_000_000_000
_EPS = 1e-12

# Broad plateaus found by causal decomposition, not single-cell optimization.
DI_LOCAL_PULLBACK_MIN_RETURN = 0.0
DI_BROADER_TREND_MAX_RETURN = 0.0
BB_MIN_IMPULSE_ATR = 1.5
BB_MIN_NEGATIVE_BREADTH_60M = 3


def _return_fraction(bars: Sequence[BarObservation], minutes: int) -> float:
    """Causal close-to-close return using the last observation at/before cutoff."""
    if not bars or minutes <= 0:
        return math.nan
    latest = bars[-1]
    latest_close = float(latest.close)
    if not math.isfinite(latest_close) or latest_close <= 0.0:
        return math.nan
    cutoff = int(latest.ts_event) - int(minutes) * _NS_PER_MINUTE
    prior: BarObservation | None = None
    for item in reversed(bars[:-1]):
        if int(item.ts_event) <= cutoff:
            prior = item
            break
    if prior is None:
        return math.nan
    prior_close = float(prior.close)
    if not math.isfinite(prior_close) or prior_close <= 0.0:
        return math.nan
    return latest_close / prior_close - 1.0


def di_pullback_resumption(ret_15m: float, ret_240m: float) -> bool:
    """Local non-negative pullback nested in a negative four-hour auction."""
    return (
        math.isfinite(ret_15m)
        and math.isfinite(ret_240m)
        and ret_15m >= DI_LOCAL_PULLBACK_MIN_RETURN
        and ret_240m < DI_BROADER_TREND_MAX_RETURN
    )


def bb_clean_synchronized_expansion(
    ret_15m: float,
    atr_ratio: float,
    negative_breadth_60m: int,
) -> bool:
    """Downside expansion of at least 1.5 ATR with broad peer participation."""
    if (
        not math.isfinite(ret_15m)
        or not math.isfinite(atr_ratio)
        or atr_ratio <= _EPS
    ):
        return False
    downside_impulse_atr = -ret_15m / atr_ratio
    return (
        downside_impulse_atr >= BB_MIN_IMPULSE_ATR
        and int(negative_breadth_60m) >= BB_MIN_NEGATIVE_BREADTH_60M
    )


def _rejected(
    raw: RouteDecision,
    reason: str,
    diagnostics: Mapping[str, float | int | str],
) -> RouteDecision:
    return RouteDecision(
        symbol=raw.symbol,
        state=UNRESOLVED,
        side=0,
        score=0.0,
        entry_reference=math.nan,
        stop_reference=math.nan,
        objective_reference=math.nan,
        episode_ts=int(raw.episode_ts),
        reasons=(reason,),
        diagnostics=dict(diagnostics),
    )


def _accepted(
    raw: RouteDecision,
    *,
    family: str,
    diagnostics: Mapping[str, float | int | str],
) -> RouteDecision:
    return RouteDecision(
        symbol=raw.symbol,
        state=ZARATUSTRA_STATE,
        side=-1,
        score=float(raw.score),
        entry_reference=float(raw.entry_reference),
        stop_reference=float(raw.stop_reference),
        objective_reference=float(raw.objective_reference),
        episode_ts=int(raw.episode_ts),
        reasons=tuple(raw.reasons)
        + (
            "STRUCTURAL_STATE_REPAIR",
            family,
        ),
        diagnostics=dict(diagnostics),
    )


def classify_symbol(
    symbol: str,
    bars: Sequence[BarObservation],
    feature: FeatureObservation,
    config: RouteConfig = RouteConfig(),
) -> RouteDecision:
    """Single-symbol view.

    DI repair is fully single-symbol.  BB repair needs universe breadth and is
    therefore resolved only by :func:`route_universe`.
    """
    raw = _BASE.classify_symbol(symbol, bars, feature, config)
    if not raw.actionable:
        return raw
    diagnostics = dict(raw.diagnostics)
    ret_15m = _return_fraction(bars, 15)
    ret_240m = _return_fraction(bars, 240)
    diagnostics.update(
        {
            "candidate55_structural_repair": 1,
            "ret_15m": ret_15m,
            "ret_240m": ret_240m,
        }
    )
    if int(diagnostics.get("used_bb_component", 0)) == 1:
        return _rejected(
            raw,
            "V15_BB_REQUIRES_UNIVERSE_BREADTH",
            diagnostics,
        )
    if int(diagnostics.get("used_di_component", 0)) != 1:
        return _rejected(raw, "V15_UNKNOWN_SOURCE_COMPONENT", diagnostics)
    accepted = di_pullback_resumption(ret_15m, ret_240m)
    diagnostics.update(
        {
            "candidate55_scenario_family": "DI_PULLBACK_RESUMPTION",
            "di_local_pullback_min_return": DI_LOCAL_PULLBACK_MIN_RETURN,
            "di_broader_trend_max_return": DI_BROADER_TREND_MAX_RETURN,
            "di_state_accepted": int(accepted),
        }
    )
    if not accepted:
        return _rejected(raw, "V15_DI_NOT_PULLBACK_RESUMPTION", diagnostics)
    return _accepted(
        raw,
        family="DI_PULLBACK_RESUMPTION",
        diagnostics=diagnostics,
    )


classify_sma_offset = classify_symbol


def route_universe(
    bars_by_symbol: Mapping[str, Sequence[BarObservation]],
    features_by_symbol: Mapping[str, FeatureObservation],
    config: RouteConfig = RouteConfig(),
) -> tuple[RouteDecision | None, dict[str, RouteDecision]]:
    """Apply source signals first, then route each causal mechanism by state."""
    raw_decisions = {
        symbol: _BASE.classify_symbol(
            symbol,
            bars,
            features_by_symbol.get(
                symbol,
                FeatureObservation(
                    bars[-1].ts_event if bars else 0,
                    ready=True,
                ),
            ),
            config,
        )
        for symbol, bars in bars_by_symbol.items()
    }
    returns_60m = {
        symbol: _return_fraction(bars, 60)
        for symbol, bars in bars_by_symbol.items()
    }
    negative_breadth_60m = sum(
        1
        for value in returns_60m.values()
        if math.isfinite(value) and value < 0.0
    )
    decisions: dict[str, RouteDecision] = {}
    for symbol, raw in raw_decisions.items():
        if not raw.actionable:
            decisions[symbol] = raw
            continue
        bars = bars_by_symbol[symbol]
        diagnostics = dict(raw.diagnostics)
        ret_15m = _return_fraction(bars, 15)
        ret_60m = returns_60m.get(symbol, math.nan)
        ret_240m = _return_fraction(bars, 240)
        atr_ratio = float(diagnostics.get("atr_ratio", math.nan))
        downside_impulse_atr = (
            -ret_15m / atr_ratio
            if math.isfinite(ret_15m)
            and math.isfinite(atr_ratio)
            and atr_ratio > _EPS
            else math.nan
        )
        diagnostics.update(
            {
                "candidate55_structural_repair": 1,
                "ret_15m": ret_15m,
                "ret_60m": ret_60m,
                "ret_240m": ret_240m,
                "negative_breadth_60m": negative_breadth_60m,
                "universe_ret_60m": {
                    key: value for key, value in sorted(returns_60m.items())
                },
                "downside_impulse_atr_15m": downside_impulse_atr,
            }
        )
        used_bb = int(diagnostics.get("used_bb_component", 0)) == 1
        used_di = int(diagnostics.get("used_di_component", 0)) == 1

        # A DI+BB event is governed by the breakout state because the band edge
        # is the immediate causal interaction; DI remains supporting context.
        if used_bb:
            accepted = bb_clean_synchronized_expansion(
                ret_15m,
                atr_ratio,
                negative_breadth_60m,
            )
            diagnostics.update(
                {
                    "candidate55_scenario_family": (
                        "BB_CLEAN_SYNCHRONIZED_EXPANSION"
                    ),
                    "bb_min_impulse_atr": BB_MIN_IMPULSE_ATR,
                    "bb_min_negative_breadth_60m": (
                        BB_MIN_NEGATIVE_BREADTH_60M
                    ),
                    "bb_state_accepted": int(accepted),
                    "di_supporting_component": int(used_di),
                }
            )
            decisions[symbol] = (
                _accepted(
                    raw,
                    family="BB_CLEAN_SYNCHRONIZED_EXPANSION",
                    diagnostics=diagnostics,
                )
                if accepted
                else _rejected(
                    raw,
                    "V15_BB_NOT_CLEAN_SYNCHRONIZED_EXPANSION",
                    diagnostics,
                )
            )
            continue

        if used_di:
            accepted = di_pullback_resumption(ret_15m, ret_240m)
            diagnostics.update(
                {
                    "candidate55_scenario_family": "DI_PULLBACK_RESUMPTION",
                    "di_local_pullback_min_return": (
                        DI_LOCAL_PULLBACK_MIN_RETURN
                    ),
                    "di_broader_trend_max_return": (
                        DI_BROADER_TREND_MAX_RETURN
                    ),
                    "di_state_accepted": int(accepted),
                }
            )
            decisions[symbol] = (
                _accepted(
                    raw,
                    family="DI_PULLBACK_RESUMPTION",
                    diagnostics=diagnostics,
                )
                if accepted
                else _rejected(
                    raw,
                    "V15_DI_NOT_PULLBACK_RESUMPTION",
                    diagnostics,
                )
            )
            continue

        decisions[symbol] = _rejected(
            raw,
            "V15_UNKNOWN_SOURCE_COMPONENT",
            diagnostics,
        )

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


__all__ = [
    "BB_MIN_IMPULSE_ATR",
    "BB_MIN_NEGATIVE_BREADTH_60M",
    "BarObservation",
    "DI_BROADER_TREND_MAX_RETURN",
    "DI_LOCAL_PULLBACK_MIN_RETURN",
    "FeatureObservation",
    "PICASSO_STATE",
    "RouteConfig",
    "RouteDecision",
    "SMA_OFFSET_STATE",
    "UNRESOLVED",
    "ZARATUSTRA_STATE",
    "_return_fraction",
    "bb_clean_synchronized_expansion",
    "classify_symbol",
    "di_pullback_resumption",
    "route_universe",
]
