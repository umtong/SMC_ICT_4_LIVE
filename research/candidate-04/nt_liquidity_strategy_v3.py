#!/usr/bin/env python3
"""Candidate-04 v3: failed-rejection acceptance continuation.

A prior-session sweep and causal reversal displacement become a probe, not an
entry. If the reversal reaches opposing liquidity first, the event is complete
without a trade. If price instead closes beyond the sweep extreme with renewed
body, volume and close-location confirmation, the rejected auction has failed
and the swept side is accepted; only then is continuation entered.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from nt_liquidity_strategy import LiquidityTransitionConfig
from nt_liquidity_strategy import LiquidityTransitionStrategy
from nt_liquidity_strategy import PendingSetup
from nt_liquidity_strategy import cost_aware_target
from nt_liquidity_strategy import net_r_at_price


@dataclass(slots=True)
class ReversalProbe:
    reversal_side: int
    created_index: int
    expires_index: int
    sweep_extreme: float
    reversal_target: float
    atr: float
    details: dict[str, Any]


class LiquidityTransitionStrategyV3(LiquidityTransitionStrategy):
    """Trade acceptance only after an observable reversal failure."""

    def __init__(self, config: LiquidityTransitionConfig) -> None:
        super().__init__(config)
        self.reversal_probe: ReversalProbe | None = None

    def _try_confirm_pending(self, row: dict[str, float | int]) -> bool:
        had_pending = self.pending is not None
        handled = super()._try_confirm_pending(row)
        consumed = had_pending and self.pending is None
        return handled or consumed

    def _submit_bracket(
        self,
        setup: PendingSetup,
        row: dict[str, float | int],
        target_net_r: float,
        details: dict[str, Any],
    ) -> bool:
        if setup.scenario != "SESSION_RANGE_FAILED_AUCTION":
            return LiquidityTransitionStrategy._submit_bracket(
                self,
                setup,
                row,
                target_net_r,
                details,
            )

        side = setup.side
        atr = self._atr()
        entry = float(row["close"])
        stop = setup.extreme - side * self.config.stop_buffer_atr * atr
        cost_rate = self.config.all_in_cost_bps_each_side / 10_000.0
        price_loss = side * (entry - stop)
        if not math.isfinite(price_loss) or price_loss <= 0.0:
            return False
        planned_loss = price_loss + cost_rate * (entry + stop)
        reversal_target = cost_aware_target(
            entry,
            side,
            planned_loss,
            target_net_r,
            cost_rate,
        )
        if setup.target_reference is not None:
            reference_r = net_r_at_price(
                entry,
                setup.target_reference,
                side,
                planned_loss,
                cost_rate,
            )
            if reference_r < self.config.session_min_opposite_target_r:
                self._event(
                    "INSUFFICIENT_OPPOSING_LIQUIDITY",
                    setup.scenario,
                    row,
                    {**details, "reference_net_r": reference_r},
                )
                return False
            cap = cost_aware_target(
                entry,
                side,
                planned_loss,
                self.config.session_max_target_r,
                cost_rate,
            )
            reversal_target = (
                min(setup.target_reference, cap)
                if side > 0
                else max(setup.target_reference, cap)
            )

        self.reversal_probe = ReversalProbe(
            reversal_side=side,
            created_index=self.bar_index,
            expires_index=self.bar_index + 90,
            sweep_extreme=setup.extreme,
            reversal_target=reversal_target,
            atr=atr,
            details={
                **details,
                "hypothetical_entry": entry,
                "hypothetical_stop": stop,
                "hypothetical_target": reversal_target,
            },
        )
        self._event(
            "REVERSAL_PROBE_ARMED",
            "SESSION_REJECTION_COMPETING_RISK",
            row,
            self.reversal_probe.details,
        )
        return True

    def _detect_session_sweep(self, row: dict[str, float | int]) -> bool:
        if self.reversal_probe is not None:
            self._process_reversal_probe(row)
            return True
        return super()._detect_session_sweep(row)

    def _process_reversal_probe(self, row: dict[str, float | int]) -> None:
        probe = self.reversal_probe
        if probe is None:
            return
        if self.bar_index > probe.expires_index:
            self._event(
                "REVERSAL_PROBE_EXPIRED",
                "SESSION_REJECTION_COMPETING_RISK",
                row,
                probe.details,
            )
            self.reversal_probe = None
            return

        reversal_target_hit = (
            float(row["high"]) >= probe.reversal_target
            if probe.reversal_side > 0
            else float(row["low"]) <= probe.reversal_target
        )
        if reversal_target_hit:
            self._event(
                "REVERSAL_SUCCEEDED_NO_TRADE",
                "SESSION_REJECTION_COMPETING_RISK",
                row,
                probe.details,
            )
            self.reversal_probe = None
            return

        continuation_side = -probe.reversal_side
        accepted = (
            float(row["close"]) > probe.sweep_extreme
            if continuation_side > 0
            else float(row["close"]) < probe.sweep_extreme
        )
        if not accepted:
            return

        atr = self._atr()
        body = continuation_side * (
            float(row["close"]) - float(row["open"])
        ) / atr
        volume_burst = self._volume_burst()
        close_location = self._close_location(row, continuation_side)
        if not (
            body >= self.config.trend_body_atr
            and volume_burst >= self.config.trend_volume_burst
            and close_location >= self.config.trend_close_location
        ):
            return

        rows = list(self.bars)
        start = max(0, probe.created_index - (self.bar_index - len(rows) + 1))
        reaction = rows[start:]
        if continuation_side > 0:
            reaction_extreme = min(float(item["low"]) for item in reaction)
        else:
            reaction_extreme = max(float(item["high"]) for item in reaction)

        setup = PendingSetup(
            scenario="SESSION_REJECTION_FAILURE_CONTINUATION",
            side=continuation_side,
            created_index=probe.created_index,
            expires_index=self.bar_index,
            extreme=reaction_extreme,
            structure=probe.sweep_extreme,
            atr=atr,
            target_reference=None,
            details=probe.details,
        )
        details = {
            **probe.details,
            "acceptance_body_atr": body,
            "acceptance_volume_burst": volume_burst,
            "acceptance_close_location": close_location,
            "accepted_sweep_extreme": probe.sweep_extreme,
        }
        self.reversal_probe = None
        submitted = LiquidityTransitionStrategy._submit_bracket(
            self,
            setup,
            row,
            self.config.trend_target_net_r,
            details,
        )
        if not submitted:
            self._event(
                "ACCEPTANCE_EXECUTION_REJECTED",
                setup.scenario,
                row,
                details,
            )


__all__ = ["LiquidityTransitionConfig", "LiquidityTransitionStrategyV3"]
