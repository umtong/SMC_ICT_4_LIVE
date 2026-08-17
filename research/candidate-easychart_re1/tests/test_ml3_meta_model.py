from __future__ import annotations

import math
from pathlib import Path
import random
import tempfile
import unittest

from ml3_meta_model import ML3MetaModel, geometry_prior
from ml3_online_features import CATEGORICAL_FEATURES, NUMERIC_FEATURES


def feature_row(signal: float, index: int) -> dict[str, float | str]:
    row: dict[str, float | str] = {}
    for position, name in enumerate(NUMERIC_FEATURES):
        row[name] = 0.1 * math.sin(index * 0.07 + position * 0.11)
    row["gross_rr"] = 1.0 + 0.5 * ((index % 7) / 6.0)
    row["aligned_seq_15m_return_z"] = signal
    for position, name in enumerate(CATEGORICAL_FEATURES):
        row[name] = f"{name}_{(index + position) % 3}"
    return row


class ML3MetaModelTest(unittest.TestCase):
    def test_geometry_prior_respects_reward_risk(self) -> None:
        self.assertAlmostEqual(geometry_prior(1.0), 0.5)
        self.assertAlmostEqual(geometry_prior(3.0), 0.25)

    def test_fit_learns_signal_and_round_trips(self) -> None:
        rng = random.Random(17)
        rows: list[dict[str, float | str]] = []
        labels: list[int] = []
        for index in range(600):
            signal = rng.gauss(0.0, 1.0)
            probability = 1.0 / (1.0 + math.exp(-(-0.15 + 1.5 * signal)))
            rows.append(feature_row(signal, index))
            labels.append(int(rng.random() < probability))
        model = ML3MetaModel.fit(rows, labels, l2=0.03)
        high = feature_row(2.0, 1_001)
        low = feature_row(-2.0, 1_002)
        high_probability = model.predict_probability(high)
        low_probability = model.predict_probability(low)
        self.assertGreater(high_probability, low_probability + 0.45)
        self.assertTrue(model.training["converged"])

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "model.json"
            model.save(path)
            loaded = ML3MetaModel.load(path)
            self.assertEqual(model.sha256, loaded.sha256)
            self.assertAlmostEqual(
                model.predict_probability(high),
                loaded.predict_probability(high),
                places=12,
            )

    def test_unseen_categories_use_other_reference(self) -> None:
        rows = [feature_row(-1.0, index) for index in range(60)] + [
            feature_row(1.0, 100 + index) for index in range(60)
        ]
        labels = [0] * 60 + [1] * 60
        model = ML3MetaModel.fit(rows, labels)
        unseen = feature_row(0.5, 999)
        for name in CATEGORICAL_FEATURES:
            unseen[name] = "UNSEEN_LIVE_VALUE"
        probability = model.predict_probability(unseen)
        self.assertTrue(0.0 < probability < 1.0)


if __name__ == "__main__":
    unittest.main()
