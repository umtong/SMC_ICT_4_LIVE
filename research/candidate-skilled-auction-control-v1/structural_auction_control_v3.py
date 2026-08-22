"""Structural auction control v3: causal effort-result confirmation.

V2 owns one public-structure lifecycle. V3 adds the missing path-dependent part
of skilled chart reading: the latest completed price/volume response must not show
clear opposing control. It does not rank plans or optimize a fitted score. It
classifies the completed response from causal bars as controlled, absorbed and
reversed, ambiguous, or opposing control. Only clear opposition invalidates the
natural-geometry proposal; the already-complete channel owners retain their own
stronger mechanism tests.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import math
from statistics import median
from typing import Any

from contracts_v5 import V5TradePlan
from domain import Candle
from structural_auction_control_v2 import StructuralProposal, _number, _text
from structural_auction_control_v2_strict import StructuralAuctionControlV2Bundle as _Base


@dataclass(slots=True)
class ResponseObservation:
    state: str
    directional_result: float
    directional_path: float
    close_location: float
    directional_flow: float
    relative_volume: float


def _bar_number(bar: Any, *names: str) -> float:
    for name in names:
        value = _number(getattr(bar, name, math.nan))
        if math.isfinite(value):
            return value
    return math.nan


def _bar_time(bar: Candle) -> int:
    for name in ("ts_close_ns", "close_time_ns", "event_time_ns", "ts_event"):
        value = _number(getattr(bar, name, math.nan))
        if math.isfinite(value):
            return int(value)
    return 0


def _side_sign(plan: V5TradePlan) -> int:
    side = _text(
        getattr(
            plan,
            "side",
            getattr(plan, "order_side", getattr(plan, "direction", "")),
        )
    )
    if any(token in side for token in ("BUY", "LONG", "BULL", "UP")):
        return 1
    if any(token in side for token in ("SELL", "SHORT", "BEAR", "DOWN")):
        return -1
    entry = _bar_number(plan, "entry_price", "entry", "limit_price")
    target = _bar_number(plan, "target_price", "target", "take_profit_price")
    if math.isfinite(entry) and math.isfinite(target) and target != entry:
        return 1 if target > entry else -1
    return 0


class StructuralAuctionControlV3Bundle(_Base):
    """V2 lifecycle ownership plus a causal completed-response verifier."""

    def __init__(
        self,
        symbol: str,
        tick_size: float,
        minimum_gross_rr: float = 1.0,
    ) -> None:
        super().__init__(symbol, tick_size, minimum_gross_rr)
        self._bars: dict[int, deque[Candle]] = {
            1: deque(maxlen=180),
            5: deque(maxlen=96),
            15: deque(maxlen=96),
            60: deque(maxlen=72),
        }
        self._response_trace: list[dict[str, Any]] = []

    def _response_observation(self, plan: V5TradePlan) -> ResponseObservation | None:
        sign = _side_sign(plan)
        bars = list(self._bars[1])
        if sign == 0 or len(bars) < 4:
            return None
        observed_time = int(
            _number(
                getattr(plan, "observed_time_ns", _bar_time(bars[-1])),
                float(_bar_time(bars[-1])),
            )
        )
        causal = [bar for bar in bars if _bar_time(bar) <= observed_time]
        if len(causal) < 4:
            return None
        latest = causal[-1]
        prior = causal[-4:-1]

        opened = _bar_number(latest, "open", "open_price")
        high = _bar_number(latest, "high", "high_price")
        low = _bar_number(latest, "low", "low_price")
        closed = _bar_number(latest, "close", "close_price")
        width = max(high - low, self.tick_size) if all(math.isfinite(v) for v in (high, low)) else math.nan
        if not all(math.isfinite(v) for v in (opened, high, low, closed, width)):
            return None

        directional_result = sign * (closed - opened) / width
        close_location = (closed - low) / width if sign > 0 else (high - closed) / width

        first_open = _bar_number(prior[0], "open", "open_price")
        path_ranges = []
        for bar in prior + [latest]:
            bar_high = _bar_number(bar, "high", "high_price")
            bar_low = _bar_number(bar, "low", "low_price")
            if math.isfinite(bar_high) and math.isfinite(bar_low):
                path_ranges.append(max(bar_high - bar_low, self.tick_size))
        path_scale = sum(path_ranges)
        directional_path = (
            sign * (closed - first_open) / path_scale
            if math.isfinite(first_open) and path_scale > 0
            else 0.0
        )

        volume = _bar_number(latest, "quote_volume", "volume", "base_volume")
        historical_volumes = [
            _bar_number(bar, "quote_volume", "volume", "base_volume")
            for bar in causal[-24:-1]
        ]
        historical_volumes = [value for value in historical_volumes if math.isfinite(value) and value > 0]
        volume_median = median(historical_volumes) if historical_volumes else math.nan
        relative_volume = volume / volume_median if math.isfinite(volume) and math.isfinite(volume_median) and volume_median > 0 else 1.0

        buy_volume = _bar_number(
            latest,
            "taker_buy_quote_volume",
            "taker_buy_base_volume",
            "taker_buy_volume",
            "buy_volume",
            "aggressive_buy_volume",
        )
        total_volume = _bar_number(latest, "quote_volume", "volume", "base_volume")
        if math.isfinite(buy_volume) and math.isfinite(total_volume) and total_volume > 0:
            directional_flow = sign * (2.0 * buy_volume / total_volume - 1.0)
        else:
            directional_flow = 0.0

        price_support = directional_result > 0.0 and close_location >= 0.5
        path_support = directional_path > 0.0
        flow_support = directional_flow >= 0.0
        absorbed = directional_flow < 0.0 and price_support and path_support
        clear_opposition = (
            directional_result < -0.15
            and directional_path < 0.0
            and close_location < 0.4
            and (directional_flow < 0.0 or relative_volume >= 1.25)
        )
        if clear_opposition:
            state = "OPPOSING_CONTROL"
        elif absorbed:
            state = "ABSORBED_AND_REVERSED"
        elif price_support and (path_support or flow_support):
            state = "CONTROLLED"
        else:
            state = "AMBIGUOUS"
        return ResponseObservation(
            state,
            directional_result,
            directional_path,
            close_location,
            directional_flow,
            relative_volume,
        )

    def _proposal(self, plan: V5TradePlan, source: str) -> StructuralProposal | None:
        proposal = super()._proposal(plan, source)
        if proposal is None:
            return None
        observation = self._response_observation(plan)
        if observation is None:
            self._inc("response_observation_unavailable")
            return proposal
        self._response_trace.append(
            {
                "scenario_kind": "causal_effort_result_observation",
                "event_time_ns": proposal.observed_time_ns,
                "plan_id": plan.plan_id,
                "source": source,
                "mechanism": proposal.mechanism,
                "response_state": observation.state,
                "directional_result": observation.directional_result,
                "directional_path": observation.directional_path,
                "close_location": observation.close_location,
                "directional_flow": observation.directional_flow,
                "relative_volume": observation.relative_volume,
            }
        )
        if source == "NATURAL_GEOMETRY_RESPONSE" and observation.state == "OPPOSING_CONTROL":
            self._inc("natural_geometry_rejected_clear_opposing_control")
            return None
        self._inc(f"response_state_{observation.state.lower()}")
        return proposal

    def on_bar(self, timeframe_minutes: int, bar: Candle) -> list[V5TradePlan]:
        if timeframe_minutes in self._bars:
            self._bars[timeframe_minutes].append(bar)
        return super().on_bar(timeframe_minutes, bar)

    def drain_trace(self) -> list[dict[str, Any]]:
        output = super().drain_trace() + self._response_trace
        self._response_trace = []
        return output

    @property
    def diagnostics(self) -> dict[str, Any]:
        base = super().diagnostics
        base["structural_auction_control_v3"] = {
            "one_minute_bars": len(self._bars[1]),
            "five_minute_bars": len(self._bars[5]),
            "fifteen_minute_bars": len(self._bars[15]),
            "sixty_minute_bars": len(self._bars[60]),
            "control_rule": "reject_only_clear_opposing_control_on_natural_geometry_proposals",
        }
        return base


MultiScaleScenarioBundle = StructuralAuctionControlV3Bundle