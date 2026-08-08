"""Candidate 15 V7 bounded residual information-transfer state and entry engine.

V6 established that a cross-market state has market ownership: only the sole
market excluded from an exactly three-market response can be a delayed receiver.
V7 completes the causal contract:

* every activation or refresh becomes a new effective evidence timestamp;
* residual displacement is normalized by prior completed five-minute ranges,
  matching the sender impulses and avoiding self-normalization;
* the residual must be behind the sender basket at confirmation;
* its fresh leg must be substantial but weaker than the weakest sender impulse;
* convergence parity must remain unconsumed and less than one planned-loss unit
  away after costs, showing partial delivery without an already completed move.

No fill, account, or outcome information enters this module. NautilusTrader
continues to own orders, fills, fees, margin, positions, and NAV.
"""
from __future__ import annotations

from dataclasses import dataclass
from math import exp, fsum, isfinite, log
from statistics import median
from typing import Any, Mapping

from candidate15_v6_residual_laggard_materializer import residual_laggard_symbol
from logic import BarObs, Direction, LogicConfig, TradePlan
from quarter_hour_persistent_initiative import (
    CommonFlowEvent,
    PersistentInitiativeContinuationEngine,
)
from response_qualified_persistent_initiative import (
    ResponseQualifiedInitiativeState,
    ResponseQualifiedPersistentQuarterHourRouter,
)


V7_MODULE = "BOUNDED_RESIDUAL_TRANSFER_MSS_FVG"
V7_ROUTER_KEY = "PORTFOLIO::BOUNDED_RESIDUAL_TRANSFER"


@dataclass(frozen=True, slots=True)
class BoundedTransferInitiativeState(ResponseQualifiedInitiativeState):
    effective_ts_ns: int
    evidence_event_ids: tuple[str, str]
    residual_symbol: str
    residual_reference_price: float
    residual_confirmation_price: float
    residual_directional_progress: float
    delivery_gap: float
    accepted_min_standardized_body: float
    accepted_median_standardized_body: float
    parity_price: float

    def __post_init__(self) -> None:
        ResponseQualifiedInitiativeState.__post_init__(self)
        if self.effective_ts_ns != self.activated_ts_ns:
            raise ValueError("effective evidence timestamp must own the activation boundary")
        if len(self.evidence_event_ids) != 2 or self.evidence_event_ids[0] == self.evidence_event_ids[1]:
            raise ValueError("bounded transfer requires two distinct evidence events")
        if self.residual_symbol in self.accepted_symbols:
            raise ValueError("residual receiver must be excluded from accepted markets")
        if not self.residual_symbol:
            raise ValueError("residual receiver identity must not be empty")
        values = (
            self.residual_reference_price,
            self.residual_confirmation_price,
            self.delivery_gap,
            self.accepted_min_standardized_body,
            self.accepted_median_standardized_body,
            self.parity_price,
        )
        if not all(isfinite(value) and value > 0.0 for value in values):
            raise ValueError("bounded transfer state requires positive finite geometry")


