#!/usr/bin/env python3
"""Apply the single-cause AAC independent-draw research ablation."""
from __future__ import annotations

from pathlib import Path


def replace_once(path: Path, old: str, new: str, label: str) -> int:
    source = path.read_text(encoding="utf-8")
    if new in source:
        return 0
    count = source.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one anchor, found {count}")
    path.write_text(source.replace(old, new, 1), encoding="utf-8")
    return 1


def apply(root: Path) -> int:
    logic = root / "logic.py"
    tests = root / "test_logic.py"
    ledger = root / "RESEARCH_DECISION_LEDGER.md"
    missing = [p.name for p in (logic, tests, ledger) if not p.is_file()]
    if missing:
        raise SystemExit(f"AAC ablation inputs missing: {missing}")

    changed = 0
    changed += replace_once(
        logic,
        '''    framed_draw_score: float = 0.0\n    framed_high_hazard: float = 0.0\n    framed_low_hazard: float = 0.0\n''',
        '''    framed_draw_score: float = 0.0\n    framed_draw_method: str = "UNRESOLVED"\n    framed_high_hazard: float = 0.0\n    framed_low_hazard: float = 0.0\n''',
        "auction draw-method state",
    )
    changed += replace_once(
        logic,
        '''            framed_draw_score=draw_score,\n            framed_high_hazard=high_hazard,\n''',
        '''            framed_draw_score=draw_score,\n            framed_draw_method=draw_method,\n            framed_high_hazard=high_hazard,\n''',
        "initial draw-method capture",
    )
    changed += replace_once(
        logic,
        '''                    a.framed_draw_side = draw_side\n                    a.framed_draw_score = draw_score\n                    a.framed_high_hazard = high_hazard\n''',
        '''                    a.framed_draw_side = draw_side\n                    a.framed_draw_score = draw_score\n                    a.framed_draw_method = draw_method\n                    a.framed_high_hazard = high_hazard\n''',
        "cascade draw-method capture",
    )
    changed += replace_once(
        logic,
        '''        if target_pool is None or target_level is None:\n            self._terminal(a, bar, "CONTINUATION_TARGET_NO_LONGER_LIVE")\n            return None\n        target_hazard = a.framed_high_hazard if side == Side.HIGH else a.framed_low_hazard\n''',
        '''        if target_pool is None or target_level is None:\n            self._terminal(a, bar, "CONTINUATION_TARGET_NO_LONGER_LIVE")\n            return None\n        # A source range may define the boundary, but using that same range's\n        # close/flow both to create and confirm continuation is circular.\n        if a.framed_draw_method != "EXTERNAL_HAZARD_DOMINANCE":\n            self._terminal(a, bar, "AAC_REQUIRES_INDEPENDENT_EXTERNAL_DRAW")\n            return None\n        target_hazard = a.framed_high_hazard if side == Side.HIGH else a.framed_low_hazard\n''',
        "AAC independent-draw approval",
    )
    changed += replace_once(
        logic,
        '''                "draw_side": side.value,\n                "draw_score": context,\n                "target_pool": target_pool.scenario_id,\n''',
        '''                "draw_side": side.value,\n                "draw_score": context,\n                "draw_method": a.framed_draw_method,\n                "target_pool": target_pool.scenario_id,\n''',
        "AAC event draw-method evidence",
    )
    changed += replace_once(
        logic,
        '''                "draw_score": a.draw_score,\n                "zone_low": a.zone_low,\n''',
        '''                "draw_score": a.draw_score,\n                "draw_method": a.framed_draw_method,\n                "zone_low": a.zone_low,\n''',
        "plan draw-method evidence",
    )

    test_anchor = '''    def test_insufficient_costed_r_is_terminal_not_tuned(self) -> None:\n        auction, confirmation = self._auction(Direction.LONG)\n        auction.target_price = 108.0\n        plan = self.engine._costed_limit_plan(auction, confirmation, "TEST")\n        self.assertIsNone(plan)\n        self.assertIsNone(self.engine.active)\n        self.assertEqual(self.engine.skips["INSUFFICIENT_COSTED_STRUCTURAL_R"], 1)\n'''
    test_addition = '''    def _aac_ready_auction(self, draw_method: str) -> tuple[Auction, BarObs]:\n        trigger = pool("AAC-HIGH", Side.HIGH, 100.0, range_id="AAC-R", opposite=90.0)\n        target = pool("AAC-TARGET", Side.HIGH, 120.0, strength=3)\n        confirmation = bar(200 * MINUTE_NS, 104.0, 108.0, 103.0, 107.0, buy=75.0)\n        auction = Auction(\n            pool=trigger, sweep=bar(190 * MINUTE_NS, 99.0, 105.0, 98.0, 104.0, buy=75.0),\n            sweep_index=0, atr=10.0, internal_level=98.0, sweep_extreme=105.0,\n            rejection_seed=False, acceptance_seed=True, cascade_count=2,\n            framed_draw_side=Side.HIGH, framed_draw_score=0.50,\n            framed_draw_method=draw_method, framed_high_hazard=0.50, framed_low_hazard=0.10,\n            continuation_target_pool_id=target.scenario_id, continuation_target_level=target.level,\n            last_crossed_level=100.0, pullback_known_index=0, pullback_extreme=101.0,\n            acceptance_impulse_extreme=105.0,\n        )\n        self.engine.pools = [trigger, target]\n        self.engine.active = auction\n        self.engine.bars = [bar(199 * MINUTE_NS, 103.0, 105.0, 102.0, 104.0), confirmation]\n        self.engine._index = 1\n        return auction, confirmation\n\n    def test_aac_rejects_self_confirming_source_range_draw(self) -> None:\n        auction, confirmation = self._aac_ready_auction("SOURCE_RANGE_ACCEPTANCE")\n        plan = self.engine._confirm_aac(auction, confirmation)\n        self.assertIsNone(plan)\n        self.assertIsNone(self.engine.active)\n        self.assertEqual(self.engine.skips["AAC_REQUIRES_INDEPENDENT_EXTERNAL_DRAW"], 1)\n\n    def test_aac_accepts_independent_external_hazard_draw(self) -> None:\n        auction, confirmation = self._aac_ready_auction("EXTERNAL_HAZARD_DOMINANCE")\n        plan = self.engine._confirm_aac(auction, confirmation)\n        self.assertIsNotNone(plan)\n        assert plan is not None\n        self.assertEqual(plan.details["draw_method"], "EXTERNAL_HAZARD_DOMINANCE")\n\n'''
    test_source = tests.read_text(encoding="utf-8")
    if "test_aac_rejects_self_confirming_source_range_draw" not in test_source:
        if test_source.count(test_anchor) != 1:
            raise SystemExit("AAC test anchor is not unique")
        tests.write_text(test_source.replace(test_anchor, test_addition + test_anchor, 1), encoding="utf-8")
        changed += 1

    heading = "## Iteration 8 — independent-draw requirement for AAC"
    ledger_source = ledger.read_text(encoding="utf-8")
    if heading not in ledger_source:
        ledger_source = ledger_source.rstrip() + f'''\n\n{heading}\n\n**Frozen evidence:** the unchanged four-market SCDAM produced five W1 fills\n(80% wins and positive after-cost NAV growth), eleven W2 fills (45.45% wins and\nnegative growth), and one W3 fill (loss). All three filled AAC plans whose draw\nwas created by `SOURCE_RANGE_ACCEPTANCE` stopped out. The W1 AAC winner was\nframed by independent external-liquidity hazard dominance.\n\n**Hypothesis:** source-range close/flow may define an auction boundary but may\nnot also self-confirm continuation. Otherwise the same observation is counted\ntwice. AAC now requires `EXTERNAL_HAZARD_DOMINANCE`; FAR, sessions, targets,\nstops, costs, exact 3% NAV risk, orders and the global slot are unchanged.\n\n**Evaluation order:** rerun frozen W1, then untouched W2 and W3 only if W1\nremains promising. Nautilus account NAV, not first-passage labels, decides.\n'''
        ledger.write_text(ledger_source + "\n", encoding="utf-8")
        changed += 1

    return changed


if __name__ == "__main__":
    root = Path(__file__).resolve().parent
    print(f"AAC independent-draw migrations applied: {apply(root)}")
