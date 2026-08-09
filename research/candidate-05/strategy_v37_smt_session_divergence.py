#!/usr/bin/env python3
"""Candidate 05 v37: intermarket divergence at completed session liquidity."""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any

from nautilus_trader.model.data import Bar

from depth_logic import DIRECTIONAL_DEPTH_MIN
from external_session_logic import utc_session_key
from flow_inflection_logic import SWEEP_TAIL_IMPROVEMENT_MIN
from logic import confirmation_passes
from retrace_logic import pending_limit_invalidated
from smt_session_context import SHARED_SMT_SESSION_CONTEXT
from smt_session_divergence_logic import PeerSessionState
from smt_session_divergence_logic import local_session_raid_response
from smt_session_divergence_logic import smt_session_divergence
from strategy_base import LiquidityResponseConfig
from strategy_base import PendingSetup
from strategy_v26 import ScenarioValidEntryStrategy
from strategy_v36_cross_asset_repricing_gate import symbol_from_instrument


@dataclass(slots=True)
class SmtSessionWatch:
    scenario_id: str
    side: int
    swept_kind: str
    session_key: int
    boundary: float
    sweep_extreme: float
    structure: float
    atr: float
    created_index: int
    created_ts: int
    response_expires_index: int
    response_index: int
    choch_expires_index: int
    phase: str
    details: dict[str, Any]


