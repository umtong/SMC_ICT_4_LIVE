#!/usr/bin/env python3
"""Run frozen RAHTF campaign with the event-aware forensic ledger only."""
from __future__ import annotations

import trendrider_rahtf_clean_v3_campaign as campaign
from trade_ledger_forensics_v2 import analyze

# Implementation repair only: the alpha policy, intervals, classification
# thresholds and predeclared predictions remain in the frozen campaign.
campaign.analyze_trades = analyze


if __name__ == "__main__":
    raise SystemExit(campaign.main())
