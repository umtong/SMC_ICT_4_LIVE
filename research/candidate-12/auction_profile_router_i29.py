"""I29 structural extension of the completed-auction profile router.

A reversal against a fully migrated value auction needs evidence that the new
raid tested genuinely new price, not merely traversed the source auction's
existing excess tail.  The test is scale-free and structural:

    raid penetration beyond the completed boundary
        >
    distance from that boundary to the completed value edge

No numeric threshold is introduced.  The source auction, previous value, raid
extreme, and I19 decision are all complete before this router is called.
"""
from __future__ import annotations

from typing import Any

from auction_profile_router import AuctionProfileRouter, RouterDecision


class ExcessTailAuctionRouter(AuctionProfileRouter):
    """Require a new-price probe before fading against migrated value."""

    @staticmethod
    def _number(value: Any, *, name: str) -> float:
        try:
            result = float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"missing or invalid {name}") from exc
        if result != result or result in (float("inf"), float("-inf")):
            raise ValueError(f"non-finite {name}")
        return result

    def evaluate(self, symbol: str, plan: Any) -> RouterDecision:
        base = super().evaluate(symbol, plan)
        if not base.approved:
            return base

        scenario = str(plan.scenario.value)
        migration = str(base.context.get("value_migration", ""))
        if scenario not in (
            self._IMMEDIATE_HIGH_REJECTIONS | self._IMMEDIATE_LOW_REJECTIONS
        ):
            return base

        context = dict(base.context)
        current = dict(context.get("current", {}))
        try:
            raid_extreme = self._number(
                plan.details.get("raid_extreme"),
                name="raid_extreme",
            )
            if scenario in self._IMMEDIATE_HIGH_REJECTIONS:
                boundary = self._number(current.get("high"), name="source_high")
                value_edge = self._number(
                    current.get("value_high"),
                    name="source_value_high",
                )
                penetration = raid_extreme - boundary
                excess_tail = boundary - value_edge
                direction = "HIGH"
                against_migration = migration == "ABOVE_PRIOR_VALUE"
            else:
                boundary = self._number(current.get("low"), name="source_low")
                value_edge = self._number(
                    current.get("value_low"),
                    name="source_value_low",
                )
                penetration = boundary - raid_extreme
                excess_tail = value_edge - boundary
                direction = "LOW"
                against_migration = migration == "BELOW_PRIOR_VALUE"
        except Exception as exc:
            context["excess_tail_test"] = {
                "exception": type(exc).__name__,
                "message": str(exc),
            }
            return RouterDecision(
                False,
                "EXCESS_TAIL_TEST_UNAVAILABLE",
                context,
            )

        context["excess_tail_test"] = {
            "direction": direction,
            "against_migration": against_migration,
            "raid_extreme": raid_extreme,
            "boundary": boundary,
            "value_edge": value_edge,
            "raid_penetration": penetration,
            "pre_existing_excess_tail": excess_tail,
            "cleared_excess_tail": penetration > excess_tail,
        }
        if penetration <= 0 or excess_tail < 0:
            return RouterDecision(
                False,
                "INVALID_EXCESS_TAIL_GEOMETRY",
                context,
            )
        if against_migration and not penetration > excess_tail:
            reason = (
                "MIGRATED_HIGH_RAID_DID_NOT_CLEAR_EXCESS_TAIL"
                if direction == "HIGH"
                else "MIGRATED_LOW_RAID_DID_NOT_CLEAR_EXCESS_TAIL"
            )
            return RouterDecision(False, reason, context)
        return RouterDecision(True, "EXCESS_TAIL_AUCTION_APPROVED", context)
