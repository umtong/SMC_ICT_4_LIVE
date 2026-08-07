#!/usr/bin/env python3
"""Run the pre-attack-value replay with market-if-touched take profit."""
from __future__ import annotations

import backtest_pre_attack_value as candidate
from strategy_event_signal_mit import Candidate07MITSerializedStrategy


# Change only the take-profit child order type. Signal discovery, timestamps,
# entry, stop, target price, position slot, NAV risk sizing, fees, funding,
# slippage model and metrics remain owned by the frozen baseline replay.
candidate.Candidate07EventSignalStrategy = Candidate07MITSerializedStrategy


if __name__ == "__main__":
    raise SystemExit(candidate.main())
