"""Activation-time execution guard for candidate-02 v104.

The causal scenario is decided on a completed minute and scheduled for the next
completed minute.  This adapter validates the actual activation bar and close
before delegating all order, fill, fee, position, liquidation, and NAV handling
to the existing NautilusTrader strategy.
"""
from __future__ import annotations

from dataclasses import replace
from decimal import Decimal, InvalidOperation
import math
from typing import Any, Mapping

from nautilus_trader.model.data import Bar

from v53_nt_core import CostConfig
from v53_nt_strategy import ScheduledSignal, V53RotationStrategy
from v104_external_liquidity_core import ActivationValidation, validate_activation


class V104ExternalLiquidityStrategy(V53RotationStrategy):
    """Reject stale or economically degraded v104 signals before submission."""

    def _reject_activation(
        self,
        signal: ScheduledSignal,
        bar: Bar,
        reason: str,
        *,
        validation: ActivationValidation | None = None,
        extra: Mapping[str, Any] | None = None,
    ) -> None:
        details = dict(signal.details)
        details.update(
            {
                "activation_entry_reference": bar.close.as_double(),
                "activation_bar_high": bar.high.as_double(),
                "activation_bar_low": bar.low.as_double(),
                "activation_validation_status": "REJECTED",
                "activation_validation_reason": reason,
            }
        )
        if validation is not None:
            details["activation_cost_after_rr"] = validation.cost_after_reward_risk
            details["activation_delivery_fraction"] = validation.delivery_fraction
        if extra:
            details.update(dict(extra))
        rejected = replace(signal, details=details)
        self._reject(rejected, int(bar.ts_init), reason)

    def _activation_costs(self, signal: ScheduledSignal) -> CostConfig | None:
        values = signal.details.get("activation_validation_costs")
        if not isinstance(values, Mapping):
            return None
        try:
            costs = CostConfig.from_mapping(dict(values))
        except (KeyError, InvalidOperation, TypeError, ValueError):
            return None

        # Every cost exposed by the inherited execution config must match the
        # signal's locked cost model.  target_fee_rate is carried in the signal
        # because the legacy v53 strategy config does not expose it.
        expected = {
            "entry_fee_rate": self.config.entry_fee_rate,
            "stop_fee_rate": self.config.stop_fee_rate,
            "entry_slippage_rate": self.config.entry_slippage_rate,
            "stop_slippage_rate": self.config.stop_slippage_rate,
            "market_impact_rate": self.config.market_impact_rate,
            "funding_rate_allowance": self.config.funding_rate_allowance,
        }
        for name, value in expected.items():
            if getattr(costs, name) != Decimal(value):
                return None
        return costs

    def _submit_signal(self, signal: ScheduledSignal, bar: Bar) -> None:
        details = signal.details
        costs = self._activation_costs(signal)
        if costs is None:
            self._reject_activation(signal, bar, "ACTIVATION_COST_MODEL_MISMATCH")
            return

        try:
            boundary = float(details["liquidity_boundary"])
            minimum_rr = float(details["minimum_target_cost_after_rr"])
            maximum_delivery = float(details["maximum_delivery_fraction"])
            structural_invalidation = float(details["old_range_invalidation"])
            target_eligibility_ns = int(details["selected_target_eligibility_ns"])
            target_expiry_ns = int(details["selected_target_expiry_ns"])
        except (KeyError, TypeError, ValueError, OverflowError):
            self._reject_activation(signal, bar, "ACTIVATION_CONTRACT_FIELDS_MISSING")
            return

        observed_ns = int(bar.ts_init)
        decision_ns = int(signal.source_max_market_time_ns)
        if target_eligibility_ns > decision_ns:
            self._reject_activation(
                signal,
                bar,
                "ACTIVATION_TARGET_WAS_NOT_KNOWN_BY_DECISION",
                extra={
                    "activation_decision_ns": decision_ns,
                    "activation_target_eligibility_ns": target_eligibility_ns,
                },
            )
            return
        if not target_eligibility_ns <= observed_ns <= target_expiry_ns:
            self._reject_activation(
                signal,
                bar,
                "ACTIVATION_TARGET_NOT_ACTIVE",
                extra={
                    "activation_target_eligibility_ns": target_eligibility_ns,
                    "activation_target_expiry_ns": target_expiry_ns,
                },
            )
            return

        instrument = self.cache.instrument(self.config.instrument_id)
        if instrument is None:
            self._reject_activation(signal, bar, "ACTIVATION_INSTRUMENT_MISSING")
            return
        entry = bar.close.as_double()
        rounded_stop = instrument.make_price(signal.stop_price).as_double()
        rounded_target = instrument.make_price(signal.target_price).as_double()
        validation = validate_activation(
            side=signal.side,
            entry=entry,
            boundary=boundary,
            stop=rounded_stop,
            target=rounded_target,
            costs=costs,
            minimum_cost_after_rr=minimum_rr,
            maximum_delivery_fraction=maximum_delivery,
            activation_high=bar.high.as_double(),
            activation_low=bar.low.as_double(),
            structural_invalidation=structural_invalidation,
        )
        if not validation.accepted:
            self._reject_activation(signal, bar, validation.reason, validation=validation)
            return
        if not (
            math.isfinite(validation.cost_after_reward_risk)
            and math.isfinite(validation.delivery_fraction)
        ):
            self._reject_activation(signal, bar, "ACTIVATION_VALIDATION_NONFINITE", validation=validation)
            return

        updated_details = dict(details)
        updated_details.update(
            {
                "activation_entry_reference": entry,
                "activation_bar_high": bar.high.as_double(),
                "activation_bar_low": bar.low.as_double(),
                "activation_validation_status": "ACCEPTED",
                "activation_validation_reason": validation.reason,
                "activation_cost_after_rr": validation.cost_after_reward_risk,
                "activation_delivery_fraction": validation.delivery_fraction,
                "activation_structural_invalidation": structural_invalidation,
                "activation_rounded_stop_price": rounded_stop,
                "activation_rounded_target_price": rounded_target,
            }
        )
        activated = replace(
            signal,
            entry_reference=entry,
            stop_price=rounded_stop,
            target_price=rounded_target,
            cost_after_reward_risk=validation.cost_after_reward_risk,
            details=updated_details,
        )
        super()._submit_signal(activated, bar)


__all__ = ["V104ExternalLiquidityStrategy"]
