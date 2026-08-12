"""Candidate 60 forced-flow lifecycle state for delayed jump reversal.

The inherited strategy owns the source jump detector, one-slot arbitration,
price reclaim, structural stop, 240-minute source clock, protection management,
costs and current-NAV risk sizing. This adapter only asks whether the completed
impulse extinguished contracts and whether aggressive flow has crossed to the
reversal side before a price-confirmed entry is allowed.
"""
from __future__ import annotations

from dataclasses import replace
import math
from typing import Any

import strategy_jump_delayed_base as _base
from router import RouteDecision, _asof

_NS_PER_MINUTE = 60_000_000_000
_ALLOWED_MODES = {
    "price_confirmation_control",
    "oi_unwind",
    "oi_unwind_flow_flip",
}


class Candidate35Config(_base.Candidate35Config, frozen=True):
    jump_force_release_mode: str = "price_confirmation_control"
    jump_force_event_lookback_minutes: int = 240


class Candidate35Strategy(_base.Candidate35Strategy):
    """Delayed reversal plus a target-contract force/release state transition."""

    def __init__(self, config: Candidate35Config) -> None:
        mode = str(config.jump_force_release_mode).strip().lower()
        if mode not in _ALLOWED_MODES:
            raise ValueError(f"unsupported jump_force_release_mode={mode!r}")
        lookback = int(config.jump_force_event_lookback_minutes)
        if lookback <= 0 or lookback % 5 != 0:
            raise ValueError(
                "jump_force_event_lookback_minutes must be a positive 5m multiple"
            )
        if str(config.jump_post_state_mode).strip().lower() != "two_bar_price":
            raise ValueError(
                "forced-flow experiment requires the frozen two_bar_price state"
            )
        super().__init__(config)
        self.diagnostics.update(
            {
                "candidate60_force_release_adapter": 1,
                "jump_force_release_mode": mode,
                "jump_force_event_lookback_minutes": lookback,
                "jump_force_release_thresholds_searched": 0,
                "jump_force_release_checks": 0,
                "jump_force_release_metrics_unresolved": 0,
                "jump_force_release_oi_rejections": 0,
                "jump_force_release_terminal_flow_rejections": 0,
                "jump_force_release_acceptances": 0,
                "jump_force_release_policy_changed_source": 0,
                "jump_force_release_policy_changed_risk": 0,
                "jump_force_release_policy_changed_management": 0,
            }
        )

    @property
    def _force_mode(self) -> str:
        return str(self.config.jump_force_release_mode).strip().lower()

    def _price_confirmation_ready(self, ts_event: int) -> bool:
        """Mirror the inherited completed-bar price condition without submitting."""
        pending = self.pending_jump
        if pending is None:
            return False
        if self.minute_index > pending.deadline_minute_index:
            return False
        elapsed = self.minute_index - pending.source_minute_index
        bucket = max(1, int(self.route_config.jump_confirmation_bucket_minutes))
        minimum = int(self.config.jump_min_confirmation_elapsed_minutes)
        if elapsed < minimum or elapsed < bucket or elapsed % bucket != 0:
            return False
        symbol = pending.decision.symbol
        recent = list(self.bars[symbol])[-elapsed:]
        if len(recent) < elapsed:
            return False
        diagnostics = dict(pending.decision.diagnostics or {})
        terminal_low = float(diagnostics["terminal_minute_low"])
        terminal_high = float(diagnostics["terminal_minute_high"])
        close = float(recent[-1].close)
        side = int(pending.decision.side)
        return close > terminal_high if side > 0 else close < terminal_low

    @staticmethod
    def _finite_positive(value: Any) -> float | None:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        if not math.isfinite(number) or number <= 0.0:
            return None
        return number

    def _force_state(self, ts_event: int) -> dict[str, float | int | bool] | None:
        pending = self.pending_jump
        if pending is None:
            return None
        symbol = pending.decision.symbol
        source_ts = int(pending.decision.episode_ts)
        lookback = int(self.config.jump_force_event_lookback_minutes)
        prior_ts = source_ts - lookback * _NS_PER_MINUTE
        prior = _asof(symbol, prior_ts)
        source = _asof(symbol, source_ts)
        current = _asof(symbol, int(ts_event))
        if prior is None or source is None or current is None:
            return None

        prior_oi = self._finite_positive(prior.get("sum_open_interest"))
        source_oi = self._finite_positive(source.get("sum_open_interest"))
        current_oi = self._finite_positive(current.get("sum_open_interest"))
        source_taker = self._finite_positive(
            source.get("sum_taker_long_short_vol_ratio")
        )
        current_taker = self._finite_positive(
            current.get("sum_taker_long_short_vol_ratio")
        )
        if None in (prior_oi, source_oi, current_oi, source_taker, current_taker):
            return None
        assert prior_oi is not None
        assert source_oi is not None
        assert current_oi is not None
        assert source_taker is not None
        assert current_taker is not None

        side = int(pending.decision.side)
        event_oi_change = source_oi / prior_oi - 1.0
        post_boundary_oi_change = current_oi / source_oi - 1.0
        oi_unwind = event_oi_change < 0.0
        if side > 0:
            source_flow_on_impulse_side = source_taker < 1.0
            confirmation_flow_on_reversal_side = current_taker > 1.0
            flow_improved_toward_reversal = current_taker > source_taker
        else:
            source_flow_on_impulse_side = source_taker > 1.0
            confirmation_flow_on_reversal_side = current_taker < 1.0
            flow_improved_toward_reversal = current_taker < source_taker
        flow_flip = (
            source_flow_on_impulse_side
            and confirmation_flow_on_reversal_side
            and flow_improved_toward_reversal
        )
        return {
            "force_prior_metrics_ts": int(prior["ts_event"]),
            "force_source_metrics_ts": int(source["ts_event"]),
            "force_confirmation_metrics_ts": int(current["ts_event"]),
            "force_prior_metrics_age_minutes": float(prior["age_minutes"]),
            "force_source_metrics_age_minutes": float(source["age_minutes"]),
            "force_confirmation_metrics_age_minutes": float(current["age_minutes"]),
            "force_prior_open_interest": prior_oi,
            "force_source_open_interest": source_oi,
            "force_confirmation_open_interest": current_oi,
            "force_event_oi_change_fraction": event_oi_change,
            "force_post_boundary_oi_change_fraction": post_boundary_oi_change,
            "force_oi_unwind": bool(oi_unwind),
            "force_source_taker_ratio": source_taker,
            "force_confirmation_taker_ratio": current_taker,
            "force_source_flow_on_impulse_side": bool(source_flow_on_impulse_side),
            "force_confirmation_flow_on_reversal_side": bool(
                confirmation_flow_on_reversal_side
            ),
            "force_flow_improved_toward_reversal": bool(
                flow_improved_toward_reversal
            ),
            "force_taker_flow_flip": bool(flow_flip),
            "force_neutral_taker_ratio": 1.0,
            "force_neutral_oi_change": 0.0,
            "force_event_lookback_minutes": lookback,
        }

    def _state_passes(self, state: dict[str, float | int | bool]) -> bool:
        mode = self._force_mode
        if mode == "price_confirmation_control":
            return True
        if mode == "oi_unwind":
            return bool(state["force_oi_unwind"])
        if mode == "oi_unwind_flow_flip":
            return bool(state["force_oi_unwind"]) and bool(
                state["force_taker_flow_flip"]
            )
        raise ValueError(mode)

    def _attach_force_state(
        self,
        state: dict[str, float | int | bool],
        *,
        accepted: bool,
    ) -> None:
        pending = self.pending_jump
        if pending is None:
            return
        diagnostics = dict(pending.decision.diagnostics or {})
        diagnostics.update(state)
        diagnostics.update(
            {
                "jump_force_release_mode": self._force_mode,
                "jump_force_release_state_pass": int(accepted),
                "jump_force_release_known_before_entry": 1,
            }
        )
        pending.decision = replace(pending.decision, diagnostics=diagnostics)

    def _try_pending_confirmation(self, ts_event: int) -> bool:
        mode = self._force_mode
        if mode == "price_confirmation_control":
            return super()._try_pending_confirmation(ts_event)
        if self.pending_jump is None:
            return False
        if not self._price_confirmation_ready(ts_event):
            # Preserve inherited expiry and all non-confirmed behavior.
            return super()._try_pending_confirmation(ts_event)

        self.diagnostics["jump_force_release_checks"] += 1
        state = self._force_state(ts_event)
        if state is None:
            self.diagnostics["jump_force_release_metrics_unresolved"] += 1
            self._event(
                "C60_FORCE_RELEASE_METRICS_UNRESOLVED",
                ts_event,
                symbol=self.pending_jump.decision.symbol,
                episode_ts=self.pending_jump.decision.episode_ts,
                mode=mode,
            )
            return False

        accepted = self._state_passes(state)
        self._attach_force_state(state, accepted=accepted)
        if not bool(state["force_oi_unwind"]):
            self.diagnostics["jump_force_release_oi_rejections"] += 1
        if mode == "oi_unwind_flow_flip" and not bool(
            state["force_taker_flow_flip"]
        ):
            self.diagnostics["jump_force_release_terminal_flow_rejections"] += 1
        if not accepted:
            self._event(
                "C60_FORCE_RELEASE_STATE_REJECTED",
                ts_event,
                symbol=self.pending_jump.decision.symbol,
                episode_ts=self.pending_jump.decision.episode_ts,
                mode=mode,
                **state,
            )
            return False

        self.diagnostics["jump_force_release_acceptances"] += 1
        self._event(
            "C60_FORCE_RELEASE_STATE_ACCEPTED",
            ts_event,
            symbol=self.pending_jump.decision.symbol,
            episode_ts=self.pending_jump.decision.episode_ts,
            mode=mode,
            **state,
        )
        return super()._try_pending_confirmation(ts_event)

    def _submit_source_decision(
        self,
        decision: RouteDecision,
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
                    "candidate": "candidate-60-force-release-v1",
                    "force_release_mode": self._force_mode,
                    "force_event_lookback_minutes": int(
                        self.config.jump_force_event_lookback_minutes
                    ),
                }
            )


__all__ = ["Candidate35Config", "Candidate35Strategy"]
