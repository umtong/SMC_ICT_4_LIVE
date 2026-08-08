"""Sequential price--aggressor-response router for Candidate 15.

The router does not predict a return target and never sizes a position.  It asks
one narrower causal question after external liquidity is traded through:

    Is aggressive flow being converted into durable price acceptance beyond the
    swept boundary, or is the flow being absorbed and the auction failing?

It calibrates a non-negative contemporaneous price/flow response from bars that
were complete before the latest sweep extreme.  Each later completed bar supplies
four bounded evidence channels: directional price change, aggressor flow,
unexplained price response, and occupancy beyond the boundary.  Evidence is
accumulated sequentially and remains UNRESOLVED until a symmetric decision
boundary is crossed.  A new sweep extreme starts a new causal episode.
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
        }


@dataclass(frozen=True, slots=True)
class RouterObservation:
    snapshot: RouterSnapshot
    created: bool
    reset: bool
    resolved_now: bool


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
    # Square-root volume weighting retains activity information while preventing
    # one exceptional bar from dominating the whole sequential episode.
    return float(bar.signed_flow) * sqrt(max(relative_volume, 0.0))


class ResponseEpisode:
    """One latest-extreme causal episode with a frozen pre-event calibration."""

    ERROR_BUDGET = 0.10
    FULL_AGREEMENT_ODDS = 2.0
    MIN_OBSERVATIONS = 2

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
        )

    def observe(self, bar: Any) -> tuple[RouterSnapshot, bool]:
        ts_ns = int(bar.ts_ns)
        if ts_ns < self.last_ts_ns:
            raise ValueError("router observations must be chronological")
        if ts_ns == self.last_ts_ns:
            return self._snapshot(ts_ns), False
        if self.state is not AuctionResolution.UNRESOLVED:
            self.last_ts_ns = ts_ns
            self.previous_close = float(bar.close)
            return self._snapshot(ts_ns), False

        close = float(bar.close)
        if close <= 0.0 or self.previous_close <= 0.0:
            raise ValueError("router prices must be positive")
        raw_return = log(close / self.previous_close)
        flow = _signed_flow(bar, self.median_volume)
        residual = raw_return - self.impact_beta * flow
        side_sign = 1.0 if self.swept_side == "HIGH" else -1.0

        price_channel = tanh(side_sign * raw_return / self.return_scale)
        aggressor_pressure = tanh(side_sign * flow / self.flow_scale)
        # Flow direction alone is not a state label.  The same aggressive burst
        # can either move price (acceptance) or be absorbed (failure).  Weight the
        # observed price response by pressure magnitude so trapped aggressors vote
        # with the realized price response rather than with their submitted side.
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

        previous_state = self.state
        if self.observations >= self.MIN_OBSERVATIONS:
            if self.evidence >= self.decision_boundary:
                self.state = AuctionResolution.ACCEPTANCE
            elif self.evidence <= -self.decision_boundary:
                self.state = AuctionResolution.FAILURE
        return self._snapshot(ts_ns), self.state is not previous_state


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

        snapshot, resolved_now = existing.observe(current_bar)
        return RouterObservation(
            snapshot=snapshot,
            created=created,
            reset=reset,
            resolved_now=resolved_now,
        )
