"""Causal auction-completion and value-migration router for Candidate 12 I28.

This module does not create entries, stops, targets, fills, or PnL. It classifies
only the completed source auction behind a frozen I19 plan:

* a poor high/low has the exact extreme shared by at least two completed
  30-minute brackets, so the auction did not finish with one-period excess;
* an excess high/low has a unique 30-minute extreme;
* value migration compares the source-session volume-weighted value with the
  immediately preceding completed six-hour auction.

The classification is causal: every slice ends at or before the source session's
pre-existing completion timestamp. The router preserves I19 geometry and only
rejects scenario/state combinations whose auction semantics contradict the
plan family.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

import pandas as pd


@dataclass(frozen=True, slots=True)
class AuctionProfile:
    start: pd.Timestamp
    end: pd.Timestamp
    high: float
    low: float
    vwap: float
    value_low: float
    value_high: float
    high_extreme_brackets: int
    low_extreme_brackets: int

    @property
    def poor_high(self) -> bool:
        return self.high_extreme_brackets >= 2

    @property
    def poor_low(self) -> bool:
        return self.low_extreme_brackets >= 2

    def to_dict(self) -> dict[str, Any]:
        return {
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
            "high": self.high,
            "low": self.low,
            "vwap": self.vwap,
            "value_low": self.value_low,
            "value_high": self.value_high,
            "high_extreme_brackets": self.high_extreme_brackets,
            "low_extreme_brackets": self.low_extreme_brackets,
            "poor_high": self.poor_high,
            "poor_low": self.poor_low,
        }


@dataclass(frozen=True, slots=True)
class RouterDecision:
    approved: bool
    reason: str
    context: dict[str, Any]


class AuctionProfileRouter:
    """Route frozen I19 plans by completed-auction semantics."""

    _IMMEDIATE_HIGH_REJECTIONS = frozenset(
        {"ASIA_HIGH_REJECTION", "LONDON_HIGH_REJECTION"}
    )
    _IMMEDIATE_LOW_REJECTIONS = frozenset(
        {"ASIA_LOW_REJECTION", "LONDON_LOW_REJECTION"}
    )

    def __init__(
        self,
        frames: Mapping[str, pd.DataFrame],
        price_increments: Mapping[str, float],
    ) -> None:
        if set(frames) != set(price_increments):
            raise ValueError("frame and price-increment universes must match")
        self.frames = dict(frames)
        self.price_increments = {
            symbol: float(increment) for symbol, increment in price_increments.items()
        }
        for symbol, frame in self.frames.items():
            if self.price_increments[symbol] <= 0:
                raise ValueError(f"non-positive price increment for {symbol}")
            required = {"high", "low", "close", "volume"}
            missing = required.difference(frame.columns)
            if missing:
                raise ValueError(f"{symbol} frame missing columns: {sorted(missing)}")
            if not isinstance(frame.index, pd.DatetimeIndex) or frame.index.tz is None:
                raise ValueError(f"{symbol} frame index must be timezone-aware")
            if not frame.index.is_monotonic_increasing:
                raise ValueError(f"{symbol} frame must be chronological")

    @staticmethod
    def _weighted_quantile(
        values: pd.Series,
        weights: pd.Series,
        quantile: float,
    ) -> float:
        if not 0 <= quantile <= 1:
            raise ValueError("quantile must be in [0, 1]")
        table = pd.DataFrame(
            {
                "value": pd.to_numeric(values, errors="raise"),
                "weight": pd.to_numeric(weights, errors="raise"),
            }
        ).sort_values("value", kind="stable")
        table = table[table["weight"] > 0]
        if table.empty:
            raise ValueError("profile has no positive volume")
        cumulative = table["weight"].cumsum()
        cutoff = float(cumulative.iloc[-1]) * quantile
        position = cumulative.searchsorted(cutoff, side="left")
        position = min(int(position), len(table.index) - 1)
        return float(table.iloc[position]["value"])

    @staticmethod
    def _source_bounds(observed: pd.Timestamp, source: str) -> tuple[pd.Timestamp, pd.Timestamp]:
        observed = observed.tz_convert("UTC")
        day = observed.floor("D")
        if source == "ASIA":
            start = day
        elif source == "LONDON":
            start = day + pd.Timedelta(hours=6)
        else:
            raise ValueError(f"unsupported completed source session: {source!r}")
        end = start + pd.Timedelta(hours=6)
        if observed < end:
            start -= pd.Timedelta(days=1)
            end -= pd.Timedelta(days=1)
        if end > observed:
            raise ValueError("source auction is not complete at the observation timestamp")
        return start, end

    def _profile(
        self,
        symbol: str,
        start: pd.Timestamp,
        end: pd.Timestamp,
    ) -> AuctionProfile:
        frame = self.frames[symbol]
        session = frame.loc[(frame.index > start) & (frame.index <= end)]
        if len(session.index) < 300:
            raise ValueError(
                f"incomplete six-hour auction for {symbol}: {start.isoformat()} -> {end.isoformat()}"
            )
        high = float(session["high"].max())
        low = float(session["low"].min())
        if not high > low:
            raise ValueError("completed auction has no positive range")
        typical = (
            pd.to_numeric(session["high"], errors="raise")
            + pd.to_numeric(session["low"], errors="raise")
            + pd.to_numeric(session["close"], errors="raise")
        ) / 3.0
        volume = pd.to_numeric(session["volume"], errors="raise")
        total_volume = float(volume.sum())
        if total_volume <= 0:
            raise ValueError("completed auction has no positive volume")
        vwap = float((typical * volume).sum() / total_volume)
        value_low = self._weighted_quantile(typical, volume, 0.15)
        value_high = self._weighted_quantile(typical, volume, 0.85)
        thirty = session.resample(
            "30min",
            label="right",
            closed="right",
        ).agg({"high": "max", "low": "min"}).dropna()
        if len(thirty.index) != 12:
            raise ValueError(
                f"expected twelve completed 30-minute brackets, found {len(thirty.index)}"
            )
        increment = self.price_increments[symbol]
        epsilon = increment * 1e-6
        high_extreme_brackets = int(
            ((high - pd.to_numeric(thirty["high"], errors="raise")) <= increment + epsilon).sum()
        )
        low_extreme_brackets = int(
            ((pd.to_numeric(thirty["low"], errors="raise") - low) <= increment + epsilon).sum()
        )
        return AuctionProfile(
            start=start,
            end=end,
            high=high,
            low=low,
            vwap=vwap,
            value_low=value_low,
            value_high=value_high,
            high_extreme_brackets=high_extreme_brackets,
            low_extreme_brackets=low_extreme_brackets,
        )

    def evaluate(self, symbol: str, plan: Any) -> RouterDecision:
        scenario = str(plan.scenario.value)
        source = str(plan.details.get("source", ""))
        governed = (
            scenario in self._IMMEDIATE_HIGH_REJECTIONS
            or scenario in self._IMMEDIATE_LOW_REJECTIONS
            or scenario.endswith("HIGH_REACCEPTANCE")
            or scenario.endswith("LOW_REACCEPTANCE")
            or scenario.endswith("HIGH_DELAYED_REJECTION")
            or scenario.endswith("LOW_DELAYED_REJECTION")
        )
        if not governed:
            return RouterDecision(
                True,
                "AUCTION_PROFILE_NOT_REQUIRED",
                {"scenario": scenario, "governed": False},
            )
        try:
            observed = pd.Timestamp(int(plan.observed_ts_ns), unit="ns", tz="UTC")
            start, end = self._source_bounds(observed, source)
            current = self._profile(symbol, start, end)
            previous = self._profile(
                symbol,
                start - pd.Timedelta(hours=6),
                start,
            )
        except Exception as exc:
            return RouterDecision(
                False,
                "AUCTION_PROFILE_UNAVAILABLE",
                {
                    "scenario": scenario,
                    "governed": True,
                    "source": source,
                    "exception": type(exc).__name__,
                    "message": str(exc),
                },
            )

        context = {
            "scenario": scenario,
            "governed": True,
            "source": source,
            "current": current.to_dict(),
            "previous": previous.to_dict(),
            "value_migration": (
                "ABOVE_PRIOR_VALUE"
                if current.vwap > previous.value_high
                else "BELOW_PRIOR_VALUE"
                if current.vwap < previous.value_low
                else "OVERLAPPING_PRIOR_VALUE"
            ),
        }

        if scenario in self._IMMEDIATE_HIGH_REJECTIONS and current.poor_high:
            return RouterDecision(False, "POOR_HIGH_UNFINISHED_AUCTION_NOT_FADEABLE", context)
        if scenario in self._IMMEDIATE_LOW_REJECTIONS and current.poor_low:
            return RouterDecision(False, "POOR_LOW_UNFINISHED_AUCTION_NOT_FADEABLE", context)

        if scenario.endswith("HIGH_REACCEPTANCE") and not current.poor_high:
            return RouterDecision(False, "HIGH_REACCEPTANCE_LACKED_UNFINISHED_AUCTION", context)
        if scenario.endswith("LOW_REACCEPTANCE") and not current.poor_low:
            return RouterDecision(False, "LOW_REACCEPTANCE_LACKED_UNFINISHED_AUCTION", context)

        if scenario.endswith("LOW_DELAYED_REJECTION") and current.vwap < previous.value_low:
            return RouterDecision(False, "DELAYED_LOW_REVERSAL_AGAINST_LOWER_VALUE", context)
        if scenario.endswith("HIGH_DELAYED_REJECTION") and current.vwap > previous.value_high:
            return RouterDecision(False, "DELAYED_HIGH_REVERSAL_AGAINST_HIGHER_VALUE", context)

        return RouterDecision(True, "AUCTION_PROFILE_APPROVED", context)
