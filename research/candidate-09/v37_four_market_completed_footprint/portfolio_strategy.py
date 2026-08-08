"""One-account four-symbol wrappers for the frozen V35 strategy."""
from __future__ import annotations

from typing import Any

from global_entry_slot_v3 import ENTRY_INTENT, POSITION_CLOSED_AWAIT_RELEASE
from global_entry_slot_v4 import FINAL_SHARED_ACCOUNT_ENTRY_COORDINATOR
from strategy_v35 import Candidate16Config, Candidate16Strategy


class SharedSlotMixin:
    """Reserve one global new-entry/position slot across all strategy instances."""

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

    def _submit_market_bracket(self, *args: Any, **kwargs: Any) -> bool:
        row = kwargs.get("row") or {}
        ts_event = int(row.get("ts", self._slot_ts()))
        acquired = FINAL_SHARED_ACCOUNT_ENTRY_COORDINATOR.acquire_entry_intent(
            owner=self._shared_slot_owner,
            ts_event=ts_event,
            reason="V35_NEW_ENTRY_BRACKET",
            context={"strategy": type(self).__name__},
        )
        if not acquired:
            self.diagnostics["shared_slot_conflicts"] = int(
                self.diagnostics["shared_slot_conflicts"]
            ) + 1
            return False
        self.diagnostics["shared_slot_acquisitions"] = int(
            self.diagnostics["shared_slot_acquisitions"]
        ) + 1
        submitted = bool(super()._submit_market_bracket(*args, **kwargs))  # type: ignore[misc]
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


class SharedAccountV35BTCStrategy(SharedSlotMixin, Candidate16Strategy):
    pass


class SharedAccountV35ETHStrategy(SharedSlotMixin, Candidate16Strategy):
    pass


class SharedAccountV35SOLStrategy(SharedSlotMixin, Candidate16Strategy):
    pass


class SharedAccountV35XRPStrategy(SharedSlotMixin, Candidate16Strategy):
    pass


STRATEGY_PATHS = {
    "BTCUSDT": "portfolio_strategy:SharedAccountV35BTCStrategy",
    "ETHUSDT": "portfolio_strategy:SharedAccountV35ETHStrategy",
    "SOLUSDT": "portfolio_strategy:SharedAccountV35SOLStrategy",
    "XRPUSDT": "portfolio_strategy:SharedAccountV35XRPStrategy",
}


__all__ = [
    "Candidate16Config",
    "SharedAccountV35BTCStrategy",
    "SharedAccountV35ETHStrategy",
    "SharedAccountV35SOLStrategy",
    "SharedAccountV35XRPStrategy",
    "STRATEGY_PATHS",
]
