"""Candidate 39 V2 NautilusTrader strategy adapter.

Candidate 35 supplies the checksum-verified data, one-account Nautilus execution,
fee/latency/fill models, accounting and reporting shell. Candidate 39 V2 changes
only the trading decision boundary:

1. interaction features and entry-confirmation features are observed separately;
2. accepted auctions are offered as passive boundary-retest LIMIT parents;
3. target space must clear realistic costs at the state's existing reward floor;
4. rejected or already-crossed protection flattens the account immediately.
"""
from __future__ import annotations

from datetime import datetime, timezone
import importlib.util
import math
from pathlib import Path
import sys
from typing import Any

from nautilus_trader.model.enums import OrderSide, OrderType, TimeInForce

HERE = Path(__file__).resolve().parent
BASE = HERE.parent / "candidate-35"
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import router as _candidate39_router

sys.modules["router"] = _candidate39_router
_spec = importlib.util.spec_from_file_location(
    "_candidate39_reused_candidate35_strategy",
    BASE / "strategy.py",
)
if _spec is None or _spec.loader is None:
    raise ImportError(f"cannot load reused strategy shell from {BASE / 'strategy.py'}")
_base = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _base
_spec.loader.exec_module(_base)

Candidate39Config = _base.Candidate35Config
Candidate35Config = Candidate39Config  # required by the reused BacktestNode runner


