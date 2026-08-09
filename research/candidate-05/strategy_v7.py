#!/usr/bin/env python3
"""Candidate 05 v7: causal micro-auction balance expansion."""
from __future__ import annotations

import math
from typing import Any

from nautilus_trader.model.data import Bar
from nautilus_trader.model.enums import OrderSide, OrderType, TimeInForce

from balance_breakout_logic import BALANCE_BARS
from balance_breakout_logic import BalanceBreakoutEvidence
from balance_breakout_logic import balance_metrics
from balance_breakout_logic import breakout_side
from logic import cost_aware_target
from logic import floor_quantity
from logic import net_r_at_price
from logic import planned_loss_per_unit
from strategy_base import LiquidityResponseConfig
from strategy_base import _as_float
from strategy_v2 import LiquidityResponseRetraceStrategy


class BalanceExpansionStrategy(LiquidityResponseRetraceStrategy):
    """Trade a verified transition from local balance to sponsored expansion.

    The strategy does not treat a candle breakout as a scenario.  A trade exists
    only when a completed twenty-minute low-efficiency auction is followed by a
    close outside its range and all observable sponsorship channels agree:
    persistent aggressive flow, efficient displacement, notional participation,
    open-interest expansion, opposing depth withdrawal and favorable resting
    depth.  The breakout bar itself is the causal invalidation unit; its opposite
    extreme plus the configured structural buffer defines the 3% NAV loss budget.
    NautilusTrader owns every order, fill, fee, position, margin and NAV change.
    """

    def __init__(self, config: LiquidityResponseConfig) -> None:
        super().__init__(config)
        self.diagnostics.update(
            {
                "balance_bars": BALANCE_BARS,
                "balance_observations": 0,
                "balance_breakouts": 0,
                "market_entry_submissions": 0,
                "metrics_not_ready": 0,
                "sponsorship_rejections": 0,
            },
        )

    def on_bar(self, bar: Bar) -> None:
        self.bar_index += 1
        row = {
            "ts": int(bar.ts_event),
            "open": _as_float(bar.open),
            "high": _as_float(bar.high),
            "low": _as_float(bar.low),
            "close": _as_float(bar.close),
            "volume": _as_float(bar.volume),
        }
        self.bars.append(row)
        self._advance_features(int(row["ts"]))
        self._record_equity(int(row["ts"]))

        if not self.portfolio.is_flat(self.config.instrument_id):
            self.diagnostics["max_open_positions_observed"] = 1
            self._manage_open_position(row)
            return
        if self.entry_pending:
            self._manage_market_entry(row)
            return
        if not self._in_evaluation(int(row["ts"])):
            return
        if self._funding_blackout(int(row["ts"])):
            return
        if not self._features_ready(int(row["ts"])):
            return
        if len(self.bars) < max(self.config.atr_period + 1, BALANCE_BARS + 1):
            return
        if self.bar_index - self.last_entry_index < self.config.cooldown_bars:
            return
        self._detect_balance_expansion(row)

    def _metrics_ready(self) -> bool:
        if self.current_feature is None:
            return False
        value = self.current_feature.get("metrics_ready", False)
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in {"true", "1", "yes"}

    def _detect_balance_expansion(self, row: dict[str, float | int]) -> None:
        if not self._metrics_ready():
            self.diagnostics["metrics_not_ready"] += 1
            return
        rows = list(self.bars)
        balance = rows[-(BALANCE_BARS + 1) : -1]
        if len(balance) != BALANCE_BARS:
            return
        atr = self._atr()
        if not math.isfinite(atr) or atr <= 0.0:
            return

        balance_high = max(float(item["high"]) for item in balance)
        balance_low = min(float(item["low"]) for item in balance)
        balance_closes = tuple(float(item["close"]) for item in balance)
        self.diagnostics["balance_observations"] += 1
        evidence = BalanceBreakoutEvidence(
            open_price=float(row["open"]),
            high=float(row["high"]),
            low=float(row["low"]),
            close=float(row["close"]),
            atr=atr,
            balance_high=balance_high,
            balance_low=balance_low,
            balance_closes=balance_closes,
            flow_15s=self._feature("flow_15s"),
            flow_60s=self._feature("flow_60s"),
            flow_3m=self._feature("flow_3m"),
            efficiency_60s=self._feature("efficiency_60s"),
            notional_burst=self._feature("notional_burst"),
            depth_imbalance_1=self._feature("depth_imbalance_1"),
            bid_depth_change_5m=self._feature("bid_depth_change_1_5m"),
            ask_depth_change_5m=self._feature("ask_depth_change_1_5m"),
            oi_change_15m=self._feature("oi_change_15m"),
            metrics_ready=True,
        )
        side = breakout_side(evidence)
        outside = float(row["close"]) > balance_high or float(row["close"]) < balance_low
        if side == 0:
            if outside:
                self.diagnostics["sponsorship_rejections"] += 1
            return

        self.scenario_counter += 1
        scenario_id = f"bae-{self.scenario_counter:07d}"
        metrics = balance_metrics(
            balance_high=balance_high,
            balance_low=balance_low,
            atr=atr,
            closes=balance_closes,
        )
        details = {
            "side": side,
            "balance_start_ns": int(balance[0]["ts"]),
            "balance_end_ns": int(balance[-1]["ts"]),
            "balance_high": balance_high,
            "balance_low": balance_low,
            "balance_range_atr": metrics["range_atr"],
            "balance_efficiency": metrics["efficiency"],
            "breakout_open": float(row["open"]),
            "breakout_high": float(row["high"]),
            "breakout_low": float(row["low"]),
            "breakout_close": float(row["close"]),
            "flow_15s": evidence.flow_15s,
            "flow_60s": evidence.flow_60s,
            "flow_3m": evidence.flow_3m,
            "efficiency_60s": evidence.efficiency_60s,
            "notional_burst": evidence.notional_burst,
            "depth_imbalance_1": evidence.depth_imbalance_1,
            "bid_depth_change_1_5m": evidence.bid_depth_change_5m,
            "ask_depth_change_1_5m": evidence.ask_depth_change_5m,
            "sum_open_interest": self._feature("sum_open_interest"),
            "oi_change_15m": evidence.oi_change_15m,
            "metrics_age_seconds": self._feature("metrics_age_seconds"),
        }
        self.diagnostics["balance_breakouts"] += 1
        self._transition(
            scenario_id,
            "BALANCE_EXPANSION_CONFIRMED",
            int(row["ts"]),
            int(row["ts"]),
            "ENTRY_ARMED",
            "FLOW_OI_AND_DEPTH_SPONSORED_BALANCE_BOS",
            float(row["close"]),
            details,
        )
        self._submit_market_bracket(scenario_id, side, row, atr, details)

    def _submit_market_bracket(
        self,
        scenario_id: str,
        side: int,
        row: dict[str, float | int],
        atr: float,
        details: dict[str, Any],
    ) -> bool:
        entry_price = self.instrument.make_price(float(row["close"]))
        invalidation_extreme = float(row["low"]) if side > 0 else float(row["high"])
        stop_price = self.instrument.make_price(
            invalidation_extreme - side * self.config.stop_buffer_atr * atr,
        )
        entry, stop = _as_float(entry_price), _as_float(stop_price)
        cost_rate = self.config.all_in_cost_bps_each_side / 10_000.0
        planned_loss = planned_loss_per_unit(
            entry,
            stop,
            side,
            cost_rate,
            self.config.adverse_slippage_bps_each_side / 10_000.0,
        )
        if not math.isfinite(planned_loss) or planned_loss <= 0.0:
            self._transition(
                scenario_id,
                "ENTRY_INVALIDATED",
                int(row["ts"]),
                int(row["ts"]),
                "CLOSED",
                "INVALID_BREAKOUT_BAR_STOP_GEOMETRY",
                float(row["close"]),
                details,
            )
            return False

        target_price = self.instrument.make_price(
            cost_aware_target(
                entry,
                side,
                planned_loss,
                self.config.acceptance_target_net_r,
                cost_rate,
            ),
        )
        target = _as_float(target_price)
        if (side > 0 and not stop < entry < target) or (side < 0 and not target < entry < stop):
            self._transition(
                scenario_id,
                "ENTRY_INVALIDATED",
                int(row["ts"]),
                int(row["ts"]),
                "CLOSED",
                "INVALID_BREAKOUT_BRACKET",
                float(row["close"]),
                details,
            )
            return False

        equity = self._equity_value()
        risk_budget = equity * self.config.risk_fraction
        quantity_value = floor_quantity(
            risk_budget / planned_loss,
            int(self.instrument.size_precision),
        )
        if quantity_value <= 0.0 or quantity_value * entry < 10.0:
            self._transition(
                scenario_id,
                "ENTRY_INVALIDATED",
                int(row["ts"]),
                int(row["ts"]),
                "CLOSED",
                "QUANTITY_BELOW_INSTRUMENT_MINIMUM",
                float(row["close"]),
                details,
            )
            return False

        order_list = self.order_factory.bracket(
            instrument_id=self.config.instrument_id,
            order_side=OrderSide.BUY if side > 0 else OrderSide.SELL,
            quantity=self.instrument.make_qty(quantity_value),
            entry_order_type=OrderType.MARKET,
            time_in_force=TimeInForce.GTC,
            tp_price=target_price,
            tp_post_only=False,
            sl_trigger_price=stop_price,
            entry_tags=["BALANCE_EXPANSION_ENTRY"],
            tp_tags=["BALANCE_EXPANSION_TARGET"],
            sl_tags=["BALANCE_EXPANSION_INVALIDATION"],
        )
        self.submit_order_list(order_list)
        self.entry_pending = True
        self.entry_pending_index = self.bar_index
        self.entry_expires_index = self.bar_index + 2
        self.entry_side = side
        self.entry_stop = stop
        self.entry_limit = entry
        self.last_entry_index = self.bar_index
        self.current_scenario_id = scenario_id
        self.current_branch = "BALANCE_EXPANSION"
        self.current_pool_level = float(details["balance_high"] if side > 0 else details["balance_low"])
        self.diagnostics["entry_submissions"] += 1
        self.diagnostics["market_entry_submissions"] += 1
        self.diagnostics["max_simultaneous_entry_intents"] = 1
        self._transition(
            scenario_id,
            "MARKET_BRACKET_SUBMITTED",
            int(row["ts"]),
            int(row["ts"]),
            "ENTRY_PENDING",
            "BREAKOUT_BAR_EXTREME_DEFINES_COST_AWARE_ONE_R_BRACKET",
            entry,
            {
                **details,
                "expected_entry": entry,
                "stop": stop,
                "target": target,
                "target_net_r_after_rounding": net_r_at_price(
                    entry,
                    target,
                    side,
                    planned_loss,
                    cost_rate,
                ),
                "quantity": quantity_value,
                "equity": equity,
                "risk_budget": risk_budget,
                "planned_loss_per_unit": planned_loss,
                "planned_account_loss": quantity_value * planned_loss,
            },
        )
        return True

    def _manage_market_entry(self, row: dict[str, float | int]) -> None:
        reason: str | None = None
        if self.bar_index > self.entry_expires_index:
            reason = "MARKET_ENTRY_NOT_FILLED_WITHIN_TWO_BARS"
        elif self._funding_blackout(int(row["ts"])):
            reason = "FUNDING_BLACKOUT_BEFORE_MARKET_FILL"
        elif not self._in_evaluation(int(row["ts"])):
            reason = "EVALUATION_ENDED_BEFORE_MARKET_FILL"
        if reason is None:
            return
        self.cancel_all_orders(self.config.instrument_id)
        if self.current_scenario_id is not None:
            self._transition(
                self.current_scenario_id,
                "UNFILLED_ENTRY_CANCELED",
                int(row["ts"]),
                int(row["ts"]),
                "CLOSED",
                reason,
                float(row["close"]),
                {"expected_entry": self.entry_limit},
            )
        self._clear_trade_state()

    def on_position_opened(self, event: Any) -> None:
        self.entry_pending = False
        self.exit_pending = False
        self.position_open_index = self.bar_index
        if self.current_scenario_id is not None:
            ts = int(getattr(event, "ts_event", self.bars[-1]["ts"]))
            self._transition(
                self.current_scenario_id,
                "POSITION_OPENED",
                ts,
                ts,
                "POSITION_OPEN",
                "NAUTILUS_MARKET_ENTRY_FILLED",
                float(self.bars[-1]["close"]),
                {"event": str(event), "expected_entry": self.entry_limit},
            )


__all__ = ["BalanceExpansionStrategy"]
