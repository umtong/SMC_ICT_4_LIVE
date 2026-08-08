"""Candidate 15 sequential price--aggressor response router.

Version 2 treats a resolved auction state as a short-lived causal token, not as a
permanent label.  A resolution may be used on the bar where it is produced or on
the immediately following completed bar.  If inherited entry confirmation has
not arrived by then, the episode becomes STALE and is a no-trade state.  A new
sweep extreme starts a fresh episode.

This lifecycle rule is structural: entry, invalidation and target must belong to
the same newly resolved auction leg.  It is not fitted from future PnL.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from math import exp, isfinite, log, sqrt, tanh
from statistics import median
from typing import Any, Sequence


class AuctionResolution(StrEnum):
    UNRESOLVED = "UNRESOLVED"
    ACCEPTANCE = "ACCEPTANCE"
    FAILURE = "FAILURE"
    STALE = "STALE"


@dataclass(frozen=True, slots=True)
class RouterSnapshot:
    scenario_id: str
    state: AuctionResolution
    sweep_ts_ns: int
    observed_ts_ns: int
    swept_side: str
    boundary: float
    observations: int
    evidence: float
    decision_boundary: float
    impact_beta: float
    return_scale: float
    flow_scale: float
    residual_scale: float
    price_channel: float
    flow_channel: float
    residual_channel: float
    occupancy_channel: float
    bar_evidence: float
    evidence_odds_proxy: float
    resolution_ts_ns: int | None
    resolution_age_bars: int | None
    max_resolution_age_bars: int
    fresh_for_entry: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "scenario_id": self.scenario_id,
            "state": self.state.value,
            "sweep_ts_ns": self.sweep_ts_ns,
            "observed_ts_ns": self.observed_ts_ns,
            "swept_side": self.swept_side,
            "boundary": self.boundary,
            "observations": self.observations,
            "evidence": self.evidence,
            "decision_boundary": self.decision_boundary,
            "impact_beta": self.impact_beta,
            "return_scale": self.return_scale,
            "flow_scale": self.flow_scale,
            "residual_scale": self.residual_scale,
            "price_channel": self.price_channel,
            "flow_channel": self.flow_channel,
            "residual_channel": self.residual_channel,
            "occupancy_channel": self.occupancy_channel,
            "bar_evidence": self.bar_evidence,
            "evidence_odds_proxy": self.evidence_odds_proxy,
            "resolution_ts_ns": self.resolution_ts_ns,
            "resolution_age_bars": self.resolution_age_bars,
            "max_resolution_age_bars": self.max_resolution_age_bars,
            "fresh_for_entry": self.fresh_for_entry,
        }


@dataclass(frozen=True, slots=True)
class RouterObservation:
    snapshot: RouterSnapshot
    created: bool
    reset: bool
    previous_state: AuctionResolution
    state_changed: bool

    @property
    def resolved_now(self) -> bool:
        return self.state_changed and self.snapshot.state in {
            AuctionResolution.ACCEPTANCE,
            AuctionResolution.FAILURE,
        }

    @property
    def expired_now(self) -> bool:
        return self.state_changed and self.snapshot.state is AuctionResolution.STALE


def _robust_scale(values: Sequence[float], floor: float = 1e-12) -> float:
    clean = [float(value) for value in values if isfinite(float(value))]
    if not clean:
        return floor
    center = median(clean)
    mad = median(abs(value - center) for value in clean)
    scale = 1.4826 * mad
    if scale <= floor:
        scale = median(abs(value) for value in clean)
    return max(float(scale), floor)


def _signed_flow(bar: Any, median_volume: float) -> float:
    volume = max(float(bar.volume), 0.0)
    relative_volume = volume / max(float(median_volume), 1e-12)
    return float(bar.signed_flow) * sqrt(max(relative_volume, 0.0))


class ResponseEpisode:
    """One latest-extreme causal episode with frozen pre-event calibration."""

    ERROR_BUDGET = 0.10
    FULL_AGREEMENT_ODDS = 2.0
    MIN_OBSERVATIONS = 2
    # One following completed bar is required because an inherited structural
    # confirmation can become decidable one bar after the response boundary is
    # crossed.  Anything later belongs to a different micro-auction.
    MAX_RESOLUTION_AGE_BARS = 1

    def __init__(
        self,
        *,
        scenario_id: str,
        sweep_ts_ns: int,
        swept_side: str,
        boundary: float,
        atr: float,
        sweep_close: float,
        median_volume: float,
        impact_beta: float,
        return_scale: float,
        flow_scale: float,
        residual_scale: float,
    ) -> None:
        if swept_side not in {"HIGH", "LOW"}:
            raise ValueError(f"unsupported swept side: {swept_side}")
        if sweep_ts_ns < 0 or atr <= 0.0 or sweep_close <= 0.0:
            raise ValueError("invalid episode origin")
        self.scenario_id = scenario_id
        self.sweep_ts_ns = int(sweep_ts_ns)
        self.swept_side = swept_side
        self.boundary = float(boundary)
        self.atr = float(atr)
        self.median_volume = max(float(median_volume), 1e-12)
        self.impact_beta = max(float(impact_beta), 0.0)
        self.return_scale = max(float(return_scale), 1e-12)
        self.flow_scale = max(float(flow_scale), 1e-12)
        self.residual_scale = max(float(residual_scale), 1e-12)
        self.decision_boundary = log(
            (1.0 - self.ERROR_BUDGET) / self.ERROR_BUDGET,
        )
        self._full_agreement_log_odds = log(self.FULL_AGREEMENT_ODDS)
        self.state = AuctionResolution.UNRESOLVED
        self.evidence = 0.0
        self.observations = 0
        self.last_ts_ns = int(sweep_ts_ns)
        self.previous_close = float(sweep_close)
        self.last_channels = (0.0, 0.0, 0.0, 0.0)
        self.last_bar_evidence = 0.0
        self.resolution_ts_ns: int | None = None
        self.resolution_age_bars: int | None = None

    @classmethod
    def calibrated(
        cls,
        *,
        scenario_id: str,
        sweep_ts_ns: int,
        swept_side: str,
        boundary: float,
        atr: float,
        sweep_bar: Any,
        prior_bars: Sequence[Any],
    ) -> "ResponseEpisode":
        if len(prior_bars) < 3:
            raise ValueError("at least three completed pre-sweep bars are required")
        volumes = [max(float(bar.volume), 0.0) for bar in prior_bars]
        median_volume = max(median(volumes), 1e-12)
        returns: list[float] = []
        flows: list[float] = []
        for previous, current in zip(prior_bars, prior_bars[1:]):
            previous_close = float(previous.close)
            current_close = float(current.close)
            if previous_close <= 0.0 or current_close <= 0.0:
                continue
            returns.append(log(current_close / previous_close))
            flows.append(_signed_flow(current, median_volume))
        if len(returns) < 2:
            raise ValueError("insufficient valid pre-sweep response observations")
        denominator = sum(value * value for value in flows)
        impact_beta = (
            max(0.0, sum(flow * ret for flow, ret in zip(flows, returns)) / denominator)
            if denominator > 1e-18
            else 0.0
        )
        residuals = [
            ret - impact_beta * flow
            for flow, ret in zip(flows, returns)
        ]
        return cls(
            scenario_id=scenario_id,
            sweep_ts_ns=sweep_ts_ns,
            swept_side=swept_side,
            boundary=boundary,
            atr=atr,
            sweep_close=float(sweep_bar.close),
            median_volume=median_volume,
            impact_beta=impact_beta,
            return_scale=_robust_scale(returns),
            flow_scale=_robust_scale(flows),
            residual_scale=_robust_scale(residuals),
        )

    def _fresh_for_entry(self) -> bool:
        return (
            self.state in {AuctionResolution.ACCEPTANCE, AuctionResolution.FAILURE}
            and self.resolution_age_bars is not None
            and self.resolution_age_bars <= self.MAX_RESOLUTION_AGE_BARS
        )

    def _snapshot(self, observed_ts_ns: int) -> RouterSnapshot:
        price, flow, residual, occupancy = self.last_channels
        return RouterSnapshot(
            scenario_id=self.scenario_id,
            state=self.state,
            sweep_ts_ns=self.sweep_ts_ns,
            observed_ts_ns=int(observed_ts_ns),
            swept_side=self.swept_side,
            boundary=self.boundary,
            observations=self.observations,
            evidence=self.evidence,
            decision_boundary=self.decision_boundary,
            impact_beta=self.impact_beta,
            return_scale=self.return_scale,
            flow_scale=self.flow_scale,
            residual_scale=self.residual_scale,
            price_channel=price,
            flow_channel=flow,
            residual_channel=residual,
            occupancy_channel=occupancy,
            bar_evidence=self.last_bar_evidence,
            evidence_odds_proxy=exp(min(abs(self.evidence), 40.0)),
            resolution_ts_ns=self.resolution_ts_ns,
            resolution_age_bars=self.resolution_age_bars,
            max_resolution_age_bars=self.MAX_RESOLUTION_AGE_BARS,
            fresh_for_entry=self._fresh_for_entry(),
        )

    def observe(
        self,
        bar: Any,
    ) -> tuple[RouterSnapshot, bool, AuctionResolution]:
        ts_ns = int(bar.ts_ns)
        if ts_ns < self.last_ts_ns:
            raise ValueError("router observations must be chronological")
        if ts_ns == self.last_ts_ns:
            return self._snapshot(ts_ns), False, self.state

        previous_state = self.state
        close = float(bar.close)
        if close <= 0.0:
            raise ValueError("router prices must be positive")

        if self.state in {AuctionResolution.ACCEPTANCE, AuctionResolution.FAILURE}:
            self.last_ts_ns = ts_ns
            self.previous_close = close
            assert self.resolution_age_bars is not None
            self.resolution_age_bars += 1
            if self.resolution_age_bars > self.MAX_RESOLUTION_AGE_BARS:
                self.state = AuctionResolution.STALE
            return self._snapshot(ts_ns), self.state is not previous_state, previous_state

        if self.state is AuctionResolution.STALE:
            self.last_ts_ns = ts_ns
            self.previous_close = close
            return self._snapshot(ts_ns), False, previous_state

        if self.previous_close <= 0.0:
            raise ValueError("router prices must be positive")
        raw_return = log(close / self.previous_close)
        flow = _signed_flow(bar, self.median_volume)
        residual = raw_return - self.impact_beta * flow
        side_sign = 1.0 if self.swept_side == "HIGH" else -1.0

        price_channel = tanh(side_sign * raw_return / self.return_scale)
        aggressor_pressure = tanh(side_sign * flow / self.flow_scale)
        flow_channel = price_channel * abs(aggressor_pressure)
        residual_channel = tanh(side_sign * residual / self.residual_scale)
        occupancy_channel = tanh(
            side_sign * (close - self.boundary) / self.atr,
        )
        channels = (
            price_channel,
            flow_channel,
            residual_channel,
            occupancy_channel,
        )
        bar_evidence = sum(channels) / len(channels)
        self.evidence += bar_evidence * self._full_agreement_log_odds
        self.observations += 1
        self.last_ts_ns = ts_ns
        self.previous_close = close
        self.last_channels = channels
        self.last_bar_evidence = bar_evidence

        if self.observations >= self.MIN_OBSERVATIONS:
            if self.evidence >= self.decision_boundary:
                self.state = AuctionResolution.ACCEPTANCE
            elif self.evidence <= -self.decision_boundary:
                self.state = AuctionResolution.FAILURE
        if self.state is not AuctionResolution.UNRESOLVED:
            self.resolution_ts_ns = ts_ns
            self.resolution_age_bars = 0
        return self._snapshot(ts_ns), self.state is not previous_state, previous_state


class SequentialAuctionRouter:
    """Own and reset one response episode per active scenario id."""

    def __init__(self, calibration_bars: int = 120, max_episodes: int = 512) -> None:
        if calibration_bars < 10:
            raise ValueError("calibration_bars must be at least ten")
        self.calibration_bars = int(calibration_bars)
        self.max_episodes = int(max_episodes)
        self._episodes: dict[str, ResponseEpisode] = {}

    @staticmethod
    def _sweep_index(bars: Sequence[Any], sweep_ts_ns: int) -> int:
        for index in range(len(bars) - 1, -1, -1):
            ts_ns = int(bars[index].ts_ns)
            if ts_ns == sweep_ts_ns:
                return index
            if ts_ns < sweep_ts_ns:
                break
        raise ValueError("latest sweep bar is absent from causal history")

    def observe(
        self,
        *,
        scenario_id: str,
        sweep_ts_ns: int,
        swept_side: str,
        boundary: float,
        atr: float,
        bars: Sequence[Any],
        current_index: int,
    ) -> RouterObservation:
        if not 0 <= current_index < len(bars):
            raise IndexError("current_index is outside bar history")
        sweep_index = self._sweep_index(bars, int(sweep_ts_ns))
        current_bar = bars[current_index]
        existing = self._episodes.get(scenario_id)
        reset = existing is not None and (
            existing.sweep_ts_ns != int(sweep_ts_ns)
            or existing.swept_side != swept_side
            or existing.boundary != float(boundary)
        )
        created = existing is None
        if existing is None or reset:
            prior_start = max(0, sweep_index - self.calibration_bars)
            prior = bars[prior_start:sweep_index]
            existing = ResponseEpisode.calibrated(
                scenario_id=scenario_id,
                sweep_ts_ns=int(sweep_ts_ns),
                swept_side=swept_side,
                boundary=float(boundary),
                atr=float(atr),
                sweep_bar=bars[sweep_index],
                prior_bars=prior,
            )
            self._episodes[scenario_id] = existing
            if len(self._episodes) > self.max_episodes:
                oldest = next(iter(self._episodes))
                if oldest != scenario_id:
                    self._episodes.pop(oldest, None)

        snapshot, state_changed, previous_state = existing.observe(current_bar)
        return RouterObservation(
            snapshot=snapshot,
            created=created,
            reset=reset,
            previous_state=previous_state,
            state_changed=state_changed,
        )
