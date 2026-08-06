from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

import select_cross_asset_windows as candidate


class RecordedIntervalTests(unittest.TestCase):
    def test_nested_evaluation_intervals_are_discovered(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "record.json").write_text(
                json.dumps(
                    {
                        "weeks": {
                            "one": {
                                "evaluation_start": "2024-01-01",
                                "evaluation_end": "2024-01-07",
                            }
                        }
                    }
                )
            )
            self.assertEqual(
                candidate.recorded_intervals(root),
                [
                    (
                        candidate.date(2024, 1, 1),
                        candidate.date(2024, 1, 7),
                    )
                ],
            )

    def test_overlap_is_inclusive(self) -> None:
        interval = [
            (candidate.date(2024, 1, 1), candidate.date(2024, 1, 7))
        ]
        self.assertTrue(
            candidate.overlaps(
                candidate.date(2024, 1, 7),
                candidate.date(2024, 1, 8),
                interval,
            )
        )
        self.assertFalse(
            candidate.overlaps(
                candidate.date(2024, 1, 8),
                candidate.date(2024, 1, 9),
                interval,
            )
        )


class SelectionTests(unittest.TestCase):
    def test_selection_is_deterministic_and_disjoint(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            evidence = root / "evidence"
            evidence.mkdir()
            (evidence / "known.json").write_text(
                json.dumps(
                    {
                        "evaluation_start": "2023-01-02",
                        "evaluation_end": "2023-03-31",
                    }
                )
            )
            candidates = {
                "successful_btc_long_candidates": [
                    {
                        "candidate_id": "v36-full-aaaaaaaaaa",
                        "family": "v36",
                        "route": "full",
                        "source_commit": "a" * 40,
                    }
                ]
            }
            first = candidate.select(root, evidence, candidates, 77)
            second = candidate.select(root, evidence, candidates, 77)
            self.assertEqual(first, second)
            screen = first["screen"]
            long_block = first["long_evaluation"]
            screen_start = candidate.date.fromisoformat(
                screen["evaluation_start"]
            )
            screen_end = candidate.date.fromisoformat(screen["evaluation_end"])
            long_start = candidate.date.fromisoformat(
                long_block["evaluation_start"]
            )
            long_end = candidate.date.fromisoformat(
                long_block["evaluation_end"]
            )
            self.assertFalse(
                candidate.overlaps(
                    screen_start,
                    screen_end,
                    [(long_start, long_end)],
                )
            )
            self.assertFalse(
                candidate.overlaps(
                    screen_start,
                    screen_end,
                    candidate.recorded_intervals(evidence),
                )
            )

    def test_no_successful_candidate_is_rejected_before_selection(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with self.assertRaises(ValueError):
                candidate.select(
                    root,
                    root,
                    {"successful_btc_long_candidates": []},
                    1,
                )


if __name__ == "__main__":
    unittest.main()
