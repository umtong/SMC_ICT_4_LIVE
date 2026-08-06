#!/usr/bin/env python3
"""Candidate 05 v22: revalidate frozen protection from the actual entry fill."""
from __future__ import annotations

import math
import re
from typing import Any

from scenario_target_logic import revalidate_frozen_milestone
from strategy_base import LiquidityResponseConfig
from strategy_base import _as_float
from strategy_v9 import ArmedEntryPath
from strategy_v21 import FrozenScenarioTargetStrategy


class ActualFillMilestoneStrategy(FrozenScenarioTargetStrategy):
    """Keep conservative sizing but activate protection from actual fill price.

    v21 sizes and validates the bracket with the adverse-slippage-capped entry,
    as it should. It also used that same worst acceptable price to decide whether
    the frozen intermediate liquidity could lock positive PnL after costs. A
    marketable limit can fill materially better than its cap. In the first BTC
    week, one candidate was therefore discarded before submission even though
    the actual NautilusTrader fill made the exact frozen milestone protectable.

    v22 changes only this lifecycle:

    * planned loss, quantity, target validity and the exchange-side structural
      stop continue to use the conservative capped entry;
    * a frozen milestone which is not protectable at that cap is retained as a
      pending scenario state rather than deleted;
    * after NautilusTrader reports the real average entry, the same milestone is
      revalidated with the same fees, adverse exit slippage and structural buffer;
    * no later pool is substituted, and no protection is armed if the original
      pool disappeared before the fill or the actual fill still cannot lock
      positive net PnL.

    Thus risk cannot increase, while a valid state transition is no longer lost
    merely because execution was better than the conservative sizing assumption.
    """

    _FLOAT_PATTERN = re.compile(r"avg_px_open=([-+0-9.eE]+)")

    def __init__(self, config: LiquidityResponseConfig) -> None:
        super().__init__(config)
        self.pending_fill_milestone_pool_id: str | None = None
        self.pending_fill_milestone_level = float("nan")
        self.pending_fill_milestone_target = float("nan")
        self.pending_fill_milestone_atr = float("nan")
        self.pending_fill_milestone_side = 0
        self.pending_fill_milestone_scenario_id: str | None = None
        self.pending_fill_milestone_submission_cap = float("nan")
        self.diagnostics.update(
            {
                "post_fill_milestone_candidates": 0,
                "post_fill_milestone_prefill_preserved": 0,
                "post_fill_milestone_activations": 0,
                "post_fill_milestone_refreshes": 0,
                "post_fill_milestone_deactivations": 0,
                "post_fill_milestone_still_unprotectable": 0,
                "post_fill_milestone_source_unavailable": 0,
                "post_fill_milestone_price_unavailable": 0,
            },
        )

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
        candidate = self._frozen_milestone_candidate(
            armed=armed,
            target=_as_float(target_price),
            submission_cap=_as_float(entry_price),
            branch=branch,
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
        if not submitted:
            self._clear_pending_fill_milestone()
            return False
        if candidate is None:
            self._clear_pending_fill_milestone()
            return True

        (
            pool_id,
            level,
            target,
            atr,
            side,
            scenario_id,
            submission_cap,
        ) = candidate
        self.pending_fill_milestone_pool_id = pool_id
        self.pending_fill_milestone_level = level
        self.pending_fill_milestone_target = target
        self.pending_fill_milestone_atr = atr
        self.pending_fill_milestone_side = side
        self.pending_fill_milestone_scenario_id = scenario_id
        self.pending_fill_milestone_submission_cap = submission_cap
        self.diagnostics["post_fill_milestone_candidates"] += 1
        if self.protection_pool_id == pool_id and math.isfinite(self.protection_stop):
            self.diagnostics["post_fill_milestone_prefill_preserved"] += 1
        return True

    def on_position_opened(self, event: Any) -> None:
        super().on_position_opened(event)
        if self.entry_cancel_requested:
            return
        self._refresh_protection_from_actual_fill(event, event_type="POSITION_OPENED")

    def on_position_changed(self, event: Any) -> None:
        super().on_position_changed(event)
        if self.entry_cancel_requested:
            return
        self._refresh_protection_from_actual_fill(event, event_type="POSITION_CHANGED")

    def _refresh_protection_from_actual_fill(
        self,
        event: Any,
        *,
        event_type: str,
    ) -> None:
        pool_id = self.pending_fill_milestone_pool_id
        scenario_id = self.pending_fill_milestone_scenario_id
        if pool_id is None or scenario_id is None or self.protection_armed:
            return
        if (
            self.protection_pool_id != pool_id
            and pool_id not in self.active_pools
        ):
            self.diagnostics["post_fill_milestone_source_unavailable"] += 1
            self._clear_pending_fill_milestone()
            return

        actual_entry = self._actual_average_entry(event)
        if not math.isfinite(actual_entry) or actual_entry <= 0.0:
            self.diagnostics["post_fill_milestone_price_unavailable"] += 1
            return

        side = self.pending_fill_milestone_side
        cost_rate = self.config.all_in_cost_bps_each_side / 10_000.0
        slippage_rate = self.config.adverse_slippage_bps_each_side / 10_000.0
        result = revalidate_frozen_milestone(
            side=side,
            entry=actual_entry,
            target=self.pending_fill_milestone_target,
            milestone=self.pending_fill_milestone_level,
            atr=self.pending_fill_milestone_atr,
            stop_buffer_atr=self.config.stop_buffer_atr,
            cost_rate=cost_rate,
            adverse_slippage_rate=slippage_rate,
        )
        if result is None:
            if self.protection_pool_id == pool_id and not self.protection_armed:
                self._clear_protection_state()
                self.diagnostics["post_fill_milestone_deactivations"] += 1
            self.diagnostics["post_fill_milestone_still_unprotectable"] += 1
            return

        raw_stop, _raw_expected_net = result
        protected_stop = _as_float(self.instrument.make_price(raw_stop))
        expected_exit = protected_stop * (1.0 - side * slippage_rate)
        expected_net = side * (expected_exit - actual_entry) - cost_rate * (
            actual_entry + expected_exit
        )
        if expected_net <= 0.0:
            self.diagnostics["post_fill_milestone_still_unprotectable"] += 1
            return

        first_activation = self.protection_pool_id != pool_id
        self.protection_pool_id = pool_id
        self.protection_milestone = self.pending_fill_milestone_level
        self.protection_stop = protected_stop
        self.protection_expected_net = expected_net
        self.protection_armed = False
        self.protection_armed_index = -1
        if first_activation:
            self.diagnostics["protectable_milestones"] += 1
            self.diagnostics["frozen_milestones_revalidated"] += 1
            self.diagnostics["post_fill_milestone_activations"] += 1
        else:
            self.diagnostics["post_fill_milestone_refreshes"] += 1

        ts = int(getattr(event, "ts_event", self.bars[-1]["ts"]))
        if (
            first_activation
            and self.current_scenario_id == scenario_id
            and self.scenario_states.get(scenario_id) != "CLOSED"
        ):
            self._transition(
                scenario_id,
                "FROZEN_MILESTONE_REVALIDATED_FROM_ACTUAL_FILL",
                ts,
                ts,
                "POSITION_OPEN",
                "ACTUAL_FILL_MADE_CHOCH_TIME_LIQUIDITY_PROTECTABLE",
                protected_stop,
                {
                    "position_event_type": event_type,
                    "actual_average_entry": actual_entry,
                    "conservative_submission_cap": self.pending_fill_milestone_submission_cap,
                    "milestone_pool_id": pool_id,
                    "milestone": self.pending_fill_milestone_level,
                    "protected_stop": protected_stop,
                    "expected_net_per_unit_after_cost_and_slippage": expected_net,
                    "target": self.pending_fill_milestone_target,
                },
            )

    def _frozen_milestone_candidate(
        self,
        *,
        armed: ArmedEntryPath,
        target: float,
        submission_cap: float,
        branch: str,
    ) -> tuple[str, float, float, float, int, str, float] | None:
        if branch not in self._FROZEN_BRANCHES:
            return None
        pool_id = armed.details.get("frozen_milestone_pool_id")
        level = armed.details.get("frozen_milestone_level")
        if pool_id is None or level is None:
            return None
        values = (float(level), target, armed.atr, submission_cap)
        if not all(math.isfinite(value) for value in values):
            return None
        return (
            str(pool_id),
            float(level),
            target,
            armed.atr,
            armed.setup.side,
            armed.setup.scenario_id,
            submission_cap,
        )

    def _actual_average_entry(self, event: Any) -> float:
        for attr in ("avg_px_open", "avg_price_open", "average_open_price"):
            value = getattr(event, attr, None)
            try:
                number = _as_float(value)
            except (TypeError, ValueError):
                continue
            if math.isfinite(number) and number > 0.0:
                return number

        position_id = getattr(event, "position_id", None)
        if position_id is not None:
            try:
                position = self.cache.position(position_id)
            except Exception:
                position = None
            if position is not None:
                try:
                    number = _as_float(getattr(position, "avg_px_open", None))
                except (TypeError, ValueError):
                    number = float("nan")
                if math.isfinite(number) and number > 0.0:
                    return number

        match = self._FLOAT_PATTERN.search(str(event))
        if match is not None:
            try:
                number = float(match.group(1))
            except ValueError:
                number = float("nan")
            if math.isfinite(number) and number > 0.0:
                return number
        return float("nan")

    def _clear_pending_fill_milestone(self) -> None:
        self.pending_fill_milestone_pool_id = None
        self.pending_fill_milestone_level = float("nan")
        self.pending_fill_milestone_target = float("nan")
        self.pending_fill_milestone_atr = float("nan")
        self.pending_fill_milestone_side = 0
        self.pending_fill_milestone_scenario_id = None
        self.pending_fill_milestone_submission_cap = float("nan")

    def _clear_trade_state(self) -> None:
        super()._clear_trade_state()
        self._clear_pending_fill_milestone()


__all__ = ["ActualFillMilestoneStrategy"]
