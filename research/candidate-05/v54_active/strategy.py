"""Controlled import shim for Candidate 05 v54."""
from strategy_v54_failed_inventory_acceptance import FailedInventoryAcceptanceStrategy
from strategy_base import LiquidityResponseConfig

LiquidityResponseStrategy = FailedInventoryAcceptanceStrategy

__all__ = ["FailedInventoryAcceptanceStrategy", "LiquidityResponseConfig", "LiquidityResponseStrategy"]
