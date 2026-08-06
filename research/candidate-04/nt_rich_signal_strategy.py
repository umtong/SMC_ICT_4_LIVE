#!/usr/bin/env python3
"""NautilusTrader execution strategy for causal rich-state intents.

The attached signal file contains only completed-data scenario decisions. This
Strategy owns the live-like boundary: it checks the global portfolio state,
selects a causal external-liquidity target, sizes from current Nautilus NAV,
submits contingent orders, manages funding/holding exits and persists portfolio
equity. No signal compiler output contains fills, positions, PnL or NAV.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

from nt_liquidity_strategy import LiquidityTransitionConfig
from nt_liquidity_strategy import LiquidityTransitionStrategy
from nt_liquidity_strategy import PendingSetup
from nt_liquidity_strategy import net_r_at_price
from nt_low_impact_external_strategy import LowImpactExternalLiquidityStrategy
from nt_low_impact_external_strategy import choose_external_liquidity_target


class RichSignalConfig(LiquidityTransitionConfig, frozen=True):
    # The parent config already defines optional fields, so msgspec requires any
    # extension field to have a default unless the entire struct is keyword-only.
    signals_path: str = ""
    minimum_target_net_r: float = 1.20
    projection_bars: int = 240


class RichSignalStrategy(LowImpactExternalLiquidityStrategy):
    """Route one causal intent at a time into Nautilus contingent orders."""

    def __init__(self, config: RichSignalConfig) -> None:
        super().__init__(config)
        self.signals_by_time: dict[int, list[dict[str, Any]]] = {}
        self.loaded_signal_count = 0

    def on_start(self) -> None:
        super().on_start()
        if not self.config.signals_path:
            raise RuntimeError("signals_path is required")
        path = Path(self.config.signals_path)
        rows = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(rows, list):
            raise RuntimeError("signals file must contain a JSON list")
        previous = -1
        for item in rows:
            timestamp = int(item["observe_time_ns"])
            if timestamp < previous:
                raise RuntimeError("signals must be sorted by observation time")
            previous = timestamp
            side = int(item["side"])
            if side not in (-1, 1):
                raise RuntimeError(f"invalid signal side: {side}")
            self.signals_by_time.setdefault(timestamp, []).append(dict(item))
        self.loaded_signal_count = len(rows)
        self._event(
            "RICH_SIGNALS_LOADED",
            "CONTROL",
            {
                "ts": int(self.bars[-1]["ts"]) if self.bars else 0,
                "close": 0.0,
            },
            {"signals": self.loaded_signal_count, "path": str(path)},
        )

    def _detect_session_sweep(self, row: dict[str, float | int]) -> bool:
        candidates = self.signals_by_time.get(int(row["ts"]), [])
        if not candidates:
            return False
        for signal in candidates:
            if self._submit_signal(signal, row):
                return True
        return False

    def _detect_trend_sweep(self, row: dict[str, float | int]) -> bool:
        return False

    def _projection_target(
        self,
        entry: float,
        stop: float,
        side: int,
        cost_rate: float,
    ) -> tuple[float, float, str] | None:
        rows = list(self.bars)
        # Exclude the current signal bar so it cannot manufacture its own target.
        history = rows[-(self.config.projection_bars + 1) : -1]
        if len(history) < self.config.projection_bars:
            return None
        high = max(float(item["high"]) for item in history)
        low = min(float(item["low"]) for item in history)
        width = high - low
        if not math.isfinite(width) or width <= 0.0:
            return None
        target = entry + side * width
        planned_loss = side * (entry - stop) + cost_rate * (entry + stop)
        net_r = net_r_at_price(entry, target, side, planned_loss, cost_rate)
        if net_r < self.config.minimum_target_net_r:
            return None
        return target, net_r, "pre_signal_4h_dealing_range_projection"

    def _submit_signal(
        self,
        signal: dict[str, Any],
        row: dict[str, float | int],
    ) -> bool:
        scenario = str(signal["scenario"])
        side = int(signal["side"])
        entry = float(row["close"])
        stop = float(signal["stop_level"])
        if not math.isfinite(stop) or side * (entry - stop) <= 0.0:
            self._event(
                "RICH_SIGNAL_INVALID_STOP",
                scenario,
                row,
                {"signal": signal, "entry": entry, "stop": stop},
            )
            return False

        last_entry = self.last_entry_by_scenario.get(scenario, -10**12)
        if self.bar_index - last_entry < self.config.cooldown_bars:
            self._event(
                "RICH_SIGNAL_COOLDOWN",
                scenario,
                row,
                {"signal": signal},
            )
            return False
        if self._funding_blackout(int(row["ts"])):
            self._event(
                "RICH_SIGNAL_FUNDING_BLACKOUT",
                scenario,
                row,
                {"signal": signal},
            )
            return False

        atr = self._atr()
        if not math.isfinite(atr) or atr <= 0.0:
            return False
        cost_rate = self.config.all_in_cost_bps_each_side / 10_000.0
        target = choose_external_liquidity_target(
            self._external_levels(side),
            entry=entry,
            stop=stop,
            side=side,
            cost_rate=cost_rate,
            minimum_net_r=self.config.minimum_target_net_r,
        )
        if target is not None:
            target_price = target.price
            target_net_r = target.net_r
            target_source = target.source
        else:
            projection = self._projection_target(entry, stop, side, cost_rate)
            if projection is None:
                self._event(
                    "RICH_SIGNAL_NO_CAUSAL_TARGET",
                    scenario,
                    row,
                    {"signal": signal, "entry": entry, "stop": stop},
                )
                return False
            target_price, target_net_r, target_source = projection

        # risk_sized_submit_bracket derives its stop trigger from setup.extreme;
        # invert that relation so the compiler's causal invalidation price is
        # preserved exactly while quantity still uses conservative expected fills.
        synthetic_extreme = stop + side * self.config.stop_buffer_atr * atr
        setup = PendingSetup(
            scenario=scenario,
            side=side,
            created_index=self.bar_index,
            expires_index=self.bar_index,
            extreme=synthetic_extreme,
            structure=entry,
            atr=atr,
            target_reference=target_price,
            details=dict(signal.get("details") or {}),
        )
        details = {
            **dict(signal.get("details") or {}),
            "compiled_signal_index": int(signal["signal_index"]),
            "compiled_signal_time": signal["signal_time"],
            "compiled_observe_time": signal["observe_time"],
            "compiled_stop_level": stop,
            "causal_target": target_price,
            "causal_target_source": target_source,
            "causal_target_net_r_at_signal": target_net_r,
            "minimum_target_net_r": self.config.minimum_target_net_r,
        }
        submitted = LiquidityTransitionStrategy._submit_bracket(
            self,
            setup,
            row,
            target_net_r,
            details,
        )
        if not submitted:
            self._event("RICH_SIGNAL_EXECUTION_REJECTED", scenario, row, details)
        return submitted


__all__ = ["RichSignalConfig", "RichSignalStrategy"]
