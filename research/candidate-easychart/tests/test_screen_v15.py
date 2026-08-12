import unittest

from domain_v3 import Side, TargetMode
from market_v7 import ExpiringArmedSetup
from screen_v15 import _raid_side, merge_same_bar_options, option_root


def setup(*, setup_id, family, side, entry, stop, target, pool):
    return ExpiringArmedSetup(
        setup_id=setup_id,
        causal_event_id=f"CE:{setup_id}",
        symbol="BTCUSDT",
        family=family,
        side=side,
        observed_time_ns=100,
        entry=entry,
        stop=stop,
        target_mode=TargetMode.FIXED_STRUCTURE,
        initial_target=target,
        fixed_target_id=f"T:{pool}",
        source_pool_id=pool,
        zone_low=min(entry, entry),
        zone_high=max(entry, entry),
        formation_extreme=stop,
        body_ratio=0.0,
        context_bias="TEST",
        source_timeframe_minutes=5,
        valid_until_ns=1000,
    )


class SemanticFamilyIdentityTests(unittest.TestCase):
    def test_merged_display_names_preserve_semantic_root(self):
        self.assertEqual(
            option_root("FAILED_BREAK_REVERSAL_MERGED_2_STRUCTURES_ISOLATED_RAID"),
            "FAILED_BREAK_REVERSAL",
        )
        self.assertEqual(
            option_root("ACCEPTED_BREAK_CONTINUATION_MERGED_3_STRUCTURES"),
            "ACCEPTED_BREAK_CONTINUATION",
        )

    def test_merged_continuation_uses_broken_side_for_peer_observation(self):
        item = setup(
            setup_id="S",
            family="ACCEPTED_BREAK_CONTINUATION_MERGED_2_STRUCTURES",
            side=Side.SHORT,
            entry=100.0,
            stop=103.0,
            target=95.0,
            pool="P",
        )
        self.assertEqual(_raid_side(item), Side.LONG)


class CausalMergeTests(unittest.TestCase):
    def test_long_merge_keeps_first_reachable_entry_full_stop_and_nearest_target(self):
        first = setup(
            setup_id="A",
            family="ROLE_FAILED_BREAK_IMMEDIATE_FAKEOUT_RECLAIMED_BOUNDARY",
            side=Side.LONG,
            entry=101.0,
            stop=96.0,
            target=112.0,
            pool="P1",
        )
        second = setup(
            setup_id="B",
            family="ROLE_FAILED_BREAK_IMMEDIATE_FAKEOUT_RECLAIMED_BOUNDARY",
            side=Side.LONG,
            entry=102.0,
            stop=95.0,
            target=110.0,
            pool="P2",
        )
        merged, diagnostics, audit = merge_same_bar_options([first, second])
        self.assertEqual(len(merged), 1)
        item = merged[0]
        self.assertAlmostEqual(item.entry, 102.0)
        self.assertAlmostEqual(item.stop, 95.0)
        self.assertAlmostEqual(item.initial_target, 110.0)
        self.assertEqual(option_root(item.family), "FAILED_BREAK_REVERSAL")
        self.assertEqual(diagnostics["duplicate_setup_intents_removed"], 1)
        self.assertEqual(audit[0]["disposition"], "MERGED_ONE_CAUSAL_OPTION")

    def test_short_merge_uses_nearest_lower_objective(self):
        first = setup(
            setup_id="A",
            family="ROLE_ACCEPTED_BREAK_FIRST_RETEST_NEEDS_ACTIVE_OBJECTIVE",
            side=Side.SHORT,
            entry=100.0,
            stop=106.0,
            target=88.0,
            pool="P1",
        )
        second = setup(
            setup_id="B",
            family="ROLE_ACCEPTED_BREAK_FIRST_RETEST_NEEDS_ACTIVE_OBJECTIVE",
            side=Side.SHORT,
            entry=99.0,
            stop=107.0,
            target=90.0,
            pool="P2",
        )
        merged, _, _ = merge_same_bar_options([first, second])
        self.assertEqual(len(merged), 1)
        item = merged[0]
        self.assertAlmostEqual(item.entry, 99.0)
        self.assertAlmostEqual(item.stop, 107.0)
        self.assertAlmostEqual(item.initial_target, 90.0)
        self.assertEqual(option_root(item.family), "ACCEPTED_BREAK_CONTINUATION")


if __name__ == "__main__":
    unittest.main()
