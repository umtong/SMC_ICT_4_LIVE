#!/usr/bin/env python3
"""Pure state-contract checks for ADOM unfilled and partial-entry lifecycle."""

from __future__ import annotations

from types import SimpleNamespace

from nautilus_lifecycle import NautilusLifecycleMixin


class _Portfolio:
    def __init__(self, *, flat: bool) -> None:
        self.flat = flat

    def is_flat(self, instrument_id):  # noqa: ANN001, ARG002
        return self.flat


class _Cache:
    def __init__(self, open_order_ids: tuple[str, ...]) -> None:
        self.open_order_ids = open_order_ids

    def orders_open(self, *, instrument_id):  # noqa: ANN001, ARG002
        return [
            SimpleNamespace(client_order_id=client_order_id)
            for client_order_id in self.open_order_ids
        ]


class _Harness(NautilusLifecycleMixin):
    def __init__(
        self,
        *,
        state: str,
        open_order_ids: tuple[str, ...],
        flat: bool,
    ) -> None:
        self.config = SimpleNamespace(instrument_id="BTCUSDT-PERP.BINANCE")
        self.cache = _Cache(open_order_ids)
        self.portfolio = _Portfolio(flat=flat)
        self._bar_index = 17
        self._entry_inflight = state == "ORDER_SUBMITTED"
        self._exit_inflight = False
        self._active_trade = {
            "scenario_id": "SCENARIO-1",
            "entry_client_order_id": "ENTRY-1",
            "entry_execution_mode": "DEFENSE_ORIGIN_LIMIT",
            "entry_order_type": "LIMIT",
            "entry_expiry_ts_ns": 1_000,
            "expected_entry_price": 100.0,
            "planned_loss_budget": 3_000.0,
            "loss_per_unit": 2.0,
        }
        self._scenario_states = {"SCENARIO-1": state}
        self.diagnostics: dict[str, object] = {}
        self.errors: list[str] = []
        self.cancel_all_calls: list[str] = []
        self.close_all_calls: list[str] = []
        self.transitions: list[dict[str, object]] = []
        self.equity_samples: list[int] = []

    def _record_external_transition(self, **kwargs) -> None:  # noqa: ANN003
        self.transitions.append(dict(kwargs))
        self._scenario_states[str(kwargs["scenario_id"])] = str(kwargs["next_state"])

    def _sample_equity(self, ts_ns: int) -> None:
        self.equity_samples.append(ts_ns)

    def cancel_all_orders(self, instrument_id) -> None:  # noqa: ANN001
        self.cancel_all_calls.append(str(instrument_id))

    def close_all_positions(self, instrument_id) -> None:  # noqa: ANN001
        self.close_all_calls.append(str(instrument_id))


def _position_opened(quantity: str = "0.040"):
    return SimpleNamespace(
        avg_px_open=100.0,
        ts_event=2_000,
        quantity=quantity,
    )


def _order_event(client_order_id: str, *, reason: str = "simulated"):
    return SimpleNamespace(
        client_order_id=client_order_id,
        ts_event=3_000,
        reason=reason,
    )


def main() -> int:
    # An entirely unfilled parent expiry resets the submitted scenario.
    unfilled = _Harness(
        state="ORDER_SUBMITTED",
        open_order_ids=(),
        flat=True,
    )
    unfilled.on_order_expired(_order_event("ENTRY-1"))
    assert unfilled._active_trade is None
    assert not unfilled._entry_inflight
    assert unfilled._scenario_states["SCENARIO-1"] == "RESET"
    assert unfilled.diagnostics["unfilled_entry_terminal_counts"] == {
        "UNFILLED_ENTRY_EXPIRED": 1,
    }

    # A full entry has no still-open parent, only its protective children.
    full = _Harness(
        state="ORDER_SUBMITTED",
        open_order_ids=("STOP-1", "TARGET-1"),
        flat=False,
    )
    full.on_position_opened(_position_opened("0.100"))
    assert full._scenario_states["SCENARIO-1"] == "POSITION"
    assert not full.cancel_all_calls
    assert not full.close_all_calls
    assert not full._active_trade.get("partial_entry_abort_requested", False)

    # A partial fill leaves the parent entry open.  The implementation must
    # abort rather than carry both a position and a working new-entry remainder.
    partial = _Harness(
        state="ORDER_SUBMITTED",
        open_order_ids=("ENTRY-1", "STOP-1", "TARGET-1"),
        flat=False,
    )
    partial.on_position_opened(_position_opened("0.040"))
    trade = partial._active_trade
    assert trade is not None
    assert trade["partial_entry_abort_requested"] is True
    assert trade["forced_exit_reason"] == "PARTIAL_ENTRY_SINGLE_SLOT_ABORT"
    assert trade["partial_entry_abort_opened_quantity"] == "0.040"
    assert partial._exit_inflight
    assert partial.cancel_all_calls == ["BTCUSDT-PERP.BINANCE"]
    assert partial.close_all_calls == ["BTCUSDT-PERP.BINANCE"]
    assert partial.diagnostics["partial_entry_abort_counts"] == {
        "DEFENSE_ORIGIN_LIMIT": 1,
    }
    assert partial.diagnostics["partial_entry_flatten_requests"] == {
        "INITIAL_ABORT": 1,
    }

    # Child cancellation is not mistaken for the parent terminal event.
    partial.on_order_canceled(_order_event("STOP-1"))
    assert "partial_entry_parent_terminal_counts" not in partial.diagnostics
    assert partial._active_trade is trade

    # The parent terminal callback preserves POSITION state and does not create
    # a duplicate close request while the original flatten is already inflight.
    partial.on_order_canceled(_order_event("ENTRY-1"))
    assert partial._active_trade is trade
    assert partial._scenario_states["SCENARIO-1"] == "POSITION"
    assert trade["partial_entry_parent_terminal_code"] == "UNFILLED_ENTRY_CANCELED"
    assert partial.diagnostics["partial_entry_parent_terminal_counts"] == {
        "UNFILLED_ENTRY_CANCELED": 1,
    }
    assert len(partial.close_all_calls) == 1

    # A later position-size change means the parent filled again before its
    # terminal event; issue a fresh reduce-only flatten attempt.
    partial.on_position_changed(SimpleNamespace(quantity="0.050"))
    assert trade["partial_entry_abort_opened_quantity"] == "0.050"
    assert len(partial.close_all_calls) == 2
    assert partial.diagnostics["partial_entry_flatten_requests"][
        "POSITION_CHANGED_AFTER_ABORT"
    ] == 1

    # A parent cancel rejection receives one bounded retry and another flatten
    # request; it never resets or drops the active POSITION record.
    partial.on_order_cancel_rejected(
        _order_event("ENTRY-1", reason="venue temporarily refused cancel"),
    )
    assert partial._active_trade is trade
    assert trade["partial_entry_cancel_retry_count"] == 1
    assert len(partial.cancel_all_calls) == 2
    assert len(partial.close_all_calls) == 3
    assert partial.diagnostics["partial_entry_cancel_rejections"] == {
        "venue temporarily refused cancel": 1,
    }
    assert any("partial-entry parent cancel rejected" in error for error in partial.errors)

    print("ADOM lifecycle contract tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
