from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from resolve_validated_winner import resolve


class ResolveValidatedWinnerTest(unittest.TestCase):
    def write(self, root: Path, name: str, value: dict) -> None:
        (root / name).write_text(json.dumps(value), encoding="utf-8")

    def test_unvalidated_master_incremental_winner_is_not_selected(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            self.write(
                root,
                "master_research_continuation.json",
                {
                    "classification": "BTC_91D_ALPHA_GATE_PASSED",
                    "winner": "strategy_v31_impact_resiliency_reversal:ImpactResiliencyReversalStrategy",
                },
            )
            result = resolve(root)
            self.assertEqual(result["classification"], "NO_VALIDATED_BTC_WINNER")

    def test_validated_audit_winner_is_selected(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            self.write(
                root,
                "master_winner_control_audit.json",
                {
                    "classification": "BTC_91D_ALPHA_GATE_PASSED",
                    "master_winner_validated": True,
                    "winner": "strategy_v31_impact_resiliency_reversal:ImpactResiliencyReversalStrategy",
                    "selection": {},
                },
            )
            result = resolve(root)
            self.assertEqual(result["classification"], "VALIDATED_BTC_WINNER_RESOLVED")
            self.assertEqual(
                result["winner"],
                "strategy_v31_impact_resiliency_reversal:ImpactResiliencyReversalStrategy",
            )

    def test_post_audit_winner_has_priority(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            self.write(
                root,
                "post_audit_continuation.json",
                {
                    "classification": "BTC_91D_ALPHA_GATE_PASSED",
                    "winner": "strategy_v32_queue_pressure_release:QueuePressureReleaseStrategy",
                },
            )
            self.write(
                root,
                "master_winner_control_audit.json",
                {
                    "classification": "BTC_91D_ALPHA_GATE_PASSED",
                    "master_winner_validated": True,
                    "winner": "strategy_v30_external_acceptance_retest:ExternalAcceptanceFirstRetestStrategy",
                    "selection": {},
                },
            )
            result = resolve(root)
            self.assertEqual(
                result["winner"],
                "strategy_v32_queue_pressure_release:QueuePressureReleaseStrategy",
            )
            self.assertEqual(result["source_evidence"], "post_audit_continuation.json")

    def test_baseline_family_master_winner_is_authoritative(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            self.write(
                root,
                "master_research_continuation.json",
                {
                    "classification": "BTC_91D_ALPHA_GATE_PASSED",
                    "winner": "strategy_v26:ScenarioValidEntryStrategy",
                },
            )
            result = resolve(root)
            self.assertEqual(result["winner"], "strategy_v26:ScenarioValidEntryStrategy")


if __name__ == "__main__":
    unittest.main()
