"""Adaptive-liquidity response policy for Candidate 55.

This is not another threshold search.  The prior exact Nautilus experiments
established two separate facts:

* immediate inventory-release absorption detects a real gross-profit engine,
  but most false positives fail within minutes;
* waiting for a ten-minute boundary retest is the wrong transition: targets are
  often consumed first and local target space is usually too small after costs.

The unresolved causal question is whether the first *completed* minute after the
interaction shows liquidity adaptation to persistent one-sided flow.  This
policy therefore freezes one result-blind state transition before evaluation:

interaction
    the unchanged real-flow sweep/reclaim plus completed-hour idiosyncratic
    displacement, OI release and premium recovery from the V1 specialist;
response at exactly one completed minute
    60-second flow crosses to the reversal side, the 60-second return is no
    longer adverse, the three-minute flow remains crowded on the old side,
    measured absorption decays versus the interaction, trade/notional urgency
    both decay versus the interaction, and OI remains in release;
entry
    next executable market fill after the response minute;
invalidation
    the unchanged absorption-interaction extreme plus the frozen 0.15 ATR
    buffer; it may never widen;
objective
    the original V1 +2R cost-after objective, recomputed from the delayed entry
    and unchanged structural invalidation.

Every boundary is natural or relative to the episode itself: zero-crossings,
continued OI release, and decay from the interaction snapshot.  There is no
fitted numerical cutoff.  An arm receives one completed response minute only;
there is no search over later minutes.

Predictions declared before evaluation:

* the fast-stop false-positive group should be removed disproportionately;
* the rare V1 gross-profit episodes should remain when the aggressor imbalance
  is being absorbed rather than merely pausing;
* if fresh periods show non-positive cost-after expectancy, negligible
  independent density, or the same immediate-stop anatomy, this state model is
  falsified and its boundaries are not tuned.
"""
from __future__ import annotations

from dataclasses import replace
import importlib.util
import math
from pathlib import Path
import sys
from typing import Any

from router import RouteDecision


_V4_PATH = (
    Path(__file__).resolve().parents[1]
    / "candidate-55"
    / "strategy_inventory_release_second_touch_v4.py"
)
_V4_SPEC = importlib.util.spec_from_file_location(
    "candidate55_inventory_release_second_touch_v4_for_adaptive_response",
    _V4_PATH,
)
if _V4_SPEC is None or _V4_SPEC.loader is None:
    raise RuntimeError(f"cannot load second-touch v4 policy: {_V4_PATH}")
_V4 = importlib.util.module_from_spec(_V4_SPEC)
sys.modules[_V4_SPEC.name] = _V4
_V4_SPEC.loader.exec_module(_V4)

_V1_PATH = (
    Path(__file__).resolve().parents[1]
    / "candidate-55"
    / "strategy_inventory_release_absorption.py"
)
_V1_SPEC = importlib.util.spec_from_file_location(
    "candidate55_inventory_release_v1_target_for_adaptive_response",
    _V1_PATH,
)
if _V1_SPEC is None or _V1_SPEC.loader is None:
    raise RuntimeError(f"cannot load inventory-release v1 target: {_V1_PATH}")
_V1 = importlib.util.module_from_spec(_V1_SPEC)
sys.modules[_V1_SPEC.name] = _V1
_V1_SPEC.loader.exec_module(_V1)


Candidate35Config = _V4.Candidate35Config
_cost_after_net_r = _V4._cost_after_net_r
_cost_aware_target = _V1._cost_aware_target


