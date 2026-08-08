from __future__ import annotations

from pathlib import Path
import unittest

from portfolio_materializer import materialize_combined_portfolio_source
from runner_materializer import materialize_runner_source
from session_auction_bridge import SessionAuctionBridge, session_logic_key
from session_auction_i7 import LogicConfig


ROOT = Path(__file__).resolve().parent
SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT")


class RelativeFailedAuctionPortfolioTests(unittest.TestCase):
    def materialized(self) -> str:
        source = (ROOT / "run_leadership_scdam_base.py").read_text(encoding="utf-8")
        source = materialize_runner_source(source)
        return materialize_combined_portfolio_source(source)

    def test_generated_nautilus_strategy_compiles(self) -> None:
        source = self.materialized()
        compile(source, "candidate11-relative-failed-auction.py", "exec")

    def test_only_completed_session_far_reaches_candidate_list(self) -> None:
        source = self.materialized()
        self.assertIn("RFAR is a single-scenario experiment", source)
        self.assertIn('ordinary_engine.mark_rejected(', source)
        self.assertIn('"RELATIVE_FAR_SESSION_ONLY"', source)
        self.assertIn('if semantic_scenario != "FAR":', source)
        self.assertIn('"RELATIVE_FAR_I7_ONLY"', source)
        self.assertIn("plans = []", source)
        self.assertIn("plans.append((session_plan, session_candidate))", source)

    def test_all_four_markets_own_independent_i7_engines(self) -> None:
        source = self.materialized()
        self.assertIn("for session_symbol in SYMBOLS:", source)
        self.assertIn("self.session_logic_keys", source)
        self.assertIn('price_increment=float(META[symbol]["price_increment"])', source)
        for symbol in SYMBOLS:
            self.assertEqual(session_logic_key(symbol), f"{symbol}::SESSION_I7")

    def test_risk_and_global_slot_contracts_remain_in_generated_source(self) -> None:
        source = self.materialized()
        self.assertIn("self.sizer = RiskSizer(logic_config.risk_fraction)", source)
        self.assertIn("self.mutex = GlobalCandidateMutex()", source)
        self.assertIn("self.mutex.state != SlotState.FREE", source)
        self.assertIn("planned_loss_budget", source)
        self.assertIn("GLOBAL_ENTRY_SUBMITTED", source)

    def test_tick_override_does_not_change_i7_alpha_or_risk(self) -> None:
        import json

        payload = json.loads((ROOT / "session_i7_config.json").read_text(encoding="utf-8"))
        base = LogicConfig(**payload["logic"])
        bridge = SessionAuctionBridge(
            base,
            "XRPUSDT-PERP.BINANCE",
            logic_key=session_logic_key("XRPUSDT"),
            price_increment=0.0001,
        )
        self.assertEqual(bridge.engine.config.price_increment, 0.0001)
        self.assertEqual(bridge.engine.config.risk_fraction, base.risk_fraction)
        self.assertEqual(bridge.engine.config.min_net_r, base.min_net_r)
        self.assertEqual(bridge.engine.config.bar_minutes, base.bar_minutes)
        self.assertEqual(base.price_increment, 0.1)


if __name__ == "__main__":
    unittest.main()
