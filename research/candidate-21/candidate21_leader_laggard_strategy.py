"""Shared-account leader-to-laggard price-discovery transfer policy.

All four project assets publish completed observations.  At each new minute the
largest prior-completed peer-implied lag is selected before any current-minute
strategy can acquire the account slot.  Only that deterministic owner may use
its current local price, flow, depth and perpetual-basis response to confirm a
catch-up leg.  The peer-implied local price is frozen as the natural objective;
orders whose post-cost geometry cannot deliver the configured minimum net R are
closed unresolved.
"""
from __future__ import annotations

import math
from statistics import median
from typing import Any

from flow_inflection_logic import has_adverse_slippage_room
from flow_inflection_logic import worst_entry_preserving_net_r
from leader_laggard_router import TransferEvidence
from leader_laggard_router import TransferThresholds
from leader_laggard_router import classify_leader_laggard_transfer
from logic import net_r_at_price
from logic import planned_loss_per_unit
from nautilus_trader.model.data import Bar
from relative_value_context import completed_history
from relative_value_context import publish
from relative_value_context import reset
from strategy_base import PendingSetup
from strategy_base import _as_float
from strategy_v9 import ArmedEntryPath
from strategy_v26 import ScenarioValidEntryStrategy
from strategy_v41_competing_auction import _construct


PROJECT_SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT")
THRESHOLDS = TransferThresholds()
LOCAL_FLOW_MIN = 0.05
LOCAL_EFFICIENCY_MIN = 0.20
LOCAL_NOTIONAL_BURST_MIN = 1.0
ENTRY_VALIDITY_BARS = 3


def symbol_from_instrument(value: Any) -> str:
    text = str(value)
    return text.split("-PERP", 1)[0].split(".", 1)[0]


def normalized_return(history: tuple[Any, ...], bars: int) -> float:
    if len(history) <= bars:
        return math.nan
    first = float(history[-(bars + 1)].close)
    last = float(history[-1].close)
    atr = float(history[-1].atr)
    if first <= 0.0 or last <= 0.0 or not math.isfinite(atr) or atr <= 0.0:
        return math.nan
    return math.log(last / first) / (atr / last)


