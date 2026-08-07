from __future__ import annotations

from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from logic import LogicConfig
from session_engine import RegionalHandoffAuctionEngine
SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT")


class TestIndependentPortfolioLogic(unittest.TestCase):
    def test_four_markets_receive_distinct_unchanged_scdam_engines(self) -> None:
        config = LogicConfig()
        engines = {
            symbol: RegionalHandoffAuctionEngine(config, f"{symbol}-PERP.BINANCE")
            for symbol in SYMBOLS
        }
        self.assertEqual(tuple(engines), SYMBOLS)
        self.assertEqual(len({id(engine) for engine in engines.values()}), 4)
        self.assertTrue(all(engine.config == config for engine in engines.values()))
        self.assertTrue(all(not engine.bars and not engine.events for engine in engines.values()))

    def test_runner_uses_one_global_slot_and_routes_state_to_emitter(self) -> None:
        source = (ROOT / "run_portfolio_scdam.py").read_text(encoding="utf-8")
        self.assertIn("self.mutex = GlobalCandidateMutex()", source)
        self.assertIn("self.logic[symbol].mark_rejected", source)
        self.assertIn("self.logic[self.active_symbol].mark_entry_filled", source)
        self.assertIn("self.logic[symbol].mark_submitted", source)
        self.assertIn("self._all_flat()", source)
        self.assertIn("self._open_orders()", source)

    def test_runner_keeps_exact_nav_risk_and_nautilus_execution_boundary(self) -> None:
        source = (ROOT / "run_portfolio_scdam.py").read_text(encoding="utf-8")
        self.assertIn("self.sizer = RiskSizer(logic_config.risk_fraction)", source)
        self.assertIn("nav=nav", source)
        self.assertIn("engine.run()", source)
        self.assertIn("engine.trader.generate_positions_report()", source)
        self.assertIn("engine.trader.generate_account_report(venue)", source)
        self.assertNotIn("def simulate_fill", source)
        self.assertNotIn("def backtest_loop", source)
        self.assertNotIn("risk_multiplier", source)
        self.assertNotIn("max_notional_limit", source)

    def test_same_timestamp_plans_are_arbitrated_before_submission(self) -> None:
        source = (ROOT / "run_portfolio_scdam.py").read_text(encoding="utf-8")
        process = source.index("def _process_batch")
        flush = source.index("arbitration = self.mutex.flush()", process)
        submit = source.index("self._submit(winner[0], winner[1])", flush)
        self.assertLess(process, flush)
        self.assertLess(flush, submit)


if __name__ == "__main__":
    unittest.main()
