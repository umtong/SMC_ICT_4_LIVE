"""Leader/laggard routing for the strongest valid ZaratustraV13 short edge.

Across all three completed V13 samples, BTC was the persistent loss contributor
while ETH/SOL/XRP supplied the positive short expectancy.  Candidate 55 treats
this as a structural role hypothesis rather than a symbol optimization: BTC is
the crypto leader; the V13 displacement/DI short is routed only to liquid
followers.  Four causal component policies are frozen before new periods:
source OR, DI-only, Bollinger-only, and DI+BB confluence.
"""
from __future__ import annotations

from dataclasses import replace
import importlib.util
import math
from pathlib import Path
import sys
from typing import Mapping, Sequence

_BASE_PATH = Path(__file__).resolve().with_name("router_zaratustra_v13.py")
_SPEC = importlib.util.spec_from_file_location(
    "candidate55_zaratustra_v13_alt_base", _BASE_PATH
)
if _SPEC is None or _SPEC.loader is None:
    raise RuntimeError(f"cannot load V13 router: {_BASE_PATH}")
_BASE = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = _BASE
_SPEC.loader.exec_module(_BASE)

BarObservation = _BASE.BarObservation
FeatureObservation = _BASE.FeatureObservation
RouteConfig = _BASE.RouteConfig
RouteDecision = _BASE.RouteDecision
UNRESOLVED = _BASE.UNRESOLVED
ZARATUSTRA_STATE = _BASE.ZARATUSTRA_STATE
PICASSO_STATE = ZARATUSTRA_STATE
SMA_OFFSET_STATE = ZARATUSTRA_STATE
_SYMBOL_PRIORITY = {"ETHUSDT": 0, "SOLUSDT": 1, "XRPUSDT": 2, "BTCUSDT": 99}

_aggregate_complete = _BASE._aggregate_complete
_directional_indicators = _BASE._directional_indicators
_adx_dx = _BASE._adx_dx


def _decode_alt_mode(mode: str) -> str:
    normalized = str(mode).strip().lower().replace("-", "_")
    allowed = {
        "alt_source_short": "source",
        "alt_di_short": "di",
        "alt_bb_short": "bb",
        "alt_confluence_short": "confluence",
    }
    if normalized not in allowed:
        raise ValueError(f"unsupported V13 alt mode: {mode}")
    return allowed[normalized]


def _reject(decision: RouteDecision, reason: str) -> RouteDecision:
    diagnostics = dict(decision.diagnostics)
    diagnostics["alt_router_rejection"] = reason
    return RouteDecision(
        decision.symbol,
        UNRESOLVED,
        0,
        0.0,
        math.nan,
        math.nan,
        math.nan,
        int(decision.episode_ts),
        (reason,),
        diagnostics,
    )


def classify_symbol(
    symbol: str,
    bars: Sequence[BarObservation],
    feature: FeatureObservation,
    config: RouteConfig = RouteConfig(),
) -> RouteDecision:
    policy = _decode_alt_mode(config.picasso_precedence_mode)
    base_config = replace(config, picasso_precedence_mode="source_short")
    decision = _BASE.classify_symbol(symbol, bars, feature, base_config)
    if not decision.actionable:
        return decision
    if symbol == "BTCUSDT":
        return _reject(decision, "LEADER_BTC_RESERVED_FOR_SEPARATE_FAMILY")
    used_di = int(decision.diagnostics.get("used_di_component", 0)) == 1
    used_bb = int(decision.diagnostics.get("used_bb_component", 0)) == 1
    accepted = (
        policy == "source"
        or (policy == "di" and used_di)
        or (policy == "bb" and used_bb)
        or (policy == "confluence" and used_di and used_bb)
    )
    if not accepted:
        return _reject(decision, "ALT_COMPONENT_POLICY_REJECTED")
    diagnostics = dict(decision.diagnostics)
    diagnostics.update(
        {
            "leader_laggard_role": "liquid_follower",
            "leader_symbol_reserved": "BTCUSDT",
            "alt_component_policy": policy,
            "source_side_filter": "short",
        }
    )
    return RouteDecision(
        decision.symbol,
        decision.state,
        decision.side,
        decision.score,
        decision.entry_reference,
        decision.stop_reference,
        decision.objective_reference,
        decision.episode_ts,
        decision.reasons
        + (
            "LEADER_LAGGARD_ALT_ROUTING",
            "ALT_COMPONENT_POLICY_" + policy.upper(),
        ),
        diagnostics,
    )


classify_sma_offset = classify_symbol


def route_universe(
    bars_by_symbol: Mapping[str, Sequence[BarObservation]],
    features_by_symbol: Mapping[str, FeatureObservation],
    config: RouteConfig = RouteConfig(),
) -> tuple[RouteDecision | None, dict[str, RouteDecision]]:
    decisions = {
        symbol: classify_symbol(
            symbol,
            bars,
            features_by_symbol.get(
                symbol,
                FeatureObservation(bars[-1].ts_event if bars else 0, ready=True),
            ),
            config,
        )
        for symbol, bars in bars_by_symbol.items()
    }
    actionable = [decision for decision in decisions.values() if decision.actionable]
    actionable.sort(
        key=lambda item: (
            -float(item.score),
            _SYMBOL_PRIORITY.get(item.symbol, 99),
            int(item.episode_ts),
        )
    )
    return (actionable[0] if actionable else None), decisions


__all__ = [
    "BarObservation", "FeatureObservation", "PICASSO_STATE", "RouteConfig",
    "RouteDecision", "SMA_OFFSET_STATE", "UNRESOLVED", "ZARATUSTRA_STATE",
    "_adx_dx", "_aggregate_complete", "_decode_alt_mode",
    "_directional_indicators", "classify_symbol", "route_universe",
]
