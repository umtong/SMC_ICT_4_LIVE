"""Exact-public-MTF wrapper for the frozen TrendRider pullback policy."""
from __future__ import annotations

from pathlib import Path

from strategy_trendrider_runoff_base import (
    Candidate35Config as _BaseConfig,
    Candidate35Strategy as _BaseStrategy,
)
from trendrider_public_mtf_context_v2 import configure_context


class Candidate35Config(_BaseConfig, frozen=True):
    trendrider_mtf_context_path: str = ""


class Candidate35Strategy(_BaseStrategy):
    def __init__(self, config: Candidate35Config) -> None:
        path = Path(str(config.trendrider_mtf_context_path))
        if not path.is_file():
            raise ValueError(f"missing exact public TrendRider MTF sidecar: {path}")
        configure_context(path)
        super().__init__(config)
        self.diagnostics.update(
            {
                "candidate57_trendrider_exact_public_mtf_v2": 1,
                "trendrider_mtf_context_path": str(path),
                "trendrider_public_daily_filter_used": 1,
                "trendrider_public_4h_confidence_used": 1,
                "trendrider_public_no_dp_fallback_used": 0,
                "trendrider_exact_mtf_thresholds_searched": 0,
                "trendrider_exact_mtf_entry_or_management_retuned": 0,
            }
        )


__all__ = ["Candidate35Config", "Candidate35Strategy"]
