#!/usr/bin/env python3
"""Candidate 05 v38: isolated session raid with a target-derived price cap."""
from __future__ import annotations

import math
from typing import Any

from flow_inflection_logic import MIN_LIQUIDITY_TARGET_NET_R
from flow_inflection_logic import worst_entry_preserving_net_r
from isolated_smt_context import SHARED_ISOLATED_SMT_CONTEXT
from isolated_smt_logic import PeerMicroState
from isolated_smt_logic import isolated_smt_reversal_context
from logic import confirmation_passes
from logic import net_r_at_price
from logic import planned_loss_per_unit
from retrace_logic import pending_limit_invalidated
from smt_session_context import SHARED_SMT_SESSION_CONTEXT
from smt_session_divergence_logic import smt_session_divergence
from strategy_base import LiquidityResponseConfig
from strategy_base import PendingSetup
from strategy_base import _as_float
from strategy_v37_smt_session_divergence import SmtSessionDivergenceStrategy
from strategy_v9 import ArmedEntryPath


class IsolatedSmtReversalStrategy(SmtSessionDivergenceStrategy):
    """Trade only a fully isolated raid after common price discovery is absent.

    v37 treated two non-confirming peers as sufficient SMT context. Both resulting
    trades lost when the third peer had already consumed corresponding session
    liquidity. v38 changes the market cause rather than fitting a score:

    * all three peers must provide fresh, strictly prior completed-minute state;
    * none may consume the corresponding prior-session liquidity;
    * at most one peer may continue the raid direction with aligned return,
      aggressive flow and the existing CHoCH efficiency threshold;
    * the local market must still reclaim, turn tail flow and depth, and complete
      the unchanged displacement/CHoCH contract.

    Once that stronger state is complete, the original opposing-liquidity target
    and structural stop are frozen by the inherited v26 chain. One GTC limit is
    placed at the worst price which still preserves 0.40 post-cost R. The limit
    does not assume immediate execution: it may fill only at its cap or better,
    and v26 cancels it if the original stop, target, source, funding or daytrade
    horizon resolves first. Current-NAV 3% sizing, fees, adverse slippage,
    NautilusTrader matching and the one-global-slot lifecycle are unchanged.
    """

    BRANCH = "SMT_ISOLATED_SESSION_REVERSAL"

    def __init__(self, config: LiquidityResponseConfig) -> None:
        super().__init__(config)
        self.diagnostics.update(
            {
                "smt_isolated_micro_states_published": 0,
                "smt_isolated_peer_evaluations": 0,
                "smt_isolated_confirmations": 0,
                "smt_isolated_mixed_session_rejections": 0,
                "smt_isolated_common_continuation_rejections": 0,
                "smt_isolated_insufficient_micro_states": 0,
                "smt_isolated_choch_eligible": 0,
                "smt_isolated_price_cap_submissions": 0,
                "smt_isolated_price_cap_geometry_rejections": 0,
            },
        )

    def _publish_smt_peer_state(self, row: dict[str, float | int]) -> None:
        super()._publish_smt_peer_state(row)
        ts = int(row["ts"])
        if not self._smt_features_ready(ts):
            return
        values = (
            self._feature("ret_60s_bps"),
            self._feature("flow_60s"),
            self._feature("efficiency_60s"),
        )
        if not all(math.isfinite(float(value)) for value in values):
            return
        SHARED_ISOLATED_SMT_CONTEXT.publish(
            PeerMicroState(
                symbol=self.smt_symbol,
                ts_event=ts,
                ret_60s_bps=float(values[0]),
                flow_60s=float(values[1]),
                efficiency_60s=float(values[2]),
            ),
        )
        self.diagnostics["smt_isolated_micro_states_published"] += 1

    def _advance_smt_watch(self, row: dict[str, float | int]) -> None:
        watch = self.smt_watch
        if (
            watch is None
            or self.bar_index <= watch.created_index
            or watch.phase != "WAIT_PEER_DIVERGENCE"
        ):
            super()._advance_smt_watch(row)
            return

        # Preserve v37's structural invalidation priority exactly.
        if pending_limit_invalidated(
            side=watch.side,
            stop=watch.sweep_extreme,
            high=float(row["high"]),
            low=float(row["low"]),
        ):
            super()._advance_smt_watch(row)
            return

        current_ts = int(row["ts"])
        maximum_age_ns = int(self.config.feature_max_age_seconds * 1_000_000_000)
        session_peers = SHARED_SMT_SESSION_CONTEXT.prior_peer_states(
            current_symbol=self.smt_symbol,
            current_ts=current_ts,
        )
        session_decision = smt_session_divergence(
            current_symbol=self.smt_symbol,
            current_ts=current_ts,
            swept_kind=watch.swept_kind,
            peer_states=session_peers,
            minimum_penetration_atr=self.config.sweep_min_penetration_atr,
            maximum_age_ns=maximum_age_ns,
            minimum_nonconfirming_peers=3,
        )
        micro_peers = SHARED_ISOLATED_SMT_CONTEXT.prior_peer_states(
            current_symbol=self.smt_symbol,
            current_ts=current_ts,
        )
        decision = isolated_smt_reversal_context(
            current_symbol=self.smt_symbol,
            current_ts=current_ts,
            side=watch.side,
            session_decision=session_decision,
            micro_states=micro_peers,
            maximum_age_ns=maximum_age_ns,
            minimum_counterflow=self.config.acceptance_flow_min,
            minimum_efficiency=self.config.rejection_confirm_efficiency_min,
        )
        self.diagnostics["smt_isolated_peer_evaluations"] += 1
        watch.details.update(
            {
                "smt_isolated_context": True,
                "smt_isolated_reason_code": decision.reason_code,
                "smt_isolated_valid_micro_peers": list(decision.valid_micro_peers),
                "smt_isolated_common_continuation_peers": list(
                    decision.common_continuation_peers,
                ),
                "smt_isolated_micro_states": [
                    {
                        "symbol": state.symbol,
                        "ts_event": state.ts_event,
                        "age_ns": current_ts - state.ts_event,
                        "ret_60s_bps": state.ret_60s_bps,
                        "flow_60s": state.flow_60s,
                        "efficiency_60s": state.efficiency_60s,
                    }
                    for state in micro_peers
                ],
            },
        )
        if not decision.confirmed:
            if decision.reason_code == "SESSION_RAID_NOT_ISOLATED_ACROSS_ALL_PEERS":
                self.diagnostics["smt_isolated_mixed_session_rejections"] += 1
            elif decision.reason_code == "COMMON_PEER_PRICE_DISCOVERY_CONTINUES_RAID_DIRECTION":
                self.diagnostics["smt_isolated_common_continuation_rejections"] += 1
            else:
                self.diagnostics["smt_isolated_insufficient_micro_states"] += 1
            self._close_smt_watch(row, decision.reason_code)
            return

        self.diagnostics["smt_isolated_confirmations"] += 1
        watch.details["smt_isolated_context_confirmed"] = True
        super()._advance_smt_watch(row)

    def _try_smt_choch(
        self,
        watch: Any,
        row: dict[str, float | int],
    ) -> None:
        atr = self._atr()
        if not confirmation_passes(
            side=watch.side,
            open_price=float(row["open"]),
            close_price=float(row["close"]),
            high=float(row["high"]),
            low=float(row["low"]),
            structure=watch.structure,
            atr=atr,
            flow_60s=self._feature("flow_60s"),
            efficiency_60s=self._feature("efficiency_60s"),
            min_body_atr=self.config.rejection_confirm_body_atr,
            min_flow=self.config.rejection_confirm_flow_min,
            min_efficiency=self.config.rejection_confirm_efficiency_min,
            min_close_location=self.config.rejection_confirm_close_location,
        ):
            return
        self.diagnostics["smt_session_choch_confirmations"] += 1
        if not self._smt_entry_slot_idle():
            self.diagnostics["smt_session_slot_conflicts"] += 1
            self._close_smt_watch(row, "LOCAL_ENTRY_SLOT_OCCUPIED_AT_ISOLATED_SMT_CHOCH")
            return

        details = {
            **watch.details,
            "smt_isolated_session_reversal": True,
            "confirmation_index": self.bar_index,
            "confirmation_ts": int(row["ts"]),
            "confirmation_close": float(row["close"]),
            "entry_transition": "CHOCH_TARGET_DERIVED_PRICE_CAP",
        }
        setup = PendingSetup(
            scenario_id=watch.scenario_id,
            branch=self.BRANCH,
            side=watch.side,
            swept_kind=watch.swept_kind,
            pool_id=f"SESSION:{watch.session_key}:{watch.swept_kind}",
            pool_level=watch.boundary,
            created_index=watch.created_index,
            expires_index=watch.choch_expires_index,
            sweep_extreme=watch.sweep_extreme,
            structure=watch.structure,
            atr=watch.atr,
            hold_count=0,
            retrace_armed=False,
            details=details,
        )
        self.smt_watch = None
        handled = self._submit_entry(setup, row)
        armed = self.armed_entry_path
        if (
            not handled
            or armed is None
            or armed.setup.scenario_id != setup.scenario_id
        ):
            if self.scenario_states.get(setup.scenario_id) != "CLOSED":
                self._transition(
                    setup.scenario_id,
                    "SMT_ISOLATED_ENTRY_PATH_FAILED",
                    int(row["ts"]),
                    int(row["ts"]),
                    "CLOSED",
                    "ISOLATED_SMT_CHOCH_COULD_NOT_FREEZE_EXECUTABLE_DESTINATION",
                    float(row["close"]),
                    details,
                )
            return

        self.diagnostics["smt_isolated_choch_eligible"] += 1
        submitted = self._submit_isolated_price_cap(armed, row)
        if submitted:
            self.diagnostics["smt_session_submissions"] += 1
            self.diagnostics["smt_isolated_price_cap_submissions"] += 1

    def _submit_isolated_price_cap(
        self,
        armed: ArmedEntryPath,
        row: dict[str, float | int],
    ) -> bool:
        side = armed.setup.side
        observed_entry = _as_float(self.instrument.make_price(float(row["close"])))
        stop = armed.stop
        target = self._frozen_target_price(armed)
        target_source = str(armed.details.get("frozen_target_source", ""))
        cost_rate = self.config.all_in_cost_bps_each_side / 10_000.0
        slippage_rate = self.config.adverse_slippage_bps_each_side / 10_000.0
        raw_bound = worst_entry_preserving_net_r(
            stop=stop,
            target=target,
            side=side,
            minimum_net_r=MIN_LIQUIDITY_TARGET_NET_R,
            cost_rate=cost_rate,
            adverse_slippage_rate=slippage_rate,
        )
        if not math.isfinite(raw_bound):
            self.diagnostics["smt_isolated_price_cap_geometry_rejections"] += 1
            self._expire_armed_entry(row, "ISOLATED_SMT_TARGET_HAS_NO_VALID_ENTRY_CAP")
            return False

        price_increment = _as_float(self.instrument.price_increment)
        entry_price = self.instrument.make_price(raw_bound)
        entry = _as_float(entry_price)
        if side > 0 and entry > raw_bound:
            entry_price = self.instrument.make_price(raw_bound - price_increment)
            entry = _as_float(entry_price)
        elif side < 0 and entry < raw_bound:
            entry_price = self.instrument.make_price(raw_bound + price_increment)
            entry = _as_float(entry_price)

        planned_loss = planned_loss_per_unit(
            entry,
            stop,
            side,
            cost_rate,
            slippage_rate,
        )
        target_price = self.instrument.make_price(target)
        target = _as_float(target_price)
        rounded_r = net_r_at_price(entry, target, side, planned_loss, cost_rate)
        if (
            not math.isfinite(planned_loss)
            or planned_loss <= 0.0
            or rounded_r + 1e-9 < MIN_LIQUIDITY_TARGET_NET_R
            or (side > 0 and not stop < entry < target)
            or (side < 0 and not target < entry < stop)
        ):
            self.diagnostics["smt_isolated_price_cap_geometry_rejections"] += 1
            self._expire_armed_entry(row, "ISOLATED_SMT_ENTRY_CAP_INVALID_AFTER_ROUNDING")
            return False

        stop_price = self.instrument.make_price(stop)
        return self._submit_price_capped_bracket(
            armed=armed,
            row=row,
            entry_price=entry_price,
            stop_price=stop_price,
            target_price=target_price,
            sizing_entry=entry,
            planned_loss=planned_loss,
            target_source=target_source,
            target_r=rounded_r,
            branch=self.BRANCH,
            event_type="SMT_ISOLATED_PRICE_CAP_SUBMITTED",
            reason="ALL_PEERS_NONCONFIRM_COMMON_CONTINUATION_ABSENT_CHOCH_CAP",
            expires_index=self.bar_index + 2,
            entry_tag="SMT_ISOLATED_TARGET_DERIVED_ENTRY",
            extra={
                "observed_choch_price": observed_entry,
                "raw_target_derived_entry_cap": raw_bound,
                "rounded_target_derived_entry_cap": entry,
                "rounded_target_net_r": rounded_r,
                "immediate_fill_assumed": False,
                "entry_cap_distance_bps": (
                    side * (entry - observed_entry) / observed_entry * 10_000.0
                ),
            },
        )


__all__ = ["IsolatedSmtReversalStrategy"]
