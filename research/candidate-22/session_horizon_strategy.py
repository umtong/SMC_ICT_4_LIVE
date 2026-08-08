"""Candidate 22 revision: keep the first-retest state until session end.

The initial screen reused Candidate 19's four-bar initiative horizon and armed
12-18 valid session expansions per week, but every episode either expired in
four minutes or failed its first touch.  That horizon belongs to a one-minute
failed-auction leg, not to a fifteen-minute opening range embedded in a
four-hour session.  This overlay changes no state evidence, entry, risk, cost,
stop or target rule.  It only lets the already-frozen first-retest contract
observe the remainder of the same auction session.
"""
from __future__ import annotations

from dataclasses import replace

from session_expansion_strategy import Candidate22Config
from session_expansion_strategy import Candidate22Strategy as _Candidate22Strategy


class Candidate22Strategy(_Candidate22Strategy):
    """Use the natural session boundary as the retest expiry."""

    def _session_duration_bars(self) -> int:
        hours = sorted({int(hour) for hour in self.config.session_hours})
        if not hours:
            raise RuntimeError("session_hours cannot be empty")
        if self.c22_session_key is None:
            raise RuntimeError("candidate22 session key is missing")
        current = int(self.c22_session_key) % 100
        try:
            index = hours.index(current)
        except ValueError as exc:
            raise RuntimeError(f"session boundary {current} not in session_hours") from exc
        following = hours[(index + 1) % len(hours)]
        span_hours = (following - current) % 24
        if span_hours == 0:
            span_hours = 24
        return span_hours * 60

    def _arm_c22_expansion(self, *, side, row) -> None:
        super()._arm_c22_expansion(side=side, row=row)
        state = self.c22_retest
        setup = self.pending
        if state is None or setup is None:
            raise RuntimeError("base Candidate 22 did not freeze expansion state")

        remaining = self._session_duration_bars() - self.c22_session_bar_count
        expires_index = self.bar_index + max(1, remaining)
        self.c22_retest = replace(state, expires_index=expires_index)
        setup.expires_index = expires_index
        setup.details["candidate22_retest_horizon"] = "REMAINDER_OF_SAME_FOUR_HOUR_SESSION"
        setup.details["retest_expires_index"] = expires_index
        setup.details["retest_remaining_bars"] = max(1, remaining)
        self._transition(
            setup.scenario_id,
            "SESSION_EXPANSION_HORIZON_ALIGNED",
            int(row["ts"]),
            int(row["ts"]),
            "FIRST_RETEST_PENDING",
            "FIRST_RETEST_REMAINS_VALID_ONLY_UNTIL_SAME_SESSION_END",
            float(row["close"]),
            setup.details,
        )


__all__ = ["Candidate22Config", "Candidate22Strategy"]
