"""Candidate 60: fresh-position build, trapping and release after a jump.

The inherited source detects a completed four-hour impulse and waits for a
causal two-bar price reclaim. The earlier force-release experiment showed that
open-interest decline alone confused short covering/long liquidation with
exhaustion. This adapter instead asks whether new contracts were created during
the impulse and whether terminal aggressive flow is on the proposed reversal
side when price is reaccepted.
"""
from __future__ import annotations

from dataclasses import replace

import strategy_jump_force_base as _base

_ALLOWED_MODES = {"control", "position_build_reversal_flow"}


class Candidate35Config(_base.Candidate35Config, frozen=True):
    jump_position_build_release_mode: str = "control"


class Candidate35Strategy(_base.Candidate35Strategy):
    """Two-bar reclaim gated by fresh-contract build and reversal-side flow."""

    def __init__(self, config: Candidate35Config) -> None:
        mode = str(config.jump_position_build_release_mode).strip().lower()
        if mode not in _ALLOWED_MODES:
            raise ValueError(
                f"unsupported jump_position_build_release_mode={mode!r}"
            )
        if str(config.jump_force_release_mode).strip().lower() != (
            "price_confirmation_control"
        ):
            raise ValueError(
                "position-build experiment requires the base force policy in control mode"
            )
        super().__init__(config)
        self.diagnostics.update(
            {
                "candidate60_position_build_release_adapter": 1,
                "jump_position_build_release_mode": mode,
                "jump_position_build_thresholds_searched": 0,
                "jump_position_build_checks": 0,
                "jump_position_build_metrics_unresolved": 0,
                "jump_position_build_oi_rejections": 0,
                "jump_position_build_flow_rejections": 0,
                "jump_position_build_acceptances": 0,
                "jump_position_build_release_at_source": 0,
                "jump_position_build_release_after_source": 0,
                "jump_position_build_policy_changed_source": 0,
                "jump_position_build_policy_changed_risk": 0,
                "jump_position_build_policy_changed_management": 0,
            }
        )

    @property
    def _position_mode(self) -> str:
        return str(self.config.jump_position_build_release_mode).strip().lower()

    def _annotate_position_state(
        self,
        state: dict[str, float | int | bool],
        *,
        accepted: bool,
    ) -> str:
        pending = self.pending_jump
        if pending is None:
            return "unresolved"
        side = int(pending.decision.side)
        source_taker = float(state["force_source_taker_ratio"])
        if side > 0:
            source_on_reversal_side = source_taker > 1.0
        else:
            source_on_reversal_side = source_taker < 1.0
        if source_on_reversal_side:
            phase = "release_established_at_source_boundary"
        elif bool(state["force_taker_flow_flip"]):
            phase = "release_after_source_boundary"
        else:
            phase = "no_reversal_side_release"

        diagnostics = dict(pending.decision.diagnostics or {})
        diagnostics.update(state)
        diagnostics.update(
            {
                "jump_position_build_release_mode": self._position_mode,
                "jump_position_build_release_state_pass": int(accepted),
                "jump_position_build_contract_creation": int(
                    float(state["force_event_oi_change_fraction"]) > 0.0
                ),
                "jump_position_build_confirmation_reversal_flow": int(
                    bool(state["force_confirmation_flow_on_reversal_side"])
                ),
                "jump_position_build_release_phase": phase,
                "jump_position_build_known_before_entry": 1,
            }
        )
        pending.decision = replace(pending.decision, diagnostics=diagnostics)
        return phase

    def _try_pending_confirmation(self, ts_event: int) -> bool:
        mode = self._position_mode
        if mode == "control":
            return super()._try_pending_confirmation(ts_event)
        if self.pending_jump is None:
            return False
        if not self._price_confirmation_ready(ts_event):
            return super()._try_pending_confirmation(ts_event)

        self.diagnostics["jump_position_build_checks"] += 1
        state = self._force_state(ts_event)
        if state is None:
            self.diagnostics["jump_position_build_metrics_unresolved"] += 1
            self._event(
                "C60_POSITION_BUILD_METRICS_UNRESOLVED",
                ts_event,
                symbol=self.pending_jump.decision.symbol,
                episode_ts=self.pending_jump.decision.episode_ts,
                mode=mode,
            )
            return False

        oi_build = float(state["force_event_oi_change_fraction"]) > 0.0
        reversal_flow = bool(state["force_confirmation_flow_on_reversal_side"])
        accepted = oi_build and reversal_flow
        phase = self._annotate_position_state(state, accepted=accepted)
        if not oi_build:
            self.diagnostics["jump_position_build_oi_rejections"] += 1
        if not reversal_flow:
            self.diagnostics["jump_position_build_flow_rejections"] += 1
        if not accepted:
            self._event(
                "C60_POSITION_BUILD_RELEASE_REJECTED",
                ts_event,
                symbol=self.pending_jump.decision.symbol,
                episode_ts=self.pending_jump.decision.episode_ts,
                mode=mode,
                release_phase=phase,
                **state,
            )
            return False

        self.diagnostics["jump_position_build_acceptances"] += 1
        if phase == "release_established_at_source_boundary":
            self.diagnostics["jump_position_build_release_at_source"] += 1
        elif phase == "release_after_source_boundary":
            self.diagnostics["jump_position_build_release_after_source"] += 1
        self._event(
            "C60_POSITION_BUILD_RELEASE_ACCEPTED",
            ts_event,
            symbol=self.pending_jump.decision.symbol,
            episode_ts=self.pending_jump.decision.episode_ts,
            mode=mode,
            release_phase=phase,
            **state,
        )
        return super()._try_pending_confirmation(ts_event)

    def _submit_source_decision(
        self,
        decision,
        ts_event: int,
        *,
        source_exit_minute: int,
    ) -> None:
        before = int(self.diagnostics["entry_submissions"])
        super()._submit_source_decision(
            decision,
            ts_event,
            source_exit_minute=source_exit_minute,
        )
        if (
            int(self.diagnostics["entry_submissions"]) > before
            and self.current_scenario is not None
        ):
            self.current_scenario.update(
                {
                    "candidate": "candidate-60-position-build-release-v2",
                    "position_build_release_mode": self._position_mode,
                }
            )


__all__ = ["Candidate35Config", "Candidate35Strategy"]
