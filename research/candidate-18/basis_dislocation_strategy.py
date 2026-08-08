"""Candidate 18 v8: failed-auction reversal only when basis is dislocated.

The same visible reclaim can be a genuine failed auction or merely a pullback
inside continuing futures-led price discovery. Candidate 18's three development
weeks separated those states cleanly: reversals were strongest when the
perpetual contract was displaced against the intended reversal direction
(long while the perpetual traded below its index, short while it traded above).

V8 therefore treats the premium sign as market context, not as a fitted score:
- immediate SHOCK continuation is unresolved and does not enter;
- a failed-auction reversal must survive the full causal initiative window;
- the latest causal premium observation must be fresh and basis-ready;
- side * premium_index must be strictly negative;
- the weak acceptance family is kept out of this focused hypothesis.

All order execution remains owned by Candidate 18 v7 and NautilusTrader.
"""
from __future__ import annotations

import math
from typing import Any

from local_twin_trigger_strategy import Candidate18Config
from local_twin_trigger_strategy import Candidate18Strategy as _Candidate18V7Strategy
from strategy_base import PendingSetup


class Candidate18Strategy(_Candidate18V7Strategy):
    """Route only sustained failed auctions with opposing perpetual basis."""

    def __init__(self, config: Candidate18Config) -> None:
        super().__init__(config=config)
        self.diagnostics.update(
            {
                "candidate18_v8_basis_fade_admitted": 0,
                "candidate18_v8_shock_suppressed": 0,
                "candidate18_v8_nonfade_suppressed": 0,
                "candidate18_v8_stale_basis_suppressed": 0,
                "candidate18_v8_unresolved_route_suppressed": 0,
                "candidate18_v8_acceptance_suppressed": 0,
                "candidate18_v8_routes": [],
            },
        )

    def _route_no_trade(
        self,
        setup: PendingSetup,
        row: dict[str, float | int],
        *,
        reason: str,
        details: dict[str, Any],
    ) -> bool:
        self._transition(
            setup.scenario_id,
            "BASIS_DISLOCATION_ROUTER",
            int(row["ts"]),
            int(row["ts"]),
            "CLOSED",
            reason,
            float(row["close"]),
            details,
        )
        self.pending = None
        self.failure_leg = None
        return False

    def _submit_entry(
        self,
        setup: PendingSetup,
        row: dict[str, float | int],
    ) -> bool:
        quality = setup.details.get("candidate18_initiative_quality") or {}
        route = str(quality.get("route") or "UNRESOLVED")
        base_route = {
            "scenario_id": setup.scenario_id,
            "branch": setup.branch,
            "initiative_route": route,
            "event_time_ns": int(row["ts"]),
        }

        if setup.branch != "REJECTION":
            self.diagnostics["candidate18_v8_acceptance_suppressed"] = int(
                self.diagnostics["candidate18_v8_acceptance_suppressed"],
            ) + 1
            record = {**base_route, "decision": "NO_TRADE_ACCEPTANCE_FAMILY"}
            self.diagnostics["candidate18_v8_routes"].append(record)
            return self._route_no_trade(
                setup,
                row,
                reason="ACCEPTANCE_NOT_PART_OF_BASIS_FADE_HYPOTHESIS",
                details={**setup.details, **record},
            )

        if route == "SHOCK":
            self.diagnostics["candidate18_v8_shock_suppressed"] = int(
                self.diagnostics["candidate18_v8_shock_suppressed"],
            ) + 1
            record = {**base_route, "decision": "NO_TRADE_IMMEDIATE_SHOCK"}
            self.diagnostics["candidate18_v8_routes"].append(record)
            return self._route_no_trade(
                setup,
                row,
                reason="FIRST_BAR_SHOCK_IS_NOT_COMPLETED_FAILED_AUCTION",
                details={**setup.details, **record},
            )

        if route != "SUSTAINED":
            self.diagnostics["candidate18_v8_unresolved_route_suppressed"] = int(
                self.diagnostics[
                    "candidate18_v8_unresolved_route_suppressed"
                ],
            ) + 1
            record = {**base_route, "decision": "NO_TRADE_UNRESOLVED_ROUTE"}
            self.diagnostics["candidate18_v8_routes"].append(record)
            return self._route_no_trade(
                setup,
                row,
                reason="INITIATIVE_DID_NOT_COMPLETE_FULL_CAUSAL_WINDOW",
                details={**setup.details, **record},
            )

        feature = self.current_feature or {}
        basis_ready = bool(feature.get("basis_ready", False))
        premium_age_seconds = float(
            feature.get("premium_age_seconds", float("inf")),
        )
        premium_index = float(feature.get("premium_index", float("nan")))
        feature_observed_time_ns = int(feature.get("observed_time_ns", 0) or 0)
        causal_basis = (
            basis_ready
            and math.isfinite(premium_index)
            and math.isfinite(premium_age_seconds)
            and 0.0 <= premium_age_seconds <= self.config.feature_max_age_seconds
            and feature_observed_time_ns <= int(row["ts"])
        )
        basis_details = {
            **base_route,
            "basis_ready": basis_ready,
            "premium_index": premium_index,
            "premium_age_seconds": premium_age_seconds,
            "basis_observed_time_ns": feature_observed_time_ns,
            "basis_signed_with_trade": setup.side * premium_index,
            "basis_fade_pressure": -setup.side * premium_index,
        }
        if not causal_basis:
            self.diagnostics["candidate18_v8_stale_basis_suppressed"] = int(
                self.diagnostics["candidate18_v8_stale_basis_suppressed"],
            ) + 1
            record = {**basis_details, "decision": "NO_TRADE_STALE_BASIS"}
            self.diagnostics["candidate18_v8_routes"].append(record)
            return self._route_no_trade(
                setup,
                row,
                reason="BASIS_OBSERVATION_NOT_FRESH_AND_CAUSAL",
                details={**setup.details, **record},
            )

        if setup.side * premium_index >= 0.0:
            self.diagnostics["candidate18_v8_nonfade_suppressed"] = int(
                self.diagnostics["candidate18_v8_nonfade_suppressed"],
            ) + 1
            record = {**basis_details, "decision": "NO_TRADE_BASIS_NOT_OPPOSING"}
            self.diagnostics["candidate18_v8_routes"].append(record)
            return self._route_no_trade(
                setup,
                row,
                reason="PERPETUAL_BASIS_SUPPORTS_DISCOVERY_NOT_REVERSAL",
                details={**setup.details, **record},
            )

        record = {
            **basis_details,
            "decision": "ENTER_SUSTAINED_BASIS_FADE",
        }
        self.diagnostics["candidate18_v8_basis_fade_admitted"] = int(
            self.diagnostics["candidate18_v8_basis_fade_admitted"],
        ) + 1
        self.diagnostics["candidate18_v8_routes"].append(record)
        setup.details.update(
            {
                "candidate18_v8_basis_route": record,
                "candidate18_version": "v8-basis-dislocation",
            },
        )
        return super()._submit_entry(setup, row)


__all__ = ["Candidate18Config", "Candidate18Strategy"]
