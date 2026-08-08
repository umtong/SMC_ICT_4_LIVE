"""Candidate 15 V9 causal beta-coherent cross-market diffusion lag.

V8 treated the median sender return as a one-for-one receiver target.  That
confounded genuine information diffusion with ordinary cross-asset beta.  V9
fits each possible receiver to the median return of the other three markets
using only completed five-minute returns strictly before the first evidence
event.  A state survives only when all fixed horizons imply positive beta and
an under-delivered receiver both at sender confirmation and at the receiver's
fresh MSS bar.

The sender events already require directional aggressor flow.  The receiver
entry leg independently requires directional flow conversion, MSS and a fresh
FVG.  Thus flow creates/receives the transfer while prior-only price beta sizes
how much delivery should have occurred.  No fill, PnL or future observation is
used here.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, replace
from math import exp, isfinite, log
from statistics import median
from typing import Any, Mapping

from bounded_transfer_initiative import (
    BoundedTransferInitiativeState,
    BoundedTransferPersistentQuarterHourRouter,
)
from logic import BarObs, Direction, LogicConfig, MINUTE_NS, TradePlan
from managed_transfer_initiative import ManagedResidualTransferContinuationEngine
from quarter_hour_persistent_initiative import CommonFlowEvent, SYMBOLS


V9_MODULE = "BETA_COHERENT_DIFFUSION_LAG_MSS_FVG"
V9_ROUTER_KEY = "PORTFOLIO::BETA_COHERENT_DIFFUSION_LAG"
V9_HORIZONS = (24, 48, 96, 192)


def _zero_intercept_beta(x: list[float], y: list[float]) -> float | None:
    if len(x) != len(y) or len(x) < 8:
        return None
    denominator = sum(value * value for value in x)
    if not isfinite(denominator) or denominator <= 0.0:
        return None
    numerator = sum(left * right for left, right in zip(x, y, strict=True))
    value = numerator / denominator
    return value if isfinite(value) else None


@dataclass(frozen=True, slots=True)
class BetaCoherentTransferState(BoundedTransferInitiativeState):
    """A fixed-prior-beta sender state plus exact plan-geometry snapshot."""

    beta_zero_intercept_by_horizon: dict[int, float]
    state_expected_progress_by_horizon: dict[int, float]
    state_delivery_gap_by_horizon: dict[int, float]
    beta_median: float
    beta_spread: float
    first_market_prices: dict[str, float]
    beta_state_parity_price: float
    geometry_ts_ns: int = 0
    geometry_market_prices: dict[str, float] | None = None

    def __post_init__(self) -> None:
        BoundedTransferInitiativeState.__post_init__(self)
        if tuple(sorted(self.beta_zero_intercept_by_horizon)) != V9_HORIZONS:
            raise ValueError("V9 requires all fixed beta horizons")
        betas = tuple(self.beta_zero_intercept_by_horizon[h] for h in V9_HORIZONS)
        if not all(isfinite(value) and value > 0.0 for value in betas):
            raise ValueError("V9 requires positive finite prior-only betas")
        gaps = tuple(self.state_delivery_gap_by_horizon[h] for h in V9_HORIZONS)
        if not all(isfinite(value) and value > 0.0 for value in gaps):
            raise ValueError("receiver must be beta-under-delivered at confirmation")
        if set(self.first_market_prices) != set(SYMBOLS):
            raise ValueError("V9 requires synchronized first-event prices")
        if not all(isfinite(value) and value > 0.0 for value in self.first_market_prices.values()):
            raise ValueError("V9 first-event prices must be positive and finite")
        if not isfinite(self.beta_state_parity_price) or self.beta_state_parity_price <= 0.0:
            raise ValueError("V9 beta parity must be positive and finite")
        if self.geometry_market_prices is not None:
            if set(self.geometry_market_prices) != set(SYMBOLS):
                raise ValueError("V9 geometry requires all synchronized markets")


class BetaCoherentTransferPersistentQuarterHourRouter(
    BoundedTransferPersistentQuarterHourRouter,
):
    """Attach prior-only receiver beta before accepting a residual state."""

    def __init__(self, config: LogicConfig, instrument_id: str = "PORTFOLIO.GLOBAL") -> None:
        super().__init__(config, instrument_id)
        self._v9_previous_five_minute_close: dict[str, float | None] = {
            symbol: None for symbol in SYMBOLS
        }
        self._v9_return_rows: deque[tuple[int, dict[str, float]]] = deque(maxlen=512)
        self._v9_event_models: dict[str, dict[str, dict[int, float]]] = {}

    def _fit_event_models(self) -> dict[str, dict[int, float]]:
        rows = list(self._v9_return_rows)
        output: dict[str, dict[int, float]] = {}
        for residual in SYMBOLS:
            accepted = tuple(symbol for symbol in SYMBOLS if symbol != residual)
            factor = [median(row[symbol] for symbol in accepted) for _, row in rows]
            receiver = [row[residual] for _, row in rows]
            horizon_models: dict[int, float] = {}
            for horizon in V9_HORIZONS:
                beta = _zero_intercept_beta(factor[-horizon:], receiver[-horizon:])
                if beta is not None:
                    horizon_models[horizon] = beta
            output[residual] = horizon_models
        return output

    def _update_return_history(self, ts_ns: int) -> None:
        if not self._is_five_minute_boundary(ts_ns):
            return
        closes: dict[str, float] = {}
        for symbol in SYMBOLS:
            parts = list(self._bars[symbol])[-5:]
            expected = [ts_ns - (4 - offset) * MINUTE_NS for offset in range(5)]
            if len(parts) != 5 or [bar.ts_ns for bar in parts] != expected:
                self.skips["QHI_V9_NONCONTIGUOUS_BETA_RETURN_BAR"] += 1
                return
            closes[symbol] = float(parts[-1].close)
        previous = dict(self._v9_previous_five_minute_close)
        self._v9_previous_five_minute_close.update(closes)
        if any(previous[symbol] is None for symbol in SYMBOLS):
            return
        returns = {
            symbol: log(closes[symbol] / float(previous[symbol]))
            for symbol in SYMBOLS
        }
        if not all(isfinite(value) for value in returns.values()):
            self.skips["QHI_V9_NONFINITE_BETA_RETURN"] += 1
            return
        self._v9_return_rows.append((ts_ns, returns))

    def _terminate_beta_unresolved(
        self,
        event: CommonFlowEvent,
        reason: str,
        details: dict[str, Any],
    ) -> None:
        self.skips[reason] += 1
        if self._state is not None:
            self._terminate(event.observed_ts_ns, reason, details)

    def _handle_event(self, event: CommonFlowEvent) -> None:
        # This snapshot is made before on_batch appends the return ending at the
        # current event, so every regression ends strictly before this event.
        self._v9_event_models[event.event_id] = self._fit_event_models()
        if len(self._v9_event_models) > 512:
            oldest = next(iter(self._v9_event_models))
            del self._v9_event_models[oldest]

        previous_candidate = self._candidate
        super()._handle_event(event)
        state = self._state
        newest_is_current = bool(
            state is not None
            and state.source_event_ids
            and state.source_event_ids[-1] == event.event_id
        )
        if (
            not isinstance(state, BoundedTransferInitiativeState)
            or isinstance(state, BetaCoherentTransferState)
            or previous_candidate is None
            or not newest_is_current
        ):
            return

        models = self._v9_event_models.get(previous_candidate.event_id, {}).get(
            state.residual_symbol,
            {},
        )
        if tuple(sorted(models)) != V9_HORIZONS:
            self._terminate_beta_unresolved(
                event,
                "QHI_V9_PRIOR_BETA_HISTORY_INCOMPLETE",
                {
                    "first_event_id": previous_candidate.event_id,
                    "residual_symbol": state.residual_symbol,
                    "available_horizons": sorted(models),
                },
            )
            return
        if not all(isfinite(models[horizon]) and models[horizon] > 0.0 for horizon in V9_HORIZONS):
            self._terminate_beta_unresolved(
                event,
                "QHI_V9_POSITIVE_BETA_CONSENSUS_ABSENT",
                {"betas": dict(models), "residual_symbol": state.residual_symbol},
            )
            return

        expected = {
            horizon: float(models[horizon] * state.median_directional_progress)
            for horizon in V9_HORIZONS
        }
        gaps = {
            horizon: float(expected[horizon] - state.residual_directional_progress)
            for horizon in V9_HORIZONS
        }
        if not all(isfinite(gaps[horizon]) and gaps[horizon] > 0.0 for horizon in V9_HORIZONS):
            self._terminate_beta_unresolved(
                event,
                "QHI_V9_RECEIVER_NOT_BETA_LAGGING_AT_CONFIRMATION",
                {
                    "betas": dict(models),
                    "expected_progress": expected,
                    "residual_progress": state.residual_directional_progress,
                    "delivery_gaps": gaps,
                },
            )
            return

        first_prices = self._event_market_closes.get(previous_candidate.event_id)
        if first_prices is None or set(first_prices) != set(SYMBOLS):
            self._terminate_beta_unresolved(
                event,
                "QHI_V9_FIRST_EVENT_PRICE_SNAPSHOT_MISSING",
                {"first_event_id": previous_candidate.event_id},
            )
            return
        sign = 1.0 if state.direction is Direction.LONG else -1.0
        beta_median = float(median(models.values()))
        beta_state_parity = float(
            first_prices[state.residual_symbol]
            * exp(sign * beta_median * state.median_directional_progress)
        )
        qualified = BetaCoherentTransferState(
            scenario_id=state.scenario_id,
            direction=state.direction,
            activated_ts_ns=state.activated_ts_ns,
            expires_ts_ns=state.expires_ts_ns,
            owner_symbol=state.owner_symbol,
            accepted_symbols=tuple(state.accepted_symbols),
            origins=dict(state.origins),
            source_event_ids=tuple(state.source_event_ids),
            confirmation_span_ns=state.confirmation_span_ns,
            overlap_symbols=tuple(state.overlap_symbols),
            median_directional_progress=state.median_directional_progress,
            advancing_symbols=tuple(state.advancing_symbols),
            origin_holding_symbols=tuple(state.origin_holding_symbols),
            effective_ts_ns=state.effective_ts_ns,
            evidence_event_ids=tuple(state.evidence_event_ids),
            residual_symbol=state.residual_symbol,
            residual_reference_price=state.residual_reference_price,
            residual_confirmation_price=state.residual_confirmation_price,
            residual_directional_progress=state.residual_directional_progress,
            delivery_gap=state.delivery_gap,
            accepted_min_standardized_body=state.accepted_min_standardized_body,
            accepted_median_standardized_body=state.accepted_median_standardized_body,
            parity_price=beta_state_parity,
            beta_zero_intercept_by_horizon=dict(models),
            state_expected_progress_by_horizon=expected,
            state_delivery_gap_by_horizon=gaps,
            beta_median=beta_median,
            beta_spread=max(models.values()) - min(models.values()),
            first_market_prices=dict(first_prices),
            beta_state_parity_price=beta_state_parity,
        )
        self._state = qualified
        self._event(
            scenario_id=qualified.scenario_id,
            event_type="QHI_V9_BETA_COHERENT_TRANSFER_STATE_QUALIFIED",
            event_time_ns=previous_candidate.observed_ts_ns,
            observed_time_ns=event.observed_ts_ns,
            previous_state="ACTIVE",
            next_state="ACTIVE",
            reason_code="PRIOR_ONLY_BETA_UNDER_DELIVERY_CONFIRMED",
            reference_price=qualified.residual_confirmation_price,
            details={
                "direction": qualified.direction.value,
                "accepted_symbols": list(qualified.accepted_symbols),
                "residual_symbol": qualified.residual_symbol,
                "evidence_event_ids": list(qualified.evidence_event_ids),
                "betas": dict(models),
                "beta_median": beta_median,
                "beta_spread": qualified.beta_spread,
                "state_expected_progress": expected,
                "state_delivery_gaps": gaps,
                "beta_state_parity_price": beta_state_parity,
                "estimation_cutoff": "STRICTLY_BEFORE_FIRST_EVIDENCE_EVENT",
            },
        )

    def _geometry_snapshot(self, ts_ns: int) -> tuple[int, dict[str, float]] | None:
        geometry_ts = ts_ns - MINUTE_NS
        prices: dict[str, float] = {}
        for symbol in SYMBOLS:
            bars = list(self._bars[symbol])
            if len(bars) < 2 or bars[-1].ts_ns != ts_ns or bars[-2].ts_ns != geometry_ts:
                return None
            prices[symbol] = float(bars[-2].close)
        return geometry_ts, prices

    def on_batch(
        self,
        ts_ns: int,
        bars: Mapping[str, BarObs],
    ) -> BetaCoherentTransferState | None:
        state = super().on_batch(ts_ns, bars)
        # Preserve the diagnostic cutoff: add the current completed 5m return
        # only after any event at this timestamp has been processed.
        self._update_return_history(ts_ns)
        if not isinstance(state, BetaCoherentTransferState):
            return None if state is None else state  # type: ignore[return-value]
        snapshot = self._geometry_snapshot(ts_ns)
        if snapshot is None:
            self.skips["QHI_V9_GEOMETRY_SNAPSHOT_UNAVAILABLE"] += 1
            return state
        geometry_ts, prices = snapshot
        refreshed = replace(
            state,
            geometry_ts_ns=geometry_ts,
            geometry_market_prices=prices,
        )
        self._state = refreshed
        return refreshed


class BetaCoherentResidualTransferContinuationEngine(
    ManagedResidualTransferContinuationEngine,
):
    """Trade only a bounded receiver MSS that remains beta-under-delivered."""

    def _fixed_completion_consumed_before_current(
        self,
        state: BetaCoherentTransferState,
        completed_end_ts_ns: int,
        completion_price: float,
    ) -> bool:
        for bar in self._bars:
            if bar.end_ts_ns <= state.effective_ts_ns or bar.end_ts_ns >= completed_end_ts_ns:
                continue
            if state.direction is Direction.LONG and bar.high >= completion_price:
                return True
            if state.direction is Direction.SHORT and bar.low <= completion_price:
                return True
        return False

    def _qualify_managed_transfer(
        self,
        plan: TradePlan,
        state: BoundedTransferInitiativeState | None,
        completed: Any,
    ) -> TradePlan | None:
        if not isinstance(state, BetaCoherentTransferState):
            self._reject_managed_plan(plan, "QHI_V9_BETA_STATE_TYPE_UNRESOLVED", {})
            return None
        if self.symbol != state.residual_symbol:
            self._reject_managed_plan(
                plan,
                "QHI_V9_PLAN_NOT_BETA_RECEIVER",
                {"symbol": self.symbol, "residual_symbol": state.residual_symbol},
            )
            return None
        prices = state.geometry_market_prices
        if (
            prices is None
            or state.geometry_ts_ns != completed.end_ts_ns
            or set(prices) != set(SYMBOLS)
        ):
            self._reject_managed_plan(
                plan,
                "QHI_V9_EXACT_GEOMETRY_CLOCK_UNAVAILABLE",
                {
                    "state_geometry_ts_ns": state.geometry_ts_ns,
                    "completed_end_ts_ns": completed.end_ts_ns,
                },
            )
            return None

        sign = 1.0 if state.direction is Direction.LONG else -1.0
        sender_geometry = float(
            median(
                sign * log(prices[symbol] / state.first_market_prices[symbol])
                for symbol in state.accepted_symbols
            )
        )
        residual_geometry = float(
            sign
            * log(
                prices[state.residual_symbol]
                / state.first_market_prices[state.residual_symbol]
            )
        )
        expected_geometry = {
            horizon: float(
                state.beta_zero_intercept_by_horizon[horizon] * sender_geometry
            )
            for horizon in V9_HORIZONS
        }
        geometry_gaps = {
            horizon: float(expected_geometry[horizon] - residual_geometry)
            for horizon in V9_HORIZONS
        }
        mss_body_atr = float(plan.details.get("mss_body_atr", float("nan")))
        body_ratio = mss_body_atr / state.accepted_min_standardized_body
        if not (
            isfinite(body_ratio)
            and 0.5 <= body_ratio < 1.0
            and all(
                isfinite(geometry_gaps[horizon]) and geometry_gaps[horizon] > 0.0
                for horizon in V9_HORIZONS
            )
        ):
            self._reject_managed_plan(
                plan,
                "QHI_V9_BETA_COHERENT_GEOMETRY_UNRESOLVED",
                {
                    "residual_to_weakest_sender_body_ratio": body_ratio,
                    "sender_geometry_progress": sender_geometry,
                    "residual_geometry_progress": residual_geometry,
                    "expected_geometry_progress": expected_geometry,
                    "geometry_delivery_gaps": geometry_gaps,
                },
            )
            return None

        beta_geometry = float(median(state.beta_zero_intercept_by_horizon.values()))
        completion_price = float(
            state.first_market_prices[state.residual_symbol]
            * exp(sign * beta_geometry * sender_geometry)
        )
        gross_completion_gain = sign * (completion_price - plan.expected_entry)
        completion_net_gain = (
            gross_completion_gain
            - plan.expected_entry * self.config.effective_maker_rate
            - completion_price * self.config.effective_maker_rate
        )
        completion_costed_r = (
            completion_net_gain / plan.loss_per_unit
            if plan.loss_per_unit > 0.0
            else float("-inf")
        )
        consumed = self._fixed_completion_consumed_before_current(
            state,
            completed.end_ts_ns,
            completion_price,
        )
        if not (
            isfinite(completion_price)
            and completion_price > 0.0
            and isfinite(completion_costed_r)
            and completion_costed_r > 0.0
            and not consumed
        ):
            self._reject_managed_plan(
                plan,
                "QHI_V9_COMPLETION_GEOMETRY_NOT_TRADABLE",
                {
                    "completion_price": completion_price,
                    "completion_costed_r": completion_costed_r,
                    "completion_consumed_before_plan": consumed,
                },
            )
            return None

        transfer = {
            "module": V9_MODULE,
            "policy": "BETA_COHERENT_DIFFUSION_LAG",
            "stage": "BETA_COHERENT_DIFFUSION_LAG",
            "effective_ts_ns": state.effective_ts_ns,
            "evidence_event_ids": list(state.evidence_event_ids),
            "estimation_cutoff": "STRICTLY_BEFORE_FIRST_EVIDENCE_EVENT",
            "beta_horizons": list(V9_HORIZONS),
            "beta_zero_intercept_by_horizon": dict(state.beta_zero_intercept_by_horizon),
            "beta_median": state.beta_median,
            "beta_spread": state.beta_spread,
            "accepted_symbols": list(state.accepted_symbols),
            "residual_symbol": state.residual_symbol,
            "state_expected_progress_by_horizon": dict(state.state_expected_progress_by_horizon),
            "state_delivery_gap_by_horizon": dict(state.state_delivery_gap_by_horizon),
            "sender_geometry_directional_progress": sender_geometry,
            "residual_geometry_directional_progress": residual_geometry,
            "geometry_expected_progress_by_horizon": expected_geometry,
            "geometry_delivery_gap_by_horizon": geometry_gaps,
            "residual_to_weakest_sender_body_ratio": body_ratio,
            "parity_price": completion_price,
            "beta_completion_price": completion_price,
            "completion_gross_gain": gross_completion_gain,
            "completion_net_gain": completion_net_gain,
            "completion_costed_r": completion_costed_r,
            "prior_parity_consumed": consumed,
            "current_bar_touched_parity": False,
            "current_bar_closed_beyond_parity": False,
            "management_trigger_model": "COMPLETED_CLOSE_AT_OR_BEYOND_BETA_DELIVERY_OR_COST_COVER",
            "management_action": "MODIFY_EXISTING_STOP_TO_MINIMUM_POSITIVE_COST_COVER",
            "final_target_model": plan.details.get("target_model"),
            "original_stop": plan.stop_price,
            "flow_contract": "SENDER_AGGRESSOR_FLOW_TO_RECEIVER_MSS_FLOW_CONVERSION",
        }
        plan.details["module"] = V9_MODULE
        plan.details["route"] = "BETA_COHERENT_DIFFUSION_LAG"
        plan.details["candidate15_v9_transfer"] = transfer
        return plan

    def mark_submitted(self, plan: TradePlan, quantity: Any, details: dict[str, Any]) -> None:
        super().mark_submitted(plan, quantity, details)
        if self.events:
            self.events[-1].details["module"] = V9_MODULE
