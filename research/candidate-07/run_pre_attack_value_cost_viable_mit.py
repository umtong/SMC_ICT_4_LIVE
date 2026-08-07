#!/usr/bin/env python3
"""Run the frozen pre-attack-value scenario with causal cost viability + MIT."""
from __future__ import annotations

import backtest_pre_attack_value as candidate
from strategy_event_signal_cost_viable import Candidate07CostViableMITStrategy


candidate.Candidate07EventSignalStrategy = Candidate07CostViableMITStrategy


if __name__ == "__main__":
    raise SystemExit(candidate.main())
