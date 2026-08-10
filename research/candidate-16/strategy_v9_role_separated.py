"""Candidate 16 v9: role-separated residual-dislocation state.

v8 proved that a strictly later convergence leg can be kept separate from the
state bar, but inherited v52 still required tail flow and depth before a state
could exist. That made the same microstructure family serve both state
selection and later confirmation, and in the first untouched week it stopped
all three OI-qualified residual inflections before the v8 transition was ever
observed.

v9 changes one economic role only:

- robust four-asset residual extreme, first inflection and non-expanding OI
  define the latent idiosyncratic-dislocation state;
- state-bar flow/depth/efficiency/burst are recorded but cannot admit or reject
  the state;
- no order can be created on the state bar;
- the unchanged v8 strictly-later residual, price, relative-return, flow and
  depth transition decides whether a new convergence leg is tradeable;
- v8 execution, stop, natural target, costs, 3% current-NAV risk and the audited
  one-account global slot remain unchanged.

The v8 execution branch name is deliberately reused so this experiment changes
state semantics only, not order construction or position accounting.
"""
from __future__ import annotations

import math
import sys
from typing import Any

from candidate_v8 import Candidate16V8Strategy
from strategy_base import PendingSetup
from strategy_v41_competing_auction import _construct, _finite
from strategy_v52_cross_sectional_residual import ROBUST_Z, _robust_z


_V8_MODULE = sys.modules[Candidate16V8Strategy.__module__]
V8_STATE_BRANCH = str(getattr(_V8_MODULE, "V8_STATE_BRANCH"))
V8_TRADE_BRANCH = str(getattr(_V8_MODULE, "V8_TRADE_BRANCH"))
V8_MAX_WAIT_BARS = int(getattr(_V8_MODULE, "V8_MAX_WAIT_BARS"))

V9_STATE_DEFINITION = "RESIDUAL_INFLECTION_PLUS_OI_NON_EXPANSION"
V9_EXECUTION_BRANCH = V8_TRADE_BRANCH


