"""Single-use mature balance with an unspent opposite objective.

A defense level may belong to only one canonical box episode.  The first
resolved sweep or accepted break retires both sides, preventing the same old
support from being recombined with a succession of nested resistances.  A sweep
bar must also leave the opposite defense untouched; otherwise the full-position
objective was already spent before entry.
"""
from __future__ import annotations

from typing import Any

import contracts_v5 as _contracts
from domain import Candle, Side
from easychart_re1_mature_balance import (
    CANONICAL_BALANCE_EPISODE_RULE,
    MatureBalanceBox,
    MatureBalanceEngine,
)


SINGLE_USE_BALANCE_LEVEL_RULE = (
    "SOURCE_AMBIGUITY_TRANSLATION:"
    "EACH_REPEATED_DEFENSE_LEVEL_MAY_BELONG_TO_ONLY_ONE_CANONICAL_BALANCE_EPISODE"
)
UNSPENT_OPPOSITE_BALANCE_TARGET_RULE = (
    "SOURCE_AMBIGUITY_TRANSLATION:"
    "THE_OUTSIDE_SWEEP_BAR_MUST_NOT_ALREADY_TOUCH_THE_OPPOSITE_DEFENSE_OBJECTIVE"
)
for _rule in (
    SINGLE_USE_BALANCE_LEVEL_RULE,
    UNSPENT_OPPOSITE_BALANCE_TARGET_RULE,
):
    if _rule not in _contracts.TRANSLATION_RULES:
        _contracts.TRANSLATION_RULES += (_rule,)


class MatureBalanceEngineV2(MatureBalanceEngine):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._claimed_balance_level_ids: set[str] = set()

    def _pair_candidates(self, bar: Candle) -> list[MatureBalanceBox]:
        return [
            box
            for box in super()._pair_candidates(bar)
            if box.support.level_id not in self._claimed_balance_level_ids
            and box.resistance.level_id not in self._claimed_balance_level_ids
        ]

    def _retire_box(
        self,
        box: MatureBalanceBox,
        bar: Candle,
        reason: str,
    ) -> None:
        self._claimed_box_ids.add(box.box_id)
        self._claimed_balance_level_ids.update(
            (box.support.level_id, box.resistance.level_id)
        )
        if self.active_box is box:
            self.active_box = None
        self._inc(reason)
        self._trace(
            reason,
            bar.ts_close_ns,
            box_id=box.box_id,
            support_level_id=box.support.level_id,
            resistance_level_id=box.resistance.level_id,
            high=bar.high,
            low=bar.low,
            close=bar.close,
            rule_provenance=(
                CANONICAL_BALANCE_EPISODE_RULE,
                SINGLE_USE_BALANCE_LEVEL_RULE,
                UNSPENT_OPPOSITE_BALANCE_TARGET_RULE,
            ),
        )

    def _arm_sweep(
        self,
        box: MatureBalanceBox,
        side: Side,
        bar: Candle,
    ) -> None:
        self._claimed_balance_level_ids.update(
            (box.support.level_id, box.resistance.level_id)
        )
        super()._arm_sweep(box, side, bar)

    def _on_five(self, bar: Candle) -> None:
        self.structure.on_bar(bar)
        if self.directional_draw_active:
            self.structure.observe_price(bar)
            return
        self._activate_box(bar)
        box = self.active_box
        if box is not None:
            if box.mature_time_ns is None:
                before = self.active_box
                self._advance_immature_box(box, bar)
                if before is not None and self.active_box is None:
                    self._claimed_balance_level_ids.update(
                        (box.support.level_id, box.resistance.level_id)
                    )
            elif bar.ts_close_ns > box.mature_time_ns:
                inside = self._inside(box, bar.close)
                support_sweep = bar.low < box.support.lower and inside
                resistance_sweep = bar.high > box.resistance.upper and inside
                if support_sweep and resistance_sweep:
                    self._retire_box(
                        box,
                        bar,
                        "mature_box_swept_both_sides_unresolved",
                    )
                elif support_sweep:
                    if bar.high >= box.resistance.lower:
                        self._retire_box(
                            box,
                            bar,
                            "support_sweep_spent_opposite_target_before_entry",
                        )
                    else:
                        self._arm_sweep(box, Side.LONG, bar)
                elif resistance_sweep:
                    if bar.low <= box.support.upper:
                        self._retire_box(
                            box,
                            bar,
                            "resistance_sweep_spent_opposite_target_before_entry",
                        )
                    else:
                        self._arm_sweep(box, Side.SHORT, bar)
                elif not inside:
                    self._retire_box(
                        box,
                        bar,
                        "mature_box_accepted_break_without_reclaim",
                    )
        self.structure.observe_price(bar)

    @property
    def diagnostics(self) -> dict[str, Any]:
        output = dict(super().diagnostics)
        output["single_use_balance_levels"] = {
            "claimed_levels": len(self._claimed_balance_level_ids),
            "rules": (
                SINGLE_USE_BALANCE_LEVEL_RULE,
                UNSPENT_OPPOSITE_BALANCE_TARGET_RULE,
            ),
        }
        return output
