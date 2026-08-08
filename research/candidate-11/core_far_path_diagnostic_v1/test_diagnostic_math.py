from __future__ import annotations

import sys
from pathlib import Path
import unittest

import pandas as pd

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from diagnose_paths import (  # noqa: E402
    classify_path,
    first_boundary_time,
    first_threshold_time,
    safe_ratio,
)


class PathMathTest(unittest.TestCase):
    def setUp(self) -> None:
        index = pd.date_range("2025-01-01T00:01:00Z", periods=3, freq="1min")
        self.frame = pd.DataFrame(
            {
                "high": [101.0, 102.6, 101.5],
                "low": [99.5, 100.0, 97.0],
            },
            index=index,
        )

    def test_long_threshold_and_boundary_times(self) -> None:
        reached = first_threshold_time(
            self.frame,
            entry=100.0,
            risk=5.0,
            direction="LONG",
            threshold_r=0.5,
        )
        self.assertEqual(reached, int(self.frame.index[1].value))
        breached = first_boundary_time(
            self.frame,
            level=98.0,
            direction="LONG",
        )
        self.assertEqual(breached, int(self.frame.index[2].value))

    def test_short_threshold_and_boundary_times(self) -> None:
        reached = first_threshold_time(
            self.frame,
            entry=102.0,
            risk=4.0,
            direction="SHORT",
            threshold_r=1.0,
        )
        self.assertEqual(reached, int(self.frame.index[2].value))
        breached = first_boundary_time(
            self.frame,
            level=102.5,
            direction="SHORT",
        )
        self.assertEqual(breached, int(self.frame.index[1].value))

    def test_path_labels_are_diagnostic_only(self) -> None:
        base = {"outcome": "STOP_LOSS", "mfe_structural_r": 0.2}
        self.assertEqual(
            classify_path(base),
            "NO_MEANINGFUL_POST_ENTRY_DELIVERY",
        )
        self.assertEqual(safe_ratio(1.0, 0.0), None)


if __name__ == "__main__":
    unittest.main()
