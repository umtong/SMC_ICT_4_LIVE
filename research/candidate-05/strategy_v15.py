#!/usr/bin/env python3
"""Candidate 05 v15: retain sponsored CHoCH orders through the causal horizon."""
from __future__ import annotations

from typing import Any

from strategy_base import LiquidityResponseConfig
from strategy_v9 import ArmedEntryPath
from strategy_v14 import SponsoredChochParticipationStrategy


class SponsoredChochFallbackStrategy(SponsoredChochParticipationStrategy):
    """Keep a cost-valid sponsored CHoCH order as the first retrace order.

    v14 submits one bounded marketable limit at an actively sponsored CHoCH.
    If the first two completed bars do not trade that cap, canceling it discards
    the unchanged rejection thesis even though the original CHoCH/retrace state
    remains valid for ``rejection_confirmation_bars``. This class changes only
    that control flow: the same order, price cap, stop, target and 3% NAV-sized
    quantity continue resting through the already-configured causal horizon.

    No second order is created and no signal, threshold, price, target, risk,
    cost, slippage or execution assumption is changed.
    """

    def __init__(self, config: LiquidityResponseConfig) -> None:
        super().__init__(config)
        self.diagnostics["sponsored_choch_fallback_horizon_extensions"] = 0

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
        if branch == "TAIL_FLOW_SPONSORED_CHOCH":
            causal_expiry = armed.created_index + self.config.rejection_confirmation_bars
            if causal_expiry > expires_index:
                expires_index = causal_expiry
                self.diagnostics["sponsored_choch_fallback_horizon_extensions"] += 1
                extra = {
                    **(extra or {}),
                    "participation_fallback": "SAME_LIMIT_REMAINS_FIRST_RETRACE_ORDER",
                    "participation_causal_expiry_index": causal_expiry,
                }
        return super()._submit_price_capped_bracket(
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


__all__ = ["SponsoredChochFallbackStrategy"]
