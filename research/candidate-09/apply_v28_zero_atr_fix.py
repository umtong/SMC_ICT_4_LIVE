#!/usr/bin/env python3
"""Ignore mathematically undefined zero-ATR observations in frozen v28.

This is an implementation guard only. No market state, threshold, target, stop,
cost, risk fraction, evaluation date, or ablation is changed. A normalized
opening-drive displacement cannot be defined when the completed historical ATR
window is exactly zero, so that observation must not create or resolve a signal.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parent / "v28_opening_drive"
ENGINE = ROOT / "state_engine.py"
TEST = ROOT / "tests" / "test_state_engine.py"


def replace_once(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    if new in text:
        return
    if old not in text:
        raise RuntimeError(f"v28 zero-ATR patch contract not found in {path}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


replace_once(
    ENGINE,
    "        if atr is not None:\n",
    "        if atr is not None and atr > 0.0:\n",
)

text = TEST.read_text(encoding="utf-8")
method = '''
    def test_zero_historical_atr_is_ignored_without_division_or_signal(self):
        e=LiquidityStateEngine(config())
        for i in range(1,481):
            if i==1:
                o=c=100.0;h=101.0;l=99.0
            else:
                o=h=l=c=100.0
            e.on_bar(FlowBar(i*MINUTE_NS,o,h,l,c,100.0,50.0,10))
        result=e.on_bar(bar(481,100.0,101.5,100.0,101.4,200,160))
        self.assertIsNone(result.signal)
'''
if "test_zero_historical_atr_is_ignored_without_division_or_signal" not in text:
    marker="\n    def test_two_closes_edge_hold_and_reexpansion_create_causal_buy(self):\n"
    if marker not in text:
        raise RuntimeError("v28 zero-ATR test insertion contract not found")
    TEST.write_text(text.replace(marker, method+marker, 1), encoding="utf-8")

print(f"patched {ENGINE} and {TEST}")
