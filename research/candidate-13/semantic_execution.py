"""Execution constants shared by Candidate 13 plan and runner materialization."""
from __future__ import annotations


# Historical sentinel retained only as explicit evidence that an immediate plan
# has no GTD lifetime. The materialized runner branches on ``entry_order_type``.
MARKET_ENTRY_SENTINEL_NS = 946684800000000000  # 2000-01-01T00:00:00Z
