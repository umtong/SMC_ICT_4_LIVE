"""Contracts for the Session Raid Reversal V2 causal next-bucket adapter."""

from __future__ import annotations

import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

import session_raid_reversal_signals_v1 as v1
import session_raid_reversal_signals_v2 as v2
from quote_resiliency_signals import QuoteResiliencySignalBundle


class SessionRaidReversalSignalsV2Tests(unittest.TestCase):
    def test_contiguous_next_bucket_is_accepted(self) -> None:
        completed = 1_000_000_000
        times = np.asarray(
            [completed + 10_000_000_000, completed + 20_000_000_000],
            dtype=np.int64,
        )
        self.assertEqual(v2.contiguous_first_execution_position_after(times, completed), 0)

    def test_warmup_candidate_cannot_jump_to_evaluation_start(self) -> None:
        completed = 1_000_000_000
        times = np.asarray(
            [completed + 24 * 60 * 60 * 1_000_000_000],
            dtype=np.int64,
        )
        self.assertIsNone(v2.contiguous_first_execution_position_after(times, completed))

    def test_gap_rejection_is_classified_separately_from_no_future_data(self) -> None:
        completed = 1_000_000_000
        observed = completed + 24 * 60 * 60 * 1_000_000_000
        data = pd.DataFrame(
            {"close": [100.0]},
            index=pd.DatetimeIndex([pd.Timestamp(observed, unit="ns", tz="UTC")]),
        )

        def fake_builder(**_kwargs):
            self.assertIsNone(
                v1.first_execution_position_after(
                    data.index.as_unit("ns").asi8,
                    completed,
                )
            )
            return QuoteResiliencySignalBundle(
                signals_by_time_ns={},
                diagnostics={v2.LEGACY_NO_LATER_REASON: 1},
                rejected_scenarios=(
                    {
                        "scenario_id": "stale-raid",
                        "scenario_family": "H4_DRAW_DIRECT_SESSION_RAID_REVERSAL",
                        "reason": v2.LEGACY_NO_LATER_REASON,
                        "trigger_time_ns": completed,
                        "details": {},
                    },
                ),
            )

        with patch.object(v1, "build_session_raid_reversal_signals", side_effect=fake_builder):
            bundle = v2.build_session_raid_reversal_signals(data=data)

        self.assertEqual(bundle.diagnostics[v2.STALE_REASON], 1)
        self.assertNotIn(v2.LEGACY_NO_LATER_REASON, bundle.diagnostics)
        self.assertEqual(bundle.rejected_scenarios[0]["reason"], v2.STALE_REASON)
        details = bundle.rejected_scenarios[0]["details"]
        self.assertEqual(details["first_later_execution_time_ns"], observed)
        self.assertGreater(details["execution_gap_ns"], v2.MAX_NEXT_BUCKET_GAP_NS)

    def test_v1_global_is_restored_after_builder_failure(self) -> None:
        data = pd.DataFrame(
            {"close": [100.0]},
            index=pd.DatetimeIndex([pd.Timestamp(10, unit="s", tz="UTC")]),
        )
        original = v1.first_execution_position_after
        with patch.object(
            v1,
            "build_session_raid_reversal_signals",
            side_effect=RuntimeError("synthetic failure"),
        ):
            with self.assertRaisesRegex(RuntimeError, "synthetic failure"):
                v2.build_session_raid_reversal_signals(data=data)
        self.assertIs(v1.first_execution_position_after, original)


if __name__ == "__main__":
    unittest.main(verbosity=2)
