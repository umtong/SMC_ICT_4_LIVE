"""Candidate 15 V10 execution-valid cost-cover materialization."""
from __future__ import annotations


def _replace(source: str, old: str, new: str, *, label: str, expected: int = 1) -> str:
    count = source.count(old)
    if count != expected:
        raise RuntimeError(
            f"Candidate 15 V10 boundary drifted at {label}: expected {expected}, found {count}",
        )
    return source.replace(old, new)


def materialize_execution_valid_cost_cover_source(source: str) -> str:
    source = _replace(
        source,
        "candidate-15-v9-strict-open-time",
        "candidate-15-v10-strict-open-time",
        label="runner-identity",
    )

    source = _replace(
        source,
        '''            tick = Decimal(str(instrument.price_increment))
            entry = Decimal(str(self.active_plan.expected_entry))
            maker = Decimal(str(execution_config["effective_maker_rate"]))
            taker = Decimal(str(execution_config["effective_taker_rate"]))
            parity = Decimal(str(transfer["parity_price"]))
            close = Decimal(str(observation.close))

            if self.active_plan.direction == Direction.LONG:
                break_even = entry * (Decimal("1") + maker) / (Decimal("1") - taker)
                units = (break_even / tick).to_integral_value(rounding=ROUND_CEILING) + 1
                lock = units * tick
                activation = max(parity, lock + tick)
                crossed = close >= activation
                lock_valid = close > lock
            else:
                break_even = entry * (Decimal("1") - maker) / (Decimal("1") + taker)
                units = (break_even / tick).to_integral_value(rounding=ROUND_FLOOR) - 1
                lock = units * tick
                activation = min(parity, lock - tick)
                crossed = close <= activation
                lock_valid = close < lock
''',
        '''            tick = Decimal(str(instrument.price_increment))
            maker = Decimal(str(execution_config["effective_maker_rate"]))
            taker = Decimal(str(execution_config["effective_taker_rate"]))
            parity = Decimal(str(transfer["parity_price"]))
            close = Decimal(str(observation.close))

            open_positions = self.cache.positions_open(
                instrument_id=instrument_id,
                strategy_id=self.id,
            )
            if len(open_positions) != 1:
                record = {
                    "type": "TRANSFER_OPEN_POSITION_NOT_UNIQUE",
                    "ts_event": ts_ns,
                    "scenario_id": self.active_plan.scenario_id,
                    "symbol": self.active_symbol,
                    "open_positions": len(open_positions),
                }
                self.errors.append(record)
                if self.cache.orders_open_count(
                    instrument_id=instrument_id,
                    strategy_id=self.id,
                ):
                    self.cancel_all_orders(instrument_id)
                self.close_all_positions(instrument_id)
                self.transfer_protected = True
                return
            position = open_positions[0]
            entry = Decimal(str(position.avg_px_open))
            cover = positive_cost_cover_trigger(
                direction=self.active_plan.direction.value,
                actual_average_entry=entry,
                price_increment=tick,
                entry_fee_rate=maker,
                exit_fee_rate=taker,
                adverse_slippage_ticks=2,
                minimum_net_ticks=1,
            )
            lock = cover.trigger_price

            open_orders = self.cache.orders_open(
                instrument_id=instrument_id,
                strategy_id=self.id,
            )
            entry_side = (
                OrderSide.BUY
                if self.active_plan.direction == Direction.LONG
                else OrderSide.SELL
            )
            live_entry_remainders = [
                order for order in open_orders if order.side == entry_side
            ]
            if live_entry_remainders:
                record = {
                    "type": "TRANSFER_COMPLETION_WITH_LIVE_ENTRY_REMAINDER",
                    "ts_event": ts_ns,
                    "scenario_id": self.active_plan.scenario_id,
                    "symbol": self.active_symbol,
                    "open_entry_orders": len(live_entry_remainders),
                    "actual_average_entry": str(entry),
                }
                self.errors.append(record)
                if self.cache.orders_open_count(
                    instrument_id=instrument_id,
                    strategy_id=self.id,
                ):
                    self.cancel_all_orders(instrument_id)
                self.close_all_positions(instrument_id)
                self.transfer_protected = True
                return

            if self.active_plan.direction == Direction.LONG:
                activation = max(parity, lock + tick)
                crossed = close >= activation
                lock_valid = close > lock
            else:
                activation = min(parity, lock - tick)
                crossed = close <= activation
                lock_valid = close < lock
''',
        label="actual-fill-slippage-aware-cost-cover",
    )

    source = _replace(
        source,
        '''                "activation_price": str(activation),
                "cost_cover_stop": str(lock),
            }
''',
        '''                "activation_price": str(activation),
                "cost_cover_stop": str(lock),
                "actual_average_entry": str(entry),
                "modeled_adverse_stop_fill": str(cover.expected_adverse_fill),
                "modeled_net_gain_per_unit": str(cover.expected_net_gain_per_unit),
                "minimum_net_gain_per_unit": str(cover.minimum_net_gain_per_unit),
                "adverse_stop_slippage_ticks": cover.adverse_slippage_ticks,
            }
''',
        label="cost-cover-audit-fields",
    )

    required = {
        "candidate-15-v10-strict-open-time": 1,
        "positive_cost_cover_trigger(": 1,
        "self.cache.positions_open(": 1,
        "position.avg_px_open": 1,
        '"TRANSFER_OPEN_POSITION_NOT_UNIQUE"': 1,
        '"TRANSFER_COMPLETION_WITH_LIVE_ENTRY_REMAINDER"': 1,
        '"adverse_stop_slippage_ticks"': 1,
        "TRANSFER_STOP_MODIFICATION_SUBMITTED": 1,
        "BetaCoherentTransferPersistentQuarterHourRouter(": 1,
        "BetaCoherentResidualTransferContinuationEngine(": 1,
    }
    bad = {
        token: (source.count(token), expected)
        for token, expected in required.items()
        if source.count(token) != expected
    }
    if bad:
        raise RuntimeError(f"Candidate 15 V10 routes were not materialized: {bad}")
    if "candidate-15-v9-strict-open-time" in source:
        raise RuntimeError("stale V9 identity survived V10 materialization")
    return source
