"""Quarter-hour information pulse -> inventory reset -> defended continuation.

The first ten seconds of a completed UTC quarter-hour minute define context,
not an entry.  At least three project markets must show aligned opening
aggressor imbalance, elevated opening activity, and an aligned full-minute
price response.  The dynamically strongest aligned market becomes the sole
candidate owner.  Entry is considered only after a local counter-directional
pullback, compatible OI/premium state, renewed aggressor flow, displayed-depth
defense, and a price reclaim.  The mature candidate-05 v26 confirmation,
price-capped bracket, exact 3% current-NAV sizing, global slot, and
NautilusTrader execution/accounting remain authoritative.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import math
from typing import Any

from nautilus_trader.model.data import Bar

from quarter_hour_context import QuarterHourConsensus
from quarter_hour_context import QuarterHourPulse
from quarter_hour_context import SHARED_QUARTER_HOUR_CONTEXT
from quarter_hour_context import reset_shared_quarter_hour_context
from strategy_base import LiquidityResponseConfig
from strategy_base import PendingSetup
from strategy_base import _as_float
from strategy_v41_competing_auction import _construct
import strategy_v26 as _v26


MIN_PULLBACK_ATR = 0.35
MAX_PULLBACK_ATR = 2.25
MAX_PRE_PULLBACK_EXTENSION_ATR = 1.50
MIN_REACCEPT_FLOW_15S = 0.04
MIN_REACCEPT_FLOW_60S = 0.02
MIN_REACCEPT_EFFICIENCY = 0.15
MAX_ADVERSE_OI_EXPANSION_5M = 0.002


def _base_class() -> type:
    found = [
        value
        for value in vars(_v26).values()
        if isinstance(value, type)
        and value.__module__ == _v26.__name__
        and value.__name__.endswith("Strategy")
    ]
    if len(found) != 1:
        raise RuntimeError(
            f"expected one v26 strategy, found {[value.__name__ for value in found]}",
        )
    return found[0]


_BASE = _base_class()


def _symbol(instrument_id: Any) -> str:
    return str(instrument_id).split("-")[0].split(".")[0]


def _finite(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return math.nan
    return number if math.isfinite(number) else math.nan


@dataclass(slots=True)
class QuarterHourTransferSetup:
    scenario_id: str
    boundary_ts: int
    side: int
    created_index: int
    expires_index: int
    phase: str
    context_close: float
    context_high: float
    context_low: float
    context_atr: float
    favorable_extreme: float
    pullback_extreme: float
    pullback_start_index: int
    details: dict[str, Any]


class QuarterHourInventoryTransferStrategy(_BASE):
    """Trade only the first defended continuation after a cross-market QH pulse."""

    def __init__(self, config: LiquidityResponseConfig) -> None:
        super().__init__(config)
        self.qh_symbol = _symbol(config.instrument_id)
        self.qh_setup: QuarterHourTransferSetup | None = None
        self.qh_last_consensus_ts = -1
        self.qh_last_boundary_published = -1
        self.qh_claimed_boundary = -1
        self.diagnostics.update(
            {
                "qh_boundaries_published": 0,
                "qh_eligible_local_pulses": 0,
                "qh_consensus_seen": 0,
                "qh_consensus_owned": 0,
                "qh_contexts_armed": 0,
                "qh_contexts_replaced": 0,
                "qh_global_context_conflicts": 0,
                "qh_global_context_releases": 0,
                "qh_pullbacks_started": 0,
                "qh_inventory_adverse": 0,
                "qh_reacceptance_pass": 0,
                "qh_reacceptance_fail": 0,
                "qh_context_expired": 0,
                "qh_context_consumed_without_pullback": 0,
                "qh_pending_setups": 0,
            },
        )

    def on_start(self) -> None:
        if self.qh_symbol == "BTCUSDT":
            reset_shared_quarter_hour_context()
        super().on_start()

    def _detect_sweep(self, row: dict[str, float | int], previous_close: float) -> None:
        # Preserve pool construction and the mature execution path, but disable
        # the inherited entry detector.  QH state is the only scenario source.
        return

    def on_bar(self, bar: Bar) -> None:
        super().on_bar(bar)
        if not self.bars:
            return
        row = self.bars[-1]
        self._publish_quarter_hour_boundary(row)
        self._adopt_latest_consensus(row)
        self._advance_quarter_hour_setup(row)

    def _feature_value(self, name: str) -> float:
        try:
            return _finite(self._feature(name))
        except Exception:
            return math.nan

    def _publish_quarter_hour_boundary(self, row: dict[str, float | int]) -> None:
        ts = int(row["ts"])
        moment = datetime.fromtimestamp(ts / 1_000_000_000, tz=timezone.utc)
        if moment.minute % 15 != 0 or ts == self.qh_last_boundary_published:
            return
        if not self._features_ready(ts):
            return
        atr = _finite(self._atr())
        flow = self._feature_value("flow_open_10s")
        burst = self._feature_value("notional_open_10s_burst")
        ret = self._feature_value("ret_60s_bps")
        efficiency = self._feature_value("efficiency_60s")
        direction = 0 if not math.isfinite(flow) or flow == 0.0 else (1 if flow > 0.0 else -1)
        pulse = QuarterHourPulse(
            symbol=self.qh_symbol,
            ts_event=ts,
            direction=direction,
            flow_open_10s=flow,
            notional_open_10s_burst=burst,
            ret_60s_bps=ret,
            efficiency_60s=efficiency,
            close=float(row["close"]),
            high=float(row["high"]),
            low=float(row["low"]),
            atr=atr,
        )
        SHARED_QUARTER_HOUR_CONTEXT.publish(pulse)
        self.qh_last_boundary_published = ts
        self.diagnostics["qh_boundaries_published"] += 1
        if (
            direction in (-1, 1)
            and math.isfinite(flow)
            and abs(flow) >= 0.08
            and math.isfinite(burst)
            and burst >= 1.15
            and math.isfinite(ret)
            and direction * ret > 0.0
            and math.isfinite(efficiency)
            and efficiency >= 0.08
        ):
            self.diagnostics["qh_eligible_local_pulses"] += 1

    def _adopt_latest_consensus(self, row: dict[str, float | int]) -> None:
        ts = int(row["ts"])
        consensus = SHARED_QUARTER_HOUR_CONTEXT.latest_consensus(before_ts=ts)
        if consensus is None or consensus.boundary_ts <= self.qh_last_consensus_ts:
            return
        self.qh_last_consensus_ts = consensus.boundary_ts
        self.diagnostics["qh_consensus_seen"] += 1
        if consensus.owner != self.qh_symbol:
            return
        self.diagnostics["qh_consensus_owned"] += 1
        if not self._can_arm_context(row):
            return
        if self.qh_setup is not None:
            if self.qh_setup.side == consensus.direction:
                return
            self._close_qh_setup(row, "OPPOSITE_QUARTER_HOUR_CONSENSUS_REPLACED_CONTEXT")
            self.diagnostics["qh_contexts_replaced"] += 1
        if not SHARED_QUARTER_HOUR_CONTEXT.claim(
            owner=self.qh_symbol,
            boundary_ts=consensus.boundary_ts,
            direction=consensus.direction,
        ):
            self.diagnostics["qh_global_context_conflicts"] += 1
            return
        self.qh_claimed_boundary = consensus.boundary_ts
        self._arm_context(consensus, row)

    def _can_arm_context(self, row: dict[str, float | int]) -> bool:
        return (
            self._in_evaluation(int(row["ts"]))
            and not self._funding_blackout(int(row["ts"]))
            and self._features_ready(int(row["ts"]))
            and self.portfolio.is_flat(self.config.instrument_id)
            and not self.entry_pending
            and not bool(getattr(self, "exit_pending", False))
            and self.pending is None
            and self.armed_entry_path is None
            and self.bar_index - self.last_entry_index >= self.config.cooldown_bars
        )

    def _arm_context(
        self,
        consensus: QuarterHourConsensus,
        row: dict[str, float | int],
    ) -> None:
        pulse = consensus.owner_pulse
        if not math.isfinite(pulse.atr) or pulse.atr <= 0.0:
            self._release_qh_claim()
            return
        self.scenario_counter += 1
        scenario_id = f"qhit-{self.scenario_counter:07d}"
        details = {
            "branch": "QH_INFORMATION_TRANSFER_CONTINUATION",
            "boundary_ts": consensus.boundary_ts,
            "direction": consensus.direction,
            "owner": consensus.owner,
            "aligned_symbols": list(consensus.aligned_symbols),
            "aligned_count": len(consensus.aligned_symbols),
            "median_abs_opening_flow": consensus.median_abs_flow,
            "median_opening_burst": consensus.median_burst,
            "owner_score": consensus.owner_score,
            "owner_flow_open_10s": pulse.flow_open_10s,
            "owner_notional_open_10s_burst": pulse.notional_open_10s_burst,
            "owner_ret_60s_bps": pulse.ret_60s_bps,
            "owner_efficiency_60s": pulse.efficiency_60s,
            "context_close": pulse.close,
            "context_high": pulse.high,
            "context_low": pulse.low,
            "context_atr": pulse.atr,
            "causal_policy": "QH_CONTEXT_THEN_INVENTORY_RESET_THEN_DEFENDED_RECLAIM",
        }
        self.qh_setup = QuarterHourTransferSetup(
            scenario_id=scenario_id,
            boundary_ts=consensus.boundary_ts,
            side=consensus.direction,
            created_index=self.bar_index,
            expires_index=self.bar_index + min(int(self.config.max_hold_bars), 240),
            phase="AWAIT_PULLBACK",
            context_close=pulse.close,
            context_high=pulse.high,
            context_low=pulse.low,
            context_atr=pulse.atr,
            favorable_extreme=pulse.high if consensus.direction > 0 else pulse.low,
            pullback_extreme=pulse.low if consensus.direction > 0 else pulse.high,
            pullback_start_index=-1,
            details=details,
        )
        self.diagnostics["qh_contexts_armed"] += 1
        self._transition(
            scenario_id,
            "QH_INFORMATION_CONTEXT_ARMED",
            consensus.boundary_ts,
            int(row["ts"]),
            "AWAIT_PULLBACK",
            "THREE_MARKET_OPENING_IMBALANCE_AND_PRICE_RESPONSE_AGREED",
            pulse.close,
            details,
        )

    def _advance_quarter_hour_setup(self, row: dict[str, float | int]) -> None:
        setup = self.qh_setup
        if setup is None or self.bar_index <= setup.created_index:
            return
        if (
            self.pending is not None
            or self.entry_pending
            or not self.portfolio.is_flat(self.config.instrument_id)
        ):
            return
        ts = int(row["ts"])
        if self.bar_index > setup.expires_index:
            self.diagnostics["qh_context_expired"] += 1
            self._close_qh_setup(row, "QUARTER_HOUR_CONTEXT_HORIZON_EXPIRED")
            return
        if not self._in_evaluation(ts) or self._funding_blackout(ts):
            self._close_qh_setup(row, "FUNDING_OR_EVALUATION_BOUNDARY")
            return
        if not self._features_ready(ts):
            return
        atr = _finite(self._atr())
        if not math.isfinite(atr) or atr <= 0.0:
            return
        side = setup.side
        if side > 0:
            setup.favorable_extreme = max(setup.favorable_extreme, float(row["high"]))
            setup.pullback_extreme = min(setup.pullback_extreme, float(row["low"]))
        else:
            setup.favorable_extreme = min(setup.favorable_extreme, float(row["low"]))
            setup.pullback_extreme = max(setup.pullback_extreme, float(row["high"]))
        adverse_atr = side * (setup.favorable_extreme - float(row["close"])) / atr
        favorable_atr = side * (setup.favorable_extreme - setup.context_close) / atr
        setup.details["latest_adverse_atr"] = adverse_atr
        setup.details["latest_favorable_atr"] = favorable_atr

        if adverse_atr > MAX_PULLBACK_ATR:
            self._close_qh_setup(row, "PULLBACK_EXCEEDED_INFORMATION_LEG_GEOMETRY")
            return
        if setup.phase == "AWAIT_PULLBACK":
            if favorable_atr > MAX_PRE_PULLBACK_EXTENSION_ATR:
                self.diagnostics["qh_context_consumed_without_pullback"] += 1
                self._close_qh_setup(row, "INFORMATION_LEG_CONSUMED_BEFORE_TRADABLE_PULLBACK")
                return
            flow60 = self._feature_value("flow_60s")
            if adverse_atr >= MIN_PULLBACK_ATR and side * flow60 <= -0.01:
                setup.phase = "AWAIT_REACCEPTANCE"
                setup.pullback_start_index = self.bar_index
                setup.details.update(
                    {
                        "pullback_start_ts": ts,
                        "pullback_start_close": float(row["close"]),
                        "pullback_start_flow_60s": flow60,
                        "pullback_start_oi_change_5m": self._feature_value("oi_change_5m"),
                        "pullback_start_premium_change_5m": self._feature_value(
                            "premium_change_5m",
                        ),
                    },
                )
                self.diagnostics["qh_pullbacks_started"] += 1
                self._transition(
                    setup.scenario_id,
                    "QH_COUNTERFLOW_PULLBACK_STARTED",
                    ts,
                    ts,
                    "AWAIT_REACCEPTANCE",
                    "COUNTER_DIRECTIONAL_FLOW_RETRACED_INFORMATION_LEG",
                    float(row["close"]),
                    setup.details,
                )
            return

        if self.bar_index <= setup.pullback_start_index:
            return
        metrics_ready = self._feature_value("metrics_ready") >= 0.5
        basis_ready = self._feature_value("basis_ready") >= 0.5
        if not metrics_ready or not basis_ready or len(self.bars) < 2:
            return
        oi_change_5m = self._feature_value("oi_change_5m")
        premium_change_5m = self._feature_value("premium_change_5m")
        flow15 = self._feature_value("flow_15s")
        flow60 = self._feature_value("flow_60s")
        flow3 = self._feature_value("flow_3m")
        ret60 = self._feature_value("ret_60s_bps")
        efficiency = self._feature_value("efficiency_60s")
        depth = self._feature_value("depth_imbalance_1")
        required = (
            oi_change_5m,
            premium_change_5m,
            flow15,
            flow60,
            flow3,
            ret60,
            efficiency,
            depth,
        )
        if not all(math.isfinite(value) for value in required):
            return
        adverse_new_inventory = (
            oi_change_5m > MAX_ADVERSE_OI_EXPANSION_5M
            and side * premium_change_5m < 0.0
            and side * flow3 < -0.03
        )
        if adverse_new_inventory:
            self.diagnostics["qh_inventory_adverse"] += 1
            self._close_qh_setup(row, "NEW_OPPOSITE_INVENTORY_INVALIDATED_INFORMATION_TRANSFER")
            return
        previous = self.bars[-2]
        structure_reclaim = (
            float(row["close"]) > float(previous["high"])
            if side > 0
            else float(row["close"]) < float(previous["low"])
        )
        inventory_compatible = oi_change_5m <= 0.0 or side * premium_change_5m >= 0.0
        reaccepted = (
            inventory_compatible
            and structure_reclaim
            and side * flow15 >= MIN_REACCEPT_FLOW_15S
            and side * flow60 >= MIN_REACCEPT_FLOW_60S
            and side * (flow15 - flow60) >= -0.02
            and side * flow3 >= -0.02
            and side * ret60 > 0.0
            and efficiency >= MIN_REACCEPT_EFFICIENCY
            and side * depth >= 0.0
        )
        if not reaccepted:
            self.diagnostics["qh_reacceptance_fail"] += 1
            return
        self.diagnostics["qh_reacceptance_pass"] += 1
        self._arm_inherited_pending(setup, row, atr)

    def _arm_inherited_pending(
        self,
        setup: QuarterHourTransferSetup,
        row: dict[str, float | int],
        atr: float,
    ) -> None:
        side = setup.side
        recent = list(self.bars)[-3:]
        if not recent:
            return
        structure = float(row["high"]) if side > 0 else float(row["low"])
        penetration_atr = abs(setup.favorable_extreme - setup.pullback_extreme) / atr
        details = {
            **setup.details,
            "pool_id": f"qh-context-{setup.boundary_ts}",
            "pool_kind": "LOW" if side > 0 else "HIGH",
            "pool_level": setup.context_close,
            "pool_source": "CROSS_MARKET_QH_INFORMATION_PULSE",
            "pool_strength": float(setup.details.get("aligned_count", 0)),
            "pool_age_minutes": self.bar_index - setup.created_index,
            "penetration_atr": penetration_atr,
            "flow_15s": self._feature_value("flow_15s"),
            "flow_60s": self._feature_value("flow_60s"),
            "flow_3m": self._feature_value("flow_3m"),
            "notional_burst": self._feature_value("notional_burst"),
            "efficiency_60s": self._feature_value("efficiency_60s"),
            "absorption_60s": self._feature_value("absorption_60s"),
            "depth_imbalance_1": self._feature_value("depth_imbalance_1"),
            "bid_depth_change_1m": self._feature_value("bid_depth_change_1_1m"),
            "ask_depth_change_1m": self._feature_value("ask_depth_change_1_1m"),
            "oi_change_5m": self._feature_value("oi_change_5m"),
            "oi_change_15m": self._feature_value("oi_change_15m"),
            "premium_change_5m": self._feature_value("premium_change_5m"),
            "pullback_extreme": setup.pullback_extreme,
            "reacceptance_ts": int(row["ts"]),
            "reacceptance_close": float(row["close"]),
            "entry_policy": "INHERITED_V26_CHOCH_AND_FIRST_RETRACE",
        }
        pending = _construct(
            PendingSetup,
            scenario_id=setup.scenario_id,
            branch="REJECTION",
            side=side,
            swept_kind="LOW" if side > 0 else "HIGH",
            pool_id=f"qh-context-{setup.boundary_ts}",
            pool_level=setup.context_close,
            created_index=self.bar_index,
            created_ts=int(row["ts"]),
            expires_index=self.bar_index + self.config.rejection_confirmation_bars,
            sweep_extreme=setup.pullback_extreme,
            structure=structure,
            atr=atr,
            hold_count=0,
            retrace_armed=False,
            details=details,
        )
        self.pending = pending
        self.qh_setup = None
        self.diagnostics["rejection_setups"] += 1
        self.diagnostics["qh_pending_setups"] += 1
        self._transition(
            pending.scenario_id,
            "QH_INVENTORY_REACCEPTANCE_CONFIRMED",
            int(row["ts"]),
            int(row["ts"]),
            "CHOCH_ARMED",
            "PULLBACK_INVENTORY_RESET_AND_FLOW_DEPTH_REACCEPTANCE_AGREED",
            float(row["close"]),
            details,
        )

    def _release_qh_claim(self) -> None:
        if self.qh_claimed_boundary < 0:
            return
        released = SHARED_QUARTER_HOUR_CONTEXT.release(
            owner=self.qh_symbol,
            boundary_ts=self.qh_claimed_boundary,
        )
        if released:
            self.diagnostics["qh_global_context_releases"] += 1
        self.qh_claimed_boundary = -1

    def _expire_pending(
        self,
        row: dict[str, float | int],
        reason: str,
    ) -> None:
        scenario_id = getattr(self.pending, "scenario_id", None)
        super()._expire_pending(row, reason)
        if (
            isinstance(scenario_id, str)
            and scenario_id.startswith("qhit-")
            and self.pending is None
            and not self.entry_pending
        ):
            self._release_qh_claim()

    def _submit_price_capped_bracket(self, *args: Any, **kwargs: Any) -> bool:
        submitted = bool(super()._submit_price_capped_bracket(*args, **kwargs))
        if not submitted and not self.entry_pending:
            self._release_qh_claim()
        return submitted

    def _clear_trade_state(self) -> None:
        super()._clear_trade_state()
        self._release_qh_claim()

    def _close_qh_setup(
        self,
        row: dict[str, float | int],
        reason: str,
    ) -> None:
        setup = self.qh_setup
        if setup is None:
            return
        self._transition(
            setup.scenario_id,
            "QH_CONTEXT_CLOSED",
            int(row["ts"]),
            int(row["ts"]),
            "CLOSED",
            reason,
            float(row["close"]),
            setup.details,
        )
        self.qh_setup = None
        self._release_qh_claim()


CandidateStrategy = QuarterHourInventoryTransferStrategy
StrategyClass = QuarterHourInventoryTransferStrategy

__all__ = [
    "QuarterHourInventoryTransferStrategy",
    "QuarterHourTransferSetup",
    "LiquidityResponseConfig",
]
