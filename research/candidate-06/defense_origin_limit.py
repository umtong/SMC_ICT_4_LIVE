"""Causal entry-placement detector for accepted-auction continuation.

The detector does not create orders or compute PnL.  It converts an already
completed SAC retest plus its already completed directional-defense bar into an
entry-placement instruction.  The full ADOM variant waits passively at the open
of that defense bar (the origin of the confirming displacement) until the fixed
auction which produced the setup ends.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from causal_clock import ONE_MINUTE_NS


@dataclass(frozen=True, slots=True)
class EntryPlacement:
    mode: str
    order_type: str
    expected_entry: float
    expiry_ts_ns: int | None
    reason: str | None
    details: Mapping[str, Any]


def _next_interval_boundary_ns(ts_ns: int, period_minutes: int) -> int:
    """Return the end of the source auction containing a completed bar.

    ``ts_ns`` is the completed-bar observation time.  Candidate market data
    stamps a source interval [t, t + 1 minute) at t + 1 minute, so auction
    membership must be calculated from ``ts_ns - ONE_MINUTE_NS``.  Without this
    conversion a defense bar observed exactly on a fixed-auction boundary would
    be assigned to the following auction and receive an impossible extra period
    of order lifetime.
    """

    if ts_ns <= ONE_MINUTE_NS:
        raise ValueError("ts_ns must be later than one completed source minute")
    if period_minutes <= 0:
        raise ValueError("period_minutes must be positive")
    period_ns = period_minutes * 60 * 1_000_000_000
    source_interval_ts_ns = ts_ns - ONE_MINUTE_NS
    return ((source_interval_ts_ns // period_ns) + 1) * period_ns


def resolve_entry_placement(
    original_signal: Any,
    resolved_signal: Any,
    snapshot: Any,
    params: Mapping[str, Any],
    *,
    confirmation_passed: bool,
    trap_armed: bool,
) -> EntryPlacement:
    """Return a causal market or defense-origin limit placement.

    Only the completed decision bar is inspected.  No future bar, fill, target
    outcome, or PnL is available to this function.
    """

    decision_ts_ns = int(snapshot.observation.ts_ns)
    close = float(snapshot.observation.close)
    configured = str(params.get("sac_entry_execution", "MARKET_AFTER_DEFENSE")).upper()
    base_details = {
        "configured_mode": configured,
        "decision_ts_ns": decision_ts_ns,
        "source_interval_ts_ns": decision_ts_ns - ONE_MINUTE_NS,
        "decision_open": float(snapshot.observation.open),
        "decision_high": float(snapshot.observation.high),
        "decision_low": float(snapshot.observation.low),
        "decision_close": close,
        "source_family": str(getattr(original_signal, "family", "")),
        "resolved_family": str(getattr(resolved_signal, "family", "")),
        "confirmation_passed": bool(confirmation_passed),
        "trap_armed": bool(trap_armed),
    }
    if (
        configured != "DEFENSE_ORIGIN_LIMIT"
        or str(getattr(original_signal, "family", "")).upper() != "SAC"
        or trap_armed
        or not confirmation_passed
    ):
        return EntryPlacement(
            mode="MARKET_AFTER_DEFENSE",
            order_type="MARKET",
            expected_entry=close,
            expiry_ts_ns=None,
            reason=None,
            details=base_details,
        )

    direction = str(getattr(resolved_signal, "direction", "")).upper()
    target = float(getattr(resolved_signal, "target_price"))
    origin = float(snapshot.observation.open)
    if direction == "LONG":
        objective_already_touched = float(snapshot.observation.high) >= target
    elif direction == "SHORT":
        objective_already_touched = float(snapshot.observation.low) <= target
    else:
        return EntryPlacement(
            mode="DEFENSE_ORIGIN_LIMIT",
            order_type="LIMIT",
            expected_entry=origin,
            expiry_ts_ns=None,
            reason="UNSUPPORTED_DEFENSE_ORIGIN_DIRECTION",
            details={**base_details, "direction": direction},
        )

    period_minutes = int(params.get("auction_period_minutes", 60))
    expiry_ts_ns = _next_interval_boundary_ns(decision_ts_ns, period_minutes)
    details = {
        **base_details,
        "direction": direction,
        "defense_origin_price": origin,
        "objective_price": target,
        "objective_already_touched": objective_already_touched,
        "auction_period_minutes": period_minutes,
        "entry_expiry_ts_ns": expiry_ts_ns,
        "remaining_seconds": (
            expiry_ts_ns - decision_ts_ns
        ) / 1_000_000_000,
    }
    if objective_already_touched:
        return EntryPlacement(
            mode="DEFENSE_ORIGIN_LIMIT",
            order_type="LIMIT",
            expected_entry=origin,
            expiry_ts_ns=expiry_ts_ns,
            reason="DEFENSE_BAR_OBJECTIVE_ALREADY_TOUCHED",
            details=details,
        )
    if expiry_ts_ns <= decision_ts_ns:
        return EntryPlacement(
            mode="DEFENSE_ORIGIN_LIMIT",
            order_type="LIMIT",
            expected_entry=origin,
            expiry_ts_ns=expiry_ts_ns,
            reason="DEFENSE_ORIGIN_ENTRY_HAS_NO_CAUSAL_LIFETIME",
            details=details,
        )
    return EntryPlacement(
        mode="DEFENSE_ORIGIN_LIMIT",
        order_type="LIMIT",
        expected_entry=origin,
        expiry_ts_ns=expiry_ts_ns,
        reason=None,
        details=details,
    )
