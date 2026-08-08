"""Candidate 09 v40: BTC-led lagged cross-impact portfolio strategy.

BTC keeps the frozen V35 completed-auction footprint continuation. ETH, SOL and
XRP use the same local liquidity map, true-acceptance state, footprint ownership,
first retest, invalidation and target, but only when a strictly earlier completed
BTC minute already repriced efficiently in the same direction and displaced at
least as far as the lagging target. The leader observation is context; the local
auction owns state and execution.
"""
from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import asdict, dataclass
import math
from threading import RLock
from typing import Any

from nautilus_trader.model.data import Bar

from effort_result_router import AuctionDecision
from global_entry_slot_v3 import ENTRY_INTENT, POSITION_CLOSED_AWAIT_RELEASE
from global_entry_slot_v4 import FINAL_SHARED_ACCOUNT_ENTRY_COORDINATOR
from strategy_v35 import Candidate16Config as _Candidate35Config
from strategy_v35 import Candidate16Strategy as _Candidate35Strategy


PROJECT_SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT")
_MINUTE_NS = 60_000_000_000


@dataclass(frozen=True, slots=True)
class CompletedLeaderState:
    symbol: str
    ts_event: int
    return_atr: float
    flow_60s: float
    efficiency_60s: float
    footprint_delta_60s: float
    stacked_buy_levels: int
    stacked_sell_levels: int


class CompletedLeaderContext:
    """Process-local causal history shared by one NautilusTrader node."""

    def __init__(self, maxlen: int = 8) -> None:
        self._maxlen = maxlen
        self._lock = RLock()
        self._states: dict[str, deque[CompletedLeaderState]] = defaultdict(
            lambda: deque(maxlen=self._maxlen)
        )

    def reset(self) -> None:
        with self._lock:
            self._states.clear()

    def publish(self, state: CompletedLeaderState) -> None:
        if state.symbol not in PROJECT_SYMBOLS:
            raise ValueError(f"unsupported project symbol: {state.symbol}")
        with self._lock:
            history = self._states[state.symbol]
            if history and state.ts_event < history[-1].ts_event:
                raise ValueError("leader states must be published monotonically")
            if history and state.ts_event == history[-1].ts_event:
                history[-1] = state
            else:
                history.append(state)

    def latest_before(self, symbol: str, current_ts: int) -> CompletedLeaderState | None:
        with self._lock:
            history = self._states.get(symbol)
            if not history:
                return None
            for state in reversed(history):
                if state.ts_event < current_ts:
                    return state
        return None


SHARED_BTC_LEADER_CONTEXT = CompletedLeaderContext()


def reset_shared_btc_leader_context() -> None:
    SHARED_BTC_LEADER_CONTEXT.reset()


def symbol_from_instrument(value: Any) -> str:
    text = str(value)
    if "-PERP" not in text:
        raise ValueError(f"unexpected project instrument id: {text}")
    return text.split("-PERP", 1)[0]


class Candidate16Config(_Candidate35Config, frozen=True):
    candidate40_require_btc_leader: bool = True
    candidate40_leader_max_age_bars: int = 3


