"""Price-protected bounded execution for the exhaustion-reversal alpha.

The alpha and risk geometry are unchanged.  A marketable LIMIT-GTD bracket is
priced at the precomputed adverse-fill budget and may accumulate partial fills
from the volume-preserving actual aggTrade window for fifteen seconds.  This is
closer to a live pay-through order than either FOK against one sampled print or
MARKET-IOC against one print: price is bounded, time is bounded, and quantity
must be supported by recorded opposite-side trade volume.
"""
from __future__ import annotations

from datetime import datetime, timezone
import math
from typing import Any

from nautilus_trader.model.enums import OrderSide, OrderType, TimeInForce

from candidate21_strategy import Candidate21Config, Candidate21Strategy
from logic import floor_quantity, net_r_at_price, planned_loss_per_unit
from strategy_base import PendingSetup, _as_float


class WindowedLimitConfig(Candidate21Config, frozen=True):
    exhaustion_entry_expiry_seconds: float = 15.0
    exhaustion_min_fill_fraction: float = 0.95


class WindowedLimitStrategy(Candidate21Strategy):
    """Execute only price-protected reversals with recorded volume capacity."""

    def __init__(self, config: WindowedLimitConfig) -> None:
        super().__init__(config=config)
        if config.exhaustion_entry_expiry_seconds <= 0.0:
            raise ValueError("exhaustion_entry_expiry_seconds must be positive")
        if not 0.0 < config.exhaustion_min_fill_fraction <= 1.0:
            raise ValueError("exhaustion_min_fill_fraction must be in (0, 1]")
        self.current_entry_worst_fill: float | None = None
        self.current_entry_side = 0
        self.diagnostics.update(
            {
                "exhaustion_windowed_limit_entries": 0,
                "entry_worst_fill_breaches": 0,
                "max_entry_worst_fill_slippage_bps": 0.0,
                "last_entry_requested_quantity": 0.0,
                "last_entry_expiry_ns": 0,
            },
        )

    def _clear_trade_state(self) -> None:
        super()._clear_trade_state()
        self.current_entry_worst_fill = None
        self.current_entry_side = 0

    def on_position_opened(self, event: Any) -> None:
        fill = float(getattr(event, "avg_px_open", float("nan")))
        worst = self.current_entry_worst_fill
        side = self.current_entry_side
        if worst is not None and side in (-1, 1) and math.isfinite(fill) and fill > 0.0:
            signed_slippage_bps = side * (fill / worst - 1.0) * 10_000.0
            self.diagnostics["max_entry_worst_fill_slippage_bps"] = max(
                float(self.diagnostics["max_entry_worst_fill_slippage_bps"]),
                signed_slippage_bps,
            )
            self.diagnostics["last_entry_first_fill"] = fill
            self.diagnostics["last_entry_worst_fill_budget"] = worst
            self.diagnostics["last_entry_first_fill_vs_budget_bps"] = signed_slippage_bps
            if signed_slippage_bps > 1e-9:
                self.diagnostics["entry_worst_fill_breaches"] = int(
                    self.diagnostics["entry_worst_fill_breaches"],
                ) + 1
        super().on_position_opened(event)

    def _submit_exhaustion_entry(
        self,
        *,
        setup: PendingSetup,
        row: dict[str, float | int],
        side: int,
        stop_raw: float,
        target_raw: float,
    ) -> bool:
        signal_close = float(row["close"])
        stop_price = self.instrument.make_price(stop_raw)
        stop = _as_float(stop_price)
        atr = self._atr()
        transition_range = max(0.0, float(row["high"]) - float(row["low"]))
        adverse_fill_distance = max(
            signal_close
            * max(
                self.config.exhaustion_entry_cap_bps,
                self.config.adverse_slippage_bps_each_side,
            )
            / 10_000.0,
            self.config.exhaustion_entry_cap_atr * atr,
            0.5 * transition_range,
        )
        worst_entry_price = self.instrument.make_price(
            signal_close + side * adverse_fill_distance,
        )
        worst_entry = _as_float(worst_entry_price)
        increment = _as_float(self.instrument.price_increment)
        if side > 0 and worst_entry <= signal_close:
            worst_entry_price = self.instrument.make_price(signal_close + increment)
            worst_entry = _as_float(worst_entry_price)
        elif side < 0 and worst_entry >= signal_close:
            worst_entry_price = self.instrument.make_price(signal_close - increment)
            worst_entry = _as_float(worst_entry_price)
        target_price = self.instrument.make_price(target_raw)
        target = _as_float(target_price)

        if (side > 0 and not stop < signal_close < worst_entry < target) or (
            side < 0 and not target < worst_entry < signal_close < stop
        ):
            self.diagnostics["exhaustion_target_consumed"] = int(
                self.diagnostics["exhaustion_target_consumed"],
            ) + 1
            self._close_exhaustion_state(
                setup,
                row,
                "EXHAUSTION_TARGET_CONSUMED_BEFORE_ENTRY",
                "WORST_FILL_STOP_TARGET_DO_NOT_BELONG_TO_ONE_REMAINING_REVERSAL_LEG",
            )
            return True

        cost_rate = self.config.all_in_cost_bps_each_side / 10_000.0
        adverse = self.config.adverse_slippage_bps_each_side / 10_000.0
        planned_loss = planned_loss_per_unit(
            worst_entry,
            stop,
            side,
            cost_rate,
            adverse,
        )
        if not math.isfinite(planned_loss) or planned_loss <= 0.0:
            self.diagnostics["exhaustion_geometry_rejected"] = int(
                self.diagnostics["exhaustion_geometry_rejected"],
            ) + 1
            self._close_exhaustion_state(
                setup,
                row,
                "EXHAUSTION_ENTRY_GEOMETRY_REJECTED",
                "INVALID_ADVERSE_FILL_PLANNED_LOSS",
            )
            return True
        target_r = net_r_at_price(
            worst_entry,
            target,
            side,
            planned_loss,
            cost_rate,
        )
        if target_r + 1e-9 < self.config.min_target_net_r:
            self.diagnostics["exhaustion_geometry_rejected"] = int(
                self.diagnostics["exhaustion_geometry_rejected"],
            ) + 1
            self._close_exhaustion_state(
                setup,
                row,
                "EXHAUSTION_ENTRY_GEOMETRY_REJECTED",
                "NATURAL_EXTERNAL_LIQUIDITY_OBJECTIVE_BELOW_MINIMUM_NET_R",
            )
            return True

        equity = self._equity_value()
        risk_budget = equity * self.config.risk_fraction
        quantity_value = floor_quantity(
            risk_budget / planned_loss,
            int(self.instrument.size_precision),
        )
        if quantity_value <= 0.0 or quantity_value * worst_entry < 10.0:
            self.diagnostics["exhaustion_geometry_rejected"] = int(
                self.diagnostics["exhaustion_geometry_rejected"],
            ) + 1
            self._close_exhaustion_state(
                setup,
                row,
                "EXHAUSTION_ENTRY_GEOMETRY_REJECTED",
                "QUANTITY_BELOW_INSTRUMENT_MINIMUM",
            )
            return True

        event_ns = int(row["ts"])
        expiry_ns = event_ns + int(
            self.config.exhaustion_entry_expiry_seconds * 1_000_000_000,
        )
        expire_time = datetime.fromtimestamp(expiry_ns / 1_000_000_000, tz=timezone.utc)
        order_list = self.order_factory.bracket(
            instrument_id=self.config.instrument_id,
            order_side=OrderSide.BUY if side > 0 else OrderSide.SELL,
            quantity=self.instrument.make_qty(quantity_value),
            entry_order_type=OrderType.LIMIT,
            entry_price=worst_entry_price,
            expire_time=expire_time,
            time_in_force=TimeInForce.GTD,
            entry_post_only=False,
            entry_tags=["ENTRY", "EXTERNAL_EXHAUSTION_LIMIT_GTD"],
            tp_price=target_price,
            sl_trigger_price=stop_price,
        )
        self.submit_order_list(order_list)
        self.entry_pending = True
        self.entry_pending_index = self.bar_index
        self.last_entry_index = self.bar_index
        self.current_scenario_id = setup.scenario_id
        self.current_branch = "EXTERNAL_LIQUIDITY_EXHAUSTION_REVERSAL"
        self.current_pool_level = setup.pool_level
        self.current_entry_worst_fill = worst_entry
        self.current_entry_side = side
        self.pending = None
        self.diagnostics["entry_submissions"] = int(
            self.diagnostics["entry_submissions"],
        ) + 1
        self.diagnostics["exhaustion_windowed_limit_entries"] = int(
            self.diagnostics["exhaustion_windowed_limit_entries"],
        ) + 1
        self.diagnostics["last_entry_requested_quantity"] = quantity_value
        self.diagnostics["last_entry_expiry_ns"] = expiry_ns
        self.diagnostics["max_simultaneous_entry_intents"] = max(
            int(self.diagnostics["max_simultaneous_entry_intents"]),
            1,
        )
        self._transition(
            setup.scenario_id,
            "EXHAUSTION_WINDOWED_LIMIT_ENTRY_SUBMITTED",
            event_ns,
            event_ns,
            "ENTRY_PENDING",
            "EXTERNAL_LIQUIDITY_EXHAUSTION_REVERSAL_LIMIT_GTD",
            worst_entry,
            {
                **setup.details,
                "side": side,
                "signal_close": signal_close,
                "entry_order_type": "LIMIT",
                "entry_time_in_force": "GTD",
                "entry_expiry_ns": expiry_ns,
                "entry_expiry_seconds": self.config.exhaustion_entry_expiry_seconds,
                "entry_limit_worst_fill": worst_entry,
                "entry_adverse_fill_distance": adverse_fill_distance,
                "entry_cap_atr_multiple": self.config.exhaustion_entry_cap_atr,
                "entry_transition_range": transition_range,
                "minimum_acceptable_fill_fraction": self.config.exhaustion_min_fill_fraction,
                "stop": stop,
                "target": target,
                "target_net_r_at_worst_fill": target_r,
                "quantity": quantity_value,
                "equity": equity,
                "risk_budget": risk_budget,
                "planned_loss_per_unit_at_worst_fill": planned_loss,
                "planned_account_loss_at_worst_fill": quantity_value * planned_loss,
                "strictly_later_entry_evidence": True,
                "actual_trade_volume_required": True,
            },
        )
        return True


# Stable aliases keep existing import paths usable while the runner explicitly
# selects the new names.
MarketEntryConfig = WindowedLimitConfig
MarketEntryStrategy = WindowedLimitStrategy


__all__ = [
    "MarketEntryConfig",
    "MarketEntryStrategy",
    "WindowedLimitConfig",
    "WindowedLimitStrategy",
]
