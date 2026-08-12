"""Candidate 57 crowded-long breakdown adapter over ZaratustraV13.

The public V13 short entry and reused NautilusTrader execution shell remain
unchanged. Candidate 57 adds one causal state router before universe
arbitration: top-position long/short ratio > 1.20 and either taker sell
dominance or source DI+BB concurrence.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
from typing import Any, Mapping, Sequence

from crowd_gate import CrowdGateConfig, CrowdGateStore, filter_and_rank

_BASE_PATH = Path(__file__).resolve().with_name("strategy_zaratustra.py")
_SPEC = importlib.util.spec_from_file_location(
    "candidate57_v13_crowd_reused_execution", _BASE_PATH
)
if _SPEC is None or _SPEC.loader is None:
    raise RuntimeError(f"cannot load reused Zaratustra execution: {_BASE_PATH}")
_BASE = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = _BASE
_SPEC.loader.exec_module(_BASE)


class Candidate35Config(_BASE.Candidate35Config, frozen=True):
    zaratustra_crowd_min_ratio: float = 1.20
    zaratustra_taker_max_ratio: float = 1.00
    zaratustra_crowd_row_max_age_seconds: float = 65.0
    zaratustra_crowd_metrics_max_age_seconds: float = 305.0


_ROUTE_GLOBALS = _BASE.Candidate35Strategy._on_complete_universe_minute.__globals__
_ORIGINAL_ROUTE_UNIVERSE = _ROUTE_GLOBALS["route_universe"]


class Candidate35Strategy(_BASE.Candidate35Strategy):
    def __init__(self, config: Candidate35Config) -> None:
        super().__init__(config)
        if str(config.zaratustra_variant) != "source_short":
            raise ValueError(
                "Candidate 57 freezes the inherited entry family to source_short"
            )
        self.crowd_gate_config = CrowdGateConfig(
            crowd_min_ratio=float(config.zaratustra_crowd_min_ratio),
            taker_max_ratio=float(config.zaratustra_taker_max_ratio),
            max_row_age_seconds=float(
                config.zaratustra_crowd_row_max_age_seconds
            ),
            max_metrics_age_seconds=float(
                config.zaratustra_crowd_metrics_max_age_seconds
            ),
        )
        self.crowd_stores = {
            symbol: CrowdGateStore(self.feature_paths[symbol])
            for symbol in self.feature_paths
        }
        self._original_route_universe = _ORIGINAL_ROUTE_UNIVERSE
        self.diagnostics.update(
            {
                "candidate": "candidate-57",
                "family": "CROWDED_LONG_BREAKDOWN_ACCEPTANCE",
                "external_source": (
                    "remiotore/ccxt-freqtrade:strategies/ZaratustraV13.py"
                ),
                "external_source_blob": (
                    "c8e46aa6b0164f6638c379e3cbd7ba7d9b28cd23"
                ),
                "source_entry_variant_frozen": "source_short",
                "crowd_gate_threshold": float(
                    self.crowd_gate_config.crowd_min_ratio
                ),
                "taker_sell_threshold": float(
                    self.crowd_gate_config.taker_max_ratio
                ),
                "crowd_gate_evaluations": 0,
                "crowd_gate_passes": 0,
                "crowd_gate_rejections": 0,
                "crowd_gate_reason_counts": {},
                "crowd_gate_confirmation_counts": {},
                "crowd_gate_before_universe_arbitration": 1,
                "source_price_stop_trailing_changed": 0,
                "project_daytrade_overlay_max_hold_minutes": int(
                    config.max_hold_minutes
                ),
                "project_funding_safety_overlay": 1,
            }
        )

    def on_start(self) -> None:
        _ROUTE_GLOBALS["route_universe"] = self._crowd_route_universe
        super().on_start()

    def _crowd_route_universe(
        self,
        bars_by_symbol: Mapping[str, Sequence[Any]],
        features_by_symbol: Mapping[str, Any],
        config: Any,
    ) -> tuple[Any | None, dict[str, Any]]:
        _, decisions = self._original_route_universe(
            bars_by_symbol=bars_by_symbol,
            features_by_symbol=features_by_symbol,
            config=config,
        )
        winner, filtered, assessments = filter_and_rank(
            decisions,
            self.crowd_stores,
            config=self.crowd_gate_config,
        )
        reasons = self.diagnostics["crowd_gate_reason_counts"]
        confirmations = self.diagnostics["crowd_gate_confirmation_counts"]
        for assessment in assessments.values():
            self.diagnostics["crowd_gate_evaluations"] += 1
            if assessment.passed:
                self.diagnostics["crowd_gate_passes"] += 1
                mode = str(assessment.confirmation_mode)
                confirmations[mode] = int(confirmations.get(mode, 0)) + 1
            else:
                self.diagnostics["crowd_gate_rejections"] += 1
                reason = str(assessment.reason)
                reasons[reason] = int(reasons.get(reason, 0)) + 1
        return winner, filtered

    def _on_complete_universe_minute(self, ts_event: int) -> None:
        super()._on_complete_universe_minute(ts_event)
        if self.current_scenario is not None:
            diagnostics = dict(self.current_scenario.get("diagnostics", {}))
            self.current_scenario.update(
                {
                    "candidate": "candidate-57",
                    "family": "CROWDED_LONG_BREAKDOWN_ACCEPTANCE",
                    "source_file": "ZaratustraV13.py",
                    "source_blob": (
                        "c8e46aa6b0164f6638c379e3cbd7ba7d9b28cd23"
                    ),
                    "source_entry_variant": "source_short",
                    "context": "TOP_TRADER_POSITION_RATIO_GT_1_20",
                    "confirmation": diagnostics.get(
                        "candidate57_confirmation_mode"
                    ),
                    "source_stop_trailing_unchanged": True,
                    "project_daytrade_overlay_max_hold_minutes": int(
                        self.config.max_hold_minutes
                    ),
                }
            )

    def on_stop(self) -> None:
        try:
            super().on_stop()
        finally:
            _ROUTE_GLOBALS["route_universe"] = _ORIGINAL_ROUTE_UNIVERSE


__all__ = ["Candidate35Config", "Candidate35Strategy"]
