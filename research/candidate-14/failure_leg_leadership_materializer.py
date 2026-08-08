"""Fail-closed leadership-window materialization for Candidate 14 v10.

A confirmed accepted-auction failure is a new economic leg. Its cross-market
measurement must begin when the accepted auction first fails, not at the
original source-liquidity sweep which belongs to the opposite prior leg.

Only plans carrying ``acceptance_failure_ts_ns`` receive the new anchor. Every
ordinary FAR, Session I7 and other plan retains the inherited sweep anchor.
No semantic threshold, order, stop, target, risk or execution rule changes.
"""
from __future__ import annotations


OLD_BLOCK = '''                leadership = self.leadership.decide(
                    symbol=symbol,
                    scenario=plan.scenario.value,
                    direction=plan.direction.value,
                    sweep_ts_ns=int(plan.details.get("sweep_ts_ns", -1)),
                    confirmation_ts_ns=ts_ns,
                )
                plan.details["market_leadership"] = leadership.to_dict()'''

NEW_BLOCK = '''                # Candidate 14 v10: the failure observation owns the
                # confirmed-acceptance-failure reversal leg.  Measuring from the
                # original sweep would mix the completed opposite acceptance leg
                # into the new reversal decision.
                original_sweep_ts_ns = int(plan.details.get("sweep_ts_ns", -1))
                failure_leg_ts = plan.details.get("acceptance_failure_ts_ns")
                leadership_anchor_ts_ns = int(
                    failure_leg_ts
                    if failure_leg_ts is not None
                    else original_sweep_ts_ns
                )
                leadership = self.leadership.decide(
                    symbol=symbol,
                    scenario=plan.scenario.value,
                    direction=plan.direction.value,
                    sweep_ts_ns=leadership_anchor_ts_ns,
                    confirmation_ts_ns=ts_ns,
                )
                plan.details["market_leadership_anchor_ts_ns"] = leadership_anchor_ts_ns
                plan.details["market_leadership_anchor_model"] = (
                    "ACCEPTANCE_FAILURE_OBSERVATION"
                    if failure_leg_ts is not None
                    else "ORIGINAL_SOURCE_SWEEP"
                )
                plan.details["market_leadership"] = leadership.to_dict()'''


def materialize_failure_leg_leadership_source(source: str) -> str:
    count = source.count(OLD_BLOCK)
    if count != 1:
        raise RuntimeError(
            "Candidate 14 v10 leadership boundary drifted: "
            f"expected one inherited SCDAM decision block, found {count}",
        )
    materialized = source.replace(OLD_BLOCK, NEW_BLOCK, 1)
    if materialized.count("Candidate 14 v10: the failure observation owns") != 1:
        raise RuntimeError("Candidate 14 v10 leadership anchor was not materialized once")
    return materialized
