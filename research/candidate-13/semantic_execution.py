"""Nautilus order-boundary adapter for immediate FAR execution.

The inherited portfolio runner always materializes a passive GTD parent.  A
confirmed failed auction can instead require immediate liquidity-taking when
waiting for a retrace would abandon the already-confirmed displacement.  The
TradePlan marks that case with an impossible historical GTD sentinel.  This
adapter converts only that exact parent into a Nautilus MARKET bracket while
leaving its stop-market and take-profit children unchanged.

The sentinel avoids mutable registries: a plan rejected by cross-market gating
cannot leak execution state into a later plan.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable

from nautilus_trader.model.enums import OrderType, TimeInForce


MARKET_ENTRY_SENTINEL_NS = 946684800000000000  # 2000-01-01T00:00:00Z
_SENTINEL = datetime.fromtimestamp(
    MARKET_ENTRY_SENTINEL_NS / 1_000_000_000,
    tz=timezone.utc,
)
_ORIGINAL_BRACKET: Callable[..., Any] | None = None


def is_market_entry_expiry(value: Any) -> bool:
    """Recognize the runner's +1 microsecond conversion of the sentinel."""
    if not isinstance(value, datetime):
        return False
    return abs((value - _SENTINEL).total_seconds()) <= 0.000002


def install() -> None:
    """Install the narrow OrderFactory boundary conversion exactly once."""
    global _ORIGINAL_BRACKET
    if _ORIGINAL_BRACKET is not None:
        return

    from nautilus_trader.common.factories import OrderFactory

    original = OrderFactory.bracket

    def bracket(self: Any, *args: Any, **kwargs: Any) -> Any:
        expire_time = kwargs.get("expire_time")
        if is_market_entry_expiry(expire_time):
            converted = dict(kwargs)
            converted["entry_order_type"] = OrderType.MARKET
            converted["time_in_force"] = TimeInForce.GTC
            converted.pop("entry_price", None)
            converted.pop("expire_time", None)
            converted.pop("entry_post_only", None)
            return original(self, *args, **converted)
        return original(self, *args, **kwargs)

    OrderFactory.bracket = bracket
    _ORIGINAL_BRACKET = original