class Candidate35Strategy(_V4.Candidate35Strategy):
    """One-slot, one-minute adaptive-liquidity response specialist."""

    def __init__(self, config: Candidate35Config) -> None:
        super().__init__(config)
        self.diagnostics.update(
            {
                "candidate": "candidate-55-inventory-release-adaptive-response-v1",
                "state_model": (
                    "inventory release + premium turn -> real-flow sweep/reclaim "
                    "-> first completed minute liquidity adaptation"
                ),
                "adaptive_response_exact_age_minutes": 1,
                "adaptive_response_checks": 0,
                "adaptive_response_stop_invalidations": 0,
                "adaptive_response_nonfinite_rejections": 0,
                "adaptive_response_flow60_rejections": 0,
                "adaptive_response_return_rejections": 0,
                "adaptive_response_flow3m_rejections": 0,
                "adaptive_response_absorption_decay_rejections": 0,
                "adaptive_response_notional_decay_rejections": 0,
                "adaptive_response_trade_rate_decay_rejections": 0,
                "adaptive_response_oi_release_rejections": 0,
                "adaptive_response_geometry_rejections": 0,
                "adaptive_response_responses": 0,
                "adaptive_response_records": [],
                "target_geometry": "inherited-v1-cost-after-two-r-from-delayed-entry",
                "risk_geometry": "frozen-absorption-interaction-extreme-plus-0.15-atr",
            }
        )

    def _reject_adaptive(
        self,
        key: tuple[str, int, int],
        ts_event: int,
        reason: str,
        counter: str,
    ) -> None:
        self.diagnostics[counter] += 1
        self._expire_arm(key, ts_event, reason)

    def _response_candidates(
        self,
        ts_event: int,
        observations: dict[str, Any],
    ) -> list[tuple[RouteDecision, tuple[str, int, int], dict[str, float]]]:
        responses: list[
            tuple[RouteDecision, tuple[str, int, int], dict[str, float]]
        ] = []
        for key, arm in list(self.armed_second_touches.items()):
            age = int(self.minute_index - arm.armed_minute)
            if age <= 0:
                continue
            if age != 1:
                self._expire_arm(key, ts_event, "NO_FIRST_COMPLETED_MINUTE_RESPONSE")
                continue

            self.diagnostics["adaptive_response_checks"] += 1
            bar = self.bars[arm.symbol][-1]
            buffer = float(self.config.microauction_stop_buffer_atr) * float(
                arm.frozen_atr
            )
            stop = (
                float(arm.episode_extreme) - buffer
                if arm.side > 0
                else float(arm.episode_extreme) + buffer
            )
            stop_invalidated = (
                float(bar.low) <= stop
                if arm.side > 0
                else float(bar.high) >= stop
            )
            if stop_invalidated:
                self._reject_adaptive(
                    key,
                    ts_event,
                    "FROZEN_INTERACTION_INVALIDATED_DURING_RESPONSE",
                    "adaptive_response_stop_invalidations",
                )
                continue

            observation = observations.get(arm.symbol)
            context = self._context_observation(arm.symbol, ts_event)
            interaction = arm.decision.diagnostics
            numbers = (
                float(observation.flow_60s) if observation is not None else math.nan,
                float(observation.flow_3m) if observation is not None else math.nan,
                float(observation.ret_60s_bps) if observation is not None else math.nan,
                float(observation.absorption_60s) if observation is not None else math.nan,
                float(observation.notional_burst) if observation is not None else math.nan,
                float(observation.trade_count_burst) if observation is not None else math.nan,
                float(context.oi_change_15m),
                float(interaction.get("absorption_60s", math.nan)),
                float(interaction.get("notional_burst", math.nan)),
                float(interaction.get("trade_count_burst", math.nan)),
            )
            if (
                observation is None
                or not observation.ready
                or not context.ready
                or not all(math.isfinite(value) for value in numbers)
            ):
                self._reject_adaptive(
                    key,
                    ts_event,
                    "ADAPTIVE_RESPONSE_INPUT_NOT_READY",
                    "adaptive_response_nonfinite_rejections",
                )
                continue

            side = int(arm.side)
            directional_flow_60s = side * float(observation.flow_60s)
            directional_flow_3m = side * float(observation.flow_3m)
            directional_return_60s = side * float(observation.ret_60s_bps)
            response_absorption = float(observation.absorption_60s)
            interaction_absorption = float(interaction["absorption_60s"])
            response_notional_burst = float(observation.notional_burst)
            interaction_notional_burst = float(interaction["notional_burst"])
            response_trade_burst = float(observation.trade_count_burst)
            interaction_trade_burst = float(interaction["trade_count_burst"])
            response_oi_change = float(context.oi_change_15m)

            checks = {
                "flow60_crossed_to_response_side": directional_flow_60s > 0.0,
                "return_no_longer_adverse": directional_return_60s >= 0.0,
                "three_minute_flow_still_crowded_old_side": directional_flow_3m < 0.0,
                "absorption_decayed_from_interaction": (
                    response_absorption < interaction_absorption
                ),
                "notional_urgency_decayed": (
                    response_notional_burst < interaction_notional_burst
                ),
                "trade_rate_urgency_decayed": (
                    response_trade_burst < interaction_trade_burst
                ),
                "open_interest_release_persists": response_oi_change < 0.0,
            }
            if not checks["flow60_crossed_to_response_side"]:
                self._reject_adaptive(
                    key,
                    ts_event,
                    "NO_60S_FLOW_ZERO_CROSS",
                    "adaptive_response_flow60_rejections",
                )
                continue
            if not checks["return_no_longer_adverse"]:
                self._reject_adaptive(
                    key,
                    ts_event,
                    "RESPONSE_RETURN_STILL_ADVERSE",
                    "adaptive_response_return_rejections",
                )
                continue
            if not checks["three_minute_flow_still_crowded_old_side"]:
                self._reject_adaptive(
                    key,
                    ts_event,
                    "OLD_SIDE_CROWDING_NOT_PRESENT",
                    "adaptive_response_flow3m_rejections",
                )
                continue
            if not checks["absorption_decayed_from_interaction"]:
                self._reject_adaptive(
                    key,
                    ts_event,
                    "ABSORPTION_NOT_DECAYING",
                    "adaptive_response_absorption_decay_rejections",
                )
                continue
            if not checks["notional_urgency_decayed"]:
                self._reject_adaptive(
                    key,
                    ts_event,
                    "NOTIONAL_URGENCY_NOT_DECAYING",
                    "adaptive_response_notional_decay_rejections",
                )
                continue
            if not checks["trade_rate_urgency_decayed"]:
                self._reject_adaptive(
                    key,
                    ts_event,
                    "TRADE_RATE_URGENCY_NOT_DECAYING",
                    "adaptive_response_trade_rate_decay_rejections",
                )
                continue
            if not checks["open_interest_release_persists"]:
                self._reject_adaptive(
                    key,
                    ts_event,
                    "OPEN_INTEREST_RELEASE_DID_NOT_PERSIST",
                    "adaptive_response_oi_release_rejections",
                )
                continue

            entry = float(bar.close)
            if (side > 0 and not (0.0 < stop < entry)) or (
                side < 0 and not (0.0 < entry < stop)
            ):
                self._reject_adaptive(
                    key,
                    ts_event,
                    "DELAYED_ENTRY_INVALIDATED_GEOMETRY",
                    "adaptive_response_geometry_rejections",
                )
                continue
            try:
                target, target_meta = _cost_aware_target(
                    entry=entry,
                    stop=stop,
                    side=side,
                    fee_bps_each_side=float(
                        self.config.all_in_cost_bps_each_side
                    ),
                    slippage_bps_each_side=float(
                        self.config.adverse_slippage_bps_each_side
                    ),
                    funding_reserve_bps=float(
                        self.config.funding_reserve_bps
                    ),
                    net_target_r=float(self.config.inventory_target_net_r),
                )
            except ValueError:
                self._reject_adaptive(
                    key,
                    ts_event,
                    "COST_AFTER_TARGET_GEOMETRY_INVALID",
                    "adaptive_response_geometry_rejections",
                )
                continue

            diagnostics = dict(interaction)
            diagnostics.update(arm.context)
            diagnostics.update(target_meta)
            diagnostics.update(
                {
                    "adaptive_response_ts": int(ts_event),
                    "adaptive_response_age_minutes": age,
                    "adaptive_response_entry_close": entry,
                    "adaptive_response_frozen_stop": stop,
                    "adaptive_response_directional_flow_60s": directional_flow_60s,
                    "adaptive_response_directional_flow_3m": directional_flow_3m,
                    "adaptive_response_directional_return_60s_bps": directional_return_60s,
                    "adaptive_response_interaction_absorption": interaction_absorption,
                    "adaptive_response_absorption": response_absorption,
                    "adaptive_response_interaction_notional_burst": interaction_notional_burst,
                    "adaptive_response_notional_burst": response_notional_burst,
                    "adaptive_response_interaction_trade_burst": interaction_trade_burst,
                    "adaptive_response_trade_burst": response_trade_burst,
                    "adaptive_response_oi_change_15m": response_oi_change,
                    "inventory_episode_key": f"{key[0]}:{key[1]}:{key[2]}",
                }
            )
            adapted = replace(
                arm.decision,
                entry_reference=entry,
                stop_reference=stop,
                objective_reference=float(target),
                reasons=(
                    *arm.decision.reasons,
                    "FIRST_COMPLETED_MINUTE_RESPONSE_ONLY",
                    "SIXTY_SECOND_FLOW_ZERO_CROSS",
                    "PRICE_NO_LONGER_ADVANCING_WITH_OLD_AGGRESSION",
                    "THREE_MINUTE_OLD_SIDE_CROWDING_REMAINS",
                    "ABSORPTION_DECAYS_FROM_INTERACTION",
                    "TRADE_AND_NOTIONAL_URGENCY_DECAY",
                    "OPEN_INTEREST_RELEASE_PERSISTS",
                    "FROZEN_INTERACTION_INVALIDATION",
                    "INHERITED_COST_AFTER_TWO_R_OBJECTIVE",
                ),
                diagnostics=diagnostics,
            )
            record = {
                "ts_event": int(ts_event),
                "symbol": arm.symbol,
                "side": side,
                "episode_key": f"{key[0]}:{key[1]}:{key[2]}",
                "entry": entry,
                "stop": stop,
                "target": float(target),
                "directional_flow_60s": directional_flow_60s,
                "directional_flow_3m": directional_flow_3m,
                "directional_return_60s_bps": directional_return_60s,
                "interaction_absorption": interaction_absorption,
                "response_absorption": response_absorption,
                "interaction_notional_burst": interaction_notional_burst,
                "response_notional_burst": response_notional_burst,
                "interaction_trade_burst": interaction_trade_burst,
                "response_trade_burst": response_trade_burst,
                "oi_change_15m": response_oi_change,
            }
            self.diagnostics["adaptive_response_responses"] += 1
            self.diagnostics["adaptive_response_records"].append(record)
            responses.append((adapted, key, record))

        return responses


__all__ = [
    "Candidate35Config",
    "Candidate35Strategy",
    "_cost_after_net_r",
]