class LaggedBtcContextMixin:
    """Publish completed states and route alt acceptance through prior BTC lead."""

    def __init__(self, config: Candidate16Config) -> None:
        self._candidate40_symbol = symbol_from_instrument(config.instrument_id)
        super().__init__(config=config)  # type: ignore[misc]
        self.diagnostics.update(
            {
                "candidate40_states_published": 0,
                "candidate40_alt_acceptances_evaluated": 0,
                "candidate40_btc_leader_passes": 0,
                "candidate40_btc_leader_blocks": 0,
                "candidate40_btc_native_paths": 0,
                "candidate40_control_paths": 0,
                "candidate40_same_timestamp_leaders_used": 0,
            }
        )

    def on_bar(self, bar: Bar) -> None:
        super().on_bar(bar)  # type: ignore[misc]
        self._candidate40_publish_completed_state()

    def _candidate40_publish_completed_state(self) -> None:
        if len(self.bars) < 2:
            return
        row = self.bars[-1]
        ts_event = int(row["ts"])
        feature = self.current_feature
        if feature is None or not bool(feature.get("feature_ready", False)):
            return
        observed = int(feature.get("observed_time_ns", 0))
        age_seconds = (ts_event - observed) / 1_000_000_000
        if age_seconds < -1e-9 or age_seconds > self.config.feature_max_age_seconds:
            return
        atr = self._atr()
        if not math.isfinite(atr) or atr <= 0.0:
            return
        previous_close = float(self.bars[-2]["close"])
        state = CompletedLeaderState(
            symbol=self._candidate40_symbol,
            ts_event=ts_event,
            return_atr=(float(row["close"]) - previous_close) / atr,
            flow_60s=self._feature("flow_60s"),
            efficiency_60s=self._feature("efficiency_60s"),
            footprint_delta_60s=self._feature("footprint_delta_60s"),
            stacked_buy_levels=int(
                max(0.0, self._feature("stacked_buy_imbalance_levels"))
            ),
            stacked_sell_levels=int(
                max(0.0, self._feature("stacked_sell_imbalance_levels"))
            ),
        )
        SHARED_BTC_LEADER_CONTEXT.publish(state)
        self.diagnostics["candidate40_states_published"] = int(
            self.diagnostics["candidate40_states_published"]
        ) + 1

    def _candidate40_leader_decision(
        self,
        *,
        direction: int,
        interaction_ts: int,
        target_progress_atr: float,
    ) -> tuple[bool, dict[str, Any]]:
        leader = SHARED_BTC_LEADER_CONTEXT.latest_before("BTCUSDT", interaction_ts)
        if leader is None:
            return False, {
                "candidate40_reason": "NO_STRICTLY_PRIOR_BTC_STATE",
                "candidate40_interaction_ts": interaction_ts,
            }
        age_ns = interaction_ts - leader.ts_event
        if age_ns <= 0:
            self.diagnostics["candidate40_same_timestamp_leaders_used"] = int(
                self.diagnostics["candidate40_same_timestamp_leaders_used"]
            ) + 1
            return False, {
                "candidate40_reason": "BTC_STATE_NOT_STRICTLY_PRIOR",
                "candidate40_interaction_ts": interaction_ts,
                "candidate40_leader": asdict(leader),
            }
        maximum_age_ns = self.config.candidate40_leader_max_age_bars * _MINUTE_NS
        directional_return = direction * leader.return_atr
        directional_flow = direction * leader.flow_60s
        directional_delta = direction * leader.footprint_delta_60s
        stack_levels = (
            leader.stacked_buy_levels if direction > 0 else leader.stacked_sell_levels
        )
        passed = (
            age_ns <= maximum_age_ns
            and directional_return >= self.config.router_acceptance_min_progress_atr
            and directional_return >= max(0.0, target_progress_atr)
            and directional_flow >= self.config.acceptance_flow_min
            and leader.efficiency_60s >= self.config.router_acceptance_min_efficiency
            and directional_delta > 0.0
            and stack_levels >= self.config.candidate33_min_stacked_levels
        )
        return passed, {
            "candidate40_reason": (
                "STRICTLY_PRIOR_BTC_LED_REPRICING"
                if passed
                else "BTC_DID_NOT_OWN_A_STRONGER_PRIOR_DIRECTIONAL_LEG"
            ),
            "candidate40_interaction_ts": interaction_ts,
            "candidate40_leader_age_ns": age_ns,
            "candidate40_maximum_age_ns": maximum_age_ns,
            "candidate40_directional_leader_return_atr": directional_return,
            "candidate40_target_progress_atr": target_progress_atr,
            "candidate40_directional_leader_flow": directional_flow,
            "candidate40_directional_leader_delta": directional_delta,
            "candidate40_leader_stack_levels": stack_levels,
            "candidate40_leader": asdict(leader),
        }

    def _complete_parent(self, row: dict[str, float | int]) -> None:
        state = self.parent_auction
        setup = self.pending
        if state is None or setup is None:
            return
        if state.decision is not AuctionDecision.ACCEPTANCE_CONTINUATION:
            super()._complete_parent(row)  # type: ignore[misc]
            return
        if self._candidate40_symbol == "BTCUSDT":
            self.diagnostics["candidate40_btc_native_paths"] = int(
                self.diagnostics["candidate40_btc_native_paths"]
            ) + 1
            super()._complete_parent(row)  # type: ignore[misc]
            return
        if not self.config.candidate40_require_btc_leader:
            self.diagnostics["candidate40_control_paths"] = int(
                self.diagnostics["candidate40_control_paths"]
            ) + 1
            super()._complete_parent(row)  # type: ignore[misc]
            return

        self.diagnostics["candidate40_alt_acceptances_evaluated"] = int(
            self.diagnostics["candidate40_alt_acceptances_evaluated"]
        ) + 1
        interaction_ts = int(setup.details.get("interaction_ts_event", row["ts"]))
        passed, details = self._candidate40_leader_decision(
            direction=state.direction,
            interaction_ts=interaction_ts,
            target_progress_atr=state.latest_progress_atr,
        )
        setup.details.update(details)
        if not passed:
            self.diagnostics["candidate40_btc_leader_blocks"] = int(
                self.diagnostics["candidate40_btc_leader_blocks"]
            ) + 1
            self._close_parent_without_trade(
                row,
                "ALT_TRUE_ACCEPTANCE_WITHOUT_PRIOR_BTC_LEADERSHIP",
                {**setup.details, "candidate40_terminal_decision": state.decision.value},
            )
            return
        self.diagnostics["candidate40_btc_leader_passes"] = int(
            self.diagnostics["candidate40_btc_leader_passes"]
        ) + 1
        super()._complete_parent(row)  # type: ignore[misc]


