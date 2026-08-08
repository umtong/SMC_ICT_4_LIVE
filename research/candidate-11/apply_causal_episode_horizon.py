#!/usr/bin/env python3
"""Apply the causal episode-memory hypothesis to Candidate 11.

The initiating liquidity sweep and the later confirmation are treated as one
auction episode only while they remain inside the detector's existing internal
structure memory. The horizon is derived from the configured five-minute
structure and twelve-structure lookback; it is not a separately optimized PnL
threshold.
"""
from __future__ import annotations

from pathlib import Path


def replace_once(path: Path, old: str, new: str, label: str) -> int:
    source = path.read_text(encoding="utf-8")
    if new in source:
        return 0
    count = source.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one migration anchor, found {count}")
    path.write_text(source.replace(old, new, 1), encoding="utf-8")
    return 1


def main() -> None:
    root = Path(__file__).resolve().parent
    logic = root / "logic.py"
    tests = root / "test_logic.py"
    missing = [path.name for path in (logic, tests) if not path.is_file()]
    if missing:
        raise SystemExit(f"causal episode source is incomplete: {missing}")

    changed = 0
    changed += replace_once(
        logic,
        '''        if self.min_net_r <= 0:\n            raise ValueError("min_net_r must be positive")\n\n\n@dataclass(slots=True)\nclass Pool:\n''',
        '''        if self.min_net_r <= 0:\n            raise ValueError("min_net_r must be positive")\n\n    @property\n    def causal_episode_bars(self) -> int:\n        """Bars for which a confirmation remains attributable to its sweep."""\n        return self.internal_tf_bars * self.internal_lookback\n\n\n@dataclass(slots=True)\nclass Pool:\n''',
        "causal episode horizon property",
    )
    changed += replace_once(
        logic,
        '''        if bar.ts_ns > a.pool.trigger_end_ts_ns:\n            self._terminal(a, bar, "SESSION_DECISION_WINDOW_EXPIRED")\n            return None\n        if a.elapsed > self.config.event_expiry_bars and bar.ts_ns >= a.pool.trigger_start_ts_ns:\n            self._terminal(a, bar, "COMPETING_HYPOTHESES_UNRESOLVED")\n            return None\n''',
        '''        if bar.ts_ns > a.pool.trigger_end_ts_ns:\n            self._terminal(a, bar, "SESSION_DECISION_WINDOW_EXPIRED")\n            return None\n        if a.elapsed > self.config.causal_episode_bars and bar.ts_ns >= a.pool.trigger_start_ts_ns:\n            self._terminal(a, bar, "CAUSAL_EPISODE_MEMORY_EXPIRED")\n            return None\n        if a.elapsed > self.config.event_expiry_bars and bar.ts_ns >= a.pool.trigger_start_ts_ns:\n            self._terminal(a, bar, "COMPETING_HYPOTHESES_UNRESOLVED")\n            return None\n''',
        "causal episode terminal",
    )

    test_source = tests.read_text(encoding="utf-8")
    test_name = "test_sweep_causality_expires_with_internal_structure_memory"
    if test_name not in test_source:
        anchor = '''\n\nif __name__ == "__main__":\n    unittest.main()\n'''
        addition = '''\n\nclass TestCausalEpisodeMemory(unittest.TestCase):\n    def test_sweep_causality_expires_with_internal_structure_memory(self) -> None:\n        config = LogicConfig()\n        self.assertEqual(config.causal_episode_bars, config.internal_tf_bars * config.internal_lookback)\n        engine = CausalAuctionEngine(config, "TEST")\n        previous = bar(1, 100.0, 101.0, 99.0, 100.0)\n        trigger = pool("EPISODE", Side.HIGH, 110.0, range_id="R", opposite=90.0)\n        trigger.trigger_start_ts_ns = 0\n        trigger.trigger_end_ts_ns = 1 << 62\n        engine.bars = [previous]\n        engine.true_ranges.extend([1.0] * config.atr_period)\n        engine.volumes.extend([100.0] * config.volume_period)\n        engine._index = 0\n        engine.active = Auction(\n            pool=trigger,\n            sweep=bar(2, 109.0, 111.0, 108.0, 110.5),\n            sweep_index=0,\n            atr=1.0,\n            internal_level=105.0,\n            sweep_extreme=111.0,\n            rejection_seed=False,\n            acceptance_seed=False,\n            elapsed=config.causal_episode_bars,\n        )\n        plan = engine.on_bar(bar(MINUTE_NS, 100.0, 101.0, 99.0, 100.0))\n        self.assertIsNone(plan)\n        self.assertIsNone(engine.active)\n        self.assertEqual(engine.skips["CAUSAL_EPISODE_MEMORY_EXPIRED"], 1)\n        self.assertEqual(engine.events[-1].reason_code, "CAUSAL_EPISODE_MEMORY_EXPIRED")\n\n\nif __name__ == "__main__":\n    unittest.main()\n'''
        if test_source.count(anchor) != 1:
            raise SystemExit("causal episode regression-test anchor is not unique")
        tests.write_text(test_source.replace(anchor, addition, 1), encoding="utf-8")
        changed += 1

    print(f"causal episode-memory migrations applied: {changed}")


if __name__ == "__main__":
    main()
