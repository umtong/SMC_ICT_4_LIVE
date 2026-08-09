"""Fifteen-minute positive-response management for frozen structural ichiFan.

Entry, structural hard stop, target, public trend-cross exit, trailing logic,
funding flattening, costs and current-NAV 3% sizing are unchanged.  Exactly one
causal management transition is added:

    Fifteen completed universe minutes after the entry fill, the current
    completed one-minute close must be strictly above the expected entry price.
    A close at or below entry means the sponsored breakout has not produced a
    positive auction response and the position is closed at market.

The rule uses no future MFE, final outcome or later observation.  It is the
simplest state retained from the recorder after the stricter signal-high barrier
failed at the account level.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any

import ichifan_structural_strategy as _structural

Candidate47IchiFanResponseConfig = _structural.Candidate47IchiFanStructuralConfig
Candidate35Config = Candidate47IchiFanResponseConfig
_RESPONSE_AGE_MINUTES = 15


@dataclass(frozen=True, slots=True)
class ResponseDecision:
    state: str
    age_minutes: int
    close: float
    entry: float
    positive: bool | None


def causal_positive_response(
    *,
    close: float,
    entry: float,
    age_minutes: int,
    already_evaluated: bool = False,
) -> ResponseDecision:
    """Return WAIT, POSITIVE, FAILED or ALREADY_EVALUATED causally."""
    closing = float(close)
    expected_entry = float(entry)
    age = int(age_minutes)
    if not (
        math.isfinite(closing)
        and math.isfinite(expected_entry)
        and closing > 0.0
        and expected_entry > 0.0
    ):
        raise ValueError("response requires finite positive completed prices")
    if age < 0:
        raise ValueError("response age cannot be negative")
    if already_evaluated:
        return ResponseDecision(
            state="ALREADY_EVALUATED",
            age_minutes=age,
            close=closing,
            entry=expected_entry,
            positive=None,
        )
    if age < _RESPONSE_AGE_MINUTES:
        return ResponseDecision(
            state="WAIT",
            age_minutes=age,
            close=closing,
            entry=expected_entry,
            positive=None,
        )
    positive = closing > expected_entry
    return ResponseDecision(
        state="POSITIVE" if positive else "FAILED",
        age_minutes=age,
        close=closing,
        entry=expected_entry,
        positive=positive,
    )


class Candidate47IchiFanStructuralResponseStrategy(
    _structural.Candidate47IchiFanStructuralStrategy,
):
    """Frozen structural policy with one fifteen-minute response decision."""

    def __init__(self, config: Candidate47IchiFanResponseConfig) -> None:
        super().__init__(config)
        self.diagnostics.update(
            {
                "ichifan_response_age_minutes": _RESPONSE_AGE_MINUTES,
                "ichifan_response_evaluations": 0,
                "ichifan_response_positive": 0,
                "ichifan_response_failed_exits": 0,
                "ichifan_response_invalid": 0,
                "ichifan_response_policy": (
                    "at-fifteen-completed-universe-minutes-require-close-"
                    "strictly-above-expected-entry"
                ),
            }
        )

    def on_position_opened(self, event: Any) -> None:
        super().on_position_opened(event)
        if self.current_scenario is not None:
            self.current_scenario.update(
                {
                    "candidate": "candidate-47-public-ichiv2-structural-response",
                    "response_age_minutes": _RESPONSE_AGE_MINUTES,
                    "response_evaluated": False,
                }
            )

    def _manage_open_position(self, ts_event: int) -> None:
        scenario = self.current_scenario
        symbol = self.current_symbol
        if scenario is not None and symbol is not None and self.bars[symbol]:
            age = int(self.minute_index - self.position_open_minute)
            evaluated = bool(scenario.get("response_evaluated", False))
            try:
                decision = causal_positive_response(
                    close=float(self.bars[symbol][-1].close),
                    entry=float(scenario.get("entry_reference", math.nan)),
                    age_minutes=age,
                    already_evaluated=evaluated,
                )
            except ValueError as error:
                if age >= _RESPONSE_AGE_MINUTES and not evaluated:
                    scenario["response_evaluated"] = True
                    scenario["response_result"] = "INVALID"
                    self.diagnostics["ichifan_response_invalid"] += 1
                    self._event(
                        "ICHIFAN_POSITIVE_RESPONSE_INVALID",
                        ts_event,
                        symbol=symbol,
                        age_minutes=age,
                        failure_cause=str(error),
                    )
            else:
                if decision.state in {"POSITIVE", "FAILED"}:
                    scenario["response_evaluated"] = True
                    scenario["response_evaluation_ts"] = int(ts_event)
                    scenario["response_actual_age_minutes"] = age
                    scenario["response_close"] = decision.close
                    scenario["response_result"] = decision.state
                    self.diagnostics["ichifan_response_evaluations"] += 1
                    if decision.positive:
                        self.diagnostics["ichifan_response_positive"] += 1
                        self._event(
                            "ICHIFAN_POSITIVE_RESPONSE_CONFIRMED",
                            ts_event,
                            symbol=symbol,
                            age_minutes=age,
                            close=decision.close,
                            entry=decision.entry,
                        )
                    else:
                        self.diagnostics["ichifan_response_failed_exits"] += 1
                        self._request_exit(
                            ts_event,
                            "ICHIFAN_POSITIVE_RESPONSE_FAILED_EXIT",
                            symbol=symbol,
                            age_minutes=age,
                            close=decision.close,
                            entry=decision.entry,
                            failure_cause=(
                                "NO_CLOSE_STRICTLY_ABOVE_EXPECTED_ENTRY_"
                                "WITHIN_FIFTEEN_MINUTES"
                            ),
                        )
                        return
        super()._manage_open_position(ts_event)


Candidate35Strategy = Candidate47IchiFanStructuralResponseStrategy

__all__ = [
    "Candidate35Config",
    "Candidate35Strategy",
    "Candidate47IchiFanResponseConfig",
    "Candidate47IchiFanStructuralResponseStrategy",
    "ResponseDecision",
    "causal_positive_response",
]
