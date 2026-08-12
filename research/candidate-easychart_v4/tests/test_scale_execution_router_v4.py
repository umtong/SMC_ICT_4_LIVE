from __future__ import annotations

from types import SimpleNamespace
import unittest

from domain import Side
from mtf_strategy_v4_scale_execution import parent_context_episode_key


class ScaleExecutionRouterTests(unittest.TestCase):
    def test_macro_plan_uses_its_own_1h_structure_event(self) -> None:
        plan = SimpleNamespace(
            scale_name="MACRO",
            setup_id="MACRO:STRUCTURE:BTCUSDT:60m:STRUCTURE_EVENT:00000123",
            side=Side.LONG,
            rule_provenance=(),
        )
        self.assertEqual(
            parent_context_episode_key(plan),
            "BTCUSDT:60m:STRUCTURE_EVENT:00000123",
        )

    def test_micro_plan_uses_router_observed_live_1h_event(self) -> None:
        plan = SimpleNamespace(
            scale_name="MICRO",
            setup_id="MICRO:STRUCTURE:BTCUSDT:15m:STRUCTURE_EVENT:00000456",
            side=Side.SHORT,
            rule_provenance=(
                "SOURCE_EXPLICIT:HIGHER_TIMEFRAME_CONTEXT_PRECEDES_LOWER_ENTRY",
                "ROUTER_OBSERVED:LIVE_1H_EVENT:FAKEOUT:CHANNEL_UPPER:BTCUSDT:60m:STRUCTURE_EVENT:00000123:SHORT",
            ),
        )
        self.assertEqual(
            parent_context_episode_key(plan),
            "BTCUSDT:60m:STRUCTURE_EVENT:00000123",
        )

    def test_unresolved_or_unprovenanced_plan_has_no_parent_key(self) -> None:
        plan = SimpleNamespace(
            scale_name="MICRO",
            setup_id="MICRO:STRUCTURE:BTCUSDT:15m:STRUCTURE_EVENT:00000456",
            side=Side.LONG,
            rule_provenance=("UNRELATED",),
        )
        self.assertIsNone(parent_context_episode_key(plan))

    def test_side_suffix_is_removed_without_truncating_event_id(self) -> None:
        plan = SimpleNamespace(
            scale_name="MICRO",
            setup_id="MICRO:STRUCTURE:XRPUSDT:15m:STRUCTURE_EVENT:00000456",
            side=Side.LONG,
            rule_provenance=(
                "ROUTER_OBSERVED:LIVE_1H_EVENT:TRAP_REENTRY:SWING_LOW:XRPUSDT:60m:STRUCTURE_EVENT:00000999:LONG",
            ),
        )
        self.assertEqual(
            parent_context_episode_key(plan),
            "XRPUSDT:60m:STRUCTURE_EVENT:00000999",
        )


if __name__ == "__main__":
    unittest.main()
