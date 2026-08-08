from __future__ import annotations

import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from candidate15_v11_completed_auction_materializer import (
    ACCEPTED_AUCTION_FAMILY,
    FAILED_AUCTION_FAMILY,
    completed_source_auction_family,
    materialize_v11_completed_auction_router_source,
)


class FamilyTests(unittest.TestCase):
    def plan(self, scenario: str, **extra: object) -> SimpleNamespace:
        details = dict(
            pool_source="ASIA",
            range_id="R1",
            sweep_ts_ns=1,
            zone_low=99,
            zone_high=101,
        )
        details.update(extra)
        return SimpleNamespace(
            scenario=SimpleNamespace(value=scenario),
            details=details,
        )

    def test_far(self) -> None:
        self.assertEqual(
            completed_source_auction_family(
                self.plan("FAR", structural_stop=98),
            ),
            FAILED_AUCTION_FAMILY,
        )

    def test_aac(self) -> None:
        self.assertEqual(
            completed_source_auction_family(
                self.plan("AAC", defended_pullback=100, source_boundary=99),
            ),
            ACCEPTED_AUCTION_FAMILY,
        )

    def test_incomplete_fails_closed(self) -> None:
        self.assertIsNone(
            completed_source_auction_family(
                SimpleNamespace(
                    scenario=SimpleNamespace(value="FAR"),
                    details={},
                ),
            ),
        )

    def test_v10_source_materializes_once(self) -> None:
        import run_leadership_scdam

        output = materialize_v11_completed_auction_router_source(
            run_leadership_scdam._SOURCE,
        )
        self.assertEqual(output.count("completed_source_auction_family(plan)"), 1)
        self.assertNotIn("C15_V9_CORE_FAMILY_QUARANTINED", output)

    def test_v11_gate_preserves_session_contract_but_quarantines_family(self) -> None:
        from market_leadership import LeadershipDecision
        from candidate15_v11_market_leadership import (
            Candidate15V11SemanticMarketLeadershipGate,
            SESSION_FAMILY_QUARANTINED,
        )

        decision = LeadershipDecision(
            True,
            "MEASURED_SESSION_DECISION",
            "ETHUSDT",
            "BTCUSDT",
            "FAR",
            "LONG",
            1,
            2,
            {},
            {},
            {},
            1.0,
            1.0,
            1.0,
            1,
            1,
            0.5,
            0.8,
        )
        gate = object.__new__(Candidate15V11SemanticMarketLeadershipGate)
        with patch.object(
            Candidate15V11SemanticMarketLeadershipGate,
            "decide",
            return_value=decision,
        ):
            output = gate.decide_session(
                symbol="BTCUSDT",
                scenario="FAR",
                direction="LONG",
                sweep_ts_ns=1,
                confirmation_ts_ns=2,
            )
        self.assertFalse(output.approved)
        self.assertEqual(output.reason, SESSION_FAMILY_QUARANTINED)


class VendorTests(unittest.TestCase):
    @unittest.skipUnless(
        (Path(__file__).parent / "c13_semantic_market_leadership_v16.py").is_file(),
        "vendor materialized in CI",
    )
    def test_rank_one_rotation_source_rejected(self) -> None:
        from market_leadership import LeadershipDecision
        from c13_semantic_market_leadership_v16 import (
            FAR_ROTATION_SOURCE_NOT_TRANSFER,
            refine_v15_decision,
        )

        decision = LeadershipDecision(
            True,
            "SEMANTIC_FAR_ROTATION_TRANSFER_EVENT_DISPLACEMENT",
            "BTCUSDT",
            "ETHUSDT",
            "FAR",
            "LONG",
            1,
            2,
            {},
            {},
            {},
            1,
            1,
            1,
            2,
            1,
            0.2,
            0.8,
        )
        output = refine_v15_decision(decision)
        self.assertFalse(output.approved)
        self.assertEqual(output.reason, FAR_ROTATION_SOURCE_NOT_TRANSFER)

    @unittest.skipUnless(
        (Path(__file__).parent / "c13_semantic_logic_v15.py").is_file(),
        "vendor materialized in CI",
    )
    def test_v15_forbids_market_chase_and_rearm(self) -> None:
        text = (
            Path(__file__).parent / "c13_semantic_logic_v15.py"
        ).read_text(encoding="utf-8")
        self.assertIn("FAR_CAUSAL_DISPLACEMENT_RETRACE_LIMIT", text)
        self.assertIn("v15_market_chase_disabled", text)
        self.assertNotIn("REARM_AFTER_MISSED_RETRACE", text)


if __name__ == "__main__":
    unittest.main()
