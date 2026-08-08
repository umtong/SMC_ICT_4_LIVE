"""Fail-closed Candidate 15 portfolio coverage and invalidation invariants."""
from __future__ import annotations

from math import isfinite


def far_stop_preserves_sweep_invalidation(
    direction: str,
    stop: float,
    sweep_invalidation: float | None,
) -> bool:
    """Return whether a FAR stop lies beyond the causal sweep invalidation.

    The failed-auction thesis is not invalidated by a traversal of a later entry
    zone while price remains on the reclaimed side of the swept extreme.  A long
    FAR therefore requires a stop at or below its original sweep stop; a short
    FAR requires a stop at or above it.
    """
    if sweep_invalidation is None:
        return False
    stop_value = float(stop)
    reference = float(sweep_invalidation)
    if not isfinite(stop_value) or not isfinite(reference):
        return False
    epsilon = max(abs(stop_value), abs(reference), 1.0) * 1e-12
    if direction == "LONG":
        return stop_value <= reference + epsilon
    if direction == "SHORT":
        return stop_value >= reference - epsilon
    return False


def materialize_candidate15_portfolio_source(source: str) -> str:
    # SESSION_I7 does not expose the continuously observed response episode that
    # defines Candidate 15.  Record it, but fail closed rather than bypassing the
    # router.
    session_old = "                        plans.append((session_plan, session_candidate))"
    if source.count(session_old) != 1:
        raise RuntimeError("Candidate 15 session coverage boundary drifted")
    session_new = '''                        self.logic[self.session_logic_key].mark_rejected(
                            session_plan,
                            ts_ns,
                            "C15_UNROUTED_SCENARIO_FAMILY",
                            {
                                "candidate15_state": "UNRESOLVED",
                                "candidate15_policy": "FAIL_CLOSED",
                                "reason": (
                                    "SESSION_I7 lacks the continuously observed "
                                    "external-liquidity response episode required "
                                    "by Candidate 15"
                                ),
                            },
                        )
                        self._capture_events(self.session_logic_key)
                        self.rejections.append({
                            "type": "C15_UNROUTED_SCENARIO_FAMILY",
                            "observed_ts_ns": session_plan.observed_ts_ns,
                            "causal_start_ts_ns": causal_start_ts_ns,
                            "scenario_id": session_plan.scenario_id,
                            "scenario": session_plan.scenario.value,
                            "market_semantic_scenario": semantic_scenario,
                            "symbol": "BTCUSDT",
                            "reason": "C15_UNROUTED_SCENARIO_FAMILY",
                            "net_structural_r": str(session_plan.net_r),
                        })'''
    source = source.replace(session_old, session_new)

    # Reject any FAR plan whose execution fallback moves the stop inside the
    # scenario's original sweep invalidation.  This is checked before leadership
    # and portfolio arbitration, and the detector receives an explicit rejected
    # lifecycle transition.
    core_old = "                leadership = self.leadership.decide(\n"
    if source.count(core_old) != 1:
        raise RuntimeError("Candidate 15 core invalidation boundary drifted")
    core_new = '''                sweep_invalidation = plan.details.get("original_sweep_stop")
                if sweep_invalidation is None:
                    sweep_invalidation = plan.details.get("sweep_extreme")
                if (
                    plan.scenario.value == "FAR"
                    and not far_stop_preserves_sweep_invalidation(
                        plan.direction.value,
                        plan.stop_price,
                        sweep_invalidation,
                    )
                ):
                    rejection_details = {
                        "candidate15_state": "NO_TRADE",
                        "candidate15_policy": "SCENARIO_TERMINAL_INVALIDATION",
                        "proposed_stop": plan.stop_price,
                        "required_sweep_invalidation": sweep_invalidation,
                        "stop_model": plan.details.get("stop_model"),
                    }
                    self.logic[symbol].mark_rejected(
                        plan,
                        ts_ns,
                        "C15_STOP_INSIDE_SWEEP_INVALIDATION",
                        rejection_details,
                    )
                    self._capture_events(symbol)
                    self.rejections.append({
                        "type": "C15_STOP_INSIDE_SWEEP_INVALIDATION",
                        "observed_ts_ns": plan.observed_ts_ns,
                        "scenario_id": plan.scenario_id,
                        "scenario": plan.scenario.value,
                        "symbol": symbol,
                        "reason": "C15_STOP_INSIDE_SWEEP_INVALIDATION",
                        "proposed_stop": plan.stop_price,
                        "required_sweep_invalidation": sweep_invalidation,
                        "net_structural_r": str(plan.net_r),
                    })
                    continue
                leadership = self.leadership.decide(
'''
    source = source.replace(core_old, core_new)
    if source.count("C15_UNROUTED_SCENARIO_FAMILY") != 3:
        raise RuntimeError("Candidate 15 session gate was not materialized")
    if source.count("C15_STOP_INSIDE_SWEEP_INVALIDATION") != 3:
        raise RuntimeError("Candidate 15 FAR invalidation gate was not materialized")
    return source