class Candidate16V9RoleSeparatedStrategy(Candidate16V8Strategy):
    """Freeze OI-qualified residual states before any microstructure gate."""

    def __init__(self, config: Any) -> None:
        super().__init__(config)
        self.diagnostics.update(
            {
                "candidate16_v9_oi_qualified_state_candidates": 0,
                "candidate16_v9_states_frozen": 0,
                "candidate16_v9_state_features_complete": 0,
                "candidate16_v9_state_flow_aligned": 0,
                "candidate16_v9_state_tail_acceleration_aligned": 0,
                "candidate16_v9_state_depth_aligned": 0,
                "candidate16_v9_state_efficiency_pass": 0,
                "candidate16_v9_state_notional_burst_pass": 0,
                "candidate16_v9_original_state_microstructure_pass": 0,
            },
        )

    def _maybe_arm_cross_sectional(
        self,
        row: dict[str, float | int],
    ) -> None:
        """Copy v52 only through OI, then freeze without a flow/depth gate."""
        if (
            self.pending is not None
            or self.entry_pending
            or not self.portfolio.is_flat(self.config.instrument_id)
            or not self._in_evaluation(int(row["ts"]))
            or not self._features_ready(int(row["ts"]))
            or self.bar_index - self.last_entry_index < self.config.cooldown_bars
            or self.bar_index - self.v52_last_signal_index
            < self.config.rejection_confirmation_bars
            or len(self.bars) < max(self.config.atr_period + 2, 8)
        ):
            return

        peer = self._peer_state(int(row["ts"]))
        if peer is None:
            return
        peer5, peer1 = peer
        own5 = self._own_normalized_return(5)
        own1 = self._own_normalized_return(1)
        if not math.isfinite(own5) or not math.isfinite(own1):
            return

        residual = own5 - peer5
        z = _robust_z(self.v52_residuals, residual)
        previous = self.v52_previous_residual
        self.v52_residuals.append(residual)
        self.v52_previous_residual = residual
        self.diagnostics["v52_peer_context_ready"] += 1

        if not math.isfinite(z) or abs(z) < ROBUST_Z:
            return
        self.diagnostics["v52_extremes"] += 1
        if (
            not math.isfinite(previous)
            or residual == 0.0
            or previous == 0.0
            or math.copysign(1.0, residual) != math.copysign(1.0, previous)
            or abs(residual) >= abs(previous)
        ):
            return

        side = -1 if residual > 0.0 else 1
        if side * (own1 - peer1) <= 0.0 or side * peer1 < -0.25:
            return
        self.diagnostics["v52_inflections"] += 1

        oi = _finite(self._feature("oi_change_15m"))
        if not math.isfinite(oi) or oi > 0.0:
            return
        self.diagnostics["v52_oi_contraction_pass"] += 1
        self.diagnostics["candidate16_v9_oi_qualified_state_candidates"] += 1

        values = {
            name: _finite(self._feature(name))
            for name in (
                "flow_15s",
                "flow_60s",
                "flow_3m",
                "depth_imbalance_1",
                "efficiency_60s",
                "notional_burst",
            )
        }
        features_complete = all(math.isfinite(value) for value in values.values())
        flow_aligned = features_complete and side * values["flow_15s"] > 0.0
        tail_acceleration_aligned = (
            features_complete
            and side * (values["flow_15s"] - values["flow_60s"]) > 0.0
        )
        depth_aligned = (
            features_complete and side * values["depth_imbalance_1"] > 0.0
        )
        efficiency_pass = features_complete and values["efficiency_60s"] >= 0.10
        burst_pass = features_complete and values["notional_burst"] >= 1.0
        legacy_microstructure_pass = bool(
            features_complete
            and flow_aligned
            and tail_acceleration_aligned
            and depth_aligned
            and efficiency_pass
            and burst_pass
        )

        self.diagnostics["candidate16_v9_state_features_complete"] += int(
            features_complete,
        )
        self.diagnostics["candidate16_v9_state_flow_aligned"] += int(flow_aligned)
        self.diagnostics["candidate16_v9_state_tail_acceleration_aligned"] += int(
            tail_acceleration_aligned,
        )
        self.diagnostics["candidate16_v9_state_depth_aligned"] += int(depth_aligned)
        self.diagnostics["candidate16_v9_state_efficiency_pass"] += int(
            efficiency_pass,
        )
        self.diagnostics["candidate16_v9_state_notional_burst_pass"] += int(
            burst_pass,
        )
        self.diagnostics["candidate16_v9_original_state_microstructure_pass"] += int(
            legacy_microstructure_pass,
        )
        # Preserve the inherited diagnostic meaning: these counters describe
        # the old v52 state gate, not all v9 states.
        if legacy_microstructure_pass:
            self.diagnostics["v52_flow_depth_pass"] += 1
            self.diagnostics["v52_setups"] += 1

        atr = _finite(self._atr())
        recent = list(self.bars)[-6:-1]
        if not math.isfinite(atr) or atr <= 0.0 or not recent:
            return
        structure = (
            max(float(item["high"]) for item in recent)
            if side > 0
            else min(float(item["low"]) for item in recent)
        )
        extreme = float(row["low"]) if side > 0 else float(row["high"])

        self.scenario_counter += 1
        scenario_id = f"v9-{self.v47_symbol}-{self.scenario_counter:07d}"
        details = {
            "branch": "CANDIDATE16_V9_ROLE_SEPARATED_RESIDUAL_STATE",
            "symbol": self.v47_symbol,
            "side": side,
            "residual": residual,
            "residual_z": z,
            "own_normalized_5m": own5,
            "peer_normalized_5m": peer5,
            "own_normalized_1m": own1,
            "peer_normalized_1m": peer1,
            "flow_15s": values["flow_15s"],
            "flow_60s": values["flow_60s"],
            "flow_3m": values["flow_3m"],
            "efficiency_60s": values["efficiency_60s"],
            "notional_burst": values["notional_burst"],
            "depth_imbalance_1": values["depth_imbalance_1"],
            "oi_change_15m": oi,
            "pool_source": "FOUR_ASSET_ROBUST_RESIDUAL",
            "pool_age_minutes": 5,
            "penetration_atr": abs(residual),
            "candidate16_v9_state_definition": V9_STATE_DEFINITION,
            "candidate16_v9_state_bar_microstructure": {
                "features_complete": features_complete,
                "flow_aligned": flow_aligned,
                "tail_acceleration_aligned": tail_acceleration_aligned,
                "depth_aligned": depth_aligned,
                "efficiency_pass": efficiency_pass,
                "notional_burst_pass": burst_pass,
                "legacy_v52_gate_pass": legacy_microstructure_pass,
                "role": "DIAGNOSTIC_ONLY",
            },
            "candidate16_v9_state_change": (
                "REMOVED_STATE_BAR_FLOW_DEPTH_EFFICIENCY_BURST_ADMISSION_GATE"
            ),
            "candidate16_v8_state": "FOUR_ASSET_RESIDUAL_DISLOCATION",
            "v8_initial_residual": residual,
            "v8_initial_abs_residual": abs(residual),
            "v8_state_close": float(row["close"]),
            "v8_state_open": float(row["open"]),
            "v8_state_index": self.bar_index,
            "v8_state_ts": int(row["ts"]),
            "v8_state_expires_index": self.bar_index + V8_MAX_WAIT_BARS,
            "v8_state_evidence_roles": {
                "residual_inflection_and_oi": "STATE_ONLY",
                "state_bar_flow_depth": "DIAGNOSTIC_ONLY",
                "strictly_later_residual_price_flow_depth": "CONFIRMATION_ONLY",
            },
            "v8_no_order_on_state_bar": True,
        }
        setup = _construct(
            PendingSetup,
            scenario_id=scenario_id,
            branch=V8_STATE_BRANCH,
            side=side,
            swept_kind="LOW" if side > 0 else "HIGH",
            pool_id=f"cross-sectional-v9-{self.v47_symbol}-{int(row['ts'])}",
            pool_level=float(row["close"]),
            created_index=self.bar_index,
            created_ts=int(row["ts"]),
            expires_index=self.bar_index + V8_MAX_WAIT_BARS,
            sweep_extreme=extreme,
            structure=structure,
            atr=atr,
            hold_count=0,
            retrace_armed=False,
            details=details,
        )
        self.pending = setup
        self.v52_last_signal_index = self.bar_index
        self.diagnostics["rejection_setups"] += 1
        self.diagnostics["candidate16_v8_states_frozen"] += 1
        self.diagnostics["candidate16_v9_states_frozen"] += 1
        self._transition(
            scenario_id,
            "CROSS_SECTIONAL_RESIDUAL_STATE_FROZEN",
            int(row["ts"]),
            int(row["ts"]),
            "WAITING_FOR_LATER_CONVERGENCE_LEG",
            "V9_OI_QUALIFIED_STATE_FROZEN_BEFORE_LATER_MICROSTRUCTURE",
            float(row["close"]),
            details,
        )


__all__ = [
    "Candidate16V9RoleSeparatedStrategy",
    "V9_EXECUTION_BRANCH",
    "V9_STATE_DEFINITION",
]
