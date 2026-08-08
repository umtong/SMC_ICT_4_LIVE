#!/usr/bin/env python3
"""Idempotently register CIRB discharge half-back placement.

This is a narrow source migration. It does not change the CIRB parent event,
branch, direction, stop, target, costs, risk, or signal timestamp. It only lets
the existing execution layer represent a post-signal passive retest at the
midpoint between the completed reversal close and the already-observed
liquidation-wave extreme.
"""

from __future__ import annotations

from pathlib import Path


MARKER = "def _resolve_cirb_discharge_half_back("

FUNCTION = r'''

def _resolve_cirb_discharge_half_back(
    original_signal: Any,
    resolved_signal: Any,
    snapshot: Any,
    params: Mapping[str, Any],
    *,
    base_details: Mapping[str, Any],
) -> EntryPlacement:
    """Return a passive half-back placement for a frozen CIRB discharge reversal.

    ``liquidity_level`` is the completed deleveraging-wave extreme already
    attached by the parent OIDB/CIRB state machine. The signal close and this
    boundary are both known before the order exists. Their midpoint is used as
    a non-fitted mitigation price. The order expires with the same completed
    five-minute inventory-response auction.
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
            mode="CROWD_DISCHARGE_HALF_BACK_LIMIT",
            order_type="LIMIT",
            expected_entry=expected_entry,
            expiry_ts_ns=None,
            reason="UNSUPPORTED_CROWD_DISCHARGE_DIRECTION",
            details={**base_details, "direction": direction},
        )

    period_minutes = int(params.get("cirb_entry_auction_period_minutes", 5))
    logical_boundary_ts_ns = _next_interval_boundary_ns(
        decision_ts_ns,
        period_minutes,
    )
    expiry_ts_ns = logical_boundary_ts_ns + BAR_BOUNDARY_GTD_ENCODING_NS
    details = {
        **base_details,
        "direction": direction,
        "crowd_discharge_extreme": boundary,
        "crowd_discharge_signal_close": signal_reference,
        "crowd_discharge_half_back_price": expected_entry,
        "boundary_is_favorable": boundary_is_favorable,
        "limit_is_passive_at_submission": limit_is_passive,
        "objective_price": target,
        "objective_already_touched": objective_already_touched,
        "auction_period_minutes": period_minutes,
        "logical_auction_boundary_ts_ns": logical_boundary_ts_ns,
        "entry_expiry_ts_ns": expiry_ts_ns,
        "bar_boundary_gtd_encoding_ns": BAR_BOUNDARY_GTD_ENCODING_NS,
        "remaining_seconds": (
            logical_boundary_ts_ns - decision_ts_ns
        ) / 1_000_000_000,
        "placement_contract": (
            "midpoint of completed CIRB discharge-reversal close and the "
            "pre-existing deleveraging-wave extreme; parent state, direction, "
            "stop and objective are unchanged; logical lifetime ends with the "
            "same five-minute response auction"
        ),
    }
    if not boundary_is_favorable:
        return EntryPlacement(
            mode="CROWD_DISCHARGE_HALF_BACK_LIMIT",
            order_type="LIMIT",
            expected_entry=expected_entry,
            expiry_ts_ns=expiry_ts_ns,
            reason="CROWD_DISCHARGE_EXTREME_NOT_ON_FAVORABLE_ENTRY_SIDE",
            details=details,
        )
    if not limit_is_passive:
        return EntryPlacement(
            mode="CROWD_DISCHARGE_HALF_BACK_LIMIT",
            order_type="LIMIT",
            expected_entry=expected_entry,
            expiry_ts_ns=expiry_ts_ns,
            reason="CROWD_DISCHARGE_HALF_BACK_IS_NOT_PASSIVE",
            details=details,
        )
    if objective_already_touched:
        return EntryPlacement(
            mode="CROWD_DISCHARGE_HALF_BACK_LIMIT",
            order_type="LIMIT",
            expected_entry=expected_entry,
            expiry_ts_ns=expiry_ts_ns,
            reason="CROWD_DISCHARGE_SIGNAL_BAR_OBJECTIVE_ALREADY_TOUCHED",
            details=details,
        )
    if logical_boundary_ts_ns <= decision_ts_ns:
        return EntryPlacement(
            mode="CROWD_DISCHARGE_HALF_BACK_LIMIT",
            order_type="LIMIT",
            expected_entry=expected_entry,
            expiry_ts_ns=expiry_ts_ns,
            reason="CROWD_DISCHARGE_HALF_BACK_HAS_NO_CAUSAL_LIFETIME",
            details=details,
        )
    return EntryPlacement(
        mode="CROWD_DISCHARGE_HALF_BACK_LIMIT",
        order_type="LIMIT",
        expected_entry=expected_entry,
        expiry_ts_ns=expiry_ts_ns,
        reason=None,
        details=details,
    )
'''


def main() -> int:
    path = Path(__file__).with_name("defense_origin_limit.py")
    text = path.read_text(encoding="utf-8")
    if MARKER not in text:
        needle = "\n\ndef resolve_entry_placement("
        if needle not in text:
            raise RuntimeError("resolve_entry_placement insertion point missing")
        text = text.replace(needle, FUNCTION + needle, 1)

    old_config = '''    configured = (\n        lcor_configured if source_family == "LCOR_RF" else sac_configured\n    )'''
    new_config = '''    cirb_configured = str(\n        params.get(\n            "cirb_discharge_reversal_entry_execution",\n            "MARKET_ON_RESPONSE_CLOSE",\n        ),\n    ).upper()\n    if source_family == "LCOR_RF":\n        configured = lcor_configured\n    elif source_family == "CIRB_D_R":\n        configured = cirb_configured\n    else:\n        configured = sac_configured'''
    if old_config in text:
        text = text.replace(old_config, new_config, 1)
    elif "cirb_discharge_reversal_entry_execution" not in text:
        raise RuntimeError("configured-mode replacement point missing")

    old_branch = '''        return _resolve_lcor_reaccept_failure_half_back(\n            original_signal,\n            resolved_signal,\n            snapshot,\n            params,\n            base_details=base_details,\n        )\n\n    if (\n        sac_configured != "DEFENSE_ORIGIN_LIMIT"'''
    new_branch = '''        return _resolve_lcor_reaccept_failure_half_back(\n            original_signal,\n            resolved_signal,\n            snapshot,\n            params,\n            base_details=base_details,\n        )\n\n    if (\n        source_family == "CIRB_D_R"\n        and resolved_family == "CIRB_D_R"\n        and cirb_configured == "CROWD_DISCHARGE_HALF_BACK_LIMIT"\n        and not trap_armed\n    ):\n        return _resolve_cirb_discharge_half_back(\n            original_signal,\n            resolved_signal,\n            snapshot,\n            params,\n            base_details=base_details,\n        )\n\n    if (\n        sac_configured != "DEFENSE_ORIGIN_LIMIT"'''
    if old_branch in text:
        text = text.replace(old_branch, new_branch, 1)
    elif "and cirb_configured == \"CROWD_DISCHARGE_HALF_BACK_LIMIT\"" not in text:
        raise RuntimeError("CIRB placement branch insertion point missing")

    path.write_text(text, encoding="utf-8")
    print("CIRB discharge half-back placement registered")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
