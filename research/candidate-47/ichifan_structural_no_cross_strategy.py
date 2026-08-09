"""One-variable exit ablation for the frozen structural ichiFan policy.

Entry, structural hard stop, remote target, 8%/6% trailing logic, funding
flattening, maximum hold, costs and current-NAV 3% risk sizing are unchanged.
The only intervention is that the public ichiV2 first 5m-close/90m-EMA
cross-down is observed and recorded but does not immediately liquidate the
position.  This tests whether that source exit is a useful invalidation or a
premature loss-realization rule in the four-asset day-trading account.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import ichifan_strategy as _exact
import ichifan_structural_strategy as _structural

Candidate47IchiFanNoCrossConfig = _structural.Candidate47IchiFanStructuralConfig
Candidate35Config = Candidate47IchiFanNoCrossConfig
SYMBOLS = _structural.SYMBOLS


class Candidate47IchiFanStructuralNoCrossStrategy(
    _structural.Candidate47IchiFanStructuralStrategy,
):
    """Frozen structural policy without the first 90m-EMA cross liquidation."""

    def __init__(self, config: Candidate47IchiFanNoCrossConfig) -> None:
        super().__init__(config)
        self.diagnostics.update(
            {
                "ichifan_cross_observations_ignored": 0,
                "ichifan_positions_with_ignored_cross": 0,
                "ichifan_no_cross_policy": (
                    "ignore-source-first-cross;retain-hard-stop-remote-target-"
                    "8pct-6pct-trailing-and-daytrade-forced-exit"
                ),
            }
        )

    def _manage_open_position(self, ts_event: int) -> None:
        symbol = self.current_symbol
        if symbol is None or not self.bars[symbol]:
            return
        latest = self.bars[symbol][-1]
        scenario = self.current_scenario or {}
        peak = max(float(scenario.get("peak_price", latest.high)), float(latest.high))
        scenario["peak_price"] = peak
        self.current_scenario = scenario
        entry = float(scenario.get("entry_reference", latest.close))

        moment = datetime.fromtimestamp(ts_event / 1_000_000_000, tz=timezone.utc)
        if moment.minute % 5 == 4:
            states = _exact.fan_states(
                _exact.aggregate_five_minute(tuple(self.bars[symbol]))
            )
            if states and states[-1].exit_cross_down:
                self.diagnostics["ichifan_cross_observations_ignored"] += 1
                if "first_ignored_cross_ts" not in scenario:
                    scenario["first_ignored_cross_ts"] = int(ts_event)
                    scenario["first_ignored_cross_close_5m"] = float(
                        states[-1].trend_close_5m
                    )
                    scenario["first_ignored_cross_close_90m"] = float(
                        states[-1].trend_close_90m
                    )
                    self.diagnostics["ichifan_positions_with_ignored_cross"] += 1
                    self._event(
                        "ICHIFAN_TREND_CROSS_OBSERVED_NOT_EXITED",
                        ts_event,
                        shifted_close_5m=states[-1].trend_close_5m,
                        shifted_close_90m=states[-1].trend_close_90m,
                    )

        trailing_active = entry > 0.0 and peak / entry - 1.0 >= 0.08
        trailing_hit = trailing_active and float(latest.close) <= peak * (1.0 - 0.06)
        if trailing_hit:
            self.diagnostics["ichifan_trailing_exits"] += 1
            self._request_exit(
                ts_event,
                "ICHIFAN_TRAILING_EXIT",
                entry=entry,
                peak=peak,
                close=float(latest.close),
            )
            return

        # Call the shared account/day-trading manager directly, bypassing only
        # Candidate47IchiFanStrategy's cross-down exit.
        _exact._base.Candidate35Strategy._manage_open_position(self, ts_event)


Candidate35Strategy = Candidate47IchiFanStructuralNoCrossStrategy
