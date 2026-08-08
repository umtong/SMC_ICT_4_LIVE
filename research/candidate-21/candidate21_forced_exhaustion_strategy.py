"""Cost-aware forced-flow exhaustion reversal on NautilusTrader.

A deleveraging impulse is only an event.  A strictly later bar must show that
same-side aggression is being absorbed by displayed liquidity.  At that moment
no reversal close is consumed: a native STOP_LIMIT bracket is armed beyond the
exhaustion bar.  A later price crossing is therefore both confirmation and
entry, while FOK preserves all-or-none protection and the pre-shock origin is
frozen as the natural target.
"""
from __future__ import annotations

from dataclasses import asdict
import math

from nautilus_trader.model.enums import OrderSide
from nautilus_trader.model.enums import OrderType
from nautilus_trader.model.enums import TimeInForce

from fok_capped_strategy import Candidate18Config
from fok_capped_strategy import Candidate18Strategy as Candidate18FokStrategy
from forced_exhaustion_router import ForcedDecision
from forced_exhaustion_router import ForcedEpisode
from forced_exhaustion_router import ForcedObservation
from forced_exhaustion_router import ForcedResponseThresholds
from forced_exhaustion_router import ForcedShockEvidence
from forced_exhaustion_router import ForcedShockThresholds
from forced_exhaustion_router import advance_forced_episode
from forced_exhaustion_router import classify_forced_shock
from logic import floor_quantity
from logic import net_r_at_price
from logic import planned_loss_per_unit
from strategy_base import PendingSetup


class Candidate21ForcedConfig(Candidate18Config, frozen=True):
    forced_shock_lookback_bars: int = 5
    forced_shock_min_move_atr: float = 1.25
    forced_shock_min_notional_burst: float = 1.50
    forced_shock_min_flow: float = 1.0 / 3.0
    forced_shock_min_efficiency: float = 0.45
    forced_max_wait_bars: int = 6
    forced_min_retrace_fraction: float = 1.0 / 3.0
    forced_min_reverse_flow: float = 0.10
    forced_min_reverse_efficiency: float = 0.20


