"""Forced-liquidation to cash-auction ownership relay (LCOR).

The initiating move is not assumed to reverse merely because open interest
contracts.  A leveraged venue may first clear weak inventory through an
external-liquidity sweep, after which the cash auction can accept the same
boundary and take ownership of continuation.  LCOR follows that categorical
sequence as one episode:

1. completed perpetual-led external sweep with extreme prior-relative OI loss;
2. no accepted spot discovery during the initiating five-minute impulse;
3. a later completed spot bar accepts the same external boundary;
4. a still later perpetual bar accepts the cash-owned auction while the forced
   OI removal remains materially retained;
5. a distinct first pullback holds both cash and perpetual ownership;
6. a distinct resumed initiative arms an entry with the pullback invalidation
   and a still-live same-leg objective.

The initiating event, spot acceptance, perpetual acceptance, pullback and
resumption can never be the same completed bar.  Orders, fills, fees, slippage,
positions and whole-account NAV remain in NautilusTrader.
"""

from __future__ import annotations

from typing import Any, Mapping

from causal_inventory_ownership_transfer_engine import (
    CausalInventoryOwnershipTransferEngine,
    _OwnershipEpisode,
)
from futures_metrics_data import FuturesMetric
from lrb_types import (
    BarObservation,
    PrimitiveSnapshot,
    ScenarioSignal,
    ScenarioStep,
    ScenarioTransition,
)


