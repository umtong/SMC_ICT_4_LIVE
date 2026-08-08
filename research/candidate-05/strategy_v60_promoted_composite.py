#!/usr/bin/env python3
"""Candidate 05 v60: cooperative combinations of independently promoted families.

No component is enabled by this module itself. The active shim selects only
families whose frozen out-of-sample decision passed. Every class is a cooperative
multiple-inheritance diamond over the same v46 Nautilus execution core:

* v55: spot-led information repricing continuation;
* v56: early-flow phase gate on the negative first-retrace branch;
* v58: forced perpetual/spot basis normalization.

Each component already calls ``super()`` in every overlapping hook, so one bar
reaches the v46 core exactly once, then the independent post-bar observers run
in MRO order. Order creation, risk sizing, fees, slippage, portfolio and NAV
remain owned by the inherited v46/Nautilus path.
"""
from __future__ import annotations

from strategy import LiquidityResponseConfig
from strategy_v55_spot_price_discovery import SpotLedPriceDiscoveryStrategy
from strategy_v56_early_flow_retrace import EarlyFlowFirstRetraceStrategy
from strategy_v58_forced_basis_reversion import ForcedBasisReversionStrategy


class SpotAndEarlyFlowComposite(
    SpotLedPriceDiscoveryStrategy,
    EarlyFlowFirstRetraceStrategy,
):
    """v55 + v56."""


class EarlyFlowAndBasisComposite(
    ForcedBasisReversionStrategy,
    EarlyFlowFirstRetraceStrategy,
):
    """v56 + v58."""


class SpotAndBasisComposite(
    SpotLedPriceDiscoveryStrategy,
    ForcedBasisReversionStrategy,
):
    """v55 + v58."""


class SpotEarlyFlowAndBasisComposite(
    SpotLedPriceDiscoveryStrategy,
    ForcedBasisReversionStrategy,
    EarlyFlowFirstRetraceStrategy,
):
    """v55 + v56 + v58."""


__all__ = [
    "EarlyFlowAndBasisComposite",
    "LiquidityResponseConfig",
    "SpotAndBasisComposite",
    "SpotAndEarlyFlowComposite",
    "SpotEarlyFlowAndBasisComposite",
]
