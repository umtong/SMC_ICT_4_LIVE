"""Causal entry-placement policies for completed auction-state decisions.

The detector never creates orders or computes PnL. It converts an already
completed scenario decision into either an immediate market placement or a
passive, causally expiring limit instruction.

Two structural limit policies are supported:

* SAC defense-origin mitigation waits at the open of the completed defense bar.
* LCOR reaccept-failure mitigation waits at the exact half-back between the
  completed second-failure close and the already-known failed ownership
  boundary. The first failure, reacceptance and second failure are all complete
  before that order can exist.
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

    ``ts_ns`` is the completed-bar observation time. Candidate market data
    stamps a source interval [t, t + 1 minute) at t + 1 minute, so auction
    membership must be calculated from ``ts_ns - ONE_MINUTE_NS``. Without this
    conversion a decision observed exactly on a fixed-auction boundary would be
    assigned to the following auction and receive an impossible extra period of
    order lifetime.
    """

    if ts_ns <= ONE_MINUTE_NS:
        raise ValueError("ts_ns must be later than one completed source minute")
    if period_minutes <= 0:
        raise ValueError("period_minutes must be positive")
    period_ns = period_minutes * 60 * 1_000_000_000
    source_interval_ts_ns = ts_ns - ONE_MINUTE_NS
    return ((source_interval_ts_ns // period_ns) + 1) * period_ns


def _market_placement(
    *,
    close: float,
    details: Mapping[str, Any],
) -> EntryPlacement:
    return EntryPlacement(
        mode="MARKET_AFTER_DEFENSE",
        order_type="MARKET",
        expected_entry=close,
        expiry_ts_ns=None,
        reason=None,
        details=details,
    )


def _resolve_lcor_reaccept_failure_half_back(
    original_signal: Any,
    resolved_signal: Any,
    snapshot: Any,
    params: Mapping[str, Any],
    *,
    base_details: Mapping[str, Any],
) -> EntryPlacement:
    """Return a passive second-failure half-back placement.

    The failed ownership boundary and the second-failure close are known at the
    completed decision timestamp. Their midpoint is the non-fitted equilibrium
    of the completed displacement away from the boundary. The limit expires at
    the end of the same fixed LCOR auction, so a later auction cannot rescue it.
    """

    decision_ts_ns = int(snapshot.observation.ts_ns)
    close = float(snapshot.observation.close)
    direction = str(getattr(resolved_signal, "direction", "")).upper()
    target = float(getattr(resolved_signal, "target_price"))
    signal_reference = float(getattr(resolved_signal, "reference_entry", close))
    boundary = float(getattr(resolved_signal, "liquidity_level"))
    expected_entry = (signal_reference + boundary) / 2.0

    if direction == "LONG":
        boundary_is_favorable = boundary < signal_reference
        limit_is_passive = expected_entry < close
        objective_already_touched = float(snapshot.observation.high) >= target
    elif direction == "SHORT":
        boundary_is_favorable = boundary > signal_reference
        limit_is_passive = expected_entry > close
        objective_already_touched = float(snapshot.observation.low) <= target
    else:
        return EntryPlacement(
            mode="FAILED_BOUNDARY_HALF_BACK_LIMIT",
            order_type="LIMIT",
            expected_entry=expected_entry,
            expiry_ts_ns=None,
            reason="UNSUPPORTED_REACCEPT_FAILURE_DIRECTION",
            details={**base_details, "direction": direction},
        )

    period_minutes = int(params.get("ciot_auction_period_minutes", 15))
    expiry_ts_ns = _next_interval_boundary_ns(decision_ts_ns, period_minutes)
    details = {
        **base_details,
        "direction": direction,
        "failed_ownership_boundary": boundary,
        "second_failure_close": signal_reference,
        "failed_boundary_half_back_price": expected_entry,
        "boundary_is_favorable": boundary_is_favorable,
        "limit_is_passive_at_submission": limit_is_passive,
        "objective_price": target,
        "objective_already_touched": objective_already_touched,
        "auction_period_minutes": period_minutes,
        "entry_expiry_ts_ns": expiry_ts_ns,
        "remaining_seconds": (
            expiry_ts_ns - decision_ts_ns
        ) / 1_000_000_000,
        "placement_contract": (
            "midpoint of completed second-failure close and pre-existing failed "
            "ownership boundary; no future bar or outcome is inspected"
        ),
    }
    if not boundary_is_favorable:
        return EntryPlacement(
            mode="FAILED_BOUNDARY_HALF_BACK_LIMIT",
            order_type="LIMIT",
            expected_entry=expected_entry,
            expiry_ts_ns=expiry_ts_ns,
            reason="FAILED_BOUNDARY_NOT_ON_FAVORABLE_ENTRY_SIDE",
            details=details,
        )
    if not limit_is_passive:
        return EntryPlacement(
            mode="FAILED_BOUNDARY_HALF_BACK_LIMIT",
            order_type="LIMIT",
            expected_entry=expected_entry,
            expiry_ts_ns=expiry_ts_ns,
            reason="FAILED_BOUNDARY_HALF_BACK_IS_NOT_PASSIVE",
            details=details,
        )
    if objective_already_touched:
        return EntryPlacement(
            mode="FAILED_BOUNDARY_HALF_BACK_LIMIT",
            order_type="LIMIT",
            expected_entry=expected_entry,
            expiry_ts_ns=expiry_ts_ns,
            reason="SECOND_FAILURE_BAR_OBJECTIVE_ALREADY_TOUCHED",
            details=details,
        )
    if expiry_ts_ns <= decision_ts_ns:
        return EntryPlacement(
            mode="FAILED_BOUNDARY_HALF_BACK_LIMIT",
            order_type="LIMIT",
            expected_entry=expected_entry,
            expiry_ts_ns=expiry_ts_ns,
            reason="FAILED_BOUNDARY_HALF_BACK_HAS_NO_CAUSAL_LIFETIME",
            details=details,
        )
    return EntryPlacement(
        mode="FAILED_BOUNDARY_HALF_BACK_LIMIT",
        order_type="LIMIT",
        expected_entry=expected_entry,
        expiry_ts_ns=expiry_ts_ns,
        reason=None,
        details=details,
    )


def resolve_entry_placement(
    original_signal: Any,
    resolved_signal: Any,
    snapshot: Any,
    params: Mapping[str, Any],
    *,
    confirmation_passed: bool,
    trap_armed: bool,
) -> EntryPlacement:
    """Return a causal market or structural passive-limit placement.

    Only the completed decision bar and fields already attached to the causal
    scenario signal are inspected. No future bar, fill, target outcome or PnL is
    available to this function.
    """

    decision_ts_ns = int(snapshot.observation.ts_ns)
    close = float(snapshot.observation.close)
    source_family = str(getattr(original_signal, "family", "")).upper()
    resolved_family = str(getattr(resolved_signal, "family", "")).upper()
    sac_configured = str(
        params.get("sac_entry_execution", "MARKET_AFTER_DEFENSE"),
    ).upper()
    lcor_configured = str(
        params.get(
            "lcor_reaccept_failure_entry_execution",
            "MARKET_ON_SECOND_FAILURE",
        ),
    ).upper()
    configured = (
        lcor_configured if source_family == "LCOR_RF" else sac_configured
    )
    base_details = {
        "configured_mode": configured,
        "decision_ts_ns": decision_ts_ns,
        "source_interval_ts_ns": decision_ts_ns - ONE_MINUTE_NS,
        "decision_open": float(snapshot.observation.open),
        "decision_high": float(snapshot.observation.high),
        "decision_low": float(snapshot.observation.low),
        "decision_close": close,
        "source_family": source_family,
        "resolved_family": resolved_family,
        "confirmation_passed": bool(confirmation_passed),
        "trap_armed": bool(trap_armed),
    }

    if (
        source_family == "LCOR_RF"
        and resolved_family == "LCOR_RF"
        and lcor_configured == "FAILED_BOUNDARY_HALF_BACK_LIMIT"
        and not trap_armed
    ):
        return _resolve_lcor_reaccept_failure_half_back(
            original_signal,
            resolved_signal,
            snapshot,
            params,
            base_details=base_details,
        )

    if (
        sac_configured != "DEFENSE_ORIGIN_LIMIT"
        or source_family != "SAC"
        or trap_armed
        or not confirmation_passed
    ):
        return _market_placement(close=close, details=base_details)

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
