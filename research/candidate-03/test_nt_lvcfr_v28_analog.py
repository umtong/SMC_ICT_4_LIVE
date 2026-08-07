from __future__ import annotations

import unittest

import numpy as np

from derive_nt_lvcfr_v28_analog_signals import train_analog


class AnalogModelTests(unittest.TestCase):
    def test_local_nonlinear_clusters_are_distinguished(self) -> None:
        rng = np.random.default_rng(19)
        negative_left = rng.normal(loc=(-2.0, 0.0), scale=0.15, size=(100, 2))
        positive_upper = rng.normal(loc=(0.0, 2.0), scale=0.15, size=(100, 2))
        negative_right = rng.normal(loc=(2.0, 0.0), scale=0.15, size=(100, 2))
        positive_lower = rng.normal(loc=(0.0, -2.0), scale=0.15, size=(100, 2))
        x = np.vstack([negative_left, positive_upper, negative_right, positive_lower])
        y = np.concatenate([
            np.zeros(100), np.ones(100), np.zeros(100), np.ones(100)
        ])
        order = np.arange(len(x)).reshape(4, 100).T.reshape(-1)
        model = train_analog(x[order], y[order], "NONLINEAR")
        # 1.0 is a valid calibrated cutoff for a perfectly pure local cluster;
        # only the disabled sentinel 1.1 is invalid.
        self.assertLessEqual(model.threshold, 1.0)
        self.assertGreater(model.predict((0.0, 2.0)), 0.75)
        self.assertLess(model.predict((2.0, 0.0)), 0.25)
        self.assertGreaterEqual(model.calibration_count, 12)

    def test_prediction_is_deterministic(self) -> None:
        x = np.column_stack([
            np.linspace(-2.0, 2.0, 400),
            np.sin(np.linspace(-2.0, 2.0, 400)),
        ])
        y = (x[:, 0] > 0.0).astype(float)
        first = train_analog(x, y, "TEST")
        second = train_analog(x, y, "TEST")
        self.assertAlmostEqual(first.predict((1.5, np.sin(1.5))), second.predict((1.5, np.sin(1.5))))
        self.assertEqual(first.threshold, second.threshold)


if __name__ == "__main__":
    unittest.main(verbosity=2)
