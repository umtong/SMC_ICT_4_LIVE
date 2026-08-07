from __future__ import annotations

import tempfile
import unittest
import zipfile
from pathlib import Path

import numpy as np

from derive_nt_lvcfr_v27_signals import (
    read_kline_archive,
    target_trigger,
    train_model,
    wilson_lower,
)


class TimestampContractTests(unittest.TestCase):
    def archive(self, timestamp: int) -> Path:
        directory = Path(tempfile.mkdtemp())
        path = directory / "sample.zip"
        row = [
            timestamp,
            100.0,
            101.0,
            99.0,
            100.5,
            1.0,
            timestamp + 59_999,
            100.0,
            10,
            0.6,
            60.0,
            0,
        ]
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("sample.csv", ",".join(map(str, row)) + "\n")
        return path

    def test_millisecond_timestamp_is_preserved(self) -> None:
        frame = read_kline_archive(self.archive(1_704_067_200_000), "futures")
        self.assertEqual(int(frame.iloc[0].open_time_ms), 1_704_067_200_000)

    def test_microsecond_timestamp_is_normalized(self) -> None:
        frame = read_kline_archive(self.archive(1_704_067_200_000_000), "spot")
        self.assertEqual(int(frame.iloc[0].open_time_ms), 1_704_067_200_000)


class CostTargetTests(unittest.TestCase):
    def test_target_is_symmetric_and_ahead(self) -> None:
        long_target = target_trigger(100.0, 99.0, 1)
        short_target = target_trigger(100.0, 101.0, -1)
        self.assertGreater(long_target, 102.0)
        self.assertLess(short_target, 98.0)
        self.assertAlmostEqual(
            long_target - 100.0,
            100.0 - short_target,
            delta=0.03,
        )


class CalibrationTests(unittest.TestCase):
    def test_wilson_lower_improves_with_more_wins(self) -> None:
        self.assertGreater(wilson_lower(18, 20), wilson_lower(12, 20))
        self.assertGreater(wilson_lower(90, 100), wilson_lower(9, 10))

    def test_uncalibrated_model_is_disabled(self) -> None:
        rng = np.random.default_rng(7)
        x = rng.normal(size=(100, 3))
        y = rng.integers(0, 2, size=100).astype(float)
        model = train_model(x, y)
        if model.calibration_count == 0:
            self.assertGreater(model.threshold, 1.0)

    def test_deterministic_separable_model_calibrates(self) -> None:
        # The fixed selection grid tops out at 30% and calibration requires at
        # least 12 selected samples. Use a calibration tail large enough that
        # the contract is satisfiable; this changes only the synthetic test.
        x = np.linspace(-3.0, 3.0, 400).reshape(-1, 1)
        y = (x[:, 0] > 0.0).astype(float)
        first = train_model(x, y)
        second = train_model(x, y)
        np.testing.assert_allclose(first.weights, second.weights)
        self.assertLess(first.threshold, 1.0)
        self.assertGreater(first.predict((2.0,)), first.predict((-2.0,)))
        self.assertGreaterEqual(first.calibration_count, 12)


if __name__ == "__main__":
    unittest.main(verbosity=2)
