"""Candidate 15 V6 residual-laggard delivery routing.

V5 proved that broad cross-market response was not itself a tradable continuation
state.  V6 preserves the causal response detector and every continuation leg,
but permits portfolio competition only when exactly one market was absent from
the latest three-market response and that same excluded market later creates its
own independent five-minute MSS/displacement/FVG leg.

The transformation is applied after the frozen V5 materializer.  It therefore
changes only market ownership of the continuation state, not the detector,
entry geometry, risk sizing, execution engine, costs, or global slot.
"""
from __future__ import annotations

from collections.abc import Iterable
from typing import Any


SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT")


def residual_laggard_symbol(accepted_symbols: Iterable[Any]) -> str | None:
    """Return the sole market excluded from a valid three-market response.

    A four-market response has no residual information receiver.  Malformed,
    duplicated, unknown, or narrower response sets fail closed.
    """
    raw = tuple(str(symbol) for symbol in accepted_symbols)
    accepted = set(raw)
    allowed = set(SYMBOLS)
    if len(raw) != 3 or len(accepted) != 3 or not accepted.issubset(allowed):
        return None
    missing = sorted(allowed - accepted)
    return missing[0] if len(missing) == 1 else None


def _replace(
    source: str,
    old: str,
    new: str,
    *,
    label: str,
    expected: int = 1,
) -> str:
    count = source.count(old)
    if count != expected:
        raise RuntimeError(
            f"Candidate 15 V6 portfolio boundary drifted at {label}: "
            f"expected {expected}, found {count}",
        )
    return source.replace(old, new)


def materialize_residual_laggard_source(source: str) -> str:
    source = _replace(
        source,
        "candidate-15-v5-strict-open-time",
        "candidate-15-v6-strict-open-time",
        label="strict-open-time-identity",
    )
    source = _replace(
        source,
        "C15_V5_CORE_FAMILY_QUARANTINED",
        "C15_V6_CORE_FAMILY_QUARANTINED",
        label="core-family-identity",
        expected=3,
    )

    old = '''                if continuation is None:
                    continue
                if ts_ns < self.config.evaluation_start_ns:
'''
    new = '''                if continuation is None:
                    continue
                accepted_symbols = tuple(
                    sorted(getattr(initiative_state, "accepted_symbols", ()))
                )
                laggard_symbol = residual_laggard_symbol(accepted_symbols)
                if laggard_symbol is None or symbol != laggard_symbol:
                    rejection_details = {
                        "candidate15_state": "NO_TRADE",
                        "candidate15_policy": "RESIDUAL_LAGGARD_ONLY",
                        "accepted_symbols": list(accepted_symbols),
                        "laggard_symbol": laggard_symbol,
                        "proposed_symbol": symbol,
                        "initiative_id": getattr(
                            initiative_state,
                            "scenario_id",
                            None,
                        ),
                    }
                    self.logic[logic_key].mark_rejected(
                        continuation,
                        ts_ns,
                        "C15_V6_NOT_RESIDUAL_LAGGARD",
                        rejection_details,
                    )
                    self._capture_events(logic_key)
                    self.rejections.append({
                        "type": "C15_V6_NOT_RESIDUAL_LAGGARD",
                        "observed_ts_ns": continuation.observed_ts_ns,
                        "scenario_id": continuation.scenario_id,
                        "scenario": continuation.scenario.value,
                        "symbol": symbol,
                        "reason": "C15_V6_NOT_RESIDUAL_LAGGARD",
                        "accepted_symbols": list(accepted_symbols),
                        "laggard_symbol": laggard_symbol,
                        "net_structural_r": str(continuation.net_r),
                    })
                    continue
                continuation.details["candidate15_v6_route"] = {
                    "policy": "EXCLUDED_RESIDUAL_MARKET_ONLY",
                    "accepted_symbols": list(accepted_symbols),
                    "laggard_symbol": laggard_symbol,
                    "initiative_id": getattr(
                        initiative_state,
                        "scenario_id",
                        None,
                    ),
                }
                if ts_ns < self.config.evaluation_start_ns:
'''
    source = _replace(
        source,
        old,
        new,
        label="residual-laggard-ownership-gate",
    )

    required = {
        "candidate-15-v6-strict-open-time": 1,
        "C15_V6_CORE_FAMILY_QUARANTINED": 3,
        "C15_V6_NOT_RESIDUAL_LAGGARD": 3,
        'continuation.details["candidate15_v6_route"]': 1,
        "residual_laggard_symbol(accepted_symbols)": 1,
        '"policy": "EXCLUDED_RESIDUAL_MARKET_ONLY"': 1,
    }
    bad = {
        token: (source.count(token), expected)
        for token, expected in required.items()
        if source.count(token) != expected
    }
    if bad:
        raise RuntimeError(f"Candidate 15 V6 routes were not materialized: {bad}")
    if "C15_V5_CORE_FAMILY_QUARANTINED" in source:
        raise RuntimeError("Candidate 15 V5 core-family identity survived V6 materialization")
    return source
