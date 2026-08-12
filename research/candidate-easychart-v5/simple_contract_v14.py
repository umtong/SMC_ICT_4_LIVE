"""Non-negotiable execution contract for the simplified EasyChart candidate.

These constants are policy, not optimization parameters.  Research may change
how a valid market scenario is discovered, but it must not silently change the
position-management or account-risk contract.
"""
from __future__ import annotations

from decimal import Decimal


FIXED_RISK_FRACTION = Decimal("0.03")
MINIMUM_GROSS_RR = Decimal("1.0")

POSITION_MANAGEMENT = "ONE_ENTRY_ONE_FULL_STOP_ONE_FULL_TARGET"
PARTIAL_PROFIT_TAKING = False
PARTIAL_STOPPING = False
DAILY_LOSS_LIMIT = None
TIME_BASED_FORCED_EXIT = None
TRADE_COUNT_LIMIT = None

# A backtest may flatten an open position only when the evaluation itself ends.
# That accounting boundary is not a strategy exit and must be reported as such.
EVALUATION_END_FLATTEN_ONLY = True


def contract_record() -> dict[str, object]:
    """Return a JSON-safe immutable description for every run artifact."""
    return {
        "fixed_risk_fraction": float(FIXED_RISK_FRACTION),
        "minimum_gross_rr": float(MINIMUM_GROSS_RR),
        "position_management": POSITION_MANAGEMENT,
        "partial_profit_taking": PARTIAL_PROFIT_TAKING,
        "partial_stopping": PARTIAL_STOPPING,
        "daily_loss_limit": DAILY_LOSS_LIMIT,
        "time_based_forced_exit": TIME_BASED_FORCED_EXIT,
        "trade_count_limit": TRADE_COUNT_LIMIT,
        "entry_stop_target_fixed_before_submission": True,
        "evaluation_end_flatten_only": EVALUATION_END_FLATTEN_ONLY,
    }
