#!/usr/bin/env python3
"""Candidate 05 v13: causal handoff after a live target pool is consumed."""
from __future__ import annotations

from nautilus_trader.model.data import Bar

from strategy_base import LiquidityResponseConfig
from strategy_base import _as_float
from strategy_v12 import SoftwareLiquidityProtectionStrategy
from target_handoff_lifecycle import TargetHandoffLifecycleMixin
from target_handoff_models import CurrentLiquidityTarget
from target_handoff_models import PendingTargetExit
from target_handoff_models import TargetSweepWatch
from target_handoff_reaction import TargetHandoffReactionMixin


class TargetLiquidityHandoffStrategy(
    TargetHandoffLifecycleMixin,
    TargetHandoffReactionMixin,
    SoftwareLiquidityProtectionStrategy,
):
    """Recover the target reaction hidden by the global one-position rule.

    No threshold is loosened. A profitable close at a live pool starts at most
    the existing rejection horizon of observation. Only a sponsored raid,
    delayed reclaim, sweep-tail turn and reversal-supporting depth can enter the
    unchanged CHoCH, path, cost, target, stop and 3% NAV execution chain.

    A target watch is an observation, not an entry intent. It therefore runs in
    parallel with the ordinary detector and may be promoted only while the
    standard setup/entry path is otherwise idle. This preserves every v12 path
    while recovering the event which was hidden during the prior position.
    """

    def __init__(self, config: LiquidityResponseConfig) -> None:
        super().__init__(config)
        self.current_liquidity_target: CurrentLiquidityTarget | None = None
        self.pending_target_exit: PendingTargetExit | None = None
        self.target_sweep_watch: TargetSweepWatch | None = None
        self.diagnostics.update(
            {
                "target_handoff_pending_exits": 0,
                "target_handoff_watches": 0,
                "target_handoff_sponsored_bars": 0,
                "target_handoff_tail_waits": 0,
                "target_handoff_depth_waits": 0,
                "target_handoff_setups": 0,
                "target_handoff_expired": 0,
                "target_handoff_non_target_closes": 0,
                "target_handoff_parallel_observation_bars": 0,
            },
        )

    def on_bar(self, bar: Bar) -> None:
        self.bar_index += 1
        row = {
            "ts": int(bar.ts_event),
            "open": _as_float(bar.open),
            "high": _as_float(bar.high),
            "low": _as_float(bar.low),
            "close": _as_float(bar.close),
            "volume": _as_float(bar.volume),
        }
        previous_close = float(self.bars[-1]["close"]) if self.bars else float(row["open"])
        self.bars.append(row)
        self._advance_features(int(row["ts"]))
        self._record_equity(int(row["ts"]))
        self._update_five_minute(row)
        self._prune_pools(row)
        self._materialize_pending_target_exit(row)

        if not self.portfolio.is_flat(self.config.instrument_id):
            self.diagnostics["max_open_positions_observed"] = 1
            self._manage_open_position(row)
            return
        if self.entry_pending:
            self._manage_pending_entry(row)
            return
        if not self._in_evaluation(int(row["ts"])):
            self.pending = None
            self._expire_armed_entry(row, "EVALUATION_ENDED_BEFORE_ENTRY_PATH_RESOLUTION")
            self._expire_target_watch(row, "EVALUATION_ENDED_BEFORE_TARGET_REACTION_RESOLUTION")
            return
        if self._funding_blackout(int(row["ts"])):
            if self.armed_entry_path is not None:
                self._expire_armed_entry(row, "FUNDING_BLACKOUT_BEFORE_ENTRY_PATH_RESOLUTION")
            elif self.target_sweep_watch is not None:
                self._expire_target_watch(row, "FUNDING_BLACKOUT_BEFORE_TARGET_REACTION_RESOLUTION")
            else:
                self._expire_pending(row, "FUNDING_BLACKOUT")
            return
        if not self._features_ready(int(row["ts"])) or len(self.bars) < self.config.atr_period + 2:
            return

        # A target reaction watch is observational only. It must not suppress
        # ordinary v12 sweep detection, pending CHoCH processing, or an already
        # armed entry path. Promotion is allowed only when that standard state
        # slot is free, preserving the global one-entry-intent constraint.
        if self.target_sweep_watch is not None:
            watch = self.target_sweep_watch
            if self.bar_index > watch.expires_index:
                self._expire_target_watch(row, "TARGET_REACTION_WINDOW_EXPIRED")
            else:
                self._observe_target_watch(row)
                if self.pending is not None or self.armed_entry_path is not None:
                    self.diagnostics["target_handoff_parallel_observation_bars"] += 1
                elif self._promote_target_watch(row):
                    return

        if self.armed_entry_path is not None:
            self._resolve_entry_path(row)
            return
        if self.pending is not None and self._process_pending(row):
            return
        if self.pending is None and self.bar_index - self.last_entry_index >= self.config.cooldown_bars:
            self._detect_sweep(row, previous_close)


__all__ = ["TargetLiquidityHandoffStrategy"]
