"""Cost-viable MIT execution control for candidate-07.

The liquidity detector, scenario direction, entry timing, structural stop and
structural target are unchanged. This adapter rejects only a target whose
causally expected adverse target fill cannot be positive after the exact entry
and exit cost contract already used by risk sizing. No empirical R threshold
is introduced.
"""
from __future__ import annotations

from decimal import Decimal
import json

from nautilus_trader.model.data import Bar

from event_signal_data import CausalTradeSignal
from execution_cost_geometry import adverse_execution_geometry
from strategy_event_signal_mit import Candidate07MITSerializedStrategy


class Candidate07CostViableMITStrategy(Candidate07MITSerializedStrategy):
    """Use MIT TP only when the declared target is positive after costs."""

    def _submit_signal(self, signal: CausalTradeSignal, bar: Bar) -> None:
        if self._instrument is None:
            raise RuntimeError("instrument is not initialized")

        current_price = Decimal(str(bar.close.as_double()))
        stop = self._instrument.make_price(
            Decimal(str(signal.stop_price))
        ).as_decimal()
        target = self._instrument.make_price(
            Decimal(str(signal.target_price))
        ).as_decimal()
        if signal.direction == "LONG":
            valid_geometry = stop < current_price < target
        else:
            valid_geometry = target < current_price < stop
        if not valid_geometry:
            super()._submit_signal(signal, bar)
            return

        fee_rate = self._instrument.taker_fee or Decimal(0)
        geometry = adverse_execution_geometry(
            direction=signal.direction,
            entry_reference=current_price,
            stop_price=stop,
            target_price=target,
            price_increment=self._instrument.price_increment.as_decimal(),
            taker_fee_rate=fee_rate,
            funding_reserve_bps=self.config.risk_funding_reserve_bps,
            adverse_slippage_ticks=1,
        )
        geometry_details = {
            "expected_entry_fill": str(geometry.expected_entry_fill),
            "expected_stop_fill": str(geometry.expected_stop_fill),
            "expected_target_fill": str(geometry.expected_target_fill),
            "entry_fee_per_unit": str(geometry.entry_fee),
            "stop_fee_per_unit": str(geometry.stop_fee),
            "target_fee_per_unit": str(geometry.target_fee),
            "funding_reserve_per_unit": str(geometry.funding_reserve),
            "per_unit_expected_loss": str(geometry.per_unit_expected_loss),
            "per_unit_expected_target_gain": str(
                geometry.per_unit_expected_target_gain
            ),
            "cost_adjusted_target_r": str(geometry.cost_adjusted_target_r),
            "target_viability_rule": (
                "strictly positive after one adverse tick on entry and target, "
                "entry/target taker fees and configured funding reserve"
            ),
        }
        if not geometry.target_is_net_positive:
            self._diagnostics.append(
                {
                    "scenario_id": signal.scenario_id,
                    "reason": "TARGET_NOT_NET_POSITIVE_AFTER_COSTS",
                    "current_price": float(current_price),
                    "stop": float(stop),
                    "target": float(target),
                    **geometry_details,
                }
            )
            return

        details = json.loads(signal.details_json)
        enriched = CausalTradeSignal(
            instrument_id=signal.instrument_id,
            scenario_id=signal.scenario_id,
            direction=signal.direction,
            entry_reference=signal.entry_reference,
            stop_price=signal.stop_price,
            target_price=signal.target_price,
            expected_rr=signal.expected_rr,
            source_pool_id=signal.source_pool_id,
            signal_kind=f"{signal.signal_kind}_COST_VIABLE",
            details_json=json.dumps(
                {**details, "cost_viability": geometry_details},
                sort_keys=True,
            ),
            observed_time_ns=signal.observed_time_ns,
            ts_event=signal.ts_event,
            ts_init=signal.ts_init,
        )
        super()._submit_signal(enriched, bar)


__all__ = ["Candidate07CostViableMITStrategy"]
