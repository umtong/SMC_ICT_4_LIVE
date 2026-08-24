#!/usr/bin/env python3
"""Run the canonical EasyChart RE1 bot in one four-symbol continuous account."""
import runpy

if __name__ == "__main__":
    runpy.run_module("run_mtf_backtest_re1_causal_channel", run_name="__main__")
