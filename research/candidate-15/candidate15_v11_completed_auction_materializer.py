"""Candidate 15 V11 completed-auction state-router materialization.

The beta-coherent transfer state is deliberately sparse.  V11 therefore restores
only the independently evidenced Candidate 13 completed-source auction families
(FAR and AAC) and lets them compete with beta transfer in the already existing
one-global-slot portfolio mutex.  SESSION_I7 and every other family remain
fail-closed.

This transform is applied after the V9 beta transform.  It does not modify
auction detection, market-leadership semantics, entries, stops, targets, sizing,
fees, Nautilus order handling, or portfolio arbitration.
"""
from __future__ import annotations

from typing import Any


FAILED_AUCTION_FAMILY = "COMPLETED_SOURCE_FAILED_AUCTION"
ACCEPTED_AUCTION_FAMILY = "COMPLETED_SOURCE_ACCEPTED_AUCTION"
UNRESOLVED_FAMILY = "UNRESOLVED_NO_TRADE"


def completed_source_auction_family(plan: Any) -> str | None:
    """Return the causally complete Candidate 13 family carried by ``plan``.

    The classification is structural, not score based.  It requires evidence
    already emitted by the frozen auction engine and never consults fills, PnL,
    future bars, symbol identity, dates, or parameterized outcome thresholds.
    """
    scenario_obj = getattr(plan, "scenario", None)
    scenario = str(getattr(scenario_obj, "value", scenario_obj or ""))
    details = getattr(plan, "details", None)
    if not isinstance(details, dict):
        return None

    common_required = (
        "pool_source",
        "range_id",
        "sweep_ts_ns",
        "zone_low",
        "zone_high",
    )
    if any(details.get(key) is None for key in common_required):
        return None

    if scenario == "FAR":
        # Candidate 13 FAR either retains the source-sweep structural stop or,
        # after unanimous exhaustion, uses the first displacement-void repair.
        # Both are first-plan execution variants of the same failed auction.
        if details.get("structural_stop") is None and details.get("stop_model") is None:
            return None
        return FAILED_AUCTION_FAMILY

    if scenario == "AAC":
        if details.get("defended_pullback") is None or details.get("source_boundary") is None:
            return None
        return ACCEPTED_AUCTION_FAMILY

    return None


def _replace(source: str, old: str, new: str, *, label: str, expected: int = 1) -> str:
    count = source.count(old)
    if count != expected:
        raise RuntimeError(
            f"Candidate 15 V11 state-router boundary drifted at {label}: "
            f"expected {expected}, found {count}",
        )
    return source.replace(old, new)


def materialize_v11_completed_auction_router_source(source: str) -> str:
    quarantine = '''                self._logic_for_plan(plan, symbol).mark_rejected(
                    plan,
                    ts_ns,
                    "C15_V9_CORE_FAMILY_QUARANTINED",
                    {
                        "candidate15_state": "NO_TRADE",
                        "candidate15_policy": "FAILED_FAMILY_QUARANTINE",
                        "prior_evidence": "V3 predeclared screen failed activity and growth",
                    },
                )
                self._capture_events(self._logic_key_for_plan(plan, symbol))
                self.rejections.append({
                    "type": "C15_V9_CORE_FAMILY_QUARANTINED",
                    "observed_ts_ns": plan.observed_ts_ns,
                    "scenario_id": plan.scenario_id,
                    "scenario": plan.scenario.value,
                    "symbol": symbol,
                    "reason": "C15_V9_CORE_FAMILY_QUARANTINED",
                    "net_structural_r": str(plan.net_r),
                })
                continue
                leadership = self.leadership.decide(
'''
    routed = '''                completed_source_family = completed_source_auction_family(plan)
                if completed_source_family is None:
                    self._logic_for_plan(plan, symbol).mark_rejected(
                        plan,
                        ts_ns,
                        "C15_V11_UNRESOLVED_CORE_FAMILY",
                        {
                            "candidate15_state": "UNRESOLVED",
                            "candidate15_policy": "FAIL_CLOSED",
                            "scenario": plan.scenario.value,
                        },
                    )
                    self._capture_events(self._logic_key_for_plan(plan, symbol))
                    self.rejections.append({
                        "type": "C15_V11_UNRESOLVED_CORE_FAMILY",
                        "observed_ts_ns": plan.observed_ts_ns,
                        "scenario_id": plan.scenario_id,
                        "scenario": plan.scenario.value,
                        "symbol": symbol,
                        "reason": "C15_V11_UNRESOLVED_CORE_FAMILY",
                        "net_structural_r": str(plan.net_r),
                    })
                    continue
                plan.details["module"] = completed_source_family
                plan.details["route"] = completed_source_family
                plan.details["candidate15_v11_family"] = {
                    "family": completed_source_family,
                    "source": plan.details.get("pool_source"),
                    "source_range_id": plan.details.get("range_id"),
                    "source_sweep_ts_ns": plan.details.get("sweep_ts_ns"),
                    "first_plan_only": True,
                    "selection_basis": "COMPLETED_SOURCE_AUCTION_STATE",
                }
                leadership = self.leadership.decide(
'''
    source = _replace(
        source,
        quarantine,
        routed,
        label="completed-source-auction-reactivation",
    )

    required = {
        "completed_source_auction_family(plan)": 1,
        'plan.details["candidate15_v11_family"]': 1,
        '"first_plan_only": True': 1,
        '"C15_V11_UNRESOLVED_CORE_FAMILY"': 3,
        '"COMPLETED_SOURCE_AUCTION_STATE"': 1,
        "BetaCoherentTransferPersistentQuarterHourRouter(": 1,
        "BetaCoherentResidualTransferContinuationEngine(": 1,
        "C15_UNROUTED_SCENARIO_FAMILY": 3,
    }
    bad = {
        token: (source.count(token), expected)
        for token, expected in required.items()
        if source.count(token) != expected
    }
    if bad:
        raise RuntimeError(f"Candidate 15 V11 state routes were not materialized for V11: {bad}")
    if "C15_V9_CORE_FAMILY_QUARANTINED" in source:
        raise RuntimeError("V11 core-family quarantine survived state-router materialization")
    # Candidate 13's failed V17 missed-retrace re-arm must not enter this branch.
    for forbidden in ("REARM_AFTER_MISSED_RETRACE", "rearmed_parent", "V17_REARM"):
        if forbidden in source:
            raise RuntimeError(f"forbidden re-arm logic entered V9: {forbidden}")
    return source
