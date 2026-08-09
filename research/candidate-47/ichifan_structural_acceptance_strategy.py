"""Three-bar follow-through management for the frozen structural ichiFan.

The entry, structural hard stop, remote target, public 5m/90m cross exit,
8%/6% trailing logic, funding flattening, costs and current-NAV 3% risk sizing
are unchanged.  This variant adds exactly one causal state transition:

    Fifteen completed universe minutes after the entry fill, the long must have
    closed above the high of the completed five-minute source signal bar.
    Otherwise the auction has failed to accept beyond the breakout interaction
    and the position is closed at market.

The rule was selected from chronological development recorder data because it
separated expanding and failed auctions in both the design and later validation
weeks.  It never reads future MFE, final trade outcome, or a later bar while
making the decision.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any

import ichifan_strategy as _exact
import ichifan_structural_strategy as _structural
import router as _router

Candidate47IchiFanAcceptanceConfig = _structural.Candidate47IchiFanStructuralConfig
Candidate35Config = Candidate47IchiFanAcceptanceConfig
SYMBOLS = _structural.SYMBOLS
_ACCEPTANCE_AGE_MINUTES = 15


@dataclass(frozen=True, slots=True)
class AcceptanceDecision:
    state: str
    age_minutes: int
    close: float
    signal_high: float
    accepted: bool | None


def causal_signal_high_acceptance(
    *,
    close: float,
    signal_high: float,
    age_minutes: int,
    already_evaluated: bool = False,
) -> AcceptanceDecision:
    """Return WAIT, ACCEPTED, FAILED or ALREADY_EVALUATED without future data."""
    closing = float(close)
    barrier = float(signal_high)
    age = int(age_minutes)
    if not (
        math.isfinite(closing)
        and math.isfinite(barrier)
        and closing > 0.0
        and barrier > 0.0
    ):
        raise ValueError("acceptance requires finite positive completed prices")
    if age < 0:
        raise ValueError("acceptance age cannot be negative")
    if already_evaluated:
        return AcceptanceDecision(
            state="ALREADY_EVALUATED",
            age_minutes=age,
            close=closing,
            signal_high=barrier,
            accepted=None,
        )
    if age < _ACCEPTANCE_AGE_MINUTES:
        return AcceptanceDecision(
            state="WAIT",
            age_minutes=age,
            close=closing,
            signal_high=barrier,
            accepted=None,
        )
    accepted = closing > barrier
    return AcceptanceDecision(
        state="ACCEPTED" if accepted else "FAILED",
        age_minutes=age,
        close=closing,
        signal_high=barrier,
        accepted=accepted,
    )


class Candidate47IchiFanStructuralAcceptanceStrategy(
    _structural.Candidate47IchiFanStructuralStrategy,
):
    """Structural ichiFan with one post-fill signal-high acceptance decision."""

    def __init__(self, config: Candidate47IchiFanAcceptanceConfig) -> None:
        super().__init__(config)
        self.diagnostics.update(
            {
                "ichifan_acceptance_age_minutes": _ACCEPTANCE_AGE_MINUTES,
                "ichifan_acceptance_evaluations": 0,
                "ichifan_acceptance_accepted": 0,
                "ichifan_acceptance_failed_exits": 0,
                "ichifan_acceptance_missing_barrier": 0,
                "ichifan_acceptance_policy": (
                    "at-15-completed-universe-minutes-require-close-above-"
                    "completed-source-signal-high"
                ),
            }
        )

    def _submit_decision(self, decision: _router.RouteDecision, ts_event: int) -> None:
        before = int(self.diagnostics["entry_submissions"])
        super()._submit_decision(decision, ts_event)
        if int(self.diagnostics["entry_submissions"]) <= before:
            return
        scenario = self.current_scenario
        if scenario is None or decision.side <= 0:
            return
        five_minute = _exact.aggregate_five_minute(tuple(self.bars[decision.symbol]))
        signal_high = (
            float(five_minute[-2].high)
            if len(five_minute) >= 2
            else math.nan
        )
        scenario.update(
            {
                "candidate": "candidate-47-public-ichiv2-structural-acceptance",
                "acceptance_signal_high": signal_high,
                "acceptance_age_minutes": _ACCEPTANCE_AGE_MINUTES,
                "acceptance_evaluated": False,
            }
        )

    def _manage_open_position(self, ts_event: int) -> None:
        scenario = self.current_scenario
        symbol = self.current_symbol
        if scenario is not None and symbol is not None and self.bars[symbol]:
            signal_high = float(scenario.get("acceptance_signal_high", math.nan))
            age = int(self.minute_index - self.position_open_minute)
            evaluated = bool(scenario.get("acceptance_evaluated", False))
            if math.isfinite(signal_high) and signal_high > 0.0:
                decision = causal_signal_high_acceptance(
                    close=float(self.bars[symbol][-1].close),
                    signal_high=signal_high,
                    age_minutes=age,
                    already_evaluated=evaluated,
                )
                if decision.state in {"ACCEPTED", "FAILED"}:
                    scenario["acceptance_evaluated"] = True
                    scenario["acceptance_evaluation_ts"] = int(ts_event)
                    scenario["acceptance_actual_age_minutes"] = age
                    scenario["acceptance_close"] = decision.close
                    scenario["acceptance_result"] = decision.state
                    self.diagnostics["ichifan_acceptance_evaluations"] += 1
                    if decision.accepted:
                        self.diagnostics["ichifan_acceptance_accepted"] += 1
                        self._event(
                            "ICHIFAN_SIGNAL_HIGH_ACCEPTED",
                            ts_event,
                            symbol=symbol,
                            age_minutes=age,
                            close=decision.close,
                            signal_high=decision.signal_high,
                        )
                    else:
                        self.diagnostics["ichifan_acceptance_failed_exits"] += 1
                        self._request_exit(
                            ts_event,
                            "ICHIFAN_SIGNAL_HIGH_ACCEPTANCE_FAILED_EXIT",
                            symbol=symbol,
                            age_minutes=age,
                            close=decision.close,
                            signal_high=decision.signal_high,
                            reason=(
                                "NO_CLOSE_ABOVE_COMPLETED_SOURCE_SIGNAL_HIGH_"
                                "WITHIN_FIFTEEN_MINUTES"
                            ),
                        )
                        return
            elif age >= _ACCEPTANCE_AGE_MINUTES and not evaluated:
                scenario["acceptance_evaluated"] = True
                scenario["acceptance_result"] = "MISSING_BARRIER"
                self.diagnostics["ichifan_acceptance_missing_barrier"] += 1
                self._event(
                    "ICHIFAN_SIGNAL_HIGH_ACCEPTANCE_UNAVAILABLE",
                    ts_event,
                    symbol=symbol,
                    age_minutes=age,
                )
        super()._manage_open_position(ts_event)


Candidate35Strategy = Candidate47IchiFanStructuralAcceptanceStrategy

__all__ = [
    "AcceptanceDecision",
    "Candidate35Config",
    "Candidate35Strategy",
    "Candidate47IchiFanAcceptanceConfig",
    "Candidate47IchiFanStructuralAcceptanceStrategy",
    "causal_signal_high_acceptance",
]
