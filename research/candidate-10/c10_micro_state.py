"""Nearest causal micro-structure break for candidate 10 v2.2."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from c10_model import BarView
from c10_model import Transition
from c10_path_state import AuctionStateMachine as PathStateMachine


class AuctionStateMachine(PathStateMachine):
    """Structural pools with a right-confirmed 1-minute approach pivot.

    The source/target pools remain 15-minute structural objects. Only the
    displacement structure changes: a sweep must break the nearest already
    confirmed 1-minute pivot in the reversal direction. The current sweep bar
    is never included in pivot discovery.
    """

    def _nearest_confirmed_micro_pivot(
        self,
        direction: int,
    ) -> tuple[float | None, dict[str, Any]]:
        bars = list(self.history)
        left_count = self.params.micro_pivot_left
        right_count = self.params.micro_pivot_right
        window_size = max(
            self.params.approach_lookback,
            left_count + right_count + 1,
        )
        window = bars[-window_size:]
        if len(window) < left_count + right_count + 1:
            return None, {
                "approach_structure_type": "RANGE_EXTREME_FALLBACK_INSUFFICIENT_HISTORY",
            }

        latest_candidate = len(window) - right_count - 1
        for index in range(latest_candidate, left_count - 1, -1):
            candidate = window[index]
            left = window[index - left_count : index]
            right = window[index + 1 : index + 1 + right_count]
            if direction < 0:
                value = candidate.low
                confirmed = all(value < item.low for item in (*left, *right))
                pivot_side = "LOW"
            else:
                value = candidate.high
                confirmed = all(value > item.high for item in (*left, *right))
                pivot_side = "HIGH"
            if not confirmed:
                continue

            confirming_bar = right[-1]
            return value, {
                "approach_structure_type": f"RIGHT_CONFIRMED_MICRO_PIVOT_{pivot_side}",
                "micro_pivot_side": pivot_side,
                "micro_pivot_event_time_ns": candidate.ts_ns,
                "micro_pivot_observed_time_ns": confirming_bar.ts_ns,
                "micro_pivot_bars_before_sweep": len(window) - 1 - index,
                "micro_pivot_left": left_count,
                "micro_pivot_right": right_count,
                "micro_pivot_search_window": len(window),
            }

        return None, {
            "approach_structure_type": "RANGE_EXTREME_FALLBACK_NO_CONFIRMED_MICRO_PIVOT",
            "micro_pivot_left": left_count,
            "micro_pivot_right": right_count,
            "micro_pivot_search_window": len(window),
        }

    def _detect_sweep(
        self,
        bar: BarView,
        atr: float,
    ) -> list[Transition]:
        events = super()._detect_sweep(bar, atr)
        setup = self.active
        if setup is None:
            return events

        range_extreme = setup.approach_level
        metadata: dict[str, Any]
        if self.params.enable_nearest_micro_pivot:
            micro_level, metadata = self._nearest_confirmed_micro_pivot(
                setup.direction,
            )
            if micro_level is not None:
                setup.approach_level = micro_level
        else:
            metadata = {
                "approach_structure_type": "RANGE_EXTREME_ABLATION",
                "micro_pivot_enabled": False,
            }

        metadata = {
            **metadata,
            "micro_pivot_enabled": self.params.enable_nearest_micro_pivot,
            "range_extreme_approach_level": range_extreme,
            "selected_approach_level": setup.approach_level,
        }
        rewritten: list[Transition] = []
        for event in events:
            if (
                event.event_type == "LIQUIDITY_EVENT"
                and event.scenario_id == setup.scenario_id
            ):
                rewritten.append(
                    replace(
                        event,
                        details={**event.details, **metadata},
                    ),
                )
            else:
                rewritten.append(event)
        return rewritten
