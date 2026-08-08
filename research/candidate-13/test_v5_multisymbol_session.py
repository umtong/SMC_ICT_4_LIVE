from __future__ import annotations

import json
from pathlib import Path
import unittest

from portfolio_materializer_v5 import materialize_multisymbol_session_source
from runner_materializer_v5 import materialize_runner_source
from session_auction_bridge import SessionAuctionBridge
from session_auction_i7 import LogicConfig as SessionLogicConfig

ROOT = Path(__file__).resolve().parent
SYMBOL_META = {
    "BTCUSDT": ("0.1", "BTCUSDT-PERP.BINANCE"),
    "ETHUSDT": ("0.01", "ETHUSDT-PERP.BINANCE"),
    "SOLUSDT": ("0.001", "SOLUSDT-PERP.BINANCE"),
    "XRPUSDT": ("0.0001", "XRPUSDT-PERP.BINANCE"),
}


class V5MultiSymbolSessionTests(unittest.TestCase):
    def test_exact_base_runner_materializes_and_compiles(self) -> None:
        source = (ROOT / "run_leadership_scdam_base.py").read_text(encoding="utf-8")
        source = materialize_runner_source(source)
        source = materialize_multisymbol_session_source(source)
        compile(source, str(ROOT / "run_leadership_scdam_base.py"), "exec")
        self.assertIn("for session_symbol in SYMBOLS:", source)
        self.assertIn("V5_SESSION_ONLY_MODULE_ISOLATION", source)
        self.assertIn('"price_increment": float(META[symbol]["price_increment"])', source)
        self.assertIn("self.session_logic_keys", source)
        self.assertNotIn('self.buffer["BTCUSDT"],\n                allow_entry=True', source)

    def test_four_bridges_have_distinct_market_metadata_and_logic_keys(self) -> None:
        payload = json.loads((ROOT / "session_i7_config.json").read_text(encoding="utf-8"))
        bridges: dict[str, SessionAuctionBridge] = {}
        for symbol, (increment, instrument_id) in SYMBOL_META.items():
            config = SessionLogicConfig(
                **{
                    **payload["logic"],
                    "price_increment": float(increment),
                }
            )
            logic_key = f"{symbol}::SESSION_I7"
            bridges[symbol] = SessionAuctionBridge(
                config,
                instrument_id,
                logic_key=logic_key,
            )
            self.assertEqual(bridges[symbol].logic_key, logic_key)
            self.assertEqual(bridges[symbol].engine.instrument_id, instrument_id)
            self.assertEqual(bridges[symbol].engine.config.price_increment, float(increment))
        self.assertEqual(len({id(bridge.engine) for bridge in bridges.values()}), 4)

    def test_materializer_fails_closed_on_source_drift(self) -> None:
        with self.assertRaises(RuntimeError):
            materialize_multisymbol_session_source("not the frozen runner")
        with self.assertRaises(RuntimeError):
            materialize_runner_source("not the frozen runner")


if __name__ == "__main__":
    unittest.main()
