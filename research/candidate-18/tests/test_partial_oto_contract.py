from __future__ import annotations

import ast
from pathlib import Path
import unittest


class PartialOtoContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.root = Path(__file__).resolve().parents[1]
        cls.candidate_source = (cls.root / "candidate.py").read_text(encoding="utf-8")
        cls.adapter = (cls.root / "candidate18_strategy.py").read_text(encoding="utf-8")
        cls.strategy = (cls.root / "partial_oto_ioc_strategy.py").read_text(
            encoding="utf-8",
        )

    def test_effective_adapter_uses_partial_oto_ioc(self) -> None:
        self.assertIn(
            "from partial_oto_ioc_strategy import Candidate18Strategy",
            self.adapter,
        )
        self.assertNotIn("fok_capped_strategy", self.adapter)

    def test_shared_venue_is_explicitly_partial_oto(self) -> None:
        tree = ast.parse(self.candidate_source)
        method = next(
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef)
            and node.name == "_candidate18_venue_config"
        )
        text = ast.unparse(method)
        self.assertIn("kwargs['oto_trigger_mode'] = 'PARTIAL'", text)
        self.assertIn(
            "candidate05_backtest.BacktestVenueConfig = _candidate18_venue_config",
            self.candidate_source,
        )

    def test_strategy_reuses_capped_ioc_parent(self) -> None:
        self.assertIn(
            "from latency_capped_ioc_strategy import Candidate18Strategy",
            self.strategy,
        )
        ioc = (self.root / "latency_capped_ioc_strategy.py").read_text(
            encoding="utf-8",
        )
        self.assertIn("time_in_force=TimeInForce.IOC", ioc)
        self.assertIn("CANDIDATE18_IOC_PRICE_CAP", ioc)
        self.assertNotIn("OrderType.MARKET", ioc)


if __name__ == "__main__":
    unittest.main()
