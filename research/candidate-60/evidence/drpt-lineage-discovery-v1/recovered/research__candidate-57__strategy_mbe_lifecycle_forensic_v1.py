"""Diagnostics-only lifecycle instrumentation for the frozen MBE2 account."""
from __future__ import annotations

from datetime import datetime, timezone
import math
from typing import Any

import router_mbe_base
from strategy_base import SYMBOLS
from strategy_mbe_fast_base import (
    Candidate35Config as Candidate35Config,
    Candidate35Strategy as _BaseStrategy,
)

_SOURCE_HORIZONS = (15, 41, 114, 180, 420)


def _finite(value: Any, default: float = math.nan) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


class Candidate35Strategy(_BaseStrategy):
    def __init__(self, config: Candidate35Config) -> None:
        super().__init__(config)
        self.diagnostics.update(
            {
                "candidate57_mbe_lifecycle_forensic_v1": 1,
                "mbe_lifecycle_policy_changed": 0,
                "mbe_lifecycle_entry_changed": 0,
                "mbe_lifecycle_stop_changed": 0,
                "mbe_lifecycle_roi_changed": 0,
                "mbe_lifecycle_source_horizons": list(_SOURCE_HORIZONS),
                "mbe_lifecycle_boundary_snapshots": 0,
                "mbe_lifecycle_horizon_snapshots": {
                    str(horizon): 0 for horizon in _SOURCE_HORIZONS
                },
            }
        )

    def _symbol_source_state(self, symbol: str) -> dict[str, Any]:
        long_signal, short_signal, diagnostics = (
            router_mbe_base.source_signals_for_bars(
                tuple(self.bars[symbol]),
                self.mbe_route_config,
            )
        )
        reason = str(diagnostics.get("reason") or "")
        return {
            "symbol": symbol,
            "ready": int(not reason),
            "reason": reason,
            "long_cross": int(bool(long_signal)),
            "short_cross": int(bool(short_signal)),
            "rsi": _finite(diagnostics.get("rsi")),
            "previous_rsi": _finite(diagnostics.get("previous_rsi")),
            "tema_to_middle_bps": _finite(
                diagnostics.get("tema_to_middle_bps")
            ),
            "tema_slope_bps": _finite(diagnostics.get("tema_slope_bps")),
            "rsi_cross_magnitude": _finite(
                diagnostics.get("rsi_cross_magnitude")
            ),
            "return_1h_bps": _finite(diagnostics.get("return_1h_bps")),
            "return_4h_bps": _finite(diagnostics.get("return_4h_bps")),
            "return_8h_bps": _finite(diagnostics.get("return_8h_bps")),
            "ema_2h_to_8h_bps": _finite(
                diagnostics.get("ema_2h_to_8h_bps")
            ),
            "realized_vol_1h_bps": _finite(
                diagnostics.get("realized_vol_1h_bps")
            ),
            "range_1h_bps": _finite(diagnostics.get("range_1h_bps")),
        }

    def _estimated_after_cost_r(
        self,
        *,
        side: int,
        entry: float,
        close: float,
        scenario: dict[str, Any],
    ) -> float:
        quantity = _finite(scenario.get("quantity"), 0.0)
        planned_loss = _finite(
            scenario.get("planned_account_loss"), 0.0
        )
        if quantity <= 0.0 or planned_loss <= 0.0:
            return math.nan
        fee_rate = float(self.config.all_in_cost_bps_each_side) / 10_000.0
        slippage_rate = (
            float(self.config.adverse_slippage_bps_each_side) / 10_000.0
        )
        funding_rate = float(self.config.funding_reserve_bps) / 10_000.0
        gross = side * (close - entry) * quantity
        reserved_cost = quantity * (
            (fee_rate + slippage_rate) * (abs(entry) + abs(close))
            + funding_rate * abs(entry)
        )
        return (gross - reserved_cost) / planned_loss

    def _snapshot(self, ts_event: int, age: int) -> dict[str, Any]:
        scenario = self.current_scenario or {}
        symbol = str(self.current_symbol or "")
        side = int(scenario.get("side", 0))
        entry = _finite(scenario.get("entry_reference"), 0.0)
        bar = self.bars[symbol][-1]
        close = float(bar.close)
        leverage = max(float(self.config.mbe_source_leverage), 1e-12)

        favourable = float(bar.high) if side > 0 else float(bar.low)
        adverse = float(bar.low) if side > 0 else float(bar.high)
        favourable_move = (
            side * (favourable - entry) / entry if entry > 0.0 else math.nan
        )
        adverse_move = (
            side * (adverse - entry) / entry if entry > 0.0 else math.nan
        )
        mfe = max(float(self._mbe_mfe_fraction), favourable_move)
        mae = min(float(self._mbe_mae_fraction), adverse_move)
        source_profit_ratio = (
            side * (close - entry) / entry * leverage
            if entry > 0.0
            else math.nan
        )

        states = [self._symbol_source_state(item) for item in SYMBOLS]
        by_symbol = {str(item["symbol"]): item for item in states}
        entry_state = by_symbol.get(symbol, {})

        def count(predicate) -> int:
            return sum(bool(predicate(item)) for item in states if item["ready"])

        rsi_ge_70 = count(lambda item: _finite(item["rsi"]) >= 70.0)
        tema_above = count(
            lambda item: _finite(item["tema_to_middle_bps"]) > 0.0
        )
        tema_rising = count(
            lambda item: _finite(item["tema_slope_bps"]) > 0.0
        )
        renewed_short_pressure = count(
            lambda item: (
                _finite(item["rsi"]) >= 70.0
                and _finite(item["tema_to_middle_bps"]) > 0.0
                and _finite(item["tema_slope_bps"]) > 0.0
            )
        )
        mean_reversion_progress = count(
            lambda item: (
                _finite(item["rsi"]) < 70.0
                and _finite(item["tema_to_middle_bps"]) > 0.0
                and _finite(item["tema_slope_bps"]) < 0.0
            )
        )
        return {
            "ts_event": int(ts_event),
            "timestamp_utc": datetime.fromtimestamp(
                ts_event / 1_000_000_000,
                tz=timezone.utc,
            ).isoformat(),
            "age_minutes": int(age),
            "symbol": symbol,
            "side": side,
            "entry_reference": entry,
            "close": close,
            "underlying_return_fraction": (
                side * (close - entry) / entry if entry > 0.0 else math.nan
            ),
            "source_profit_ratio": source_profit_ratio,
            "estimated_after_cost_r": self._estimated_after_cost_r(
                side=side,
                entry=entry,
                close=close,
                scenario=scenario,
            ),
            "mfe_underlying_fraction": mfe,
            "mae_underlying_fraction": mae,
            "mfe_source_profit_ratio": mfe * leverage,
            "mae_source_profit_ratio": mae * leverage,
            "entry_topology_count": int(
                scenario.get("mbe_actionable_candidates") or 0
            ),
            "ready_symbols": sum(int(item["ready"]) for item in states),
            "raw_long_cross_breadth": sum(
                int(item["long_cross"]) for item in states
            ),
            "raw_short_cross_breadth": sum(
                int(item["short_cross"]) for item in states
            ),
            "rsi_reoverbought_breadth": rsi_ge_70,
            "tema_above_middle_breadth": tema_above,
            "tema_rising_breadth": tema_rising,
            "renewed_short_pressure_breadth": renewed_short_pressure,
            "mean_reversion_progress_breadth": mean_reversion_progress,
            "entry_symbol_short_cross": int(
                entry_state.get("short_cross") or 0
            ),
            "entry_symbol_rsi": _finite(entry_state.get("rsi")),
            "entry_symbol_previous_rsi": _finite(
                entry_state.get("previous_rsi")
            ),
            "entry_symbol_tema_to_middle_bps": _finite(
                entry_state.get("tema_to_middle_bps")
            ),
            "entry_symbol_tema_slope_bps": _finite(
                entry_state.get("tema_slope_bps")
            ),
            "entry_symbol_return_1h_bps": _finite(
                entry_state.get("return_1h_bps")
            ),
            "entry_symbol_return_4h_bps": _finite(
                entry_state.get("return_4h_bps")
            ),
            "entry_symbol_return_8h_bps": _finite(
                entry_state.get("return_8h_bps")
            ),
            "entry_symbol_ema_2h_to_8h_bps": _finite(
                entry_state.get("ema_2h_to_8h_bps")
            ),
            "entry_symbol_realized_vol_1h_bps": _finite(
                entry_state.get("realized_vol_1h_bps")
            ),
            "entry_symbol_range_1h_bps": _finite(
                entry_state.get("range_1h_bps")
            ),
            "estimated_after_cost_r_contract": (
                "reference_close_minus_fee_slippage_funding_reserve"
            ),
        }

    def _manage_open_position(self, ts_event: int) -> None:
        scenario = self.current_scenario or {}
        family = str(scenario.get("scenario_family") or "")
        if family == "mbe" and self.current_symbol is not None:
            moment = datetime.fromtimestamp(
                ts_event / 1_000_000_000,
                tz=timezone.utc,
            )
            if moment.minute % 5 == 4:
                age = max(0, self.minute_index - self.position_open_minute)
                snapshot = self._snapshot(ts_event, age)
                boundaries = scenario.setdefault(
                    "mbe_lifecycle_boundary_snapshots", []
                )
                boundaries.append(snapshot)
                self.diagnostics["mbe_lifecycle_boundary_snapshots"] += 1

                recorded = {
                    int(value)
                    for value in scenario.setdefault(
                        "mbe_lifecycle_recorded_horizons", []
                    )
                }
                horizon_snapshots = scenario.setdefault(
                    "mbe_lifecycle_horizon_snapshots", []
                )
                for horizon in _SOURCE_HORIZONS:
                    if age >= horizon and horizon not in recorded:
                        horizon_snapshots.append(
                            {
                                **snapshot,
                                "source_horizon_minutes": int(horizon),
                            }
                        )
                        recorded.add(horizon)
                        self.diagnostics["mbe_lifecycle_horizon_snapshots"][
                            str(horizon)
                        ] += 1
                scenario["mbe_lifecycle_recorded_horizons"] = sorted(recorded)
        super()._manage_open_position(ts_event)


__all__ = ["Candidate35Config", "Candidate35Strategy"]
