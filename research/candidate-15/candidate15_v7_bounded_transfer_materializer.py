"""Fail-closed Candidate 15 V7 bounded-transfer portfolio materialization."""
from __future__ import annotations


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
            f"Candidate 15 V7 portfolio boundary drifted at {label}: "
            f"expected {expected}, found {count}",
        )
    return source.replace(old, new)


def materialize_bounded_transfer_source(source: str) -> str:
    source = _replace(
        source,
        "candidate-15-v6-strict-open-time",
        "candidate-15-v7-strict-open-time",
        label="strict-open-time-identity",
    )
    source = _replace(
        source,
        "ResponseQualifiedPersistentQuarterHourRouter(",
        "BoundedTransferPersistentQuarterHourRouter(",
        label="bounded-transfer-router",
    )
    source = _replace(
        source,
        "PersistentInitiativeContinuationEngine(",
        "BoundedResidualTransferContinuationEngine(",
        label="bounded-transfer-continuation",
    )
    source = _replace(
        source,
        "            self.initiative_key = QHI_ROUTER_KEY\n",
        "            self.initiative_key = V7_ROUTER_KEY\n",
        label="bounded-transfer-router-key",
    )
    source = _replace(
        source,
        "C15_V6_CORE_FAMILY_QUARANTINED",
        "C15_V7_CORE_FAMILY_QUARANTINED",
        label="core-family-identity",
        expected=3,
    )
    source = _replace(
        source,
        "C15_V6_NOT_RESIDUAL_LAGGARD",
        "C15_V7_NOT_RESIDUAL_RECEIVER",
        label="residual-ownership-identity",
        expected=3,
    )
    source = _replace(
        source,
        'continuation.details["candidate15_v6_route"]',
        'continuation.details["candidate15_v7_ownership"]',
        label="ownership-evidence-identity",
    )

    required = {
        "candidate-15-v7-strict-open-time": 1,
        "BoundedTransferPersistentQuarterHourRouter(": 1,
        "BoundedResidualTransferContinuationEngine(": 1,
        "            self.initiative_key = V7_ROUTER_KEY\n": 1,
        "C15_V7_CORE_FAMILY_QUARANTINED": 3,
        "C15_V7_NOT_RESIDUAL_RECEIVER": 3,
        'continuation.details["candidate15_v7_ownership"]': 1,
    }
    bad = {
        token: (source.count(token), expected)
        for token, expected in required.items()
        if source.count(token) != expected
    }
    if bad:
        raise RuntimeError(f"Candidate 15 V7 routes were not materialized: {bad}")
    for stale in (
        "candidate-15-v6-strict-open-time",
        "C15_V6_CORE_FAMILY_QUARANTINED",
        "C15_V6_NOT_RESIDUAL_LAGGARD",
        'continuation.details["candidate15_v6_route"]',
    ):
        if stale in source:
            raise RuntimeError(f"stale V6 identity survived V7 materialization: {stale}")
    return source
