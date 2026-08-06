"""Rolling completed-hour auction liquidity relay for candidate-06 v0.7."""

from __future__ import annotations

from causal_clock import source_bar_datetime
from lrb_types import PrimitiveSnapshot
from session_equilibrium_engine import SessionEquilibriumRetestEngine


class RollingAuctionLiquidityRelayEngine(SessionEquilibriumRetestEngine):
    """Trade only reactions to the immediately preceding completed hour.

    Each UTC hour is an auction/dealing range.  Its high and low become objective
    liquidity only after the hour completes.  During the next hour, a boundary
    sweep can branch into the inherited rejection-displacement-retracement or
    acceptance-retest continuation paths.  No incomplete-hour extreme is used.
    """

    def __init__(self, params):
        super().__init__(params)
        self._hour_key: str | None = None
        self._hour_high: float | None = None
        self._hour_low: float | None = None
        self._previous_hour_key: str | None = None
        self._previous_hour_high: float | None = None
        self._previous_hour_low: float | None = None
        self._minute_in_hour = 0

    def observe(self, snapshot: PrimitiveSnapshot, *, allow_new: bool):
        self._roll_hour_before(snapshot)
        return super().observe(snapshot, allow_new=allow_new)

    def _active_window(self, minute: int) -> str | None:
        maximum = int(self.params.get("auction_entry_window_minutes", 55))
        return "ROLLING_HOURLY_AUCTION" if self._minute_in_hour < maximum else None

    def _sweep_candidates(self, snapshot: PrimitiveSnapshot, window: str):
        if (
            self._previous_hour_high is None
            or self._previous_hour_low is None
            or self._prior_close is None
            or self._hour_key is None
        ):
            return []
        observation = snapshot.observation
        minimum = float(self.params.get("auction_sweep_min_atr", self.params.get("session_sweep_min_atr", 0.10))) * snapshot.atr
        result = []
        # The parent records consumption with its current-day scope.  We clear
        # that set on each hourly roll, so this matching key means one episode
        # per side of one completed-hour range, never repeated signals from the
        # same liquidity event.
        scope = self._current_day or self._hour_key
        if (
            (scope, "PREVIOUS_HOUR_HIGH", "UPPER") not in self._consumed
            and self._prior_close <= self._previous_hour_high
            and observation.high >= self._previous_hour_high + minimum
        ):
            result.append(
                (
                    "UPPER",
                    "PREVIOUS_HOUR_HIGH",
                    self._previous_hour_high,
                    self._previous_hour_high,
                    self._previous_hour_low,
                )
            )
        if (
            (scope, "PREVIOUS_HOUR_LOW", "LOWER") not in self._consumed
            and self._prior_close >= self._previous_hour_low
            and observation.low <= self._previous_hour_low - minimum
        ):
            result.append(
                (
                    "LOWER",
                    "PREVIOUS_HOUR_LOW",
                    self._previous_hour_low,
                    self._previous_hour_high,
                    self._previous_hour_low,
                )
            )
        return result

    def _finish_observation(self, snapshot: PrimitiveSnapshot) -> None:
        super()._finish_observation(snapshot)
        observation = snapshot.observation
        self._hour_high = observation.high if self._hour_high is None else max(self._hour_high, observation.high)
        self._hour_low = observation.low if self._hour_low is None else min(self._hour_low, observation.low)

    def _roll_hour_before(self, snapshot: PrimitiveSnapshot) -> None:
        # `ts_ns` is the completed-bar event time.  The 01:00 event belongs to
        # the 00:59-01:00 source interval and must finish the 00 UTC auction.
        dt = source_bar_datetime(snapshot.observation.ts_ns)
        key = dt.strftime("%Y-%m-%dT%H")
        self._minute_in_hour = dt.minute
        if self._hour_key is None:
            self._hour_key = key
            return
        if key == self._hour_key:
            return
        self._previous_hour_key = self._hour_key
        self._previous_hour_high = self._hour_high
        self._previous_hour_low = self._hour_low
        self._hour_key = key
        self._hour_high = None
        self._hour_low = None
        self._consumed.clear()
