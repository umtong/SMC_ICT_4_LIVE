#!/usr/bin/env python3
"""Candidate 05 v55: route spot-led price discovery to one pullback trade.

v46 remains authoritative for genuine perpetual inventory failures. v55 adds an
economically separate continuation family: completed spot order flow must lead
perpetual return, perpetual price and broad flow must then accept that lead, and
only an aligned internal-liquidity pullback may use the inherited causal CHoCH,
live target, price-capped bracket, fees, slippage, current-NAV sizing and
NautilusTrader lifecycle.

The implementation intentionally reuses the already-tested internal pool and
inventory-transfer machinery. A temporary accepted context is visible only
inside the detector call and is restored immediately; external pools, targets,
orders and account state are not replaced.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any

from spot_led_repricing_logic import SPOT_CONTEXT_MAX_AGE_BARS
from spot_led_repricing_logic import SPOT_CONTEXT_MIN_AGE_BARS
from spot_led_repricing_logic import spot_context_accepted
from spot_led_repricing_logic import spot_context_entry_eligible
from spot_led_repricing_logic import spot_context_invalidated
from spot_led_repricing_logic import spot_led_repricing_direction
from strategy import LiquidityResponseConfig
from strategy import QuarterHourContext
from strategy_v46_no_post_retrace_breakaway import NoPostRetraceBreakawayStrategy


BRANCH = "SPOT_LED_PRICE_DISCOVERY_PULLBACK"
_REQUIRED_SPOT_FEATURES = {
    "spot_flow_15s",
    "spot_flow_60s",
    "spot_flow_3m",
    "spot_ret_60s_bps",
    "spot_efficiency_60s",
    "spot_notional_burst",
    "spot_trade_vwap_60s",
    "perp_minus_spot_return_bps",
    "perp_spot_basis_bps",
}


@dataclass(slots=True)
class SpotPriceDiscoveryContext:
    direction: int
    created_index: int
    created_ts: int
    boundary_high: float
    boundary_low: float
    boundary_close: float
    atr: float
    favorable_extreme: float
    accepted: bool
    details: dict[str, Any]


class SpotLedPriceDiscoveryStrategy(NoPostRetraceBreakawayStrategy):
    """Keep v46 reversals and add one mutually exclusive spot-led continuation."""

    def __init__(self, config: LiquidityResponseConfig) -> None:
        super().__init__(config)
        self.spot_price_discovery_context: SpotPriceDiscoveryContext | None = None
        self.diagnostics.update(
            {
                "spot_led_signal_bars": 0,
                "spot_led_same_direction_repeats": 0,
                "spot_led_opposite_signal_conflicts": 0,
                "spot_led_contexts_created": 0,
                "spot_led_contexts_accepted": 0,
                "spot_led_contexts_invalidated": 0,
                "spot_led_contexts_expired": 0,
                "spot_led_context_detector_activations": 0,
                "spot_led_internal_pullbacks_armed": 0,
                "spot_led_side_mismatch_rejections": 0,
                "spot_led_submissions": 0,
            },
        )

    def on_start(self) -> None:
        super().on_start()
        available = set(self.features[0]) if self.features else set()
        missing = sorted(_REQUIRED_SPOT_FEATURES - available)
        if missing:
            raise RuntimeError(
                "spot price-discovery feature contract was not installed: "
                f"{missing}",
            )

    def on_bar(self, bar: Any) -> None:
        # Parent consumes the current completed bar first. Updating the spot
        # context afterwards makes it available no earlier than the next bar.
        super().on_bar(bar)
        if not self.bars:
            return
        self._advance_spot_price_discovery_context(self.bars[-1])

    def _spot_observation_ready(self) -> bool:
        feature = self.current_feature
        if feature is None or not bool(feature.get("feature_ready", False)):
            return False
        return all(
            math.isfinite(float(feature.get(name, math.nan)))
            for name in _REQUIRED_SPOT_FEATURES
        )

    def _advance_spot_price_discovery_context(
        self,
        row: dict[str, float | int],
    ) -> None:
        atr = self._atr()
        if not math.isfinite(atr) or atr <= 0.0:
            return

        context = self.spot_price_discovery_context
        if context is not None:
            age = self.bar_index - context.created_index
            if spot_context_invalidated(
                direction=context.direction,
                boundary_low=context.boundary_low,
                boundary_high=context.boundary_high,
                current_close=float(row["close"]),
                atr=context.atr,
            ):
                self.spot_price_discovery_context = None
                self.diagnostics["spot_led_contexts_invalidated"] += 1
                context = None
            elif age > SPOT_CONTEXT_MAX_AGE_BARS:
                self.spot_price_discovery_context = None
                self.diagnostics["spot_led_contexts_expired"] += 1
                context = None
            else:
                context.favorable_extreme = (
                    max(context.favorable_extreme, float(row["high"]))
                    if context.direction > 0
                    else min(context.favorable_extreme, float(row["low"]))
                )
                if (
                    not context.accepted
                    and self._spot_observation_ready()
                    and spot_context_accepted(
                        direction=context.direction,
                        boundary_close=context.boundary_close,
                        favorable_extreme=context.favorable_extreme,
                        atr=context.atr,
                        perpetual_flow_3m=self._feature("flow_3m"),
                    )
                ):
                    context.accepted = True
                    context.details.update(
                        {
                            "accepted_index": self.bar_index,
                            "accepted_ts": int(row["ts"]),
                            "accepted_close": float(row["close"]),
                            "accepted_perpetual_flow_3m": self._feature("flow_3m"),
                            "accepted_favorable_extreme": context.favorable_extreme,
                        },
                    )
                    self.diagnostics["spot_led_contexts_accepted"] += 1

        if not self._spot_observation_ready():
            return
        direction = spot_led_repricing_direction(
            spot_flow_15s=self._feature("spot_flow_15s"),
            spot_flow_60s=self._feature("spot_flow_60s"),
            spot_notional_burst=self._feature("spot_notional_burst"),
            spot_return_bps=self._feature("spot_ret_60s_bps"),
            spot_efficiency=self._feature("spot_efficiency_60s"),
            perpetual_return_bps=self._feature("ret_60s_bps"),
        )
        if direction == 0:
            return
        self.diagnostics["spot_led_signal_bars"] += 1

        current = self.spot_price_discovery_context
        if current is not None:
            if current.direction == direction:
                self.diagnostics["spot_led_same_direction_repeats"] += 1
                current.details["same_direction_signal_count"] = int(
                    current.details.get("same_direction_signal_count", 1),
                ) + 1
                return
            # Opposing completed spot leads make the market state ambiguous. Do
            # not reverse on the same observation; require a new clean lead.
            self.spot_price_discovery_context = None
            self.diagnostics["spot_led_opposite_signal_conflicts"] += 1
            return

        details = {
            "spot_signal_index": self.bar_index,
            "spot_signal_ts": int(row["ts"]),
            "spot_direction": direction,
            "spot_flow_15s": self._feature("spot_flow_15s"),
            "spot_flow_60s": self._feature("spot_flow_60s"),
            "spot_flow_3m": self._feature("spot_flow_3m"),
            "spot_ret_60s_bps": self._feature("spot_ret_60s_bps"),
            "perpetual_ret_60s_bps": self._feature("ret_60s_bps"),
            "perp_minus_spot_return_bps": self._feature(
                "perp_minus_spot_return_bps",
            ),
            "spot_efficiency_60s": self._feature("spot_efficiency_60s"),
            "spot_notional_burst": self._feature("spot_notional_burst"),
            "perp_spot_basis_bps": self._feature("perp_spot_basis_bps"),
            "same_direction_signal_count": 1,
        }
        self.spot_price_discovery_context = SpotPriceDiscoveryContext(
            direction=direction,
            created_index=self.bar_index,
            created_ts=int(row["ts"]),
            boundary_high=float(row["high"]),
            boundary_low=float(row["low"]),
            boundary_close=float(row["close"]),
            atr=atr,
            favorable_extreme=(
                float(row["high"]) if direction > 0 else float(row["low"])
            ),
            accepted=False,
            details=details,
        )
        self.diagnostics["spot_led_contexts_created"] += 1

    def _active_spot_context(self) -> SpotPriceDiscoveryContext | None:
        context = self.spot_price_discovery_context
        if context is None:
            return None
        age = self.bar_index - context.created_index
        if not spot_context_entry_eligible(
            setup_side=context.direction,
            context_direction=context.direction,
            context_age_bars=age,
            context_accepted=context.accepted,
        ):
            return None
        return context

    def _detect_sweep(
        self,
        row: dict[str, float | int],
        previous_close: float,
    ) -> None:
        context = self._active_spot_context()
        saved_quarter_context = self.quarter_context
        before_scenario = None if self.pending is None else self.pending.scenario_id
        if context is not None:
            # Reuse the mature, tested aligned-context path without making spot
            # observations part of the ordinary quarter-hour context store.
            self.quarter_context = QuarterHourContext(
                direction=context.direction,
                created_index=context.created_index,
                created_ts=context.created_ts,
                boundary_high=context.boundary_high,
                boundary_low=context.boundary_low,
                boundary_close=context.boundary_close,
                atr=context.atr,
                favorable_extreme=context.favorable_extreme,
                accepted=True,
            )
            self.diagnostics["spot_led_context_detector_activations"] += 1
        try:
            super()._detect_sweep(row, previous_close)
        finally:
            self.quarter_context = saved_quarter_context

        setup = self.pending
        if (
            context is None
            or setup is None
            or setup.scenario_id == before_scenario
            or setup.details.get("hybrid_state") != "INTERNAL_INVENTORY_TRAP"
        ):
            return
        age = self.bar_index - context.created_index
        if not spot_context_entry_eligible(
            setup_side=int(setup.side),
            context_direction=context.direction,
            context_age_bars=age,
            context_accepted=context.accepted,
        ):
            self.diagnostics["spot_led_side_mismatch_rejections"] += 1
            self._expire_pending(
                row,
                "INTERNAL_PULLBACK_DID_NOT_ALIGN_WITH_SPOT_PRICE_DISCOVERY",
            )
            return

        setup.branch = BRANCH
        setup.details.update(
            {
                **context.details,
                "spot_led_price_discovery": True,
                "spot_context_age_bars": age,
                "spot_context_accepted": context.accepted,
                "spot_context_favorable_extreme": context.favorable_extreme,
                "spot_context_router": "SPOT_LED_INFORMATION_REPRICING",
                "branch": BRANCH,
            },
        )
        self.diagnostics["spot_led_internal_pullbacks_armed"] += 1

    def _submit_price_capped_bracket(self, *args: Any, **kwargs: Any) -> bool:
        armed = kwargs.get("armed")
        is_spot_led = bool(
            armed is not None
            and armed.setup.details.get("spot_led_price_discovery", False)
        )
        if is_spot_led:
            kwargs["branch"] = BRANCH
            kwargs["entry_tag"] = "SPOT_LED_PRICE_DISCOVERY_PULLBACK_ENTRY"
            extra = dict(kwargs.get("extra") or {})
            extra.update(
                {
                    "spot_led_price_discovery": True,
                    "spot_context_router": "SPOT_LED_INFORMATION_REPRICING",
                },
            )
            kwargs["extra"] = extra
        submitted = super()._submit_price_capped_bracket(*args, **kwargs)
        if is_spot_led and submitted:
            self.diagnostics["spot_led_submissions"] += 1
            # One spot information event is allowed one completed order attempt.
            self.spot_price_discovery_context = None
        return submitted


LiquidityResponseStrategy = SpotLedPriceDiscoveryStrategy

__all__ = [
    "BRANCH",
    "LiquidityResponseConfig",
    "LiquidityResponseStrategy",
    "SpotLedPriceDiscoveryStrategy",
    "SpotPriceDiscoveryContext",
]
