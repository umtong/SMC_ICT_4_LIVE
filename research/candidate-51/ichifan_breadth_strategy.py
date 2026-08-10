"""Static cross-asset breadth context for the pinned Candidate 47 IchiFan entry.

The entry, score arbitration, structural stop, 90-minute cross exit, source
trailing policy, risk sizing and execution are unchanged.  The only experiment
is whether a rising-edge candidate is supported by at least two simultaneously
active source conditions across BTC, ETH, SOL and XRP.  This reuses the static
same-direction breadth context that helped EDTMA, without turning breadth into
a dynamic position exit.
"""
from __future__ import annotations

from dataclasses import replace
from typing import Any

import ichifan_strategy as _exact
import ichifan_structural_strategy as _struct


class Candidate51IchiFanBreadthConfig(
    _struct.Candidate47IchiFanStructuralConfig,
    frozen=True,
):
    ichifan_static_breadth_min: int = 1


class Candidate51IchiFanBreadthStrategy(
    _struct.Candidate47IchiFanStructuralStrategy,
):
    def __init__(self, config: Candidate51IchiFanBreadthConfig) -> None:
        super().__init__(config)
        minimum = int(config.ichifan_static_breadth_min)
        if minimum not in {1, 2}:
            raise ValueError("focused IchiFan breadth experiment supports only 1 or 2")
        self.diagnostics.update(
            {
                "ichifan_static_breadth_min": minimum,
                "ichifan_breadth_evaluations": 0,
                "ichifan_breadth_rejections": 0,
                "ichifan_breadth_passes": 0,
                "ichifan_selected_breadth_counts": {},
                "ichifan_selected_active_peers": {},
            }
        )

    def _active_source_states(self) -> tuple[int, list[str], dict[str, dict[str, Any]]]:
        active: list[str] = []
        snapshots: dict[str, dict[str, Any]] = {}
        for symbol in _exact.SYMBOLS:
            five = _exact.aggregate_five_minute(tuple(self.bars[symbol]))
            states = _exact.fan_states(five)
            if not states or not states[-1].ready:
                snapshots[symbol] = {"ready": False, "active": False}
                continue
            state = states[-1]
            is_active = bool(state.entry)
            if is_active:
                active.append(symbol)
            snapshots[symbol] = {
                "ready": True,
                "active": is_active,
                "fan_magnitude": float(state.fan_magnitude),
                "fan_gain": float(state.fan_gain),
                "trend_close_5m": float(state.trend_close_5m),
                "trend_close_90m": float(state.trend_close_90m),
            }
        return len(active), active, snapshots

    def _submit_decision(self, decision, ts_event: int) -> None:
        self.diagnostics["ichifan_breadth_evaluations"] += 1
        count, active, snapshots = self._active_source_states()
        counts = self.diagnostics["ichifan_selected_breadth_counts"]
        counts[str(count)] = int(counts.get(str(count), 0)) + 1
        for symbol in active:
            peers = self.diagnostics["ichifan_selected_active_peers"]
            peers[symbol] = int(peers.get(symbol, 0)) + 1

        minimum = int(self.config.ichifan_static_breadth_min)
        if count < minimum:
            self.diagnostics["ichifan_breadth_rejections"] += 1
            self._event(
                "ICHIFAN_STATIC_BREADTH_REJECTED",
                ts_event,
                symbol=decision.symbol,
                required=minimum,
                active_count=count,
                active_symbols=active,
                peer_states=snapshots,
            )
            return

        self.diagnostics["ichifan_breadth_passes"] += 1
        diagnostics = dict(decision.diagnostics)
        diagnostics.update(
            {
                "ichifan_static_breadth_min": minimum,
                "ichifan_active_source_count": count,
                "ichifan_active_source_symbols": ",".join(active),
            }
        )
        before = int(self.diagnostics["entry_submissions"])
        super()._submit_decision(
            replace(decision, diagnostics=diagnostics),
            ts_event,
        )
        if int(self.diagnostics["entry_submissions"]) > before and self.current_scenario is not None:
            self.current_scenario.update(
                {
                    "ichifan_static_breadth_min": minimum,
                    "ichifan_active_source_count": count,
                    "ichifan_active_source_symbols": active,
                    "ichifan_peer_state_snapshot": snapshots,
                }
            )


Candidate35Config = Candidate51IchiFanBreadthConfig
Candidate35Strategy = Candidate51IchiFanBreadthStrategy

__all__ = [
    "Candidate35Config",
    "Candidate35Strategy",
    "Candidate51IchiFanBreadthConfig",
    "Candidate51IchiFanBreadthStrategy",
]