class BoundedTransferPersistentQuarterHourRouter(
    ResponseQualifiedPersistentQuarterHourRouter,
):
    """Attach one causal residual receiver and transfer geometry to each state."""

    def __init__(self, config: LogicConfig, instrument_id: str = "PORTFOLIO.GLOBAL") -> None:
        super().__init__(config, instrument_id)
        self._event_market_closes: dict[str, dict[str, float]] = {}

    def _snapshot(self, event: CommonFlowEvent) -> dict[str, float]:
        snapshot: dict[str, float] = {}
        for symbol in self._bars:
            if not self._bars[symbol]:
                raise RuntimeError(f"missing synchronized market close for {symbol}")
            snapshot[symbol] = float(self._bars[symbol][-1].close)
        self._event_market_closes[event.event_id] = snapshot
        if len(self._event_market_closes) > 512:
            oldest = next(iter(self._event_market_closes))
            del self._event_market_closes[oldest]
        return snapshot

    def _terminate_unowned(
        self,
        event: CommonFlowEvent,
        reason: str,
        details: dict[str, Any],
    ) -> None:
        if self._state is not None:
            self._terminate(event.observed_ts_ns, reason, details)

    def _handle_event(self, event: CommonFlowEvent) -> None:
        previous_candidate = self._candidate
        second_closes = self._snapshot(event)
        super()._handle_event(event)
        state = self._state
        if state is None or previous_candidate is None:
            return
        # A rejected response leaves the prior state intact. Only a state whose
        # newest source is the current event may own new transfer evidence.
        if not state.source_event_ids or state.source_event_ids[-1] != event.event_id:
            return
        first_closes = self._event_market_closes.get(previous_candidate.event_id)
        if first_closes is None:
            self._terminate_unowned(
                event,
                "QHI_V7_FIRST_EVENT_MARKET_SNAPSHOT_MISSING",
                {"first_event_id": previous_candidate.event_id},
            )
            return

        accepted = tuple(sorted(state.accepted_symbols))
        residual = residual_laggard_symbol(accepted)
        if residual is None:
            self._terminate_unowned(
                event,
                "QHI_V7_NO_UNIQUE_RESIDUAL_RECEIVER",
                {"accepted_symbols": list(accepted)},
            )
            return
        first_price = float(first_closes[residual])
        second_price = float(second_closes[residual])
        if first_price <= 0.0 or second_price <= 0.0:
            self._terminate_unowned(
                event,
                "QHI_V7_INVALID_RESIDUAL_REFERENCE_PRICE",
                {"residual_symbol": residual},
            )
            return

        sign = 1.0 if state.direction is Direction.LONG else -1.0
        residual_progress = sign * log(second_price / first_price)
        delivery_gap = float(state.median_directional_progress) - residual_progress
        bodies = [
            float(event.standardized_bodies[symbol])
            for symbol in accepted
            if symbol in event.standardized_bodies
        ]
        if len(bodies) != 3 or any(not isfinite(value) or value <= 0.0 for value in bodies):
            self._terminate_unowned(
                event,
                "QHI_V7_ACCEPTED_BODY_GEOMETRY_INCOMPLETE",
                {"accepted_symbols": list(accepted), "standardized_bodies": bodies},
            )
            return
        if not isfinite(delivery_gap) or delivery_gap <= 0.0:
            self._terminate_unowned(
                event,
                "QHI_V7_RESIDUAL_NOT_BEHIND_AT_CONFIRMATION",
                {
                    "residual_symbol": residual,
                    "accepted_median_progress": state.median_directional_progress,
                    "residual_directional_progress": residual_progress,
                    "delivery_gap": delivery_gap,
                },
            )
            return

        accepted_min_body = min(bodies)
        accepted_median_body = median(bodies)
        parity_price = first_price * exp(sign * float(state.median_directional_progress))
        qualified = BoundedTransferInitiativeState(
            scenario_id=state.scenario_id,
            direction=state.direction,
            # A refresh changes accepted ownership and therefore starts a new
            # evidence boundary even though the portfolio state id is retained.
            activated_ts_ns=event.observed_ts_ns,
            expires_ts_ns=state.expires_ts_ns,
            owner_symbol=state.owner_symbol,
            accepted_symbols=accepted,
            origins=dict(state.origins),
            source_event_ids=tuple(state.source_event_ids),
            confirmation_span_ns=state.confirmation_span_ns,
            overlap_symbols=tuple(state.overlap_symbols),
            median_directional_progress=state.median_directional_progress,
            advancing_symbols=tuple(state.advancing_symbols),
            origin_holding_symbols=tuple(state.origin_holding_symbols),
            effective_ts_ns=event.observed_ts_ns,
            evidence_event_ids=(previous_candidate.event_id, event.event_id),
            residual_symbol=residual,
            residual_reference_price=first_price,
            residual_confirmation_price=second_price,
            residual_directional_progress=residual_progress,
            delivery_gap=delivery_gap,
            accepted_min_standardized_body=accepted_min_body,
            accepted_median_standardized_body=accepted_median_body,
            parity_price=parity_price,
        )
        self._state = qualified
        self._event(
            scenario_id=qualified.scenario_id,
            event_type="QHI_V7_BOUNDED_TRANSFER_STATE_QUALIFIED",
            event_time_ns=previous_candidate.observed_ts_ns,
            observed_time_ns=event.observed_ts_ns,
            previous_state="ACTIVE",
            next_state="ACTIVE",
            reason_code="THREE_SENDER_ONE_RESIDUAL_TRANSFER_STATE",
            reference_price=second_price,
            details={
                "direction": qualified.direction.value,
                "accepted_symbols": list(accepted),
                "residual_symbol": residual,
                "evidence_event_ids": list(qualified.evidence_event_ids),
                "residual_reference_price": first_price,
                "residual_confirmation_price": second_price,
                "residual_directional_progress": residual_progress,
                "accepted_median_progress": qualified.median_directional_progress,
                "delivery_gap": delivery_gap,
                "accepted_min_standardized_body": accepted_min_body,
                "accepted_median_standardized_body": accepted_median_body,
                "parity_price": parity_price,
                "effective_ts_ns": qualified.effective_ts_ns,
                "expires_ts_ns": qualified.expires_ts_ns,
            },
        )


