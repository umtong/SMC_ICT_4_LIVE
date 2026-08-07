from __future__ import annotations

import json
from pathlib import Path
import unittest

from portfolio_materializer import materialize_combined_portfolio_source
from session_auction_bridge import SessionAuctionBridge, session_logic_key
from session_auction_i7 import LogicConfig


ROOT = Path(__file__).resolve().parent
SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT")
TICKS = {
    "BTCUSDT": 0.1,
    "ETHUSDT": 0.01,
    "SOLUSDT": 0.001,
    "XRPUSDT": 0.0001,
}


class MultiSessionPortfolioTests(unittest.TestCase):
    @staticmethod
    def frozen_config() -> LogicConfig:
        payload = json.loads((ROOT / "session_i7_config.json").read_text(encoding="utf-8"))
        return LogicConfig(**payload["logic"])

    def test_every_symbol_has_an_independent_session_logic_key(self) -> None:
        keys = {symbol: session_logic_key(symbol) for symbol in SYMBOLS}
        self.assertEqual(len(set(keys.values())), len(SYMBOLS))
        for symbol, key in keys.items():
            self.assertEqual(key, f"{symbol}::SESSION_I7")

    def test_tick_override_changes_only_bridge_execution_metadata(self) -> None:
        base = self.frozen_config()
        self.assertEqual(base.price_increment, 0.1)
        for symbol, tick in TICKS.items():
            bridge = SessionAuctionBridge(
                base,
                f"{symbol}-PERP.BINANCE",
                logic_key=session_logic_key(symbol),
                price_increment=tick,
            )
            self.assertEqual(bridge.logic_key, session_logic_key(symbol))
            self.assertEqual(bridge.engine.config.price_increment, tick)
            self.assertEqual(bridge.engine.config.risk_fraction, base.risk_fraction)
            self.assertEqual(bridge.engine.config.min_net_r, base.min_net_r)
            self.assertEqual(bridge.engine.config.bar_minutes, base.bar_minutes)
        # dataclasses.replace must not mutate the shared frozen config.
        self.assertEqual(base.price_increment, 0.1)

    def test_materialized_portfolio_is_symmetric_and_compiles(self) -> None:
        source = (ROOT / "run_leadership_scdam_base.py").read_text(encoding="utf-8")
        materialized = materialize_combined_portfolio_source(source)
        compile(materialized, "candidate11-multi-session-generated.py", "exec")
        self.assertIn("for session_symbol in SYMBOLS:", materialized)
        self.assertIn("self.session_logic_keys", materialized)
        self.assertIn('price_increment=float(META[symbol]["price_increment"])', materialized)
        self.assertIn("symbol=session_symbol", materialized)
        self.assertIn("Candidate 11 multi-session semantic gate", materialized)
        self.assertNotIn(
            'session_plan = self.logic[self.session_logic_key].on_bar(\n'
            '                self.buffer["BTCUSDT"]',
            materialized,
        )

    def test_protocol_changes_opportunity_set_not_alpha_thresholds(self) -> None:
        protocol = json.loads((ROOT / "protocol.json").read_text(encoding="utf-8"))
        self.assertFalse(protocol["success_claim"])
        self.assertEqual(
            protocol["execution_lock"]["session_module_symbols"],
            list(SYMBOLS),
        )
        self.assertEqual(protocol["aggregate_gate"]["risk_fraction"], 0.03)
        self.assertEqual(
            protocol["aggregate_gate"]["global_pending_entry_plus_position_limit"],
            1,
        )
        forbidden = protocol["hypothesis"]["forbidden_changes"]
        self.assertIn("No threshold relaxation", forbidden)


if __name__ == "__main__":
    unittest.main()