class LeaderLaggardTransferMixin:
    """Deterministically route the strongest peer-owned lag into one account."""

    def __init__(self, config: Any) -> None:
        self.cross_asset_symbol = symbol_from_instrument(config.instrument_id)
        super().__init__(config)  # type: ignore[misc]
        self.diagnostics.update(
            {
                "candidate21_transfer_states_published": 0,
                "candidate21_transfer_owner_evaluations": 0,
                "candidate21_transfer_owner_selected": 0,
                "candidate21_transfer_non_owner": 0,
                "candidate21_transfer_peer_events": 0,
                "candidate21_transfer_local_reprices": 0,
                "candidate21_transfer_flow_depth_basis_pass": 0,
                "candidate21_transfer_cost_geometry_rejected": 0,
                "candidate21_transfer_submissions": 0,
                "candidate21_transfer_same_timestamp_peer_uses": 0,
            },
        )

    def on_start(self) -> None:
        if self.cross_asset_symbol == "BTCUSDT":
            reset()
        super().on_start()  # type: ignore[misc]

    def on_bar(self, bar: Bar) -> None:
        super().on_bar(bar)  # type: ignore[misc]
        if not self.bars:
            return
        row = self.bars[-1]
        atr = self._atr() if len(self.bars) > self.config.atr_period else math.nan
        publish(
            symbol=self.cross_asset_symbol,
            ts=int(row["ts"]),
            close=float(row["close"]),
            atr=float(atr),
        )
        self.diagnostics["candidate21_transfer_states_published"] += 1

    def _detect_sweep(self, row: dict[str, float | int], previous_close: float) -> None:
        del previous_close
        self._maybe_submit_transfer(row)

    def _histories(self, ts: int, count: int = 8) -> dict[str, tuple[Any, ...]] | None:
        result: dict[str, tuple[Any, ...]] = {}
        for symbol in PROJECT_SYMBOLS:
            history = completed_history(symbol, before_ts=ts, count=count)
            if len(history) < 6:
                return None
            if history[-1].ts >= ts:
                self.diagnostics["candidate21_transfer_same_timestamp_peer_uses"] += 1
                return None
            result[symbol] = history
        return result

    def _prior_owner(self, ts: int) -> tuple[str, float] | None:
        histories = self._histories(ts)
        if histories is None:
            return None
        candidates: list[tuple[float, int, str]] = []
        for order, symbol in enumerate(PROJECT_SYMBOLS):
            own5 = normalized_return(histories[symbol], 5)
            peers = [
                normalized_return(histories[peer], 5)
                for peer in PROJECT_SYMBOLS
                if peer != symbol
            ]
            if not math.isfinite(own5) or not all(math.isfinite(value) for value in peers):
                continue
            peer_median = median(peers)
            if abs(peer_median) < THRESHOLDS.minimum_peer_median_atr:
                continue
            side = 1 if peer_median > 0.0 else -1
            confirming = sum(
                side * value >= THRESHOLDS.minimum_confirming_peer_atr
                for value in peers
            )
            gap = side * (peer_median - own5)
            if (
                confirming >= THRESHOLDS.minimum_confirming_peers
                and gap >= THRESHOLDS.minimum_lag_gap_atr
            ):
                candidates.append((gap, -order, symbol))
        if not candidates:
            return None
        gap, _, symbol = max(candidates)
        return symbol, gap

    def _current_evidence(
        self,
        row: dict[str, float | int],
        histories: dict[str, tuple[Any, ...]],
    ) -> TransferEvidence | None:
        atr = float(self._atr())
        close = float(row["close"])
        if len(self.bars) < 6 or not math.isfinite(atr) or atr <= 0.0 or close <= 0.0:
            return None
        first = float(self.bars[-6]["close"])
        prior = float(self.bars[-2]["close"])
        prior2 = float(self.bars[-3]["close"])
        if min(first, prior, prior2) <= 0.0:
            return None
        own5 = math.log(close / first) / (atr / close)
        own1 = math.log(close / prior) / (atr / close)
        previous_own1 = math.log(prior / prior2) / (atr / prior)
        peer5 = tuple(
            normalized_return(histories[symbol], 5)
            for symbol in PROJECT_SYMBOLS
            if symbol != self.cross_asset_symbol
        )
        peer1 = tuple(
            normalized_return(histories[symbol], 1)
            for symbol in PROJECT_SYMBOLS
            if symbol != self.cross_asset_symbol
        )
        return TransferEvidence(
            peer_returns_5m_atr=peer5,  # type: ignore[arg-type]
            peer_returns_1m_atr=peer1,  # type: ignore[arg-type]
            own_return_5m_atr=own5,
            own_return_1m_atr=own1,
            previous_own_return_1m_atr=previous_own1,
            close=close,
            atr=atr,
        )

    def _maybe_submit_transfer(self, row: dict[str, float | int]) -> None:
        ts = int(row["ts"])
        self.diagnostics["candidate21_transfer_owner_evaluations"] += 1
        owner = self._prior_owner(ts)
        if owner is None:
            return
        owner_symbol, prior_gap = owner
        if owner_symbol != self.cross_asset_symbol:
            self.diagnostics["candidate21_transfer_non_owner"] += 1
            return
        self.diagnostics["candidate21_transfer_owner_selected"] += 1
        histories = self._histories(ts)
        if histories is None:
            return
        evidence = self._current_evidence(row, histories)
        if evidence is None:
            return
        decision = classify_leader_laggard_transfer(evidence, THRESHOLDS)
        if decision.side == 0:
            return
        self.diagnostics["candidate21_transfer_peer_events"] += 1
        self.diagnostics["candidate21_transfer_local_reprices"] += 1

        side = decision.side
        flow15 = self._feature("flow_15s")
        flow60 = self._feature("flow_60s")
        depth = self._feature("depth_imbalance_1")
        efficiency = self._feature("efficiency_60s")
        burst = self._feature("notional_burst")
        premium = self._feature("premium_change_1m")
        basis_ready = self._feature("basis_ready") > 0.5
        values = (flow15, flow60, depth, efficiency, burst, premium)
        if not basis_ready or not all(math.isfinite(value) for value in values):
            return
        if not (
            side * flow15 >= LOCAL_FLOW_MIN
            and side * flow60 > 0.0
            and side * depth > 0.0
            and efficiency >= LOCAL_EFFICIENCY_MIN
            and burst >= LOCAL_NOTIONAL_BURST_MIN
            and side * premium > 0.0
        ):
            return
        self.diagnostics["candidate21_transfer_flow_depth_basis_pass"] += 1

        atr = float(evidence.atr)
        observed_entry = _as_float(self.instrument.make_price(float(row["close"])))
        recent = list(self.bars)[-3:]
        extreme = (
            min(float(item["low"]) for item in recent)
            if side > 0
            else max(float(item["high"]) for item in recent)
        )
        stop_raw = extreme - side * self.config.stop_buffer_atr * atr
        stop_price = self.instrument.make_price(stop_raw)
        stop = _as_float(stop_price)
        target_price = self.instrument.make_price(decision.target)
        target = _as_float(target_price)
        cost_rate = self.config.all_in_cost_bps_each_side / 10_000.0
        slippage_rate = self.config.adverse_slippage_bps_each_side / 10_000.0
        raw_bound = worst_entry_preserving_net_r(
            stop=stop,
            target=target,
            side=side,
            minimum_net_r=self.config.min_target_net_r,
            cost_rate=cost_rate,
            adverse_slippage_rate=slippage_rate,
        )
        if not math.isfinite(raw_bound):
            self.diagnostics["candidate21_transfer_cost_geometry_rejected"] += 1
            return
        increment = _as_float(self.instrument.price_increment)
        entry_price = self.instrument.make_price(raw_bound)
        entry = _as_float(entry_price)
        if side > 0 and entry > raw_bound:
            entry_price = self.instrument.make_price(raw_bound - increment)
            entry = _as_float(entry_price)
        elif side < 0 and entry < raw_bound:
            entry_price = self.instrument.make_price(raw_bound + increment)
            entry = _as_float(entry_price)
        if not has_adverse_slippage_room(
            observed_price=observed_entry,
            limit_price=entry,
            side=side,
            adverse_slippage_rate=slippage_rate,
        ):
            self.diagnostics["candidate21_transfer_cost_geometry_rejected"] += 1
            return
        planned_loss = planned_loss_per_unit(
            entry,
            stop,
            side,
            cost_rate,
            slippage_rate,
        )
        target_r = net_r_at_price(
            entry,
            target,
            side,
            planned_loss,
            cost_rate,
        )
        geometry = (
            stop < observed_entry <= entry < target
            if side > 0
            else target < entry <= observed_entry < stop
        )
        if (
            not geometry
            or not math.isfinite(planned_loss)
            or planned_loss <= 0.0
            or target_r + 1e-9 < self.config.min_target_net_r
        ):
            self.diagnostics["candidate21_transfer_cost_geometry_rejected"] += 1
            return

        self.scenario_counter += 1
        scenario_id = f"c21-transfer-{self.cross_asset_symbol}-{self.scenario_counter:07d}"
        details = {
            "candidate21_parent": "PRIOR_COMPLETED_SYNCHRONIZED_PEER_PRICE_DISCOVERY",
            "deterministic_owner": owner_symbol,
            "prior_owner_gap_atr": prior_gap,
            "peer_median_5m_atr": decision.peer_median_5m_atr,
            "peer_median_1m_atr": decision.peer_median_1m_atr,
            "local_lag_gap_atr": decision.lag_gap_atr,
            "local_return_5m_atr": evidence.own_return_5m_atr,
            "local_return_1m_atr": evidence.own_return_1m_atr,
            "local_previous_return_1m_atr": evidence.previous_own_return_1m_atr,
            "confirming_peers": decision.confirming_peers,
            "flow_15s": flow15,
            "flow_60s": flow60,
            "depth_imbalance_1": depth,
            "efficiency_60s": efficiency,
            "notional_burst": burst,
            "premium_change_1m": premium,
            "implied_target": target,
            "entry_limit": entry,
            "stop": stop,
            "target_net_r": target_r,
        }
        setup = _construct(
            PendingSetup,
            scenario_id=scenario_id,
            branch="CROSS_ASSET_LEADER_LAGGARD_TRANSFER",
            side=side,
            swept_kind="LOW" if side > 0 else "HIGH",
            pool_id=f"peer-implied-{self.cross_asset_symbol}-{ts}",
            pool_level=target,
            created_index=self.bar_index,
            created_ts=ts,
            expires_index=self.bar_index + ENTRY_VALIDITY_BARS,
            sweep_extreme=extreme,
            structure=float(row["close"]),
            atr=atr,
            hold_count=0,
            retrace_armed=False,
            details=details,
        )
        armed = ArmedEntryPath(
            setup=setup,
            flow_state="LOCAL_REPRICE_CONFIRMED",
            choch_close=observed_entry,
            stop=stop,
            atr=atr,
            created_index=self.bar_index,
            created_ts=ts,
            details=details,
        )
        self.armed_entry_path = armed
        submitted = self._submit_price_capped_bracket(
            armed=armed,
            row=row,
            entry_price=entry_price,
            stop_price=stop_price,
            target_price=target_price,
            sizing_entry=entry,
            planned_loss=planned_loss,
            target_source="CROSS_ASSET_PEER_IMPLIED_VALUE",
            target_r=target_r,
            branch="CROSS_ASSET_LEADER_LAGGARD_TRANSFER",
            event_type="CROSS_ASSET_TRANSFER_LIMIT_SUBMITTED",
            reason=decision.reason,
            expires_index=self.bar_index + ENTRY_VALIDITY_BARS,
            entry_tag="CANDIDATE21_LEADER_LAGGARD_TRANSFER",
            extra=details,
        )
        if submitted:
            self.diagnostics["candidate21_transfer_submissions"] += 1


SystemicRepricingGateMixin = LeaderLaggardTransferMixin


class SystemicRepricingGateStrategy(
    LeaderLaggardTransferMixin,
    ScenarioValidEntryStrategy,
):
    """Single-symbol importable form; the alpha requires the shared node."""


CandidateStrategy = SystemicRepricingGateStrategy
StrategyClass = SystemicRepricingGateStrategy
CrossAssetRepricingGateStrategy = SystemicRepricingGateStrategy


__all__ = [
    "LeaderLaggardTransferMixin",
    "SystemicRepricingGateMixin",
    "SystemicRepricingGateStrategy",
    "symbol_from_instrument",
]
