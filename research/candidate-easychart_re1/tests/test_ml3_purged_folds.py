from __future__ import annotations

import unittest

import numpy as np

from train_ml3_meta_model_purged import purged_expanding_folds


class PurgedExpandingFoldTest(unittest.TestCase):
    def test_label_crossing_validation_start_is_removed(self) -> None:
        minute = 60_000_000_000
        timestamps = np.arange(12, dtype=np.int64) * minute
        label_ends = timestamps + minute
        # Row 3 resolves at minute 9, so it must not enter a fold beginning
        # before minute 10 even though its plan was observed much earlier.
        label_ends[3] = 9 * minute
        folds = purged_expanding_folds(
            timestamps,
            label_ends,
            folds=2,
            minimum_train_rows=4,
        )
        self.assertTrue(folds)
        for train_index, _validation_index, report in folds:
            boundary = report["validation_start_ns"]
            self.assertTrue(np.all(label_ends[train_index] < boundary))
            if boundary <= 9 * minute:
                self.assertNotIn(3, train_index.tolist())

    def test_embargo_is_applied_to_label_end(self) -> None:
        minute = 60_000_000_000
        timestamps = np.arange(16, dtype=np.int64) * minute
        label_ends = timestamps.copy()
        folds = purged_expanding_folds(
            timestamps,
            label_ends,
            folds=2,
            minimum_train_rows=5,
            embargo_minutes=2,
        )
        self.assertTrue(folds)
        for train_index, _validation_index, report in folds:
            cutoff = report["validation_start_ns"] - 2 * minute
            self.assertTrue(np.all(label_ends[train_index] < cutoff))


if __name__ == "__main__":
    unittest.main()
