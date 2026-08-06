"""Completed fixed-interval auction liquidity relay for candidate-06."""

from __future__ import annotations

from causal_clock import source_bar_datetime
from lrb_types import PrimitiveSnapshot
from auction_relay_engine import RollingAuctionLiquidityRelayEngine


class FixedIntervalAuctionLiquidityRelayEngine(RollingAuctionLiquidityRelayEngine):
    """Use only the immediately preceding completed fixed UTC auction.

    The period is a structural clock horizon, not a rolling lookback: every
    source bar belongs to one non-overlapping UTC bucket, and its high/low become
    observable liquidity only after that bucket has fully completed.
    """

    def __init__(self, params):
        super().__init__(params)
        self._period_minutes = int(self.params.get("auction_period_minutes", 30))
        if self._period_minutes < 15 or 1440 % self._period_minutes != 0:
            raise ValueError(
                "auction_period_minutes must be at least 15 and divide one UTC day",
            )

    def _active_window(self, minute: int) -> str | None:
        default = max(1, self._period_minutes - 5)
        maximum = min(
            self._period_minutes,
            int(self.params.get("auction_entry_window_minutes", default)),
        )
        return f"COMPLETED_{self._period_minutes}M_AUCTION" if self._minute_in_hour < maximum else None

    def _sweep_candidates(self, snapshot: PrimitiveSnapshot, window: str):
        if (
            self._previous_hour_high is None
            or self._previous_hour_low is None
            or self._prior_close is None
            or self._hour_key is None
        ):
            return []
        observation = snapshot.observation
        minimum = float(
            self.params.get(
                "auction_sweep_min_atr",
                self.params.get("session_sweep_min_atr", 0.10),
            ),
        ) * snapshot.atr
        high_name = f"PREVIOUS_{self._period_minutes}M_AUCTION_HIGH"
        low_name = f"PREVIOUS_{self._period_minutes}M_AUCTION_LOW"
        scope = self._current_day or self._hour_key
        result = []
        if (
            (scope, high_name, "UPPER") not in self._consumed
            and self._prior_close <= self._previous_hour_high
            and observation.high >= self._previous_hour_high + minimum
        ):
            result.append(
                (
                    "UPPER",
                    high_name,
                    self._previous_hour_high,
                    self._previous_hour_high,
                    self._previous_hour_low,
                ),
            )
        if (
            (scope, low_name, "LOWER") not in self._consumed
            and self._prior_close >= self._previous_hour_low
            and observation.low <= self._previous_hour_low - minimum
        ):
            result.append(
                (
                    "LOWER",
                    low_name,
                    self._previous_hour_low,
                    self._previous_hour_high,
                    self._previous_hour_low,
                ),
            )
        return result

    def _roll_hour_before(self, snapshot: PrimitiveSnapshot) -> None:
        dt = source_bar_datetime(snapshot.observation.ts_ns)
        source_minute = int(dt.timestamp() // 60)
        bucket = source_minute // self._period_minutes
        position = source_minute % self._period_minutes
        key = str(bucket)
        self._minute_in_hour = position
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