class Candidate21ForcedStrategy(Candidate18FokStrategy):
    """Arm a cost-viable reversal only after causal forced-flow exhaustion."""

    def __init__(self, config: Candidate21ForcedConfig) -> None:
        super().__init__(config=config)
        self.shock_thresholds = ForcedShockThresholds(
            config.forced_shock_min_move_atr,
            config.forced_shock_min_notional_burst,
            config.forced_shock_min_flow,
            config.forced_shock_min_efficiency,
        )
        self.response_thresholds = ForcedResponseThresholds(
            config.forced_max_wait_bars,
            config.forced_min_retrace_fraction,
            config.forced_min_reverse_flow,
            config.forced_min_reverse_efficiency,
        )
        self.forced_episode: ForcedEpisode | None = None
        self.diagnostics.update(
            {
                "candidate21_forced_events_armed": 0,
                "candidate21_forced_events_not_ready": 0,
                "candidate21_forced_event_geometry_eligible": 0,
                "candidate21_forced_event_geometry_rejected": 0,
                "candidate21_forced_exhaustions": 0,
                "candidate21_forced_reprices": 0,
                "candidate21_forced_invalidated": 0,
                "candidate21_forced_expired": 0,
                "candidate21_forced_geometry_rejected": 0,
                "candidate21_forced_fok_entries": 0,
                "candidate21_forced_stop_limit_fok_entries": 0,
            },
        )

    def _clear_trade_state(self) -> None:
        super()._clear_trade_state()
        self.forced_episode = None

    def _expire_pending(self, row: dict[str, float | int], reason: str) -> None:
        forced = self.pending is not None and self.pending.branch == "FORCED_EXHAUSTION"
        super()._expire_pending(row, reason)
        if forced:
            self.forced_episode = None

    def _ready(self) -> bool:
        return (
            self._feature("metrics_ready") > 0.5
            and self._feature("basis_ready") > 0.5
            and all(
                math.isfinite(self._feature(name))
                for name in (
                    "oi_change_15m",
                    "premium_change_5m",
                    "premium_change_1m",
                )
            )
        )

    @staticmethod
    def _depth_field(side: int) -> str:
        return "bid_depth_change_1_1m" if side > 0 else "ask_depth_change_1_1m"

    def _planned_geometry(
        self,
        *,
        side: int,
        signal: float,
        stop: float,
        target: float,
        atr: float,
        stop_entry: bool,
    ) -> tuple[float, float, float, float]:
        """Return trigger, worst fill, planned loss and target net R."""
        structural_risk = abs(signal - stop)
        if (
            side not in (-1, 1)
            or not all(math.isfinite(value) for value in (signal, stop, target, atr))
            or signal <= 0.0
            or stop <= 0.0
            or target <= 0.0
            or atr <= 0.0
            or structural_risk <= 0.0
        ):
            return math.nan, math.nan, math.nan, -math.inf
        trigger = signal + side * self.config.entry_rearm_atr * atr if stop_entry else signal
        entry_limit = trigger + (
            side * self.config.entry_limit_risk_expansion * abs(trigger - stop)
        )
        geometry = (
            stop < trigger <= entry_limit < target
            if side > 0
            else target < entry_limit <= trigger < stop
        )
        if not geometry:
            return trigger, entry_limit, math.nan, -math.inf
        cost_rate = self.config.all_in_cost_bps_each_side / 10_000.0
        slippage_rate = self.config.adverse_slippage_bps_each_side / 10_000.0
        planned_loss = planned_loss_per_unit(
            entry_limit,
            stop,
            side,
            cost_rate,
            slippage_rate,
        )
        if not math.isfinite(planned_loss) or planned_loss <= 0.0:
            return trigger, entry_limit, planned_loss, -math.inf
        target_r = net_r_at_price(
            entry_limit,
            target,
            side,
            planned_loss,
            cost_rate,
        )
        return trigger, entry_limit, planned_loss, target_r

    def _event_has_tradeable_geometry(
        self,
        *,
        direction: int,
        close: float,
        high: float,
        low: float,
        origin: float,
        atr: float,
    ) -> tuple[bool, dict[str, float]]:
        """Reject an event when even an immediate reversal cannot pay costs."""
        side = -direction
        stop = (low if side > 0 else high) - side * self.config.stop_buffer_atr * atr
        trigger, entry_limit, planned_loss, target_r = self._planned_geometry(
            side=side,
            signal=close,
            stop=stop,
            target=origin,
            atr=atr,
            stop_entry=False,
        )
        return target_r >= self.config.min_target_net_r, {
            "optimistic_stop": stop,
            "optimistic_trigger": trigger,
            "optimistic_entry_limit": entry_limit,
            "optimistic_planned_loss_per_unit": planned_loss,
            "optimistic_target_net_r": target_r,
        }

    def _detect_sweep(self, row: dict[str, float | int], previous_close: float) -> None:
        del previous_close
        if self.pending is not None or self.forced_episode is not None:
            return
        if not self._ready():
            self.diagnostics["candidate21_forced_events_not_ready"] += 1
            return
        atr = self._atr()
        lookback = int(self.config.forced_shock_lookback_bars)
        rows = list(self.bars)
        if not math.isfinite(atr) or atr <= 0.0 or len(rows) < lookback + 1:
            return
        origin = float(rows[-(lookback + 1)]["close"])
        close = float(row["close"])
        evidence = ForcedShockEvidence(
            move_atr=(close - origin) / atr,
            notional_burst=self._feature("notional_burst"),
            flow_3m=self._feature("flow_3m"),
            efficiency_60s=self._feature("efficiency_60s"),
            oi_change_15m=self._feature("oi_change_15m"),
            premium_change_5m=self._feature("premium_change_5m"),
        )
        direction = classify_forced_shock(evidence, self.shock_thresholds)
        if direction == 0:
            return
        leg = rows[-lookback:]
        high = max(float(item["high"]) for item in leg)
        low = min(float(item["low"]) for item in leg)
        if (direction > 0 and high <= origin) or (direction < 0 and low >= origin):
            return

        geometry_ok, geometry_details = self._event_has_tradeable_geometry(
            direction=direction,
            close=close,
            high=high,
            low=low,
            origin=origin,
            atr=atr,
        )
        if not geometry_ok:
            self.diagnostics["candidate21_forced_event_geometry_rejected"] += 1
            return
        self.diagnostics["candidate21_forced_event_geometry_eligible"] += 1

        self.scenario_counter += 1
        scenario_id = f"c21-forced-{self.scenario_counter:07d}"
        state = ForcedEpisode(
            scenario_id=scenario_id,
            shock_direction=direction,
            shock_index=self.bar_index,
            last_index=self.bar_index,
            expires_index=self.bar_index + self.config.forced_max_wait_bars,
            origin_price=origin,
            event_high=high,
            event_low=low,
            event_close=close,
            atr=atr,
            event_efficiency=evidence.efficiency_60s,
            event_oi_change_15m=evidence.oi_change_15m,
            event_premium_change_5m=evidence.premium_change_5m,
            event_notional_burst=evidence.notional_burst,
            event_flow_3m=evidence.flow_3m,
            latest_high=high,
            latest_low=low,
        )
        side = -direction
        details = {
            "candidate21_parent": "FORCED_POSITION_DELEVERAGING_CASCADE",
            "shock_direction": direction,
            "shock_origin": origin,
            "shock_move_atr": evidence.move_atr,
            "shock_notional_burst": evidence.notional_burst,
            "shock_flow_3m": evidence.flow_3m,
            "shock_efficiency_60s": evidence.efficiency_60s,
            "shock_oi_change_15m": evidence.oi_change_15m,
            "shock_premium_change_5m": evidence.premium_change_5m,
            "natural_target": origin,
            **geometry_details,
        }
        self.forced_episode = state
        self.pending = PendingSetup(
            scenario_id=scenario_id,
            branch="FORCED_EXHAUSTION",
            side=side,
            swept_kind="UP_CASCADE" if direction > 0 else "DOWN_CASCADE",
            pool_id=f"forced-{scenario_id}",
            pool_level=origin,
            created_index=self.bar_index,
            expires_index=state.expires_index,
            sweep_extreme=high if direction > 0 else low,
            structure=origin,
            atr=atr,
            hold_count=0,
            retrace_armed=False,
            details=details,
        )
        self.diagnostics["candidate21_forced_events_armed"] += 1
        self._transition(
            scenario_id,
            "FORCED_FLOW_EVENT_OPENED",
            int(row["ts"]),
            int(row["ts"]),
            state.decision.value,
            "DELEVERAGING_IMPULSE_IS_AN_EVENT_NOT_AN_ENTRY",
            close,
            details,
        )

    def _process_pending(self, row: dict[str, float | int]) -> bool:
        if self.pending is not None and self.pending.branch == "FORCED_EXHAUSTION":
            return self._process_forced(row)
        return super()._process_pending(row)

    def _process_forced(self, row: dict[str, float | int]) -> bool:
        setup, state = self.pending, self.forced_episode
        if setup is None or state is None:
            self._expire_pending(row, "MISSING_FORCED_FLOW_STATE")
            return True
        if self.bar_index <= setup.created_index:
            return True
        if not self._ready():
            if self.bar_index >= setup.expires_index:
                self._expire_pending(row, "FORCED_FLOW_OBSERVATIONS_BECAME_STALE")
            return True
        side = -state.shock_direction
        previous = state.decision
        state = advance_forced_episode(
            state,
            ForcedObservation(
                bar_index=self.bar_index,
                high=float(row["high"]),
                low=float(row["low"]),
                close=float(row["close"]),
                flow_60s=self._feature("flow_60s"),
                flow_3m=self._feature("flow_3m"),
                ret_60s_bps=self._feature("ret_60s_bps"),
                efficiency_60s=self._feature("efficiency_60s"),
                depth_imbalance_1=self._feature("depth_imbalance_1"),
                defending_depth_change_1m=self._feature(self._depth_field(side)),
                oi_change_15m=self._feature("oi_change_15m"),
                premium_change_1m=self._feature("premium_change_1m"),
            ),
            self.response_thresholds,
        )
        self.forced_episode = state
        setup.sweep_extreme = (
            state.latest_high if state.shock_direction > 0 else state.latest_low
        )
        terminal = asdict(state)
        terminal["decision"] = state.decision.value
        setup.details["latest_forced_flow_state"] = terminal
        self._transition(
            setup.scenario_id,
            "FORCED_FLOW_OBSERVED",
            int(row["ts"]),
            int(row["ts"]),
            state.decision.value,
            state.reason,
            float(row["close"]),
            setup.details,
        )

        if (
            previous is ForcedDecision.WAITING_EXHAUSTION
            and state.decision is ForcedDecision.WAITING_REVERSAL
        ):
            self.diagnostics["candidate21_forced_exhaustions"] += 1
            setup.details["terminal_exhaustion_state"] = terminal
            return self._submit_exhaustion_rearm(setup, state, row)
        if state.decision in (
            ForcedDecision.WAITING_EXHAUSTION,
            ForcedDecision.WAITING_REVERSAL,
        ):
            return True
        if state.decision is ForcedDecision.INVALIDATED:
            self.diagnostics["candidate21_forced_invalidated"] += 1
            self.pending = None
            self.forced_episode = None
            return True
        if state.decision is ForcedDecision.EXPIRED:
            self.diagnostics["candidate21_forced_expired"] += 1
            self.pending = None
            self.forced_episode = None
            return True

        # This path remains for replay compatibility, but the live policy arms
        # at exhaustion so a completed reversal bar cannot consume the target.
        self.diagnostics["candidate21_forced_reprices"] += 1
        self.pending = None
        self.forced_episode = None
        return True

    def _submit_exhaustion_rearm(
        self,
        setup: PendingSetup,
        state: ForcedEpisode,
        row: dict[str, float | int],
    ) -> bool:
        side = setup.side
        atr = self._atr()
        exhaustion_signal = float(row["high"]) if side > 0 else float(row["low"])
        stop = setup.sweep_extreme - side * self.config.stop_buffer_atr * atr
        target = float(setup.details["natural_target"])
        trigger, entry_limit, planned_loss, target_r = self._planned_geometry(
            side=side,
            signal=exhaustion_signal,
            stop=stop,
            target=target,
            atr=atr,
            stop_entry=True,
        )
        if (
            not math.isfinite(planned_loss)
            or planned_loss <= 0.0
            or target_r < self.config.min_target_net_r
        ):
            self.diagnostics["candidate21_forced_geometry_rejected"] += 1
            self._expire_pending(
                row,
                "EXHAUSTION_REARM_BELOW_MINIMUM_NET_R_AFTER_COSTS",
            )
            return False

        equity = self._equity_value()
        risk_budget = equity * self.config.risk_fraction
        quantity_value = floor_quantity(
            risk_budget / planned_loss,
            int(self.instrument.size_precision),
        )
        if quantity_value <= 0.0 or quantity_value * entry_limit < 10.0:
            self._expire_pending(row, "FORCED_FLOW_QUANTITY_BELOW_MINIMUM")
            return False

        order_side = OrderSide.BUY if side > 0 else OrderSide.SELL
        order_list = self.order_factory.bracket(
            instrument_id=self.config.instrument_id,
            order_side=order_side,
            quantity=self.instrument.make_qty(quantity_value),
            time_in_force=TimeInForce.FOK,
            entry_order_type=OrderType.STOP_LIMIT,
            entry_trigger_price=self.instrument.make_price(trigger),
            entry_price=self.instrument.make_price(entry_limit),
            entry_post_only=False,
            entry_tags=["ENTRY", "CANDIDATE21_FORCED_EXHAUSTION_STOP_FOK"],
            tp_price=self.instrument.make_price(target),
            sl_trigger_price=self.instrument.make_price(stop),
        )
        self.submit_order_list(order_list)
        self.entry_pending = True
        self.entry_pending_index = self.bar_index
        self.last_entry_index = self.bar_index
        self.current_scenario_id = setup.scenario_id
        self.current_branch = "FORCED_EXHAUSTION_REVERSAL"
        self.current_pool_level = target
        self.pending = None
        self.forced_episode = None
        self.diagnostics["entry_submissions"] += 1
        self.diagnostics["candidate21_forced_fok_entries"] += 1
        self.diagnostics["candidate21_forced_stop_limit_fok_entries"] += 1
        self.diagnostics["max_simultaneous_entry_intents"] = max(
            int(self.diagnostics["max_simultaneous_entry_intents"]),
            1,
        )
        self._transition(
            setup.scenario_id,
            "ENTRY_SUBMITTED",
            int(row["ts"]),
            int(row["ts"]),
            "ENTRY_PENDING",
            "EXHAUSTION_CONFIRMED_STOP_LIMIT_REARM",
            trigger,
            {
                **setup.details,
                "side": side,
                "exhaustion_bar_index": self.bar_index,
                "entry_trigger": trigger,
                "entry_limit_worst_fill": entry_limit,
                "entry_order_type": "STOP_LIMIT",
                "entry_time_in_force": "FOK",
                "entry_all_or_none": True,
                "stop": stop,
                "target": target,
                "target_source": "FROZEN_PRE_SHOCK_ORIGIN",
                "target_net_r": target_r,
                "quantity": quantity_value,
                "equity": equity,
                "risk_budget": risk_budget,
                "planned_loss_per_unit_at_worst_fill": planned_loss,
                "planned_account_loss_at_worst_fill": quantity_value * planned_loss,
                "exhaustion_state": asdict(state),
            },
        )
        return True


__all__ = ["Candidate21ForcedConfig", "Candidate21ForcedStrategy"]