class SharedSlotMixin:
    """Reserve one global slot at the actual V35 submit-entry boundary."""

    def __init__(self, config: Candidate16Config) -> None:
        self._shared_slot_owner = str(config.instrument_id)
        super().__init__(config=config)  # type: ignore[misc]
        self.diagnostics.update(
            {
                "shared_slot_acquisitions": 0,
                "shared_slot_conflicts": 0,
                "shared_slot_position_opens": 0,
                "shared_slot_position_closes": 0,
                "shared_slot_releases": 0,
                "shared_slot_mismatches": 0,
            }
        )

    def _slot_ts(self) -> int:
        return int(self.bars[-1]["ts"]) if self.bars else 0

    def _release_if_idle(self, reason: str, event: Any | None = None) -> None:
        if FINAL_SHARED_ACCOUNT_ENTRY_COORDINATOR.owner != self._shared_slot_owner:
            return
        try:
            flat = self.portfolio.is_flat(self.config.instrument_id)
        except Exception:
            flat = False
        if not flat or self.entry_pending:
            return
        if FINAL_SHARED_ACCOUNT_ENTRY_COORDINATOR.phase not in {
            ENTRY_INTENT,
            POSITION_CLOSED_AWAIT_RELEASE,
        }:
            return
        released = FINAL_SHARED_ACCOUNT_ENTRY_COORDINATOR.release(
            owner=self._shared_slot_owner,
            ts_event=int(getattr(event, "ts_event", self._slot_ts())),
            reason=reason,
            context={"strategy": type(self).__name__},
        )
        key = "shared_slot_releases" if released else "shared_slot_mismatches"
        self.diagnostics[key] = int(self.diagnostics[key]) + 1

    def _submit_entry(
        self,
        setup: Any,
        row: dict[str, float | int],
    ) -> bool:
        acquired = FINAL_SHARED_ACCOUNT_ENTRY_COORDINATOR.acquire_entry_intent(
            owner=self._shared_slot_owner,
            ts_event=int(row["ts"]),
            reason="V40_NEW_ENTRY_BRACKET",
            context={
                "strategy": type(self).__name__,
                "scenario_id": getattr(setup, "scenario_id", None),
            },
        )
        if not acquired:
            self.diagnostics["shared_slot_conflicts"] = int(
                self.diagnostics["shared_slot_conflicts"]
            ) + 1
            self._expire_pending(row, "GLOBAL_NEW_ENTRY_SLOT_OCCUPIED")
            return False
        self.diagnostics["shared_slot_acquisitions"] = int(
            self.diagnostics["shared_slot_acquisitions"]
        ) + 1
        submitted = bool(super()._submit_entry(setup, row))  # type: ignore[misc]
        if not submitted:
            self._release_if_idle("ENTRY_SUBMISSION_RETURNED_FALSE")
        return submitted

    def on_position_opened(self, event: Any) -> None:
        transitioned = FINAL_SHARED_ACCOUNT_ENTRY_COORDINATOR.position_opened(
            owner=self._shared_slot_owner,
            ts_event=int(getattr(event, "ts_event", self._slot_ts())),
            reason="NAUTILUS_POSITION_OPENED",
            context={"strategy": type(self).__name__, "event": str(event)},
        )
        key = "shared_slot_position_opens" if transitioned else "shared_slot_mismatches"
        self.diagnostics[key] = int(self.diagnostics[key]) + 1
        super().on_position_opened(event)  # type: ignore[misc]

    def on_position_closed(self, event: Any) -> None:
        transitioned = FINAL_SHARED_ACCOUNT_ENTRY_COORDINATOR.position_closed(
            owner=self._shared_slot_owner,
            ts_event=int(getattr(event, "ts_event", self._slot_ts())),
            reason="NAUTILUS_POSITION_CLOSED",
            context={"strategy": type(self).__name__, "event": str(event)},
        )
        key = "shared_slot_position_closes" if transitioned else "shared_slot_mismatches"
        self.diagnostics[key] = int(self.diagnostics[key]) + 1
        super().on_position_closed(event)  # type: ignore[misc]
        self._release_if_idle("POSITION_CLOSED_AND_LOCAL_STATE_CLEARED", event)

    def _clear_trade_state(self) -> None:
        super()._clear_trade_state()  # type: ignore[misc]
        self._release_if_idle("LOCAL_TRADE_STATE_CLEARED")

    def on_order_rejected(self, event: Any) -> None:
        super().on_order_rejected(event)  # type: ignore[misc]
        self._release_if_idle("ORDER_REJECTED_AND_FLAT", event)

    def on_order_denied(self, event: Any) -> None:
        parent = getattr(super(), "on_order_denied", None)
        if callable(parent):
            parent(event)
        self._release_if_idle("ORDER_DENIED_AND_FLAT", event)

    def on_stop(self) -> None:
        super().on_stop()  # type: ignore[misc]
        self._release_if_idle("STRATEGY_STOPPED")