class Candidate39Strategy(_base.Candidate35Strategy):
    """One four-asset account with causal setup/confirmation and passive entry."""

    def __init__(self, config: Candidate39Config) -> None:
        super().__init__(config)
        self.diagnostics.update(
            {
                "confirmation_feature_stale_episodes": 0,
                "cost_space_rejections": 0,
                "non_passive_entry_rejections": 0,
                "limit_entry_submissions": 0,
                "emergency_flattens": 0,
                "unresolved_reason_counts": {},
            }
        )

    def _on_complete_universe_minute(self, ts_event: int) -> None:
        """Route a common clock using frozen interaction and current confirmation."""
        self.minute_index += 1
        self.diagnostics["complete_universe_minutes"] += 1
        self._record_equity(ts_event)

        open_symbols = [
            symbol
            for symbol in _base.SYMBOLS
            if not self.portfolio.is_flat(self.instrument_ids[symbol])
        ]
        self.diagnostics["max_open_positions_observed"] = max(
            int(self.diagnostics["max_open_positions_observed"]),
            len(open_symbols),
        )
        if len(open_symbols) > 1:
            self.diagnostics["global_position_violations"] += 1
            for symbol in open_symbols:
                self.cancel_all_orders(self.instrument_ids[symbol])
                self.close_all_positions(self.instrument_ids[symbol])
            return
        if open_symbols:
            self.current_symbol = open_symbols[0]
            self._manage_open_position(ts_event)
            return

        if self.entry_pending:
            self.diagnostics["max_simultaneous_entry_intents"] = max(
                int(self.diagnostics["max_simultaneous_entry_intents"]),
                1,
            )
            # A boundary-retest setup is valid for one additional 15-minute
            # auction. It is not allowed to become a stale standing order.
            if self.minute_index - self.entry_pending_minute > self.route_config.prior_bars:
                assert self.current_symbol is not None
                self.cancel_all_orders(self.instrument_ids[self.current_symbol])
                self.diagnostics["entry_expirations"] += 1
                self._event(
                    "ENTRY_EXPIRED",
                    ts_event,
                    reason="BOUNDARY_NOT_RETESTED_WITHIN_ONE_AUCTION",
                    validity_minutes=int(self.route_config.prior_bars),
                )
                self._clear_trade_state()
            return

        if not (self.config.evaluation_start_ns <= ts_event <= self.config.evaluation_end_ns):
            return
        if self._funding_blackout(ts_event):
            return
        if self.minute_index - self.last_entry_minute < self.config.cooldown_minutes:
            return

        moment = datetime.fromtimestamp(ts_event / 1_000_000_000, tz=timezone.utc)
        if moment.minute % 15 != 2:
            return
        required = max(
            self.route_config.context_bars
            + self.route_config.prior_bars
            + self.route_config.response_bars,
            self.route_config.atr_period
            + self.route_config.prior_bars
            + self.route_config.response_bars
            + 1,
        )
        if any(len(self.bars[symbol]) < required for symbol in _base.SYMBOLS):
            return

        interactions: dict[str, _candidate39_router.FeatureObservation] = {}
        confirmations: dict[str, _candidate39_router.FeatureObservation] = {}
        for symbol in _base.SYMBOLS:
            interaction_ts = list(self.bars[symbol])[-3].ts_event
            confirmation_ts = list(self.bars[symbol])[-1].ts_event
            interactions[symbol] = self.features[symbol].observation(
                interaction_ts,
                self.config.feature_max_age_seconds,
            )
            confirmations[symbol] = self.features[symbol].observation(
                confirmation_ts,
                self.config.feature_max_age_seconds,
            )
        if not all(item.ready for item in interactions.values()):
            self.diagnostics["feature_stale_episodes"] += 1
            return
        if not all(item.ready for item in confirmations.values()):
            self.diagnostics["confirmation_feature_stale_episodes"] += 1
            return

        self.diagnostics["quarter_hour_decisions"] += 1
        winner, decisions = _candidate39_router.route_universe(
            bars_by_symbol={
                symbol: tuple(self.bars[symbol]) for symbol in _base.SYMBOLS
            },
            features_by_symbol=interactions,
            confirmation_features_by_symbol=confirmations,
            config=self.route_config,
        )
        for decision in decisions.values():
            counts = self.diagnostics["route_counts"]
            counts[decision.state] = int(counts.get(decision.state, 0)) + 1
            if not decision.actionable and decision.reasons:
                reasons = self.diagnostics["unresolved_reason_counts"]
                reason = str(decision.reasons[0])
                reasons[reason] = int(reasons.get(reason, 0)) + 1
        if winner is None:
            self.diagnostics["unresolved_episodes"] += 1
            return
        self._submit_decision(winner, ts_event)

    def _submit_decision(
        self,
        decision: _candidate39_router.RouteDecision,
        ts_event: int,
    ) -> None:
        symbol = decision.symbol
        instrument = self.instruments[symbol]
        side = int(decision.side)
        entry = float(decision.entry_reference)
        stop = float(decision.stop_reference)
        target = float(decision.objective_reference)
        current_close = float(self.bars[symbol][-1].close)

        if side > 0 and not (stop < entry < target):
            self._event("INVALID_BRACKET", ts_event, symbol=symbol, reason="LONG_GEOMETRY")
            return
        if side < 0 and not (target < entry < stop):
            self._event("INVALID_BRACKET", ts_event, symbol=symbol, reason="SHORT_GEOMETRY")
            return
        # The limit must sit behind current price in the intended direction.
        # Otherwise the setup has already crossed back through its boundary.
        if side * (current_close - entry) < -1e-12:
            self.diagnostics["non_passive_entry_rejections"] += 1
            self._event(
                "NON_PASSIVE_ENTRY_REJECTED",
                ts_event,
                symbol=symbol,
                side=side,
                current_close=current_close,
                entry_reference=entry,
            )
            return

        fee_rate = self.config.all_in_cost_bps_each_side / 10_000.0
        slippage_rate = self.config.adverse_slippage_bps_each_side / 10_000.0
        funding_rate = self.config.funding_reserve_bps / 10_000.0

        # A limit parent cannot fill worse than its price. Stop/target children
        # retain adverse execution assumptions. Both legs include fees and the
        # configured funding reserve.
        adverse_stop = stop * (1.0 - side * slippage_rate)
        adverse_target = target * (1.0 - side * slippage_rate)
        per_unit_loss = (
            abs(entry - adverse_stop)
            + fee_rate * (abs(entry) + abs(adverse_stop))
            + funding_rate * abs(entry)
        )
        net_reward = (
            side * (adverse_target - entry)
            - fee_rate * (abs(entry) + abs(adverse_target))
            - funding_rate * abs(entry)
        )
        if not math.isfinite(per_unit_loss) or per_unit_loss <= 0.0:
            self._event("INVALID_RISK_GEOMETRY", ts_event, symbol=symbol)
            return
        cost_aware_net_r = net_reward / per_unit_loss
        policy_floor = float(
            decision.diagnostics.get(
                "policy_target_r_floor",
                self.config.continuation_target_r,
            )
        )
        if (
            not math.isfinite(net_reward)
            or net_reward <= 0.0
            or not math.isfinite(cost_aware_net_r)
            or cost_aware_net_r + 1e-12 < policy_floor
        ):
            self.diagnostics["cost_space_rejections"] += 1
            self._event(
                "COST_SPACE_REJECTED",
                ts_event,
                symbol=symbol,
                state=decision.state,
                side=side,
                entry=entry,
                stop=stop,
                target=target,
                net_reward_per_unit=net_reward,
                planned_loss_per_unit=per_unit_loss,
                cost_aware_net_r=cost_aware_net_r,
                policy_target_r_floor=policy_floor,
            )
            return

        equity = self._equity_value()
        risk_budget = equity * self.config.risk_fraction
        raw_quantity = risk_budget / per_unit_loss
        quantity = _base._floor_quantity(raw_quantity, int(instrument.size_precision))
        exchange_max = _base._as_float(
            getattr(instrument, "max_quantity", None),
            math.inf,
        )
        if math.isfinite(exchange_max) and quantity > exchange_max:
            quantity = _base._floor_quantity(exchange_max, int(instrument.size_precision))
            self.diagnostics["exchange_max_quantity_bounds"] += 1
        minimum_quantity = _base._as_float(
            getattr(instrument, "min_quantity", None),
            0.0,
        )
        minimum_notional = _base._as_float(
            getattr(instrument, "min_notional", None),
            0.0,
        )
        if (
            quantity <= 0.0
            or quantity + 1e-12 < minimum_quantity
            or quantity * entry + 1e-12 < minimum_notional
        ):
            self._event(
                "QUANTITY_BELOW_EXCHANGE_MINIMUM",
                ts_event,
                symbol=symbol,
                quantity=quantity,
                raw_quantity=raw_quantity,
            )
            return

        order_side = OrderSide.BUY if side > 0 else OrderSide.SELL
        order_list = self.order_factory.bracket(
            instrument_id=self.instrument_ids[symbol],
            order_side=order_side,
            quantity=instrument.make_qty(quantity),
            time_in_force=TimeInForce.GTC,
            entry_order_type=OrderType.LIMIT,
            entry_price=instrument.make_price(entry),
            tp_price=instrument.make_price(target),
            sl_trigger_price=instrument.make_price(stop),
        )
        self.submit_order_list(order_list)
        self.entry_pending = True
        self.entry_pending_minute = self.minute_index
        self.last_entry_minute = self.minute_index
        self.current_symbol = symbol
        self.current_scenario = {
            "scenario_id": f"c39v2-{self.diagnostics['entry_submissions'] + 1:07d}",
            "candidate": "candidate-39-causal-auction-state-router-v2",
            "symbol": symbol,
            "state": decision.state,
            "side": side,
            "score": decision.score,
            "entry_reference": entry,
            "entry_order_type": "LIMIT",
            "entry_validity_minutes": int(self.route_config.prior_bars),
            "stop": stop,
            "target": target,
            "quantity": quantity,
            "risk_budget": risk_budget,
            "planned_loss_per_unit": per_unit_loss,
            "planned_account_loss": quantity * per_unit_loss,
            "net_reward_per_unit": net_reward,
            "cost_aware_net_r": cost_aware_net_r,
            "policy_target_r_floor": policy_floor,
            "episode_ts": decision.episode_ts,
            "reasons": list(decision.reasons),
            "diagnostics": dict(decision.diagnostics),
            "non_scalping": True,
            "signal_horizon_minutes": 15,
            "maximum_hold_minutes": int(self.config.max_hold_minutes),
        }
        self.diagnostics["entry_submissions"] += 1
        self.diagnostics["limit_entry_submissions"] += 1
        selected = self.diagnostics["selected_symbols"]
        selected[symbol] = int(selected.get(symbol, 0)) + 1
        self.diagnostics["max_simultaneous_entry_intents"] = max(
            int(self.diagnostics["max_simultaneous_entry_intents"]),
            1,
        )
        self._event("ENTRY_SUBMITTED", ts_event, **self.current_scenario)

    def on_position_opened(self, event: Any) -> None:
        super().on_position_opened(event)
        scenario = self.current_scenario
        symbol = self.current_symbol
        if not scenario or symbol is None or not self.bars[symbol]:
            return
        side = int(scenario.get("side", 0))
        stop = float(scenario.get("stop", math.nan))
        latest = self.bars[symbol][-1]
        crossed = (side > 0 and latest.low <= stop) or (side < 0 and latest.high >= stop)
        if not crossed:
            return
        instrument_id = self.instrument_ids[symbol]
        self.cancel_all_orders(instrument_id)
        self.close_all_positions(instrument_id)
        self.diagnostics["emergency_flattens"] += 1
        self._event(
            "EMERGENCY_FLATTEN",
            int(getattr(event, "ts_event", self._latest_ts())),
            symbol=symbol,
            reason="PROTECTIVE_STOP_ALREADY_CROSSED_ON_ENTRY_BAR",
            stop=stop,
            bar_high=float(latest.high),
            bar_low=float(latest.low),
        )

    def on_position_closed(self, event: Any) -> None:
        ts_event = int(getattr(event, "ts_event", self._latest_ts()))
        record = dict(self.current_scenario or {})
        record.update(
            {
                "ts_event": ts_event,
                "realized_pnl": str(getattr(event, "realized_pnl", None)),
                "event": str(event),
            }
        )
        self.closed_scenarios.append(record)
        event_details = dict(record)
        event_details.pop("ts_event", None)
        self._event("POSITION_CLOSED", ts_event, **event_details)
        self._clear_trade_state()

    def on_order_rejected(self, event: Any) -> None:
        super().on_order_rejected(event)
        if self._global_flat():
            return
        symbol = self.current_symbol
        if symbol is None:
            for candidate in _base.SYMBOLS:
                if not self.portfolio.is_flat(self.instrument_ids[candidate]):
                    symbol = candidate
                    break
        if symbol is None:
            return
        instrument_id = self.instrument_ids[symbol]
        self.cancel_all_orders(instrument_id)
        self.close_all_positions(instrument_id)
        self.diagnostics["emergency_flattens"] += 1
        self._event(
            "EMERGENCY_FLATTEN",
            int(getattr(event, "ts_event", self._latest_ts())),
            symbol=symbol,
            reason="ORDER_REJECTION_WHILE_POSITION_LIVE",
            rejected_event=str(event),
        )


Candidate35Strategy = Candidate39Strategy
