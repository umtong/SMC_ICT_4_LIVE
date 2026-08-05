#!/usr/bin/env python3
"""Patch the pinned Nautilus engine configuration for multi-segment suites."""

from pathlib import Path

path = Path(__file__).with_name("nautilus_backtest.py")
text = path.read_text(encoding="utf-8")
old = 'config=BacktestEngineConfig(logging=LoggingConfig(log_level="ERROR")),'
new = 'config=BacktestEngineConfig(bypass_logging=True),'
if text.count(old) != 1:
    raise SystemExit(f"expected exactly one engine logging configuration, found {text.count(old)}")
path.write_text(text.replace(old, new, 1), encoding="utf-8")
