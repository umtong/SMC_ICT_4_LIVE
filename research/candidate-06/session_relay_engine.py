"""Session-to-session liquidity relay engine for candidate-06 v0.6."""

from __future__ import annotations

from lrb_types import PrimitiveSnapshot
from session_equilibrium_engine import SessionEquilibriumRetestEngine


class SessionLiquidityRelayEngine(SessionEquilibriumRetestEngine):
    """Let New York trade completed London-morning liquidity as well as Asia/PD.

    London expansion may raid the completed Asia range.  New York expansion may
    raid the completed 07:00-12:00 UTC London range.  Entry ordering remains the
    v0.4/v0.5 sweep -> post-sweep displacement -> first retracement sequence.
    """

    def __init__(self, params):
        super().__init__(params)
        self._london_high: float | None = None
        self._london_low: float | None = None

    def _roll_day(self, day: str) -> None:
        previous = self._current_day
        super()._roll_day(day)
        if previous is not None and day != previous:
            self._london_high = None
            self._london_low = None

    def _finish_observation(self, snapshot: PrimitiveSnapshot) -> None:
        observation = snapshot.observation
        dt = self._datetime(observation.ts_ns)
        day = dt.date().isoformat()
        self._roll_day(day)
        minute = dt.hour * 60 + dt.minute
        london_range_start = int(self.params.get("london_range_start_minute_utc", 420))
        london_range_end = int(self.params.get("london_range_end_minute_utc", 720))
        if london_range_start <= minute < london_range_end:
            self._london_high = observation.high if self._london_high is None else max(self._london_high, observation.high)
            self._london_low = observation.low if self._london_low is None else min(self._london_low, observation.low)
        super()._finish_observation(snapshot)

    def _sweep_candidates(self, snapshot: PrimitiveSnapshot, window: str):
        result = list(super()._sweep_candidates(snapshot, window))
        if (
            window == "NEW_YORK_EXPANSION"
            and bool(self.params.get("session_use_london_levels", True))
            and self._london_high is not None
            and self._london_low is not None
            and self._prior_close is not None
        ):
            observation = snapshot.observation
            minimum = float(self.params.get("session_sweep_min_atr", self.params.get("sweep_min_atr", 0.10))) * snapshot.atr
            day = self._current_day or ""
            if (
                (day, "LONDON_HIGH", "UPPER") not in self._consumed
                and self._prior_close <= self._london_high
                and observation.high >= self._london_high + minimum
            ):
                result.append(("UPPER", "LONDON_HIGH", self._london_high, self._london_high, self._london_low))
            if (
                (day, "LONDON_LOW", "LOWER") not in self._consumed
                and self._prior_close >= self._london_low
                and observation.low <= self._london_low - minimum
            ):
                result.append(("LOWER", "LONDON_LOW", self._london_low, self._london_high, self._london_low))
        return result
