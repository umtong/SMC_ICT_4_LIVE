#!/usr/bin/env python3
"""Make liquidation path summaries robust when every plan is invalid."""

from pathlib import Path

path = Path(__file__).with_name("liquidation_path_diagnostics.py")
text = path.read_text(encoding="utf-8")
old = '''        "cost_dominated_paths": int((valid.get("price_risk_fraction", 1.0) < 0.65).sum()),
        "insufficient_net_rr_paths": int((valid.get("net_reward_risk", 2.0) < 1.20).sum()),
'''
new = '''        "cost_dominated_paths": (
            int((valid["price_risk_fraction"] < 0.65).sum())
            if "price_risk_fraction" in valid.columns
            else 0
        ),
        "insufficient_net_rr_paths": (
            int((valid["net_reward_risk"] < 1.20).sum())
            if "net_reward_risk" in valid.columns
            else 0
        ),
'''
count = text.count(old)
if count != 1:
    raise SystemExit(f"expected one liquidation summary match, found {count}")
path.write_text(text.replace(old, new, 1), encoding="utf-8")