class SmtSessionDivergenceStrategy(ScenarioValidEntryStrategy):
    """Trade a session raid only when peers fail to confirm and structure turns.

    A completed four-hour activity-session high or low is the external liquidity
    reference. A local instrument must penetrate it by the existing sweep ATR
    distance with the existing activity burst. On the first later completed
    minute, at least two of the other three instruments must have failed to
    penetrate their corresponding prior-session level during the local raid
    minute. Same-timestamp peer observations are prohibited.

    SMT is only context. The local market must still reclaim the boundary with
    improving reversal tail flow and supportive resting depth, then break the
    sweep bar's opposite structure with the unchanged v26 displacement contract.
    The resulting setup enters through inherited v26 path selection, target,
    structural stop, costs, 3% current-NAV sizing and NautilusTrader lifecycle.
    """

    BRANCH = "SMT_SESSION_DIVERGENCE"

    def __init__(self, config: LiquidityResponseConfig) -> None:
        super().__init__(config)
        self.smt_symbol = symbol_from_instrument(config.instrument_id)
        self.smt_session_key: int | None = None
        self.smt_current_high = -math.inf
        self.smt_current_low = math.inf
        self.smt_previous_high = float("nan")
        self.smt_previous_low = float("nan")
        self.smt_high_consumed = False
        self.smt_low_consumed = False
        self.smt_watch: SmtSessionWatch | None = None
        self.smt_scenario_counter = 0
        self.diagnostics.update(
            {
                "smt_session_states_published": 0,
                "smt_session_external_high_raids": 0,
                "smt_session_external_low_raids": 0,
                "smt_session_ambiguous_two_sided_raids": 0,
                "smt_session_watches": 0,
                "smt_session_peer_evaluations": 0,
                "smt_session_peer_divergences": 0,
                "smt_session_peer_confirmations": 0,
                "smt_session_insufficient_peer_states": 0,
                "smt_session_same_timestamp_states_used": 0,
                "smt_session_response_observations": 0,
                "smt_session_responses": 0,
                "smt_session_response_expiries": 0,
                "smt_session_stop_invalidations": 0,
                "smt_session_choch_confirmations": 0,
                "smt_session_choch_expiries": 0,
                "smt_session_slot_conflicts": 0,
                "smt_session_submissions": 0,
                "smt_session_closed": 0,
            },
        )

    def on_bar(self, bar: Bar) -> None:
        super().on_bar(bar)
        if not self.bars:
            return
        row = self.bars[-1]
        self._roll_smt_session(row)
        self._publish_smt_peer_state(row)

        ts = int(row["ts"])
        if not self._in_evaluation(ts):
            self._close_smt_watch(row, "EVALUATION_ENDED_DURING_SMT_SESSION_SCENARIO")
            return
        if self._funding_blackout(ts):
            self._close_smt_watch(row, "FUNDING_BLACKOUT_DURING_SMT_SESSION_SCENARIO")
            return
        if not self._smt_features_ready(ts):
            return

        if self.smt_watch is not None:
            self._advance_smt_watch(row)
        if self.smt_watch is None:
            self._detect_smt_session_raid(row)

    def _smt_features_ready(self, ts: int) -> bool:
        feature = self.current_feature
        if feature is None or not bool(feature.get("feature_ready", False)):
            return False
        observed = int(feature.get("observed_time_ns", 0))
        age = (ts - observed) / 1_000_000_000
        if age < -1e-9:
            raise RuntimeError("future feature observation reached v37")
        return age <= self.config.feature_max_age_seconds

    def _roll_smt_session(self, row: dict[str, float | int]) -> None:
        key = utc_session_key(int(row["ts"]), tuple(self.config.session_hours))
        if self.smt_session_key is None:
            self.smt_session_key = key
        elif key != self.smt_session_key:
            if math.isfinite(self.smt_current_high) and math.isfinite(self.smt_current_low):
                self.smt_previous_high = self.smt_current_high
                self.smt_previous_low = self.smt_current_low
            self.smt_session_key = key
            self.smt_current_high = -math.inf
            self.smt_current_low = math.inf
            self.smt_high_consumed = False
            self.smt_low_consumed = False
        self.smt_current_high = max(self.smt_current_high, float(row["high"]))
        self.smt_current_low = min(self.smt_current_low, float(row["low"]))

    def _publish_smt_peer_state(self, row: dict[str, float | int]) -> None:
        atr = self._atr()
        if (
            not math.isfinite(atr)
            or atr <= 0.0
            or not math.isfinite(self.smt_previous_high)
            or not math.isfinite(self.smt_previous_low)
            or not self._smt_features_ready(int(row["ts"]))
        ):
            return
        SHARED_SMT_SESSION_CONTEXT.publish(
            PeerSessionState(
                symbol=self.smt_symbol,
                ts_event=int(row["ts"]),
                high=float(row["high"]),
                low=float(row["low"]),
                close=float(row["close"]),
                atr=atr,
                previous_session_high=self.smt_previous_high,
                previous_session_low=self.smt_previous_low,
                flow_15s=self._feature("flow_15s"),
                flow_60s=self._feature("flow_60s"),
                depth_imbalance=self._feature("depth_imbalance_1"),
            ),
        )
        self.diagnostics["smt_session_states_published"] += 1

    def _detect_smt_session_raid(self, row: dict[str, float | int]) -> None:
        if len(self.bars) < 2 or not math.isfinite(self.smt_previous_high) or not math.isfinite(self.smt_previous_low):
            return
        if float(self._feature("notional_burst")) < self.config.sweep_min_notional_burst:
            return
        atr = self._atr()
        if not math.isfinite(atr) or atr <= 0.0:
            return
        previous_close = float(self.bars[-2]["close"])
        high_raid = (
            not self.smt_high_consumed
            and previous_close <= self.smt_previous_high
            and float(row["high"]) >= self.smt_previous_high + self.config.sweep_min_penetration_atr * atr
        )
        low_raid = (
            not self.smt_low_consumed
            and previous_close >= self.smt_previous_low
            and float(row["low"]) <= self.smt_previous_low - self.config.sweep_min_penetration_atr * atr
        )
        if high_raid and low_raid:
            self.smt_high_consumed = True
            self.smt_low_consumed = True
            self.diagnostics["smt_session_ambiguous_two_sided_raids"] += 1
            return
        if not high_raid and not low_raid:
            return
        if high_raid:
            self.smt_high_consumed = True
            side = -1
            swept_kind = "HIGH"
            boundary = self.smt_previous_high
            sweep_extreme = float(row["high"])
            structure = float(row["low"])
            self.diagnostics["smt_session_external_high_raids"] += 1
        else:
            self.smt_low_consumed = True
            side = 1
            swept_kind = "LOW"
            boundary = self.smt_previous_low
            sweep_extreme = float(row["low"])
            structure = float(row["high"])
            self.diagnostics["smt_session_external_low_raids"] += 1

        self.smt_scenario_counter += 1
        scenario_id = f"smt-{self.smt_scenario_counter:07d}"
        details = {
            "smt_session_divergence": True,
            "side": side,
            "swept_kind": swept_kind,
            "session_key": self.smt_session_key,
            "session_boundary": boundary,
            "sweep_extreme": sweep_extreme,
            "choch_structure": structure,
            "sweep_index": self.bar_index,
            "sweep_ts": int(row["ts"]),
            "sweep_open": float(row["open"]),
            "sweep_high": float(row["high"]),
            "sweep_low": float(row["low"]),
            "sweep_close": float(row["close"]),
            "sweep_atr": atr,
            "sweep_notional_burst": self._feature("notional_burst"),
            "pattern_action": "SESSION_RAID_PEER_DIVERGENCE_RECLAIM_CHOCH",
        }
        self.smt_watch = SmtSessionWatch(
            scenario_id=scenario_id,
            side=side,
            swept_kind=swept_kind,
            session_key=int(self.smt_session_key or 0),
            boundary=boundary,
            sweep_extreme=sweep_extreme,
            structure=structure,
            atr=atr,
            created_index=self.bar_index,
            created_ts=int(row["ts"]),
            response_expires_index=self.bar_index + 3,
            response_index=-1,
            choch_expires_index=-1,
            phase="WAIT_PEER_DIVERGENCE",
            details=details,
        )
        self.diagnostics["smt_session_watches"] += 1
        self._transition(
            scenario_id,
            "SMT_SESSION_RAID_ARMED",
            int(row["ts"]),
            int(row["ts"]),
            "WAIT_PEER_DIVERGENCE",
            "LOCAL_COMPLETED_SESSION_LIQUIDITY_PENETRATED_WITH_ACTIVITY",
            boundary,
            details,
        )

    def _advance_smt_watch(self, row: dict[str, float | int]) -> None:
        watch = self.smt_watch
        if watch is None or self.bar_index <= watch.created_index:
            return
        if pending_limit_invalidated(
            side=watch.side,
            stop=watch.sweep_extreme,
            high=float(row["high"]),
            low=float(row["low"]),
        ):
            self.diagnostics["smt_session_stop_invalidations"] += 1
            self._close_smt_watch(row, "SESSION_RAID_EXTREME_ACCEPTED_BEFORE_SMT_CHOCH")
            return

        if watch.phase == "WAIT_PEER_DIVERGENCE":
            peers = SHARED_SMT_SESSION_CONTEXT.prior_peer_states(
                current_symbol=self.smt_symbol,
                current_ts=int(row["ts"]),
            )
            decision = smt_session_divergence(
                current_symbol=self.smt_symbol,
                current_ts=int(row["ts"]),
                swept_kind=watch.swept_kind,
                peer_states=peers,
                minimum_penetration_atr=self.config.sweep_min_penetration_atr,
                maximum_age_ns=int(self.config.feature_max_age_seconds * 1_000_000_000),
            )
            self.diagnostics["smt_session_peer_evaluations"] += 1
            if len(decision.valid_peers) < 2:
                self.diagnostics["smt_session_insufficient_peer_states"] += 1
                self._close_smt_watch(row, "INSUFFICIENT_PRIOR_COMPLETED_PEER_SESSION_STATES")
                return
            if not decision.confirmed:
                self.diagnostics["smt_session_peer_confirmations"] += 1
                self._close_smt_watch(row, "PEERS_CONFIRMED_LOCAL_SESSION_LIQUIDITY_DIRECTION")
                return
            self.diagnostics["smt_session_peer_divergences"] += 1
            watch.phase = "WAIT_LOCAL_RESPONSE"
            watch.details.update(
                {
                    "smt_valid_peers": list(decision.valid_peers),
                    "smt_same_side_sweep_peers": list(decision.same_side_sweep_peers),
                    "smt_nonconfirming_peers": list(decision.nonconfirming_peers),
                    "smt_peer_states": [
                        {
                            "symbol": state.symbol,
                            "ts_event": state.ts_event,
                            "age_ns": int(row["ts"]) - state.ts_event,
                            "high": state.high,
                            "low": state.low,
                            "atr": state.atr,
                            "previous_session_high": state.previous_session_high,
                            "previous_session_low": state.previous_session_low,
                        }
                        for state in peers
                    ],
                },
            )
            self._transition(
                watch.scenario_id,
                "SMT_SESSION_DIVERGENCE_CONFIRMED",
                int(row["ts"]),
                int(row["ts"]),
                "WAIT_LOCAL_RESPONSE",
                "AT_LEAST_TWO_PEERS_FAILED_TO_CONFIRM_CORRESPONDING_SESSION_RAID",
                watch.boundary,
                dict(watch.details),
            )

        if watch.phase == "WAIT_LOCAL_RESPONSE":
            self.diagnostics["smt_session_response_observations"] += 1
            if self.bar_index > watch.response_expires_index:
                self.diagnostics["smt_session_response_expiries"] += 1
                self._close_smt_watch(row, "THREE_BAR_LOCAL_RESPONSE_WINDOW_EXPIRED")
                return
            if not local_session_raid_response(
                side=watch.side,
                swept_kind=watch.swept_kind,
                boundary=watch.boundary,
                close=float(row["close"]),
                flow_15s=self._feature("flow_15s"),
                flow_60s=self._feature("flow_60s"),
                depth_imbalance=self._feature("depth_imbalance_1"),
                minimum_tail_improvement=SWEEP_TAIL_IMPROVEMENT_MIN,
                minimum_directional_depth=DIRECTIONAL_DEPTH_MIN,
            ):
                return
            watch.phase = "WAIT_LOCAL_CHOCH"
            watch.response_index = self.bar_index
            watch.choch_expires_index = self.bar_index + self.config.rejection_confirmation_bars
            watch.details.update(
                {
                    "response_index": self.bar_index,
                    "response_ts": int(row["ts"]),
                    "response_close": float(row["close"]),
                    "response_flow_15s": self._feature("flow_15s"),
                    "response_flow_60s": self._feature("flow_60s"),
                    "response_depth_imbalance": self._feature("depth_imbalance_1"),
                },
            )
            self.diagnostics["smt_session_responses"] += 1
            self._transition(
                watch.scenario_id,
                "SMT_LOCAL_RAID_RESPONSE_CONFIRMED",
                int(row["ts"]),
                int(row["ts"]),
                "WAIT_LOCAL_CHOCH",
                "SESSION_LEVEL_RECLAIMED_WITH_TAIL_FLOW_TURN_AND_CURRENT_DEPTH",
                float(row["close"]),
                dict(watch.details),
            )

        if watch.phase == "WAIT_LOCAL_CHOCH":
            if self.bar_index > watch.choch_expires_index:
                self.diagnostics["smt_session_choch_expiries"] += 1
                self._close_smt_watch(row, "EXISTING_REJECTION_CHOCH_WINDOW_EXPIRED")
                return
            self._try_smt_choch(watch, row)

    def _try_smt_choch(
        self,
        watch: SmtSessionWatch,
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
            self._close_smt_watch(row, "LOCAL_ENTRY_SLOT_OCCUPIED_AT_SMT_CHOCH")
            return
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
            details={
                **watch.details,
                "confirmation_index": self.bar_index,
                "confirmation_ts": int(row["ts"]),
                "confirmation_close": float(row["close"]),
            },
        )
        self.smt_watch = None
        handled = self._submit_entry(setup, row)
        if handled:
            self.diagnostics["smt_session_submissions"] += 1
        elif self.armed_entry_path is None and not self.entry_pending:
            self._transition(
                setup.scenario_id,
                "SMT_SESSION_ENTRY_PATH_FAILED",
                int(row["ts"]),
                int(row["ts"]),
                "CLOSED",
                "SMT_CHOCH_COULD_NOT_FORM_EXECUTABLE_INHERITED_PATH",
                float(row["close"]),
                setup.details,
            )

    def _smt_entry_slot_idle(self) -> bool:
        return (
            self.portfolio.is_flat(self.config.instrument_id)
            and not self.entry_pending
            and not self.exit_pending
            and self.pending is None
            and self.armed_entry_path is None
        )

    def _submit_price_capped_bracket(self, *args: Any, **kwargs: Any) -> bool:
        armed = kwargs.get("armed")
        if armed is not None and bool(armed.setup.details.get("smt_session_divergence")):
            kwargs["branch"] = self.BRANCH
            extra = dict(kwargs.get("extra") or {})
            extra["smt_inherited_entry_path"] = kwargs.get("event_type")
            kwargs["extra"] = extra
        return bool(super()._submit_price_capped_bracket(*args, **kwargs))

    def _close_smt_watch(
        self,
        row: dict[str, float | int],
        reason: str,
    ) -> None:
        watch = self.smt_watch
        if watch is None:
            return
        self.smt_watch = None
        self.diagnostics["smt_session_closed"] += 1
        if self.scenario_states.get(watch.scenario_id) == "CLOSED":
            return
        self._transition(
            watch.scenario_id,
            "SMT_SESSION_SCENARIO_CLOSED",
            int(row["ts"]),
            int(row["ts"]),
            "CLOSED",
            reason,
            watch.boundary,
            dict(watch.details),
        )

    def on_stop(self) -> None:
        if self.smt_watch is not None and self.bars:
            self._close_smt_watch(
                self.bars[-1],
                "BACKTEST_ENDED_WITH_SMT_SESSION_WATCH_OPEN",
            )
        super().on_stop()


__all__ = ["SmtSessionDivergenceStrategy"]
