"""Runtime lifecycle and actual-fill validity corrections for Candidate 51.

Two generic execution defects are corrected without changing any alpha rule:

1. Nautilus passes the close timestamp positionally to ``_event``.  The legacy
   callback also expanded a record containing ``ts_event``, duplicating that
   argument.  The corrected record uses ``closed_ts_event``.
2. A market entry is sized and bracketed from the completed signal-bar
   reference.  The next executable fill can gap through the structural stop,
   beyond the objective, or far enough that the actual stop-out loss exceeds
   the current-NAV 3% budget.  In that state a protective stop is rejected as
   already in the market.  The intended scenario no longer exists, so the only
   valid action is immediate flattening; moving the stop would rewrite the
   scenario after seeing the fill.

This patch is installed before the importable strategy is instantiated.  It
changes neither signal classification nor thresholds, objectives, planned
stops, risk fraction, fees, slippage model, or account accounting.
"""
from __future__ import annotations

import math
from typing import Any

import strategy


def _as_float(value: Any, default: float = math.nan) -> float:
    if value is None:
        return default
    method = getattr(value, "as_double", None)
    if callable(method):
        number = float(method())
        return number if math.isfinite(number) else default
    text = str(value).strip().split()[0].replace("_", "").replace(",", "")
    try:
        number = float(text)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def _on_position_opened(self: Any, event: Any) -> None:
    self.entry_pending = False
    self.position_open_minute = self.minute_index
    ts_event = int(getattr(event, "ts_event", self._latest_ts()))
    scenario = self.current_scenario
    self._event("POSITION_OPENED", ts_event, event=str(event))
    if not scenario:
        return

    symbol = str(scenario.get("symbol", self.current_symbol or ""))
    side = int(scenario.get("side", 0))
    stop = _as_float(scenario.get("stop"))
    target = _as_float(scenario.get("target"))
    quantity = _as_float(scenario.get("quantity"), 0.0)
    risk_budget = _as_float(scenario.get("risk_budget"), 0.0)
    fill = _as_float(getattr(event, "avg_px_open", None))
    if not math.isfinite(fill):
        # The textual PositionOpened contract contains avg_px_open even on
        # engines that do not expose it as a direct attribute.
        text = str(event)
        marker = "avg_px_open="
        if marker in text:
            fill = _as_float(text.split(marker, 1)[1].split(",", 1)[0])

    geometry_valid = (
        side > 0 and 0.0 < stop < fill < target
    ) or (
        side < 0 and 0.0 < target < fill < stop
    )
    fee_rate = float(self.config.all_in_cost_bps_each_side) / 10_000.0
    funding_rate = float(self.config.funding_reserve_bps) / 10_000.0
    actual_loss_per_unit = (
        abs(fill - stop)
        + fee_rate * (abs(fill) + abs(stop))
        + funding_rate * abs(fill)
        if geometry_valid else math.inf
    )
    actual_account_loss = quantity * actual_loss_per_unit
    risk_valid = (
        geometry_valid
        and quantity > 0.0
        and risk_budget > 0.0
        and math.isfinite(actual_account_loss)
        and actual_account_loss <= risk_budget + max(0.01, risk_budget * 1e-9)
    )
    scenario.update(
        {
            "actual_entry_fill": fill if math.isfinite(fill) else None,
            "actual_planned_loss_per_unit": (
                actual_loss_per_unit
                if math.isfinite(actual_loss_per_unit) else None
            ),
            "actual_planned_account_loss": (
                actual_account_loss
                if math.isfinite(actual_account_loss) else None
            ),
            "actual_fill_geometry_valid": bool(geometry_valid),
            "actual_fill_risk_valid": bool(risk_valid),
        }
    )
    if risk_valid:
        return

    diagnostics = self.diagnostics
    diagnostics["fill_invalidations"] = int(
        diagnostics.get("fill_invalidations", 0)
    ) + 1
    if not geometry_valid:
        diagnostics["fill_beyond_bracket_invalidations"] = int(
            diagnostics.get("fill_beyond_bracket_invalidations", 0)
        ) + 1
        reason = "ACTUAL_FILL_OUTSIDE_FROZEN_BRACKET"
    else:
        diagnostics["fill_risk_budget_invalidations"] = int(
            diagnostics.get("fill_risk_budget_invalidations", 0)
        ) + 1
        reason = "ACTUAL_FILL_EXCEEDS_THREE_PERCENT_LOSS_BUDGET"
    self._event(
        "ENTRY_FILL_INVALIDATED",
        ts_event,
        symbol=symbol,
        side=side,
        reason=reason,
        actual_fill=fill if math.isfinite(fill) else None,
        frozen_stop=stop if math.isfinite(stop) else None,
        frozen_target=target if math.isfinite(target) else None,
        quantity=quantity,
        risk_budget=risk_budget,
        actual_planned_account_loss=(
            actual_account_loss if math.isfinite(actual_account_loss) else None
        ),
    )
    instrument_id = self.instrument_ids.get(symbol)
    if instrument_id is not None:
        self.cancel_all_orders(instrument_id)
        self.close_all_positions(instrument_id)


def _on_position_closed(self: Any, event: Any) -> None:
    ts_event = int(getattr(event, "ts_event", self._latest_ts()))
    record = dict(self.current_scenario or {})
    record.update(
        {
            "closed_ts_event": ts_event,
            "realized_pnl": str(getattr(event, "realized_pnl", None)),
            "event": str(event),
        },
    )
    self.closed_scenarios.append(record)
    self._event("POSITION_CLOSED", ts_event, **record)
    self._clear_trade_state()


strategy.Candidate35Strategy.on_position_opened = _on_position_opened
strategy.Candidate35Strategy.on_position_closed = _on_position_closed
