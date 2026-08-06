#!/usr/bin/env python3
"""Lifecycle-safe wrappers for V21 directional internal-liquidity scenarios.

The V21 state machine can causally terminate a pending setup on invalidation,
weak first break, funding blackout or absence of an external target. The common
base loop assumes a pending object still exists whenever confirmation returns
False, which is not true for those terminal branches. This module changes only
that control-flow assumption; all market-state definitions, orders and risk
logic remain unchanged.
"""
from __future__ import annotations

from typing import Any

from nt_directional_internal_strategy import CompositeDirectionalInternalStrategy
from nt_directional_internal_strategy import FourHourDirectionalInternalStrategy
from nt_directional_internal_strategy import OneHourDirectionalInternalStrategy
from nt_directional_internal_strategy import OneHourFvgDirectionalInternalStrategy
from nt_liquidity_strategy import _as_float


class LifecycleSafeDirectionalMixin:
    """Mirror the common bar lifecycle and guard a causally cleared setup."""

    def on_bar(self, bar: Any) -> None:
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
        self._record_equity(int(bar.ts_event))
        self._roll_session(row)

        if not self.portfolio.is_flat(self.config.instrument_id):
            self._manage_open_position(row)
            return

        if self.entry_pending:
            if self.bar_index - self.entry_pending_index > 2:
                self.cancel_all_orders(self.config.instrument_id)
                self.entry_pending = False
                self.current_scenario = None
            return

        if not self._in_evaluation(int(bar.ts_event)):
            self.pending = None
            return

        if self._funding_blackout(int(bar.ts_event)):
            self.pending = None
            return

        if len(self.bars) < max(
            self.config.volume_window + 2,
            self.config.trend_lookback_bars + 2,
            self.config.atr_period + 2,
        ):
            return

        if self.pending is not None:
            if self._try_confirm_pending(row):
                return
            # A V21 terminal transition can clear the setup while returning
            # False because no order was submitted. Do not dereference it again.
            if self.pending is not None and self.bar_index > self.pending.expires_index:
                self._event(
                    "SETUP_EXPIRED",
                    self.pending.scenario,
                    row,
                    self.pending.details,
                )
                self.pending = None

        if self.pending is None:
            if self._detect_session_sweep(row):
                return
            self._detect_trend_sweep(row)


class OneHourDirectionalInternalStrategyV2(
    LifecycleSafeDirectionalMixin,
    OneHourDirectionalInternalStrategy,
):
    pass


class OneHourFvgDirectionalInternalStrategyV2(
    LifecycleSafeDirectionalMixin,
    OneHourFvgDirectionalInternalStrategy,
):
    pass


class FourHourDirectionalInternalStrategyV2(
    LifecycleSafeDirectionalMixin,
    FourHourDirectionalInternalStrategy,
):
    pass


class CompositeDirectionalInternalStrategyV2(
    LifecycleSafeDirectionalMixin,
    CompositeDirectionalInternalStrategy,
):
    pass


__all__ = [
    "CompositeDirectionalInternalStrategyV2",
    "FourHourDirectionalInternalStrategyV2",
    "LifecycleSafeDirectionalMixin",
    "OneHourDirectionalInternalStrategyV2",
    "OneHourFvgDirectionalInternalStrategyV2",
]
