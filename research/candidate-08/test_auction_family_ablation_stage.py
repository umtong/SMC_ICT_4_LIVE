"""Contracts for reproducible candidate-08 failed-stage selection."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from auction_family_ablation_decision import (
    FAILED_AUCTION_FAMILY,
    IMPLEMENTATION_REVISION,
    INITIATIVE_FAMILY,
)
from auction_family_ablation_stage import choose_failed_stage


def _summary(
    *,
    suite: str,
    passed: bool = False,
    initiative_pnl: float = 100.0,
    failed_pnl: float = -50.0,
) -> dict:
    return {
        "suite": suite,
        "implementation_revision": IMPLEMENTATION_REVISION,
        "auction_family_mode": "both",
        "diagnostic_family_ablation": False,
        "scenario_attribution_passed": True,
        "suite_gate_passed": passed,
        "closed_trades": 2,
        "scenario_family_results": {
            INITIATIVE_FAMILY: {
                "signals": 2,
                "closed_trades": 1,
                "wins": int(initiative_pnl > 0),
                "realized_pnl_usdt": initiative_pnl,
            },
            FAILED_AUCTION_FAMILY: {
                "signals": 2,
                "closed_trades": 1,
                "wins": int(failed_pnl > 0),
                "realized_pnl_usdt": failed_pnl,
            },
        },
    }


def _write(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


class FailedStageSelectionContracts(unittest.TestCase):
    def test_failed_first_stage_is_selected_and_hashed(self) -> None:
        with TemporaryDirectory() as raw:
            root = Path(raw) / "root"
            first = root / "first"
            screen = root / "screen"
            summary_path = first / "suite_metrics.json"
            _write(summary_path, _summary(suite="first"))
            _write(screen / "suite_metrics.json", _summary(suite="screen"))

            decision = choose_failed_stage(
                root=root,
                first_output=first,
                screen_output=screen,
                first_passed=False,
                screen_status="0",
            )

            expected_hash = hashlib.sha256(summary_path.read_bytes()).hexdigest()
            self.assertTrue(decision["selected"])
            self.assertEqual(decision["suite"], "first")
            self.assertEqual(decision["family_mode"], "initiative_only")
            self.assertEqual(decision["base_summary_path"], str(summary_path))
            self.assertEqual(decision["base_summary_sha256"], expected_hash)
            self.assertEqual(
                decision["output"],
                str(root / "first-ablation-initiative_only-v1"),
            )

    def test_failed_screen_is_selected_only_after_passed_first(self) -> None:
        with TemporaryDirectory() as raw:
            root = Path(raw) / "root"
            first = root / "first"
            screen = root / "screen"
            _write(first / "suite_metrics.json", _summary(suite="first", passed=True))
            _write(
                screen / "suite_metrics.json",
                _summary(
                    suite="screen",
                    initiative_pnl=-100.0,
                    failed_pnl=50.0,
                ),
            )

            decision = choose_failed_stage(
                root=root,
                first_output=first,
                screen_output=screen,
                first_passed=True,
                screen_status="0",
            )

            self.assertTrue(decision["selected"])
            self.assertEqual(decision["suite"], "screen")
            self.assertEqual(decision["family_mode"], "failed_auction_only")
            self.assertEqual(decision["retained_family"], FAILED_AUCTION_FAMILY)
            self.assertEqual(decision["removed_family"], INITIATIVE_FAMILY)

    def test_missing_or_failed_screen_run_has_no_stale_evidence_path(self) -> None:
        with TemporaryDirectory() as raw:
            root = Path(raw) / "root"
            first = root / "first"
            screen = root / "screen"
            _write(first / "suite_metrics.json", _summary(suite="first", passed=True))
            _write(screen / "suite_metrics.json", _summary(suite="screen"))

            decision = choose_failed_stage(
                root=root,
                first_output=first,
                screen_output=screen,
                first_passed=True,
                screen_status="1",
            )

            self.assertFalse(decision["selected"])
            self.assertEqual(
                decision["reason"],
                "NO_VALID_FAILED_BASE_STAGE_FOR_ABLATION",
            )
            self.assertIsNone(decision["base_summary_path"])
            self.assertIsNone(decision["base_summary_sha256"])
            self.assertIsNone(decision["output"])

    def test_passed_screen_has_no_ablation_path(self) -> None:
        with TemporaryDirectory() as raw:
            root = Path(raw) / "root"
            first = root / "first"
            screen = root / "screen"
            _write(first / "suite_metrics.json", _summary(suite="first", passed=True))
            _write(screen / "suite_metrics.json", _summary(suite="screen", passed=True))

            decision = choose_failed_stage(
                root=root,
                first_output=first,
                screen_output=screen,
                first_passed=True,
                screen_status="0",
            )

            self.assertFalse(decision["selected"])
            self.assertEqual(
                decision["reason"],
                "NO_VALID_FAILED_BASE_STAGE_FOR_ABLATION",
            )

    def test_first_stage_contribution_rule_is_not_overridden_by_screen_file(self) -> None:
        with TemporaryDirectory() as raw:
            root = Path(raw) / "root"
            first = root / "first"
            screen = root / "screen"
            _write(
                first / "suite_metrics.json",
                _summary(
                    suite="first",
                    initiative_pnl=-100.0,
                    failed_pnl=-50.0,
                ),
            )
            _write(
                screen / "suite_metrics.json",
                _summary(
                    suite="screen",
                    initiative_pnl=100.0,
                    failed_pnl=-50.0,
                ),
            )

            decision = choose_failed_stage(
                root=root,
                first_output=first,
                screen_output=screen,
                first_passed=False,
                screen_status="0",
            )

            self.assertFalse(decision["selected"])
            self.assertEqual(
                decision["reason"],
                "BOTH_FAMILIES_ECONOMICALLY_NEGATIVE",
            )
            self.assertEqual(decision["suite"], "first")


if __name__ == "__main__":
    unittest.main(verbosity=2)
