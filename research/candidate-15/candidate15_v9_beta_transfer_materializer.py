"""Fail-closed Candidate 15 V9 beta-coherent portfolio materialization."""
from __future__ import annotations


def _replace(source: str, old: str, new: str, *, label: str, expected: int = 1) -> str:
    count = source.count(old)
    if count != expected:
        raise RuntimeError(
            f"Candidate 15 V9 portfolio boundary drifted at {label}: "
            f"expected {expected}, found {count}",
        )
    return source.replace(old, new)


def materialize_beta_coherent_transfer_source(source: str) -> str:
    source = _replace(
        source,
        "candidate-15-v8-strict-open-time",
        "candidate-15-v9-strict-open-time",
        label="strict-open-time-identity",
    )
    source = _replace(
        source,
        "ManagedTransferPersistentQuarterHourRouter(",
        "BetaCoherentTransferPersistentQuarterHourRouter(",
        label="beta-router",
    )
    source = _replace(
        source,
        "ManagedResidualTransferContinuationEngine(",
        "BetaCoherentResidualTransferContinuationEngine(",
        label="beta-continuation",
    )
    source = _replace(
        source,
        "            self.initiative_key = V8_ROUTER_KEY\n",
        "            self.initiative_key = V9_ROUTER_KEY\n",
        label="beta-router-key",
    )
    source = _replace(
        source,
        "C15_V8_CORE_FAMILY_QUARANTINED",
        "C15_V9_CORE_FAMILY_QUARANTINED",
        label="core-family-identity",
        expected=3,
    )
    source = _replace(
        source,
        "C15_V8_NOT_RESIDUAL_RECEIVER",
        "C15_V9_NOT_BETA_RECEIVER",
        label="receiver-identity",
        expected=3,
    )
    source = _replace(
        source,
        'continuation.details["candidate15_v8_ownership"]',
        'continuation.details["candidate15_v9_ownership"]',
        label="ownership-evidence-identity",
    )
    source = _replace(
        source,
        'self.active_plan.details.get("candidate15_v8_transfer")',
        'self.active_plan.details.get("candidate15_v9_transfer")',
        label="management-transfer-evidence",
    )

    required = {
        "candidate-15-v9-strict-open-time": 1,
        "BetaCoherentTransferPersistentQuarterHourRouter(": 1,
        "BetaCoherentResidualTransferContinuationEngine(": 1,
        "            self.initiative_key = V9_ROUTER_KEY\n": 1,
        "C15_V9_CORE_FAMILY_QUARANTINED": 3,
        "C15_V9_NOT_BETA_RECEIVER": 3,
        'continuation.details["candidate15_v9_ownership"]': 1,
        'self.active_plan.details.get("candidate15_v9_transfer")': 1,
        "TRANSFER_STOP_MODIFICATION_SUBMITTED": 1,
    }
    bad = {
        token: (source.count(token), expected)
        for token, expected in required.items()
        if source.count(token) != expected
    }
    if bad:
        raise RuntimeError(f"Candidate 15 V9 routes were not materialized: {bad}")
    for stale in (
        "candidate-15-v8-strict-open-time",
        "C15_V8_CORE_FAMILY_QUARANTINED",
        "C15_V8_NOT_RESIDUAL_RECEIVER",
        'continuation.details["candidate15_v8_ownership"]',
        'self.active_plan.details.get("candidate15_v8_transfer")',
    ):
        if stale in source:
            raise RuntimeError(f"stale V8 identity survived V9 materialization: {stale}")
    return source
