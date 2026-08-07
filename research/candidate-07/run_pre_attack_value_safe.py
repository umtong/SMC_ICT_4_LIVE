#!/usr/bin/env python3
"""Run the pre-attack-value replay with serialized research events."""
from __future__ import annotations

import backtest_pre_attack_value as candidate
from strategy_event_signal_safe import Candidate07SerializedEventStrategy


# Replace only the strategy class used by the replay.  Configuration, signal
# discovery, bars, orders, risk, fee model, funding and metrics are unchanged.
candidate.Candidate07EventSignalStrategy = Candidate07SerializedEventStrategy


if __name__ == "__main__":
    raise SystemExit(candidate.main())
