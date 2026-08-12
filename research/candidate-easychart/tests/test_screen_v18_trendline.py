from __future__ import annotations

import unittest

from domain_v3 import Side, TargetMode
from market_v7 import ExpiringArmedSetup
from screen_v18_trendline import select_first_reachable_same_bar_options


def setup(
    setup_id: str,
    *,
    side: Side,
    entry: float,
    stop: float,
    target: float,
) -> ExpiringArmedSetup:
    return ExpiringArmedSetup(
        setup_id=setup_id,
        causal_event_id=f"event-{setup_id}",
        symbol="BTCUSDT",
        family="TRENDLINE_ACCEPTED_BREAK_FIRST_RETEST_OVERLAPPING_OB",
        side=side,
        observed_time_ns=100,
        entry=entry,
        stop=stop,
        target_mode=TargetMode.FIXED_STRUCTURE,
        initial_target=target,
        fixed_target_id=f"target-{setup_id}",
        source_pool_id=f"line-{setup_id}",
        zone_low=min(entry, entry + 0.1),
        zone_high=max(entry, entry + 0.1),
        formation_extreme=stop,
        body_ratio=2.0,
        source_timeframe_minutes=5,
        valid_until_ns=1000,
    )


class SameBarTrendlineSelectionTests(unittest.TestCase):
    def test_long_keeps_first_reachable_entry_and_its_own_geometry(self) -> None:
        deep = setup("deep", side=Side.LONG, entry=99.0, stop=94.0, target=110.0)
        near = setup("near", side=Side.LONG, entry=101.0, stop=98.0, target=106.0)
        selected, diagnostics, audit = select_first_reachable_same_bar_options(
            [deep, near]
        )
        self.assertEqual(selected, [near])
        self.assertEqual(selected[0].stop, 98.0)
        self.assertEqual(selected[0].initial_target, 106.0)
        self.assertEqual(diagnostics["same_bar_alternate_line_options_removed"], 1)
        self.assertEqual(audit[0]["chosen_setup_id"], "near")

    def test_short_keeps_first_reachable_entry(self) -> None:
        near = setup("near", side=Side.SHORT, entry=99.0, stop=102.0, target=94.0)
        deep = setup("deep", side=Side.SHORT, entry=101.0, stop=104.0, target=95.0)
        selected, _, _ = select_first_reachable_same_bar_options([deep, near])
        self.assertEqual(selected, [near])


if __name__ == "__main__":
    unittest.main()
