"""Runtime binding for one-minute constituent flow delivery validation."""
from easychart_re1_delivery_draw_v4 import FlowValidatedLiquidityDraw


class FlowValidatedLiquidityDrawFixed(FlowValidatedLiquidityDraw):
    TRIGGER_MINUTES = 1
