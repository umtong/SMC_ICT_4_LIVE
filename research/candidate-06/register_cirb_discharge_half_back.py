#!/usr/bin/env python3
"""Idempotently register CIRB discharge half-back placement.

This narrow source migration leaves the CIRB parent event, branch, direction,
stop, target, costs, risk and signal timestamp unchanged.  It adds a passive
retest at the midpoint between the completed reversal close and the already-
observed liquidation-wave extreme.  In rescue-only mode, already-economic
market entries remain market orders; half-back is used only when the exact
existing cost model would otherwise reject the response-close entry.
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
    """Return a causal half-back placement for a frozen CIRB discharge reversal.

    ``liquidity_level`` is the completed deleveraging-wave extreme already
    attached by the parent OIDB/CIRB state machine. The signal close and this
    boundary are known before the order exists. Their midpoint is a non-fitted
    mitigation price. The order expires with the same five-minute response
    auction.  Optional rescue-only routing preserves a response-close market
    entry whenever the exact predeclared fee/slippage model already clears the
    minimum net reward/risk gate.
    """

    decision_ts_ns = int(snapshot.observation.ts_ns)
    close = float(snapshot.observation.close)
    direction = str(getattr(resolved_signal, "direction", "")).upper()
    target = float(getattr(resolved_signal, "target_price"))
    stop = float(getattr(resolved_signal, "stop_price"))
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

    fee = float(params.get("cirb_entry_effective_fee_rate", 0.0))
    tick = float(params.get("cirb_entry_tick_size", 0.0))
    one_tick_slippage = bool(
        params.get("cirb_entry_one_tick_slippage_per_fill", False),
    )
    slippage_loss = 2.0 * tick if one_tick_slippage else 0.0
    market_loss_per_unit = (
        abs(close - stop) + close * fee + stop * fee + slippage_loss
    )
    market_reward_after_cost = (
        abs(target - close) - close * fee - target * fee - slippage_loss
    )
    market_net_rr = (
        market_reward_after_cost / market_loss_per_unit
        if market_loss_per_unit > 0.0
        else -1.0
    )
    minimum_net_rr = float(
        params.get("minimum_net_rr_after_entry_delay", 0.60),
    )
    rescue_only = bool(
        params.get("cirb_discharge_half_back_rescue_only", False),
    )
    rescue_details = {
        **base_details,
        "direction": direction,
        "crowd_discharge_rescue_only": rescue_only,
        "market_entry_before_rescue": close,
        "market_net_rr_before_rescue": market_net_rr,
        "minimum_net_rr_after_entry_delay": minimum_net_rr,
        "entry_fee_rate_used_for_rescue": fee,
        "entry_tick_size_used_for_rescue": tick,
        "entry_one_tick_slippage_used_for_rescue": one_tick_slippage,
    }
    if rescue_only and market_net_rr >= minimum_net_rr:
        return _market_placement(
            close=close,
            details={
                **rescue_details,
                "crowd_discharge_rescue_decision": "MARKET_GEOMETRY_ALREADY_ECONOMIC",
            },
        )

    period_minutes = int(params.get("cirb_entry_auction_period_minutes", 5))
    logical_boundary_ts_ns = _next_interval_boundary_ns(
        decision_ts_ns,
        period_minutes,
    )
    expiry_ts_ns = logical_boundary_ts_ns + BAR_BOUNDARY_GTD_ENCODING_NS
    details = {
        **rescue_details,
        "crowd_discharge_rescue_decision": (
            "HALF_BACK_REQUIRED_BY_COST_AFTER_GEOMETRY"
            if rescue_only
            else "HALF_BACK_PREDECLARED_FOR_ALL_DISCHARGE_REVERSALS"
        ),
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
