"""Candidate 39 V3 trapped-inventory release state machine.

V2 remains the authoritative classifier for accepted continuation, cascade
reclaim and peer-led repricing. V3 adds one independent state instead of
loosening those policies:

1. a completed 15-minute attack opens or preserves leverage beyond a range;
2. the attack fails back into prior value while OI has not cleared;
3. the setup remains pending -- the first reclaim is not an entry;
4. a later, separately completed micro-auction releases in the opposite
   direction with current aggressor-flow confirmation;
5. entry is a passive retest of that new release boundary, with invalidation and
   objective from the same release/prior-value auction.

The module deliberately reuses Candidate 39 V2 geometry and universe routing.
It does not create an account, fill model or portfolio simulator.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
import importlib.util
import math
from pathlib import Path
import sys
from typing import Mapping, Sequence

HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location(
    "_candidate39_v2_router_for_v3",
    HERE / "router.py",
)
if _spec is None or _spec.loader is None:
    raise ImportError(f"cannot load Candidate 39 V2 router from {HERE / 'router.py'}")
_v2 = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _v2
_spec.loader.exec_module(_v2)

# Re-export the V2 contract so Candidate 35/39 adapters can bind this module as
# ``router`` without changing execution or feature-store code.
BarObservation = _v2.BarObservation
FeatureObservation = _v2.FeatureObservation
RouteConfig = _v2.RouteConfig
RouteDecision = _v2.RouteDecision
causal_atr = _v2.causal_atr
classify_symbol = _v2.classify_symbol
route_universe = _v2.route_universe

_EPS = 1e-12
MINUTE_NS = 60_000_000_000


def _finite(value: float, default: float = math.nan) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def _clamp(value: float, low: float, high: float) -> float:
    return min(high, max(low, value))


@dataclass(frozen=True, slots=True)
class TrappedBuildConfig:
    """Structural timing for a failed leveraged attack.

    Threshold scales are inherited from :class:`RouteConfig`; these fields only
    define causal separation and setup lifetime.
    """

    minimum_release_bars: int = 3
    setup_expiry_minutes: int = 45
    micro_balance_bars: int = 2
    minimum_directional_release_bars: int = 2
    attack_resume_tolerance_atr: float = 0.10
    release_break_tolerance_atr: float = 0.02
    minimum_oi_persistence_fraction: float = 0.25


@dataclass(frozen=True, slots=True)
class TrappedBuildSetup:
    symbol: str
    attack_side: int
    detected_ts: int
    expires_ts: int
    boundary: float
    attack_extreme: float
    context_high: float
    context_low: float
    context_mid: float
    atr: float
    setup_score: float
    attack_oi_change: float
    interaction_oi_change: float
    pre_attack_oi_change: float
    reasons: tuple[str, ...] = ()
    diagnostics: Mapping[str, float | int | bool | str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ReleaseEvaluation:
    status: str
    reason: str
    decision: RouteDecision | None = None
    diagnostics: Mapping[str, float | int | bool | str] = field(default_factory=dict)

    @property
    def terminal(self) -> bool:
        return self.status in {
            "INVALIDATED",
            "EXPIRED",
            "RELEASED",
            "GEOMETRY_REJECTED",
        }


def detect_trapped_build(
    *,
    symbol: str,
    bars: Sequence[BarObservation],
    pre_attack_feature: FeatureObservation,
    attack_feature: FeatureObservation,
    interaction_feature: FeatureObservation,
    confirmation_feature: FeatureObservation,
    route_config: RouteConfig = RouteConfig(),
    trap_config: TrappedBuildConfig = TrappedBuildConfig(),
) -> TrappedBuildSetup | None:
    """Detect a failed attack with uncleared newly built inventory.

    ``pre_attack_feature`` is observed at the last completed context minute;
    ``attack_feature`` is observed at the completed attack close;
    ``interaction_feature`` is frozen at response minute one; and
    ``confirmation_feature`` is response minute three. Each observation has a
    distinct role and no future row is accepted by the strategy adapter.
    """
    if not (
        pre_attack_feature.ready
        and attack_feature.ready
        and interaction_feature.ready
        and confirmation_feature.ready
    ):
        return None
    try:
        context = _v2._extract_context(bars, attack_feature, route_config)
    except ValueError:
        return None

    side = int(context["impulse_side"])
    if side not in (-1, 1):
        return None
    atr = float(context["atr"])
    if not math.isfinite(atr) or atr <= 0.0:
        return None

    context_range = float(context["context_range_atr"])
    range_ok = (
        route_config.min_context_range_atr
        <= context_range
        <= route_config.max_context_range_atr
    )
    break_extreme = float(context["break_extreme_atr"])
    break_close = float(context["break_close_atr"])
    retention = float(context["break_retention"])
    response_hold = float(context["response_close_from_boundary_atr"])
    impulse = float(context["impulse_atr"])
    opening_alignment = float(context["opening_flow_alignment"])
    participation = float(context["participation"])
    attack_oi = _finite(attack_feature.oi_change_15m)
    interaction_oi = _finite(interaction_feature.oi_change_15m)
    pre_attack_oi = _finite(pre_attack_feature.oi_change_15m)
    confirmation_premium = _finite(confirmation_feature.premium_z, 0.0)

    # New positions may have accumulated immediately before the attack or in
    # the attack itself. A fully cleared OI event belongs to V2 cascade logic,
    # not to the trapped-build family.
    minimum_persistent_oi = (
        route_config.min_oi_build
        * trap_config.minimum_oi_persistence_fraction
    )
    build_present = max(attack_oi, pre_attack_oi) >= route_config.min_oi_build
    inventory_not_cleared = interaction_oi >= -minimum_persistent_oi

    crossed_boundary = break_extreme >= route_config.min_sweep_atr
    attack_closed_beyond = break_close > 0.0
    # A failed auction must actually return to prior value. Weak wick retention
    # while the completed response remains outside is a continuation candidate,
    # not a trapped-reversal setup.
    failed_value = response_hold < 0.0
    attack_sponsored = (
        opening_alignment >= route_config.min_flow_alignment
        and participation >= route_config.min_participation_ratio
    )

    if not (
        range_ok
        and impulse >= route_config.min_impulse_atr_continuation
        and crossed_boundary
        and attack_closed_beyond
        and failed_value
        and build_present
        and inventory_not_cleared
        and attack_sponsored
        and abs(confirmation_premium) <= route_config.max_abs_premium_z
    ):
        return None

    detected_ts = int(context["episode_ts"])
    setup_score = (
        0.80 * _clamp(impulse / max(route_config.min_impulse_atr_continuation, _EPS), 0.0, 2.0)
        + 0.75 * _clamp(break_extreme / max(route_config.min_sweep_atr, _EPS), 0.0, 2.0)
        + 0.85 * _clamp((1.0 - retention) / max(1.0 - route_config.min_break_retention, _EPS), 0.0, 2.0)
        + 0.85 * _clamp(max(attack_oi, pre_attack_oi) / max(route_config.min_oi_build, _EPS), 0.0, 2.0)
        + 0.55 * _clamp(opening_alignment / max(route_config.min_flow_alignment, _EPS), 0.0, 2.0)
        + 0.35 * _clamp(participation / max(route_config.min_participation_ratio, _EPS), 0.0, 2.0)
    )
    diagnostics = dict(context)
    diagnostics.update(
        {
            "pre_attack_observed_time_ns": int(pre_attack_feature.observed_time_ns),
            "attack_observed_time_ns": int(attack_feature.observed_time_ns),
            "interaction_observed_time_ns": int(interaction_feature.observed_time_ns),
            "setup_confirmation_observed_time_ns": int(confirmation_feature.observed_time_ns),
            "pre_attack_oi_change_15m": pre_attack_oi,
            "attack_oi_change_15m": attack_oi,
            "interaction_oi_change_15m": interaction_oi,
            "inventory_not_cleared": inventory_not_cleared,
            "failed_value": failed_value,
            "strict_prior_value_reentry": failed_value,
            "setup_policy": "PENDING_UNTIL_SEPARATE_OPPOSITE_RELEASE_AUCTION",
        }
    )
    return TrappedBuildSetup(
        symbol=symbol,
        attack_side=side,
        detected_ts=detected_ts,
        expires_ts=detected_ts + trap_config.setup_expiry_minutes * MINUTE_NS,
        boundary=float(context["boundary"]),
        attack_extreme=float(context["impulse_extreme"]),
        context_high=float(context["context_high"]),
        context_low=float(context["context_low"]),
        context_mid=float(context["context_mid"]),
        atr=atr,
        setup_score=setup_score,
        attack_oi_change=attack_oi,
        interaction_oi_change=interaction_oi,
        pre_attack_oi_change=pre_attack_oi,
        reasons=(
            "LEVERAGED_ATTACK_OPENED_OR_PRESERVED",
            "ATTACK_FAILED_BACK_INTO_PRIOR_VALUE",
            "OPEN_INTEREST_NOT_CLEARED",
            "WAIT_FOR_SEPARATE_RELEASE_AUCTION",
        ),
        diagnostics=diagnostics,
    )


def _post_setup_bars(
    bars: Sequence[BarObservation],
    detected_ts: int,
) -> list[BarObservation]:
    return [bar for bar in bars if int(bar.ts_event) > detected_ts]


def evaluate_trapped_release(
    *,
    setup: TrappedBuildSetup,
    bars: Sequence[BarObservation],
    current_feature: FeatureObservation,
    route_config: RouteConfig = RouteConfig(),
    trap_config: TrappedBuildConfig = TrappedBuildConfig(),
) -> ReleaseEvaluation:
    """Evaluate one pending setup on the current completed minute."""
    if not bars:
        return ReleaseEvaluation("PENDING", "NO_POST_SETUP_BAR")
    now = int(bars[-1].ts_event)
    if now > setup.expires_ts:
        return ReleaseEvaluation("EXPIRED", "TRAPPED_BUILD_SETUP_EXPIRED")
    if not current_feature.ready:
        return ReleaseEvaluation("PENDING", "CURRENT_RELEASE_FEATURE_NOT_READY")

    post = _post_setup_bars(bars, setup.detected_ts)
    if len(post) < trap_config.minimum_release_bars:
        return ReleaseEvaluation(
            "PENDING",
            "WAITING_FOR_SEPARATE_RELEASE_BARS",
            diagnostics={"post_setup_bars": len(post)},
        )

    attack_side = int(setup.attack_side)
    trade_side = -attack_side
    current_close = float(post[-1].close)
    current_oi = _finite(current_feature.oi_change_15m)

    # A resumed close beyond the old attack boundary or a new attack extreme
    # means the failed-auction premise no longer exists.
    resumed = attack_side * (current_close - setup.boundary) > (
        trap_config.attack_resume_tolerance_atr * setup.atr
    )
    extreme_breached = (
        current_close >= setup.attack_extreme
        if attack_side > 0
        else current_close <= setup.attack_extreme
    )
    if resumed or extreme_breached:
        return ReleaseEvaluation(
            "INVALIDATED",
            "ATTACK_REACCEPTED_OR_EXTREME_BREACHED",
            diagnostics={
                "current_close": current_close,
                "resumed": resumed,
                "extreme_breached": extreme_breached,
            },
        )

    # OI contraction *during* the later release is not an invalidation: it can
    # be the trapped inventory finally exiting. Sequence separates this family
    # from V2 cascade logic, where contraction belongs to the original attack.
    balance_count = min(trap_config.micro_balance_bars, len(post) - 1)
    balance = post[-(balance_count + 1) : -1]
    release = post[-1:]
    if not balance or not release:
        return ReleaseEvaluation("PENDING", "MICRO_BALANCE_NOT_COMPLETE")
    release_leg = [*balance, release[-1]]

    if trade_side > 0:
        micro_boundary = max(bar.high for bar in balance)
        release_break = current_close - micro_boundary
        raw_stop = min(
            min(bar.low for bar in release_leg),
            setup.boundary - route_config.boundary_hold_tolerance_atr * setup.atr,
        )
        objective = setup.context_high
    else:
        micro_boundary = min(bar.low for bar in balance)
        release_break = micro_boundary - current_close
        raw_stop = max(
            max(bar.high for bar in release_leg),
            setup.boundary + route_config.boundary_hold_tolerance_atr * setup.atr,
        )
        objective = setup.context_low

    release_progress = trade_side * (current_close - balance[0].open) / setup.atr
    directional_bars = sum(
        1
        for bar in post[-max(3, trap_config.minimum_release_bars) :]
        if trade_side * (bar.close - bar.open) > 0.0
    )
    current_flow_alignment = trade_side * _finite(current_feature.flow_60s, 0.0)
    release_break_atr = release_break / setup.atr
    distinct_release = (
        release_progress >= route_config.min_opposite_initiative_atr
        and release_break_atr >= trap_config.release_break_tolerance_atr
        and directional_bars >= trap_config.minimum_directional_release_bars
        and current_flow_alignment >= route_config.min_flow_flip
    )
    diagnostics: dict[str, float | int | bool | str] = dict(setup.diagnostics)
    diagnostics.update(
        {
            "release_observed_time_ns": int(current_feature.observed_time_ns),
            "release_time_ns": now,
            "post_setup_bars": len(post),
            "trade_side": trade_side,
            "micro_release_boundary": micro_boundary,
            "release_progress_atr": release_progress,
            "release_break_atr": release_break_atr,
            "release_directional_bars": directional_bars,
            "release_flow_alignment": current_flow_alignment,
            "current_oi_change_15m": current_oi,
            "entry_policy": "PASSIVE_NEW_RELEASE_BOUNDARY_RETEST_LIMIT",
        }
    )
    if not distinct_release:
        return ReleaseEvaluation(
            "PENDING",
            "SEPARATE_RELEASE_NOT_CONFIRMED",
            diagnostics=diagnostics,
        )

    release_score = (
        setup.setup_score
        + 0.90 * _clamp(release_progress / max(route_config.min_opposite_initiative_atr, _EPS), 0.0, 2.0)
        + 0.75 * _clamp(release_break_atr / max(trap_config.release_break_tolerance_atr, _EPS), 0.0, 2.0)
        + 0.90 * _clamp(current_flow_alignment / max(route_config.min_flow_flip, _EPS), 0.0, 2.0)
        + 0.45 * _clamp(-current_oi / max(route_config.min_oi_contraction, _EPS), 0.0, 2.0)
        + 0.20 * directional_bars
    )
    # Reuse V2's strict structural geometry. A confirmed market view without
    # same-leg reward space is terminal NO TRADE, not a reason to move the stop.
    decision = _v2._decision(
        symbol=setup.symbol,
        state="TRAPPED_BUILD_RELEASE",
        side=trade_side,
        score=release_score,
        target_r=route_config.reversal_target_r,
        context={
            **diagnostics,
            "atr": setup.atr,
            "episode_ts": now,
            "entry": micro_boundary,
        },
        entry=micro_boundary,
        raw_stop=raw_stop,
        raw_objective=objective,
        reasons=(
            "LEVERAGED_ATTACK_FAILED_INTO_VALUE",
            "OPEN_INTEREST_REMAINED_UNCLEARED",
            "SEPARATE_OPPOSITE_RELEASE_AUCTION",
            "CURRENT_AGGRESSOR_FLOW_CONFIRMED_RELEASE",
            "PASSIVE_NEW_RELEASE_BOUNDARY_RETEST",
        ),
        config=route_config,
    )
    if decision.actionable:
        decision = replace(
            decision,
            diagnostics={
                **dict(decision.diagnostics),
                "entry_policy": "PASSIVE_NEW_RELEASE_BOUNDARY_RETEST_LIMIT",
                "setup_episode_ts": setup.detected_ts,
                "release_episode_ts": now,
            },
        )
    if not decision.actionable:
        return ReleaseEvaluation(
            "GEOMETRY_REJECTED",
            decision.reasons[0] if decision.reasons else "RELEASE_GEOMETRY_REJECTED",
            decision=decision,
            diagnostics=diagnostics,
        )
    return ReleaseEvaluation(
        "RELEASED",
        "TRAPPED_BUILD_RELEASE_CONFIRMED",
        decision=decision,
        diagnostics=diagnostics,
    )


def select_release_winner(
    decisions: Sequence[RouteDecision],
    ambiguity_score_gap: float,
) -> RouteDecision | None:
    actionable = [decision for decision in decisions if decision.actionable]
    if not actionable:
        return None
    actionable.sort(
        key=lambda decision: (
            decision.score,
            decision.expected_target_r,
            decision.symbol == "BTCUSDT",
            decision.symbol,
        ),
        reverse=True,
    )
    winner = actionable[0]
    opposite = [item for item in actionable[1:] if item.side != winner.side]
    if opposite and winner.score - opposite[0].score < ambiguity_score_gap:
        return None
    return winner
