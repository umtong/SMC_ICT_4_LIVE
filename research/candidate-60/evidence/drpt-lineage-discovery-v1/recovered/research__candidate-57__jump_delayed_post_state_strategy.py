"""Delayed post-cascade state adapter for the source jump specialist.

The inherited strategy already owns the causal pending-jump state, completed
five-minute price confirmation, structural extension stop, original event clock,
transient protection, one-slot execution and current-NAV risk sizing.  This
adapter adds only two pre-entry requirements selected before the fresh interval:

* delayed cells cannot confirm before two completed five-minute bars;
* the OI-stable cell requires target-contract Binance open interest at the
  confirmation boundary to be no more than 1% below its source-boundary value.
"""
from __future__ import annotations

import math

import strategy_jump_transient_base as _base
from router import _asof


class Candidate35Config(_base.Candidate35Config, frozen=True):
    jump_post_state_mode: str = "immediate"
    jump_min_confirmation_elapsed_minutes: int = 0
    jump_oi_max_decline_fraction: float = 0.01


class Candidate35Strategy(_base.Candidate35Strategy):
    def __init__(self, config: Candidate35Config) -> None:
        mode = str(config.jump_post_state_mode).strip().lower()
        if mode not in {
            "immediate",
            "two_bar_price",
            "two_bar_price_oi_stable",
        }:
            raise ValueError(f"unsupported jump_post_state_mode={mode!r}")
        minimum_elapsed = int(config.jump_min_confirmation_elapsed_minutes)
        if minimum_elapsed < 0:
            raise ValueError("jump_min_confirmation_elapsed_minutes must be >= 0")
        tolerance = float(config.jump_oi_max_decline_fraction)
        if not math.isfinite(tolerance) or tolerance < 0.0:
            raise ValueError("jump_oi_max_decline_fraction must be finite and >= 0")
        super().__init__(config)
        self.diagnostics.update(
            {
                "jump_post_state_mode": mode,
                "jump_min_confirmation_elapsed_minutes": minimum_elapsed,
                "jump_oi_max_decline_fraction": tolerance,
                "jump_confirmation_waiting_for_two_bars": 0,
                "jump_confirmation_oi_unresolved": 0,
                "jump_confirmation_oi_rejected": 0,
                "jump_confirmation_oi_accepted": 0,
                "jump_post_state_source_detector_changed": 0,
                "jump_post_state_management_changed": 0,
                "jump_post_state_known_before_entry": 1,
            }
        )

    def _oi_state(self, ts_event: int) -> dict[str, float | int | bool] | None:
        pending = self.pending_jump
        if pending is None:
            return None
        symbol = pending.decision.symbol
        source_ts = int(pending.decision.episode_ts)
        source = _asof(symbol, source_ts)
        current = _asof(symbol, int(ts_event))
        if source is None or current is None:
            return None
        source_oi = float(source.get("sum_open_interest", math.nan))
        current_oi = float(current.get("sum_open_interest", math.nan))
        if (
            not math.isfinite(source_oi)
            or not math.isfinite(current_oi)
            or source_oi <= 0.0
        ):
            return None
        change = current_oi / source_oi - 1.0
        tolerance = float(self.config.jump_oi_max_decline_fraction)
        return {
            "source_metrics_ts": int(source["ts_event"]),
            "current_metrics_ts": int(current["ts_event"]),
            "source_metrics_age_minutes": float(source["age_minutes"]),
            "current_metrics_age_minutes": float(current["age_minutes"]),
            "source_sum_open_interest": source_oi,
            "current_sum_open_interest": current_oi,
            "open_interest_change_fraction": change,
            "open_interest_stable": bool(change >= -tolerance),
        }

    def _try_pending_confirmation(self, ts_event: int) -> bool:
        pending = self.pending_jump
        if pending is None:
            return False
        mode = str(self.config.jump_post_state_mode).strip().lower()
        if mode == "immediate":
            return super()._try_pending_confirmation(ts_event)

        elapsed = self.minute_index - pending.source_minute_index
        bucket = max(1, int(self.route_config.jump_confirmation_bucket_minutes))
        if elapsed < bucket or elapsed % bucket != 0:
            return False
        minimum_elapsed = int(self.config.jump_min_confirmation_elapsed_minutes)
        if elapsed < minimum_elapsed:
            self.diagnostics["jump_confirmation_waiting_for_two_bars"] += 1
            return False

        if mode == "two_bar_price_oi_stable":
            state = self._oi_state(ts_event)
            if state is None:
                self.diagnostics["jump_confirmation_oi_unresolved"] += 1
                self._event(
                    "JUMP_CONFIRMATION_OI_UNRESOLVED",
                    ts_event,
                    symbol=pending.decision.symbol,
                    episode_ts=pending.decision.episode_ts,
                    elapsed_minutes=elapsed,
                )
                return False
            if not bool(state["open_interest_stable"]):
                self.diagnostics["jump_confirmation_oi_rejected"] += 1
                self._event(
                    "JUMP_CONFIRMATION_OI_NOT_STABLE",
                    ts_event,
                    symbol=pending.decision.symbol,
                    episode_ts=pending.decision.episode_ts,
                    elapsed_minutes=elapsed,
                    **state,
                )
                return False
            self.diagnostics["jump_confirmation_oi_accepted"] += 1
            if pending.decision.diagnostics is not None:
                pending.decision.diagnostics.update(
                    {
                        "confirmation_oi_source_ts": state[
                            "source_metrics_ts"
                        ],
                        "confirmation_oi_current_ts": state[
                            "current_metrics_ts"
                        ],
                        "confirmation_oi_source": state[
                            "source_sum_open_interest"
                        ],
                        "confirmation_oi_current": state[
                            "current_sum_open_interest"
                        ],
                        "confirmation_oi_change_fraction": state[
                            "open_interest_change_fraction"
                        ],
                        "confirmation_oi_stable": 1,
                    }
                )

        return super()._try_pending_confirmation(ts_event)


__all__ = ["Candidate35Config", "Candidate35Strategy"]
