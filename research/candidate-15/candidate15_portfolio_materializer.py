"""Fail-closed Candidate 15 portfolio coverage.

Candidate 15's response router is defined on the external-liquidity episode
interface exposed by SCDAM_CORE.  SESSION_I7 currently exposes a completed plan
but not a continuously observed compatible response episode.  Letting that
module trade would bypass Candidate 15's defining state decision, so its plans
are recorded and explicitly rejected as UNRESOLVED until a dedicated session
router is implemented and independently validated.
"""
from __future__ import annotations


def materialize_candidate15_portfolio_source(source: str) -> str:
    old = "                        plans.append((session_plan, session_candidate))"
    count = source.count(old)
    if count != 1:
        raise RuntimeError(
            "Candidate 15 session coverage boundary drifted: "
            f"expected one append, found {count}",
        )
    new = '''                        self.logic[self.session_logic_key].mark_rejected(
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
    materialized = source.replace(old, new)
    if materialized.count("C15_UNROUTED_SCENARIO_FAMILY") != 3:
        raise RuntimeError("Candidate 15 fail-closed session gate was not materialized")
    return materialized
