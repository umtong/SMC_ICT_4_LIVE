#!/usr/bin/env python3
"""Apply the consumed-liquidity causal research variant.

A completed source-session boundary is one finite liquidity pool. The pool is
consumed at its first qualifying price trade-through, whether or not the model
subsequently obtains FAR/AAC evidence or an executable entry.
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

    source = engine.read_text(encoding="utf-8")
    required = (
        "self._consumed_boundaries",
        "def _boundary_key(",
        "SOURCE_BOUNDARY_ALREADY_CONSUMED",
    )
    missing = [marker for marker in required if marker not in source]
    if missing:
        raise SystemExit(f"first consumed-boundary migration is missing: {missing}")

    changed = 0
    changed += int(replace_once(
        engine,
        '''    ) -> None:\n        boundary_key = self._boundary_key(symbol, context, side)\n        if boundary_key in self._consumed_boundaries:\n            raise RuntimeError("consumed boundary cannot start a new episode")\n        self._consumed_boundaries.add(boundary_key)\n        extreme = bar.high if side == BoundarySide.HIGH else bar.low\n''',
        '''    ) -> None:\n        # Boundary consumption occurs at the first trade-through before the\n        # evidence classifier is evaluated. Episode creation cannot re-arm it.\n        extreme = bar.high if side == BoundarySide.HIGH else bar.low\n''',
        "move consumption before evidence classification",
    ))
    changed += int(replace_once(
        engine,
        '''            if high and low:\n                self._skip("AMBIGUOUS_BOTH_SIDES_RAIDED")\n                continue\n''',
        '''            if high and low:\n                # A wide completed bar physically consumed both finite pools,\n                # even though intrabar ordering is unknowable and no scenario\n                # may be approved from it.\n                for ambiguous_side in (BoundarySide.HIGH, BoundarySide.LOW):\n                    key = self._boundary_key(symbol, context, ambiguous_side)\n                    if key not in self._consumed_boundaries:\n                        self._consumed_boundaries.add(key)\n                        self._event(\n                            symbol,\n                            ts_ns,\n                            "SOURCE_BOUNDARY_CONSUMED",\n                            {\n                                "side": ambiguous_side.value,\n                                "source_session": context.source_session,\n                                "target_session": context.target_session,\n                                "classification": "AMBIGUOUS_BOTH_SIDES_RAIDED",\n                            },\n                        )\n                self._skip("AMBIGUOUS_BOTH_SIDES_RAIDED")\n                continue\n''',
        "consume both sides on ambiguous trade-through",
    ))
    changed += int(replace_once(
        engine,
        '''                boundary_key = self._boundary_key(symbol, context, side)\n                if boundary_key in self._consumed_boundaries:\n                    self._skip("SOURCE_BOUNDARY_ALREADY_CONSUMED")\n                    continue\n                evidence = self.market_complex.evaluate(observations, symbol=symbol, side=side)\n''',
        '''                boundary_key = self._boundary_key(symbol, context, side)\n                if boundary_key in self._consumed_boundaries:\n                    self._skip("SOURCE_BOUNDARY_ALREADY_CONSUMED")\n                    continue\n                # The first price trade-through consumes the pool before model\n                # classification. Insufficient evidence is terminal for this\n                # source-range side rather than permission to retry later.\n                self._consumed_boundaries.add(boundary_key)\n                self._event(\n                    symbol,\n                    ts_ns,\n                    "SOURCE_BOUNDARY_CONSUMED",\n                    {\n                        "side": side.value,\n                        "source_session": context.source_session,\n                        "target_session": context.target_session,\n                        "classification": "PENDING_COMPLEX_EVIDENCE",\n                    },\n                )\n                evidence = self.market_complex.evaluate(observations, symbol=symbol, side=side)\n''',
        "consume first trade-through before evidence",
    ))

    test_source = tests.read_text(encoding="utf-8")
    test_name = "test_insufficient_first_touch_still_consumes_boundary"
    if test_name not in test_source:
        anchor = '''    def test_aac_uses_breadth_and_separate_frozen_impulse(self):\n'''
        addition = '''    def test_insufficient_first_touch_still_consumes_boundary(self):\n        engine = ComplexSCDAMEngine(EngineConfig(min_net_r=0.1))\n        self.warm(engine)\n        contexts = {symbol: context() for symbol in SYMBOLS}\n\n        # All peers raid but close back inside. This is neither idiosyncratic\n        # FAR nor broad-close AAC, yet the physical high-side pool is consumed.\n        first = {symbol: bar(symbol, 31, 99, 103, 98, 99, 0.0) for symbol in SYMBOLS}\n        self.assertEqual(engine.on_snapshot(first, contexts), [])\n        self.assertNotIn("BTCUSDT", engine._active)\n\n        # A later idiosyncratic BTC raid may not reuse that same ASIA high.\n        repeated = {symbol: bar(symbol, 32, 95, 99, 94, 95) for symbol in SYMBOLS}\n        repeated["BTCUSDT"] = bar("BTCUSDT", 32, 99, 104, 98, 99, 0.4)\n        self.assertEqual(engine.on_snapshot(repeated, contexts), [])\n        self.assertNotIn("BTCUSDT", engine._active)\n        self.assertGreaterEqual(\n            engine.skip_reasons["SOURCE_BOUNDARY_ALREADY_CONSUMED"],\n            1,\n        )\n\n'''
        if test_source.count(anchor) != 1:
            raise SystemExit("first-touch consumption test anchor is not unique")
        tests.write_text(test_source.replace(anchor, addition + anchor, 1), encoding="utf-8")
        changed += 1

    print(f"first-touch consumed-liquidity migration applied: {changed}")


if __name__ == "__main__":
    main()
