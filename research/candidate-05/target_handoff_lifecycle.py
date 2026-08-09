"""Execution-lifecycle bridge for Candidate 05 target-liquidity handoff."""
from __future__ import annotations

import math
from typing import Any

from logic import Pool
from strategy_base import _as_float
from strategy_v9 import ArmedEntryPath
from target_handoff_logic import target_exit_matches
from target_handoff_models import CurrentLiquidityTarget
from target_handoff_models import PendingTargetExit
from target_handoff_models import TargetSweepWatch


class TargetHandoffLifecycleMixin:
    """Capture target metadata and align execution events with completed bars."""

    def _submit_price_capped_bracket(
        self,
        *,
        armed: ArmedEntryPath,
        row: dict[str, float | int],
        entry_price: Any,
        stop_price: Any,
        target_price: Any,
        sizing_entry: float,
        planned_loss: float,
        target_source: str,
        target_r: float,
        branch: str,
        event_type: str,
        reason: str,
        expires_index: int,
        entry_tag: str,
        extra: dict[str, Any] | None = None,
    ) -> bool:
        target = _as_float(target_price)
        pool = self._target_pool_snapshot(
            target_source=target_source,
            target=target,
            side=armed.setup.side,
            observed_ns=int(row["ts"]),
        )
        submitted = super()._submit_price_capped_bracket(
            armed=armed,
            row=row,
            entry_price=entry_price,
            stop_price=stop_price,
            target_price=target_price,
            sizing_entry=sizing_entry,
            planned_loss=planned_loss,
            target_source=target_source,
            target_r=target_r,
            branch=branch,
            event_type=event_type,
            reason=reason,
            expires_index=expires_index,
            entry_tag=entry_tag,
            extra=extra,
        )
        if submitted:
            self.current_liquidity_target = CurrentLiquidityTarget(
                pool=pool,
                target=target,
                target_source=target_source,
                entry_side=armed.setup.side,
                source_scenario_id=armed.setup.scenario_id,
            )
        return submitted

    def on_position_closed(self, event: Any) -> None:
        pending_exit = self._qualify_target_exit(
            event=event,
            target=self.current_liquidity_target,
        )
        super().on_position_closed(event)
        if pending_exit is None:
            return

        # Resting brackets are usually matched before on_bar. Defer the event
        # until that same completed bar supplies causal OHLC/flow/depth.
        if self.bars and int(self.bars[-1]["ts"]) == pending_exit.event_ts:
            row = self.bars[-1].copy()
            self._arm_target_watch(pending_exit, row)
            self._observe_target_watch(row)
            self._promote_target_watch(row)
        else:
            self.pending_target_exit = pending_exit
            self.diagnostics["target_handoff_pending_exits"] += 1

    def _qualify_target_exit(
        self,
        *,
        event: Any,
        target: CurrentLiquidityTarget | None,
    ) -> PendingTargetExit | None:
        if target is None or not target.target_source.startswith("POOL:"):
            return None
        average_exit = _as_float(getattr(event, "avg_px_close", None))
        realized_pnl = _as_float(getattr(event, "realized_pnl", None))
        if not target_exit_matches(
            average_exit=average_exit,
            target=target.target,
            price_increment=_as_float(self.instrument.price_increment),
            realized_pnl=realized_pnl,
        ):
            self.diagnostics["target_handoff_non_target_closes"] += 1
            return None
        return PendingTargetExit(
            target=target,
            event_ts=int(getattr(event, "ts_event", 0)),
            average_exit=average_exit,
            realized_pnl=realized_pnl,
        )

    def _materialize_pending_target_exit(self, row: dict[str, float | int]) -> None:
        pending_exit = self.pending_target_exit
        if pending_exit is None or int(row["ts"]) < pending_exit.event_ts:
            return
        self.pending_target_exit = None
        self._arm_target_watch(pending_exit, row)

    def _arm_target_watch(
        self,
        pending_exit: PendingTargetExit,
        row: dict[str, float | int],
    ) -> None:
        target = pending_exit.target
        touched = (
            float(row["high"]) >= target.target
            if target.entry_side > 0
            else float(row["low"]) <= target.target
        )
        if not touched:
            self.diagnostics["target_handoff_non_target_closes"] += 1
            return
        watch = self._build_target_watch(target=target, row=row)
        if watch is None:
            return
        active = self.active_pools.get(watch.pool.pool_id)
        if active is not None:
            self._consume_pool(active, row, "PROFIT_TARGET_FILLED_AT_LIVE_LIQUIDITY")
        self.target_sweep_watch = watch
        self.diagnostics["target_handoff_watches"] += 1
        self._transition(
            watch.scenario_id,
            "TARGET_SWEEP_WATCH_ARMED",
            int(row["ts"]),
            int(row["ts"]),
            "TARGET_REACTION_OBSERVATION",
            "PREVIOUS_POSITION_CLOSED_AT_CONSUMED_LIVE_LIQUIDITY",
            watch.pool.level,
            {
                **self._watch_details(watch),
                "average_target_exit": pending_exit.average_exit,
                "realized_pnl": pending_exit.realized_pnl,
            },
        )

    def _target_pool_snapshot(
        self,
        *,
        target_source: str,
        target: float,
        side: int,
        observed_ns: int,
    ) -> Pool:
        pool_id = ""
        if target_source.startswith("POOL:"):
            pool_id = target_source.split(":", 1)[1]
            active = self.active_pools.get(pool_id)
            if active is not None:
                return active
        return Pool(
            pool_id=pool_id or f"derived-target-{self.scenario_counter:07d}",
            kind="HIGH" if side > 0 else "LOW",
            level=target,
            event_time_ns=observed_ns,
            observed_time_ns=observed_ns,
            source="TARGET_SOURCE_SNAPSHOT",
            strength=1,
            created_index=self.bar_index,
        )

    def _build_target_watch(
        self,
        *,
        target: CurrentLiquidityTarget,
        row: dict[str, float | int],
    ) -> TargetSweepWatch | None:
        atr = self._atr()
        if not math.isfinite(atr) or atr <= 0.0:
            return None
        self.scenario_counter += 1
        return TargetSweepWatch(
            scenario_id=f"lrr-{self.scenario_counter:07d}",
            source_scenario_id=target.source_scenario_id,
            pool=target.pool,
            previous_entry_side=target.entry_side,
            started_index=self.bar_index,
            started_ts=int(row["ts"]),
            expires_index=self.bar_index + self.config.rejection_confirmation_bars,
            atr=atr,
            open=float(row["open"]),
            high=float(row["high"]),
            low=float(row["low"]),
            sweep_sponsored=False,
            sponsor_directional_flow=-math.inf,
            sponsor_notional_burst=float("nan"),
            sponsor_efficiency=float("nan"),
            rows_observed=0,
        )

    def _clear_trade_state(self) -> None:
        super()._clear_trade_state()
        self.current_liquidity_target = None

    def on_stop(self) -> None:
        if self.target_sweep_watch is not None and self.bars:
            self._expire_target_watch(
                self.bars[-1],
                "BACKTEST_ENDED_WITH_TARGET_REACTION_OPEN",
            )
        super().on_stop()


__all__ = ["TargetHandoffLifecycleMixin"]