class BoundedResidualTransferContinuationEngine(
    PersistentInitiativeContinuationEngine,
):
    """Emit only mature, unconsumed partial-catch-up auction legs."""

    def _parity_was_consumed(
        self,
        state: BoundedTransferInitiativeState,
        observed_ts_ns: int,
    ) -> bool:
        for bar in self._bars:
            if bar.end_ts_ns <= state.effective_ts_ns or bar.end_ts_ns > observed_ts_ns:
                continue
            if state.direction is Direction.LONG and bar.high >= state.parity_price:
                return True
            if state.direction is Direction.SHORT and bar.low <= state.parity_price:
                return True
        return False

    def _reject_transfer_plan(
        self,
        plan: TradePlan,
        reason: str,
        details: dict[str, Any],
    ) -> None:
        self.skips[reason] += 1
        self.mark_rejected(plan, plan.observed_ts_ns, reason, details)

    def _qualify_transfer(
        self,
        plan: TradePlan,
        state: BoundedTransferInitiativeState | None,
    ) -> TradePlan | None:
        if not isinstance(state, BoundedTransferInitiativeState):
            self._reject_transfer_plan(
                plan,
                "QHI_V7_TRANSFER_STATE_TYPE_UNRESOLVED",
                {},
            )
            return None
        if self.symbol != state.residual_symbol:
            self._reject_transfer_plan(
                plan,
                "QHI_V7_PLAN_NOT_STATE_RESIDUAL",
                {
                    "symbol": self.symbol,
                    "residual_symbol": state.residual_symbol,
                    "accepted_symbols": list(state.accepted_symbols),
                },
            )
            return None

        mss_body_atr = float(plan.details.get("mss_body_atr", float("nan")))
        body_ratio = mss_body_atr / state.accepted_min_standardized_body
        sign = 1.0 if state.direction is Direction.LONG else -1.0
        gross_parity_gain = sign * (state.parity_price - plan.expected_entry)
        parity_net_gain = (
            gross_parity_gain
            - plan.expected_entry * self.config.effective_maker_rate
            - state.parity_price * self.config.effective_maker_rate
        )
        parity_costed_r = (
            parity_net_gain / plan.loss_per_unit
            if plan.loss_per_unit > 0.0
            else float("-inf")
        )
        parity_consumed = self._parity_was_consumed(state, plan.observed_ts_ns)
        geometry = {
            "module": V7_MODULE,
            "policy": "BOUNDED_PARTIAL_CATCH_UP",
            "effective_ts_ns": state.effective_ts_ns,
            "evidence_event_ids": list(state.evidence_event_ids),
            "accepted_symbols": list(state.accepted_symbols),
            "residual_symbol": state.residual_symbol,
            "residual_reference_price": state.residual_reference_price,
            "residual_confirmation_price": state.residual_confirmation_price,
            "residual_directional_progress": state.residual_directional_progress,
            "accepted_median_progress": state.median_directional_progress,
            "delivery_gap": state.delivery_gap,
            "accepted_min_standardized_body": state.accepted_min_standardized_body,
            "accepted_median_standardized_body": state.accepted_median_standardized_body,
            "residual_mss_body_atr": mss_body_atr,
            "residual_to_weakest_sender_body_ratio": body_ratio,
            "parity_price": state.parity_price,
            "parity_gross_gain": gross_parity_gain,
            "parity_net_gain": parity_net_gain,
            "parity_costed_r": parity_costed_r,
            "parity_consumed_before_plan": parity_consumed,
        }
        qualified = (
            isfinite(body_ratio)
            and 0.5 <= body_ratio < 1.0
            and isfinite(parity_costed_r)
            and 0.0 < parity_costed_r < 1.0
            and not parity_consumed
        )
        if not qualified:
            self._reject_transfer_plan(
                plan,
                "QHI_V7_BOUNDED_TRANSFER_GEOMETRY_UNRESOLVED",
                geometry,
            )
            return None

        plan.details["module"] = V7_MODULE
        plan.details["route"] = "BOUNDED_RESIDUAL_INFORMATION_TRANSFER"
        plan.details["candidate15_v7_transfer"] = geometry
        return plan

    def on_bar(
        self,
        observation: BarObs,
        *,
        state: BoundedTransferInitiativeState | None,
        external_engine: Any,
    ) -> TradePlan | None:
        # Unlike V4-V6, normalize the completed residual leg with only prior
        # completed five-minute ranges. The current leg is appended afterward.
        completed = self._aggregate.update(observation)
        if completed is None:
            return None
        previous_close = self._bars[-1].close if self._bars else None
        self._bars.append(completed)
        if len(self._bars) > 512:
            del self._bars[:-384]

        plan = None
        if state is None:
            self.skips["QHI_CONTINUATION_WITHOUT_ACTIVE_INITIATIVE"] += 1
        elif observation.ts_ns >= state.expires_ts_ns:
            self.skips["QHI_CONTINUATION_INITIATIVE_EXPIRED"] += 1
        else:
            plan = self._build_plan(
                completed=completed,
                observed_ts_ns=observation.ts_ns,
                state=state,
                external_engine=external_engine,
            )
        self._confirm_pivot(observation.ts_ns)
        self._ranges.append(self._true_range(completed, previous_close))
        if plan is None:
            return None
        return self._qualify_transfer(plan, state)

    def mark_submitted(
        self,
        plan: TradePlan,
        quantity: Any,
        details: dict[str, Any],
    ) -> None:
        super().mark_submitted(plan, quantity, details)
        if self.events:
            self.events[-1].details["module"] = V7_MODULE

    def mark_trade_terminal(self, ts_ns: int, reason: str) -> None:
        before = len(self.events)
        super().mark_trade_terminal(ts_ns, reason)
        if len(self.events) > before:
            self.events[-1].details["module"] = V7_MODULE
