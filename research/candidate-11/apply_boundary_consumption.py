#!/usr/bin/env python3
"""Apply the single-cause consumed-liquidity research variant.

A completed source-session boundary is one finite liquidity pool. After the
first qualifying trade-through, later candles cannot create a new independent
FAR/AAC hypothesis from the same symbol/range/side.
"""
from __future__ import annotations

from pathlib import Path


def replace_once(path: Path, old: str, new: str, label: str) -> bool:
    source = path.read_text(encoding="utf-8")
    if new in source:
        return False
    count = source.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one migration anchor, found {count}")
    path.write_text(source.replace(old, new, 1), encoding="utf-8")
    return True


def main() -> None:
    root = Path(__file__).resolve().parent
    engine = root / "complex_engine.py"
    tests = root / "test_complex_engine.py"
    if not engine.is_file() or not tests.is_file():
        raise SystemExit("synchronized complex source is not materialized")

    changed = 0
    changed += int(replace_once(
        engine,
        '''        self._active: dict[str, _Episode] = {}\n        self.events: list[dict[str, object]] = []\n''',
        '''        self._active: dict[str, _Episode] = {}\n        # A completed source-session boundary represents one finite liquidity\n        # pool. Once that side is first traded through, later candles cannot\n        # manufacture a new independent hypothesis from the same consumed pool.\n        self._consumed_boundaries: set[tuple[object, ...]] = set()\n        self.events: list[dict[str, object]] = []\n''',
        "consumed boundary state",
    ))
    changed += int(replace_once(
        engine,
        '''    def _start_episode(\n        self,\n''',
        '''    @staticmethod\n    def _boundary_key(\n        symbol: str,\n        context: AuctionContext,\n        side: BoundarySide,\n    ) -> tuple[object, ...]:\n        return (\n            symbol,\n            context.source_session,\n            context.target_session,\n            context.source_low,\n            context.source_high,\n            context.valid_until_ns,\n            side.value,\n        )\n\n    def _start_episode(\n        self,\n''',
        "boundary identity",
    ))
    changed += int(replace_once(
        engine,
        '''    ) -> None:\n        extreme = bar.high if side == BoundarySide.HIGH else bar.low\n        episode = _Episode(\n''',
        '''    ) -> None:\n        boundary_key = self._boundary_key(symbol, context, side)\n        if boundary_key in self._consumed_boundaries:\n            raise RuntimeError("consumed boundary cannot start a new episode")\n        self._consumed_boundaries.add(boundary_key)\n        extreme = bar.high if side == BoundarySide.HIGH else bar.low\n        episode = _Episode(\n''',
        "consume first traded-through boundary",
    ))
    changed += int(replace_once(
        engine,
        '''            for side, crossed in ((BoundarySide.HIGH, high), (BoundarySide.LOW, low)):\n                if not crossed:\n                    continue\n                evidence = self.market_complex.evaluate(observations, symbol=symbol, side=side)\n''',
        '''            for side, crossed in ((BoundarySide.HIGH, high), (BoundarySide.LOW, low)):\n                if not crossed:\n                    continue\n                boundary_key = self._boundary_key(symbol, context, side)\n                if boundary_key in self._consumed_boundaries:\n                    self._skip("SOURCE_BOUNDARY_ALREADY_CONSUMED")\n                    continue\n                evidence = self.market_complex.evaluate(observations, symbol=symbol, side=side)\n''',
        "block repeated boundary hypotheses",
    ))

    test_name = "test_completed_source_boundary_is_consumed_only_once"
    test_source = tests.read_text(encoding="utf-8")
    if test_name not in test_source:
        anchor = '''    def test_aac_uses_breadth_and_separate_frozen_impulse(self):\n'''
        addition = '''    def test_completed_source_boundary_is_consumed_only_once(self):\n        engine = ComplexSCDAMEngine(EngineConfig(min_net_r=0.1))\n        self.warm(engine)\n        contexts = {symbol: context() for symbol in SYMBOLS}\n        first = {symbol: bar(symbol, 31, 95, 99, 94, 95) for symbol in SYMBOLS}\n        first["BTCUSDT"] = bar("BTCUSDT", 31, 99, 103, 98, 99, 0.4)\n        self.assertEqual(engine.on_snapshot(first, contexts), [])\n        self.assertIn("BTCUSDT", engine._active)\n\n        # The completed ASIA high was consumed even when the local episode\n        # terminates without producing a trade plan.\n        engine._active.pop("BTCUSDT")\n        repeated = {symbol: bar(symbol, 32, 95, 99, 94, 95) for symbol in SYMBOLS}\n        repeated["BTCUSDT"] = bar("BTCUSDT", 32, 99, 104, 98, 99, 0.4)\n        self.assertEqual(engine.on_snapshot(repeated, contexts), [])\n        self.assertNotIn("BTCUSDT", engine._active)\n        self.assertEqual(engine.skip_reasons["SOURCE_BOUNDARY_ALREADY_CONSUMED"], 1)\n\n        # A newly completed source range defines a new finite pool.\n        next_contexts = {\n            symbol: AuctionContext(\n                "ASIA", "LONDON", 91.0, 101.0, 80.0, 120.0, 300 * MINUTE_NS,\n            )\n            for symbol in SYMBOLS\n        }\n        fresh = {symbol: bar(symbol, 33, 96, 100, 95, 96) for symbol in SYMBOLS}\n        fresh["BTCUSDT"] = bar("BTCUSDT", 33, 100, 104, 99, 100, 0.4)\n        self.assertEqual(engine.on_snapshot(fresh, next_contexts), [])\n        self.assertIn("BTCUSDT", engine._active)\n\n'''
        if test_source.count(anchor) != 1:
            raise SystemExit("consumed-boundary regression-test anchor is not unique")
        tests.write_text(test_source.replace(anchor, addition + anchor, 1), encoding="utf-8")
        changed += 1

    print(f"consumed-liquidity research migration applied: {changed}")


if __name__ == "__main__":
    main()