class SharedAccountV40BTCStrategy(
    SharedSlotMixin,
    LaggedBtcContextMixin,
    _Candidate35Strategy,
):
    pass


class SharedAccountV40ETHStrategy(
    SharedSlotMixin,
    LaggedBtcContextMixin,
    _Candidate35Strategy,
):
    pass


class SharedAccountV40SOLStrategy(
    SharedSlotMixin,
    LaggedBtcContextMixin,
    _Candidate35Strategy,
):
    pass


class SharedAccountV40XRPStrategy(
    SharedSlotMixin,
    LaggedBtcContextMixin,
    _Candidate35Strategy,
):
    pass


STRATEGY_PATHS = {
    "BTCUSDT": "portfolio_strategy:SharedAccountV40BTCStrategy",
    "ETHUSDT": "portfolio_strategy:SharedAccountV40ETHStrategy",
    "SOLUSDT": "portfolio_strategy:SharedAccountV40SOLStrategy",
    "XRPUSDT": "portfolio_strategy:SharedAccountV40XRPStrategy",
}


__all__ = [
    "Candidate16Config",
    "CompletedLeaderContext",
    "CompletedLeaderState",
    "SHARED_BTC_LEADER_CONTEXT",
    "SharedAccountV40BTCStrategy",
    "SharedAccountV40ETHStrategy",
    "SharedAccountV40SOLStrategy",
    "SharedAccountV40XRPStrategy",
    "STRATEGY_PATHS",
    "reset_shared_btc_leader_context",
]
