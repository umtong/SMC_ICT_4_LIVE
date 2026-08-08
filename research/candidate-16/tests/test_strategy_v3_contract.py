from __future__ import annotations

from datetime import date, timedelta
from hashlib import sha256
import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class Candidate16V3ContractTests(unittest.TestCase):
    def test_v3_does_not_change_v2_parameters_or_gate(self) -> None:
        v2 = json.loads((ROOT / "config_v2.json").read_text(encoding="utf-8"))
        v3 = json.loads((ROOT / "config_v3.json").read_text(encoding="utf-8"))
        self.assertEqual(v3, v2)
        self.assertEqual(v3["risk_fraction"], 0.03)

    def test_topbook_data_reuses_candidate03_and_candidate05(self) -> None:
        source = (ROOT / "features_v3.py").read_text(encoding="utf-8")
        self.assertIn("candidate05_features.load_range", source)
        self.assertIn("candidate03_data.download_verified", source)
        self.assertIn("bookTicker", source)
        self.assertNotIn("simulate_fill", source)
        self.assertNotIn("portfolio", source.lower())

    def test_strategy_routes_coarse_depth_roles_to_topbook_facts(self) -> None:
        source = (ROOT / "strategy_v3.py").read_text(encoding="utf-8")
        self.assertIn(
            '"depth_imbalance_1": "topbook_quote_imbalance_end"',
            source,
        )
        self.assertIn(
            '"bid_depth_change_1_1m": "topbook_bid_queue_response"',
            source,
        )
        self.assertIn(
            '"ask_depth_change_1_1m": "topbook_ask_queue_response"',
            source,
        )
        self.assertIn(
            '"ret_60s_bps": "topbook_mid_ret_60s_bps"',
            source,
        )
        self.assertIn("Candidate16V2Strategy", source)

    def test_execution_and_risk_stay_in_nautilus_runner(self) -> None:
        source = (ROOT / "candidate_v3.py").read_text(encoding="utf-8")
        self.assertIn("research/candidate-05/backtest.py", source)
        self.assertIn("NautilusTrader BacktestNode", source)
        self.assertIn('"risk_fraction": 0.03', source)
        self.assertIn('"max_global_entry_or_position": 1', source)

    def test_pre_registered_week_is_deterministic(self) -> None:
        seed = (
            "candidate16-v3-top-of-book-resiliency|"
            "0d43da0256af7d4d2a1aa81dcdb98fec8f625cda|"
            "independent-week"
        )
        mondays: list[date] = []
        cursor = date(2022, 1, 3)
        while cursor <= date(2025, 12, 29):
            mondays.append(cursor)
            cursor += timedelta(days=7)
        digest = sha256(seed.encode("utf-8")).hexdigest()
        index = int(digest, 16) % len(mondays)
        self.assertEqual(digest, "5bcc531832c121c21e26750e8bf72ec0ca2b04dd500b339b9583b112cfb56ebe")
        self.assertEqual(index, 49)
        self.assertEqual(mondays[index], date(2022, 12, 12))


if __name__ == "__main__":
    unittest.main()
