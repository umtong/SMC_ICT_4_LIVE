#!/usr/bin/env python3
"""Candidate 05 v36: block local reversals during common market repricing."""
from __future__ import annotations

import math
from typing import Any

from nautilus_trader.model.data import Bar

from cross_asset_repricing_context import SHARED_CROSS_ASSET_CONTEXT
from cross_asset_repricing_logic import PeerAuctionState
from cross_asset_repricing_logic import systemic_repricing_decision
from depth_logic import DIRECTIONAL_DEPTH_MIN
from strategy_base import LiquidityResponseConfig
from strategy_v26 import ScenarioValidEntryStrategy


REVERSAL_BRANCH_PREFIX = "TAIL_FLOW_"


def symbol_from_instrument(value: Any) -> str:
    text = str(value)
    if "-PERP" not in text:
        raise ValueError(f"unexpected project instrument id: {text}")
    return text.split("-PERP", 1)[0]


class SystemicRepricingGateMixin:
    """Use prior-completed peers only as a causal reversal veto.

    A local liquidity rejection remains necessary. This mixin changes no local
    detector, CHoCH, entry, stop, target, cost or size. It vetoes only inherited
    ``TAIL_FLOW_*`` reversal orders when at least two of the other three project
    instruments confirmed efficient repricing in the opposite direction on
    their latest strictly earlier completed minute.
    """

    def __init__(self, config: LiquidityResponseConfig) -> None:
        self.cross_asset_symbol = symbol_from_instrument(config.instrument_id)
        super().__init__(config)  # type: ignore[misc]
        self.diagnostics.update(
            {
                "cross_asset_states_published": 0,
                "cross_asset_reversal_evaluations": 0,
                "cross_asset_reversal_blocks": 0,
                "cross_asset_reversal_allows": 0,
                "cross_asset_insufficient_peer_states": 0,
                "cross_asset_same_timestamp_states_used": 0,
            },
        )

    def on_bar(self, bar: Bar) -> None:
        super().on_bar(bar)  # type: ignore[misc]
        self._publish_completed_cross_asset_state()

    def _publish_completed_cross_asset_state(self) -> None:
        if len(self.bars) < 2:
            return
        row = self.bars[-1]
        ts = int(row["ts"])
        feature = self.current_feature
        if feature is None or not bool(feature.get("feature_ready", False)):
            return
        observed = int(feature.get("observed_time_ns", 0))
        age_seconds = (ts - observed) / 1_000_000_000
        if age_seconds < -1e-9 or age_seconds > self.config.feature_max_age_seconds:
            return
        atr = self._atr()
        if not math.isfinite(atr) or atr <= 0.0:
            return
        previous_close = float(self.bars[-2]["close"])
        state = PeerAuctionState(
            symbol=self.cross_asset_symbol,
            ts_event=ts,
            return_atr=(float(row["close"]) - previous_close) / atr,
            flow_3m=self._feature("flow_3m"),
            efficiency_60s=self._feature("efficiency_60s"),
            depth_imbalance=self._feature("depth_imbalance_1"),
        )
        SHARED_CROSS_ASSET_CONTEXT.publish(state)
        self.diagnostics["cross_asset_states_published"] += 1

    def _submit_price_capped_bracket(self, *args: Any, **kwargs: Any) -> bool:
        branch = str(kwargs.get("branch", ""))
        if not branch.startswith(REVERSAL_BRANCH_PREFIX):
            return bool(super()._submit_price_capped_bracket(*args, **kwargs))  # type: ignore[misc]

        armed = kwargs.get("armed")
        row = kwargs.get("row") or {}
        side = int(getattr(getattr(armed, "setup", None), "side", 0))
        current_ts = int(row.get("ts", self.bars[-1]["ts"]))
        peers = SHARED_CROSS_ASSET_CONTEXT.prior_peer_states(
            current_symbol=self.cross_asset_symbol,
            current_ts=current_ts,
        )
        decision = systemic_repricing_decision(
            trade_side=side,
            current_symbol=self.cross_asset_symbol,
            current_ts=current_ts,
            peer_states=peers,
            minimum_return_atr=self.config.acceptance_close_atr,
            minimum_efficiency=self.config.acceptance_efficiency_min,
            minimum_directional_depth=DIRECTIONAL_DEPTH_MIN,
            maximum_age_ns=int(self.config.feature_max_age_seconds * 1_000_000_000),
        )
        self.diagnostics["cross_asset_reversal_evaluations"] += 1
        if len(decision.eligible_peers) < 2:
            self.diagnostics["cross_asset_insufficient_peer_states"] += 1
        if not decision.blocked:
            self.diagnostics["cross_asset_reversal_allows"] += 1
            return bool(super()._submit_price_capped_bracket(*args, **kwargs))  # type: ignore[misc]

        self.diagnostics["cross_asset_reversal_blocks"] += 1
        details = {
            "cross_asset_policy": "PRIOR_COMPLETED_TWO_OF_THREE_SYSTEMIC_REPRICING_VETO",
            "local_symbol": self.cross_asset_symbol,
            "proposed_trade_side": side,
            "systemic_repricing_direction": decision.repricing_direction,
            "eligible_peers": list(decision.eligible_peers),
            "confirming_peers": list(decision.confirming_peers),
            "peer_states": [
                {
                    "symbol": state.symbol,
                    "ts_event": state.ts_event,
                    "age_ns": current_ts - state.ts_event,
                    "return_atr": state.return_atr,
                    "flow_3m": state.flow_3m,
                    "efficiency_60s": state.efficiency_60s,
                    "depth_imbalance": state.depth_imbalance,
                }
                for state in peers
            ],
        }
        if armed is not None:
            armed.details.update(details)
        if getattr(self, "armed_entry_path", None) is armed:
            self._expire_armed_entry(
                row,
                "TWO_PEERS_CONFIRMED_OPPOSITE_SYSTEMIC_REPRICING",
            )
        elif armed is not None:
            self._transition(
                armed.setup.scenario_id,
                "CROSS_ASSET_REPRICING_VETO",
                current_ts,
                current_ts,
                "CLOSED",
                "TWO_PEERS_CONFIRMED_OPPOSITE_SYSTEMIC_REPRICING",
                float(row.get("close", 0.0)),
                details,
            )
        return False


class SystemicRepricingGateStrategy(
    SystemicRepricingGateMixin,
    ScenarioValidEntryStrategy,
):
    """Importable single-symbol class; peer gate activates only in shared node."""


__all__ = [
    "REVERSAL_BRANCH_PREFIX",
    "SystemicRepricingGateMixin",
    "SystemicRepricingGateStrategy",
    "symbol_from_instrument",
]
