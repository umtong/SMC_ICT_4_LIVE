"""Pure data contracts for Candidate 37."""
from __future__ import annotations

from dataclasses import dataclass, field
import math
from typing import Any, Mapping


SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT")


@dataclass(frozen=True)
class BarObservation:
    ts_event: int
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass(frozen=True)
class FeatureObservation:
    """API-compatible optional observation reserved for later evidence."""

    observed_time_ns: int
    ready: bool = False
    flow_open_10s: float = math.nan
    notional_open_10s_burst: float = math.nan
    flow_60s: float = math.nan
    efficiency_60s: float = math.nan
    oi_change_15m: float = math.nan
    premium_z: float = math.nan


@dataclass(frozen=True)
class RouteConfig:
    # These names match the reused Candidate 35 execution config.
    atr_period: int = 30
    min_impulse_atr_continuation: float = 1.35
    min_impulse_atr_reversal: float = 1.75
    min_response_atr: float = 0.10
    min_participation_ratio: float = 1.55
    min_route_score: float = 3.20
    ambiguity_score_gap: float = 0.22
    continuation_target_r: float = 2.20
    reversal_target_r: float = 1.80

    activity_lookback: int = 60
    ramp_bars: int = 4
    max_shock_age_bars: int = 2
    common_breadth: int = 3
    min_common_agreement: float = 0.75
    max_common_retrace: float = 0.52
    min_laggard_gap_atr: float = 0.18
    min_endogenous_ramp_score: float = 0.42
    reversal_reclaim_fraction: float = 0.25
    stop_buffer_atr: float = 0.08
    min_risk_atr: float = 0.08
    max_risk_atr: float = 3.20


@dataclass(frozen=True)
class RouteDecision:
    symbol: str
    state: str
    side: int = 0
    score: float = 0.0
    expected_target_r: float = 0.0
    entry_reference: float = math.nan
    stop_reference: float = math.nan
    objective_reference: float = math.nan
    episode_ts: int = 0
    reasons: tuple[str, ...] = ()
    diagnostics: Mapping[str, Any] = field(default_factory=dict)

    @property
    def actionable(self) -> bool:
        return (
            self.side in (-1, 1)
            and self.state in {"SYNC_PROPAGATION", "ENDOGENOUS_EXHAUSTION"}
            and math.isfinite(self.entry_reference)
            and math.isfinite(self.stop_reference)
            and math.isfinite(self.objective_reference)
        )


@dataclass(frozen=True)
class Snapshot:
    atr: float
    tr_atr: float
    net_atr: float
    direction: int
    volume_ratio: float
    activity: float
    efficiency: float
    abruptness: float
    ramp_score: float
    ramp_direction_share: float
