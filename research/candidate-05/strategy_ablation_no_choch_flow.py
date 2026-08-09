#!/usr/bin/env python3
"""Diagnostic ablation: remove only the CHoCH flow-state approval."""
from __future__ import annotations

from typing import Any

from retrace_logic import structural_stop
from strategy_base import LiquidityResponseConfig
from strategy_base import PendingSetup
from strategy_base import _as_float
from strategy_v9 import ArmedEntryPath
from strategy_v12 import SoftwareLiquidityProtectionStrategy


class NoChochFlowAblationStrategy(SoftwareLiquidityProtectionStrategy):
    """Arm every otherwise valid CHoCH, leaving all other rules unchanged.

    This is not a production candidate. It isolates whether the CHoCH
    aggressor-flow state filter is suppressing independent opportunities or
    correctly excluding structurally weak reversals. Data, sweep detection,
    depth confirmation, stops, targets, execution, costs and risk sizing are
    identical to v12.
    """

    def __init__(self, config: LiquidityResponseConfig) -> None:
        super().__init__(config)
        self.diagnostics["ablation_no_choch_flow_armed"] = 0

    def _submit_entry(self, setup: PendingSetup, row: dict[str, float | int]) -> bool:
        side = setup.side
        flow_state = "ABLATION_NO_CHOCH_FLOW_FILTER"
        atr = self._atr()
        stop = _as_float(
            self.instrument.make_price(
                structural_stop(setup.sweep_extreme, side, atr, self.config.stop_buffer_atr),
            ),
        )
        choch_close = _as_float(self.instrument.make_price(float(row["close"])))
        if (side > 0 and not stop < choch_close) or (side < 0 and not choch_close < stop):
            self._expire_pending(row, "INVALID_ARMED_ENTRY_STOP_GEOMETRY")
            return False

        details: dict[str, Any] = {
            **setup.details,
            "flow_state": flow_state,
            "choch_flow_15s": self._feature("flow_15s"),
            "choch_flow_60s": self._feature("flow_60s"),
            "choch_flow_3m": self._feature("flow_3m"),
            "choch_depth_imbalance_1": self._feature("depth_imbalance_1"),
            "side": side,
            "sweep_extreme": setup.sweep_extreme,
            "confirmation_close": choch_close,
            "stop": stop,
            "ablation": "REMOVE_CHOCH_FLOW_STATE_APPROVAL_ONLY",
        }
        self.armed_entry_path = ArmedEntryPath(
            setup=setup,
            flow_state=flow_state,
            choch_close=choch_close,
            stop=stop,
            atr=atr,
            created_index=self.bar_index,
            created_ts=int(row["ts"]),
            details=details,
        )
        self.pending = None
        self.diagnostics["entry_path_armed"] += 1
        self.diagnostics["ablation_no_choch_flow_armed"] += 1
        self._transition(
            setup.scenario_id,
            "ENTRY_PATH_ARMED_ABLATION_NO_CHOCH_FLOW",
            int(row["ts"]),
            int(row["ts"]),
            "ONE_MINUTE_PATH_OBSERVATION",
            "ABLATION_WAIT_FOR_RETRACE_OR_BREAKAWAY_WITHOUT_CHOCH_FLOW_FILTER",
            choch_close,
            details,
        )
        return True


__all__ = ["NoChochFlowAblationStrategy"]