class LiquidationCashOwnershipRelayEngine(
    CausalInventoryOwnershipTransferEngine,
):
    """Trade same-direction continuation only after cash assumes ownership."""

    BRANCH = "LIQUIDATION_CASH_RELAY"
    FAMILY = "LCOR"
    INVALIDATION = "LIQUIDATION_CASH_OWNERSHIP_RELAY_INVALIDATED"

    def _maybe_start_episode(
        self,
        snapshot: PrimitiveSnapshot,
        spot: BarObservation,
        metric: FuturesMetric,
    ) -> ScenarioTransition | None:
        del spot
        prior_metric = self._last_metric
        previous = self._previous
        spot_atr = self._spot_atr()
        if (
            prior_metric is None
            or prior_metric.open_interest <= 0.0
            or previous is None
            or previous.perp_width <= 0.0
            or spot_atr is None
            or len(self._bars) < 5
            or self._bucket_position(snapshot.observation.ts_ns) >= self._entry_window
        ):
            return None
        change = (metric.open_interest - prior_metric.open_interest) / prior_metric.open_interest
        if change >= 0.0:
            return None
        threshold = self._threshold("UNWIND")
        if threshold is None or -change < threshold:
            return None
        impulse = self._five_minute_impulse()
        if impulse is None:
            return None
        event_open, event_high, event_low, event_close = impulse
        move_atr = (event_close - event_open) / max(snapshot.atr, 1e-12)
        minimum_move = float(self.params.get("lcor_event_move_atr", 0.30))
        metric_flow_floor = float(self.params.get("lcor_metric_flow_floor", 0.06))
        metric_flow = metric.signed_taker_ratio
        if move_atr >= minimum_move and metric_flow >= metric_flow_floor:
            side = "BUY"
            direction = "LONG"
        elif move_atr <= -minimum_move and metric_flow <= -metric_flow_floor:
            side = "SELL"
            direction = "SHORT"
        else:
            return None

        min_sweep = float(self.params.get("lcor_min_sweep_atr", 0.10))
        accept_distance = float(self.params.get("lcor_accept_close_atr", 0.05))
        perp_boundary = previous.perp_high if side == "BUY" else previous.perp_low
        spot_boundary = previous.spot_high if side == "BUY" else previous.spot_low
        perp_sweep_ts = self._first_crossing(
            market="PERP",
            side=side,
            level=perp_boundary,
            distance=min_sweep * snapshot.atr,
            accepted=False,
        )
        spot_accept_ts = self._first_crossing(
            market="SPOT",
            side=side,
            level=spot_boundary,
            distance=accept_distance * spot_atr,
            accepted=True,
        )
        # The episode exists only when forced liquidation led and the cash
        # auction had not yet accepted during the initiating impulse.
        if perp_sweep_ts is None or spot_accept_ts is not None:
            return None
        key = (previous.end_ts_ns, side, self.BRANCH)
        if key in self._consumed:
            return None
        self._consumed.add(key)
        self._sequence += 1
        episode = _OwnershipEpisode(
            scenario_id=f"LCOR-{snapshot.observation.ts_ns}-{self._sequence:06d}",
            branch=self.BRANCH,
            side=side,
            direction=direction,
            state="FORCED_LIQUIDATION_AWAITING_CASH_OWNERSHIP",
            started_index=snapshot.index,
            started_ts_ns=snapshot.observation.ts_ns,
            prior_auction_end_ts_ns=previous.end_ts_ns,
            perp_boundary=perp_boundary,
            spot_boundary=spot_boundary,
            prior_perp_high=previous.perp_high,
            prior_perp_low=previous.perp_low,
            event_open=event_open,
            event_high=event_high,
            event_low=event_low,
            event_close=event_close,
            event_mid=(event_open + event_close) / 2.0,
            event_range=max(event_high - event_low, snapshot.atr * 0.25),
            event_extreme=event_high if side == "BUY" else event_low,
            atr=max(snapshot.atr, 1e-12),
            baseline_oi=prior_metric.open_interest,
            event_oi=metric.open_interest,
            oi_change=change,
            oi_threshold=threshold,
            spot_owner_ts_ns=None,
            perp_owner_ts_ns=perp_sweep_ts,
            high_since_event=event_high,
            low_since_event=event_low,
        )
        self._episode = episode
        return ScenarioTransition(
            scenario_id=episode.scenario_id,
            event_type="LCOR_CONTEXT_TRANSITION",
            previous_state="IDLE",
            next_state=episode.state,
            reason_code=(
                "PERPETUAL_LED_EXTERNAL_SWEEP_WITH_EXTREME_OI_CONTRACTION_"
                "AND_INITIAL_SPOT_NON_ACCEPTANCE"
            ),
            reference_price=perp_boundary,
            details={
                "branch": self.BRANCH,
                "side": side,
                "direction": direction,
                "oi_change_fraction": change,
                "prior_only_oi_threshold": threshold,
                "baseline_open_interest": prior_metric.open_interest,
                "event_open_interest": metric.open_interest,
                "metric_signed_taker_ratio": metric_flow,
                "perp_sweep_ts_ns": perp_sweep_ts,
                "spot_accept_ts_ns": None,
                "prior_auction_end_ts_ns": previous.end_ts_ns,
                "perp_boundary": perp_boundary,
                "spot_boundary": spot_boundary,
            },
        )

    def _cash_accepts(
        self,
        episode: _OwnershipEpisode,
        spot: BarObservation,
        spot_atr: float,
    ) -> bool:
        distance = float(self.params.get("lcor_accept_close_atr", 0.05)) * spot_atr
        if episode.side == "BUY":
            return spot.close >= episode.spot_boundary + distance
        return spot.close <= episode.spot_boundary - distance

    def _cash_holds(
        self,
        episode: _OwnershipEpisode,
        spot: BarObservation,
        spot_atr: float,
    ) -> bool:
        if episode.spot_owner_ts_ns is None:
            return True
        tolerance = float(
            self.params.get("lcor_spot_hold_tolerance_atr", 0.03),
        ) * spot_atr
        if episode.side == "BUY":
            return spot.close >= episode.spot_boundary - tolerance
        return spot.close <= episode.spot_boundary + tolerance

    def _spot_holds_ownership(
        self,
        episode: _OwnershipEpisode,
        spot: BarObservation,
        spot_atr: float,
    ) -> bool:
        if episode.branch == self.BRANCH:
            return self._cash_holds(episode, spot, spot_atr)
        return super()._spot_holds_ownership(episode, spot, spot_atr)

    def _contraction_ceiling(self, episode: _OwnershipEpisode) -> float:
        removed = max(episode.baseline_oi - episode.event_oi, 0.0)
        retained = float(
            self.params.get("lcor_forced_removal_retention_fraction", 0.35),
        )
        return episode.baseline_oi - removed * retained

    def _contraction_update(
        self,
        episode: _OwnershipEpisode,
        metric: FuturesMetric | None,
    ) -> tuple[bool, bool, dict[str, float]]:
        if metric is None or metric.ts_ns <= episode.started_ts_ns:
            return episode.inventory_confirmed, False, {}
        ceiling = self._contraction_ceiling(episode)
        confirmed = metric.open_interest <= ceiling
        lost = episode.inventory_confirmed and metric.open_interest > ceiling
        return confirmed, lost, {
            "forced_removal_retention_ceiling": ceiling,
            "observed_open_interest": metric.open_interest,
        }

    def _perpetual_accepts_after_cash(
        self,
        episode: _OwnershipEpisode,
        snapshot: PrimitiveSnapshot,
    ) -> bool:
        if (
            episode.spot_owner_ts_ns is None
            or snapshot.observation.ts_ns <= episode.spot_owner_ts_ns
        ):
            return False
        obs = snapshot.observation
        distance = float(
            self.params.get("lcor_perp_accept_close_atr", 0.04),
        ) * episode.atr
        flow = float(self.params.get("lcor_response_flow_ratio", 0.05))
        location = float(
            self.params.get("lcor_response_close_location", 0.58),
        )
        if episode.direction == "LONG":
            return (
                obs.close >= episode.perp_boundary + distance
                and obs.close > obs.open
                and snapshot.flow_ratio >= flow
                and snapshot.close_location >= location
            )
        return (
            obs.close <= episode.perp_boundary - distance
            and obs.close < obs.open
            and snapshot.flow_ratio <= -flow
            and snapshot.close_location <= 1.0 - location
        )

    def _emit_relay(
        self,
        episode: _OwnershipEpisode,
        snapshot: PrimitiveSnapshot,
        *,
        allow_new: bool,
    ) -> ScenarioStep:
        if not allow_new:
            return self._reset(
                snapshot,
                "ENTRY_SLOT_UNAVAILABLE_AT_CASH_OWNERSHIP_RESUMPTION",
            )
        obs = snapshot.observation
        buffer_value = float(
            self.params.get("lcor_stop_buffer_atr", 0.08),
        ) * episode.atr
        if episode.direction == "LONG":
            stop = float(episode.pullback_low) - buffer_value
        else:
            stop = float(episode.pullback_high) + buffer_value
        target = self._target(episode, snapshot, obs.close, stop)
        if target is None:
            return self._reset(
                snapshot,
                "NO_STILL_LIVE_CASH_OWNED_OBJECTIVE_WITH_SUFFICIENT_SPACE",
                {
                    "entry": obs.close,
                    "stop": stop,
                    "high_since_event": episode.high_since_event,
                    "low_since_event": episode.low_since_event,
                },
            )
        target_price, target_reason = target
        previous = episode.state
        episode.state = "LCOR_SIGNALLED"
        episode.signal_index = snapshot.index
        context = ScenarioTransition(
            scenario_id=episode.scenario_id,
            event_type="LCOR_CONTEXT_TRANSITION",
            previous_state=previous,
            next_state=episode.state,
            reason_code=(
                "CASH_OWNERSHIP_FIRST_PULLBACK_AND_RENEWED_INITIATIVE_CONFIRMED"
            ),
            reference_price=obs.close,
            details={
                "direction": episode.direction,
                "stop": stop,
                "target": target_price,
                "target_reason": target_reason,
            },
        )
        entry = self._entry_transition(
            episode,
            reason="LCOR_ENTRY_ARMED",
            reference=obs.close,
            details={
                "direction": episode.direction,
                "stop": stop,
                "target": target_price,
                "target_reason": target_reason,
            },
        )
        signal = ScenarioSignal(
            scenario_id=self._entry_scenario_id(episode),
            family=self.FAMILY,
            direction=episode.direction,
            observed_ts_ns=obs.ts_ns,
            reference_entry=obs.close,
            stop_price=stop,
            target_price=target_price,
            target_reason=target_reason,
            atr=episode.atr,
            liquidity_level=episode.perp_boundary,
            details={
                "context_scenario_id": episode.scenario_id,
                "ownership_branch": self.BRANCH,
                "initiating_side": episode.side,
                "oi_change_fraction": episode.oi_change,
                "prior_only_oi_threshold": episode.oi_threshold,
                "prior_auction_end_ts_ns": episode.prior_auction_end_ts_ns,
                "spot_owner_ts_ns": episode.spot_owner_ts_ns,
                "perp_sweep_ts_ns": episode.perp_owner_ts_ns,
                "pullback_index": episode.pullback_index,
                "causal_exit_reason_codes": (self.INVALIDATION,),
                "causal_exit_open_position": True,
            },
        )
        return ScenarioStep(transitions=(context, entry), signal=signal)

    def _advance_signalled_relay(
        self,
        episode: _OwnershipEpisode,
        snapshot: PrimitiveSnapshot,
        spot: BarObservation,
        metric: FuturesMetric | None,
        spot_atr: float,
    ) -> ScenarioStep:
        assert episode.signal_index is not None
        obs = snapshot.observation
        floor = float(self.params.get("lcor_response_flow_ratio", 0.05))
        cash_holds = self._cash_holds(episode, spot, spot_atr)
        ceiling = self._contraction_ceiling(episode)
        inventory_lost = (
            metric is not None
            and metric.ts_ns > episode.started_ts_ns
            and metric.open_interest > ceiling
        )
        if episode.direction == "LONG":
            price_invalid = (
                obs.close < episode.perp_boundary
                and snapshot.flow_ratio <= -floor
            )
        else:
            price_invalid = (
                obs.close > episode.perp_boundary
                and snapshot.flow_ratio >= floor
            )
        if not cash_holds or inventory_lost or price_invalid:
            return self._reset(
                snapshot,
                self.INVALIDATION,
                {
                    "cash_holds": cash_holds,
                    "inventory_lost": inventory_lost,
                    "price_invalid": price_invalid,
                    "forced_removal_retention_ceiling": ceiling,
                },
            )
        if snapshot.index - episode.signal_index >= int(
            self.params.get("lcor_post_signal_context_bars", 8),
        ):
            return self._reset(
                snapshot,
                "LIQUIDATION_CASH_RELAY_POST_SIGNAL_CONTEXT_MATURED",
            )
        return ScenarioStep()

    def _advance_episode(
        self,
        snapshot: PrimitiveSnapshot,
        spot: BarObservation,
        metric: FuturesMetric | None,
        *,
        allow_new: bool,
    ) -> ScenarioStep:
        episode = self._episode
        assert episode is not None
        if episode.branch != self.BRANCH:
            return super()._advance_episode(
                snapshot,
                spot,
                metric,
                allow_new=allow_new,
            )
        spot_atr = self._spot_atr()
        if spot_atr is None:
            return ScenarioStep()
        if episode.state == "LCOR_SIGNALLED":
            return self._advance_signalled_relay(
                episode,
                snapshot,
                spot,
                metric,
                spot_atr,
            )
        if snapshot.index <= episode.started_index:
            return ScenarioStep()
        obs = snapshot.observation
        episode.high_since_event = max(episode.high_since_event, obs.high)
        episode.low_since_event = min(episode.low_since_event, obs.low)
        if snapshot.index - episode.started_index > int(
            self.params.get("lcor_episode_bars", 30),
        ):
            return self._reset(
                snapshot,
                "LIQUIDATION_CASH_OWNERSHIP_EPISODE_EXPIRED",
            )

        transitions: list[ScenarioTransition] = []
        if episode.spot_owner_ts_ns is None:
            if (
                metric is not None
                and metric.ts_ns > episode.started_ts_ns
                and metric.open_interest >= episode.baseline_oi
            ):
                return self._reset(
                    snapshot,
                    "FORCED_LIQUIDATION_RELEVERAGED_BEFORE_CASH_ACCEPTANCE",
                    {
                        "baseline_open_interest": episode.baseline_oi,
                        "observed_open_interest": metric.open_interest,
                    },
                )
            if not self._cash_accepts(episode, spot, spot_atr):
                return ScenarioStep()
            episode.spot_owner_ts_ns = spot.ts_ns
            previous = episode.state
            transitions.append(
                self._context_transition(
                    episode,
                    previous,
                    "CASH_AUCTION_ACCEPTED_FORCED_LIQUIDATION_BOUNDARY",
                    "LATER_SPOT_ACCEPTANCE_ASSUMED_OWNERSHIP_AFTER_DELEVERAGING",
                    spot.close,
                    {
                        "spot_owner_ts_ns": spot.ts_ns,
                        "spot_boundary": episode.spot_boundary,
                    },
                ),
            )
            # Strict chronology: the same completed spot-acceptance bar cannot
            # also confirm perpetual acceptance or become the pullback.
            return ScenarioStep(transitions=tuple(transitions))

        if not self._cash_holds(episode, spot, spot_atr):
            return self._reset(
                snapshot,
                "CASH_AUCTION_LOST_OWNERSHIP_BEFORE_ENTRY",
                {
                    "spot_close": spot.close,
                    "spot_boundary": episode.spot_boundary,
                },
            )

        inventory, lost, detail = self._contraction_update(episode, metric)
        if lost:
            return self._reset(
                snapshot,
                "FORCED_LIQUIDATION_REMOVAL_NO_LONGER_RETAINED",
                detail,
            )
        if inventory and not episode.inventory_confirmed:
            episode.inventory_confirmed = True
            previous = episode.state
            transitions.append(
                self._context_transition(
                    episode,
                    previous,
                    "FORCED_REMOVAL_RETAINED_UNDER_CASH_OWNERSHIP",
                    "FORCED_OI_REMOVAL_REMAINED_MATERIALLY_RETAINED_AFTER_CASH_ACCEPTANCE",
                    obs.close,
                    detail,
                ),
            )

        if (
            not episode.auction_confirmed
            and self._perpetual_accepts_after_cash(episode, snapshot)
        ):
            episode.auction_confirmed = True
            episode.auction_confirmation_index = snapshot.index
            episode.auction_confirmation_high = obs.high
            episode.auction_confirmation_low = obs.low
            previous = episode.state
            transitions.append(
                self._context_transition(
                    episode,
                    previous,
                    "PERPETUAL_ACCEPTED_CASH_OWNED_AUCTION",
                    "PERPETUAL_ACCEPTED_ONLY_AFTER_STRICTLY_EARLIER_CASH_OWNERSHIP",
                    obs.close,
                    {
                        "spot_owner_ts_ns": episode.spot_owner_ts_ns,
                        "perpetual_accept_ts_ns": obs.ts_ns,
                        "perp_boundary": episode.perp_boundary,
                    },
                ),
            )

        if not (episode.inventory_confirmed and episode.auction_confirmed):
            return ScenarioStep(transitions=tuple(transitions))

        if episode.pullback_index is None:
            if snapshot.index <= int(episode.auction_confirmation_index):
                return ScenarioStep(transitions=tuple(transitions))
            if self._pullback_holds(episode, snapshot, spot, spot_atr):
                episode.pullback_index = snapshot.index
                episode.pullback_high = obs.high
                episode.pullback_low = obs.low
                previous = episode.state
                transitions.append(
                    self._context_transition(
                        episode,
                        previous,
                        "FIRST_CASH_OWNED_PULLBACK_HELD",
                        "FIRST_OPPOSING_FLOW_PULLBACK_HELD_CASH_OWNED_BOUNDARY",
                        obs.close,
                        {
                            "pullback_high": obs.high,
                            "pullback_low": obs.low,
                            "spot_close": spot.close,
                        },
                    ),
                )
            return ScenarioStep(transitions=tuple(transitions))

        if self._resumed(episode, snapshot):
            emitted = self._emit_relay(
                episode,
                snapshot,
                allow_new=allow_new,
            )
            return ScenarioStep(
                transitions=(*transitions, *emitted.transitions),
                signal=emitted.signal,
            )
        return ScenarioStep(transitions=tuple(transitions))
