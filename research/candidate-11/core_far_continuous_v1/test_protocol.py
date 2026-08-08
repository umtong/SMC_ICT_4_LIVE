from __future__ import annotations

from datetime import date, timedelta
import hashlib
import json
from pathlib import Path
import random
import sys
import unittest

HERE = Path(__file__).resolve().parent
SOURCE_ROOT = HERE.parent / "session_portfolio_v1"
for path in (HERE, SOURCE_ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from run_block import source_lock, validate_protocol  # noqa: E402


def interval(record: dict[str, str]) -> tuple[date, date]:
    return date.fromisoformat(record["start"]), date.fromisoformat(
        record["end_exclusive"]
    )


def overlaps(left: tuple[date, date], right: tuple[date, date]) -> bool:
    return left[0] < right[1] and right[0] < left[1]


class CoreFarProtocolTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.protocol = json.loads((HERE / "protocol.json").read_text(encoding="utf-8"))

    def test_evidence_roles_are_binding(self) -> None:
        validate_protocol(self.protocol)
        hierarchy = self.protocol["importance_contract"]["evidence_hierarchy"]
        self.assertFalse(hierarchy["TEMPORARY_TEST"]["can_advance_candidate"])
        self.assertFalse(hierarchy["TEMPORARY_TEST"]["can_claim_alpha"])
        self.assertFalse(hierarchy["DEVELOPMENT_GATE"]["can_claim_alpha"])
        self.assertFalse(
            hierarchy["FRESH_VALIDATION"]["can_change_source_between_blocks"]
        )
        self.assertFalse(self.protocol["success_claim_allowed"])
        self.assertFalse(self.protocol["validation_eligible"])

    def test_block_selection_is_reproducible(self) -> None:
        selection = self.protocol["selection"]
        warmup = timedelta(days=int(selection["warmup_days"]))
        starts: list[date] = []
        cursor = date.fromisoformat(selection["eligible_start_first"])
        last = date.fromisoformat(selection["eligible_start_last"])
        while cursor <= last:
            starts.append(cursor)
            cursor += timedelta(days=1)
        random.Random(int(selection["seed"])).shuffle(starts)

        exclusions = [
            (start - warmup, end)
            for start, end in map(interval, selection["opened_interval_exclusions"].values())
        ]
        accepted: list[tuple[date, date]] = []
        years: set[int] = set()
        for start in starts:
            end = start + timedelta(days=int(selection["evaluation_days"]))
            candidate_with_warmup = (start - warmup, end)
            if any(overlaps(candidate_with_warmup, opened) for opened in exclusions):
                continue
            if any(
                overlaps(candidate_with_warmup, (prior_start - warmup, prior_end))
                for prior_start, prior_end in accepted
            ):
                continue
            if start.year in years:
                continue
            accepted.append((start, end))
            years.add(start.year)
            if len(accepted) == 3:
                break

        expected = [interval(record) for record in selection["blocks"].values()]
        self.assertEqual(accepted, expected)
        self.assertEqual(len({start.year for start, _ in expected}), 3)
        self.assertTrue(all((end - start).days == 28 for start, end in expected))

    def test_locked_parent_blobs_match_protocol(self) -> None:
        lock = source_lock(self.protocol)
        records = lock["files"]
        expected = self.protocol["locked_source"]["blobs"]
        for name, sha in expected.items():
            self.assertTrue(records[name]["origin_git_blob_verified"])
            self.assertEqual(records[name]["origin_git_blob"], sha)
            self.assertEqual(len(records[name]["sha256"]), hashlib.sha256().digest_size * 2)


if __name__ == "__main__":
    unittest.main()
