"""Frozen-invalidation repair of the inventory-release second-touch policy.

V3 proved the implementation could run, but every apparent response was rejected
or expired.  Direct code-path inspection found a causal geometry error rather
than an economic verdict: V2 used the most adverse *completed-hour context
extreme* both as the causal episode owner and as the trade invalidation.  Those
are different roles.  The resulting stop moved far away from the absorption
interaction and was widened again while the arm waited, consuming essentially
all target space.

This policy changes no thresholds and adds no fitted filter.  It keeps the V2
context, interaction, ten-minute response, entry, structural objective, costs,
risk sizing and global-slot arbitration.  It only restores the intended
lifecycle:

* the completed-hour extreme timestamp owns episode independence;
* the absorption interaction bar extreme plus the already frozen 0.15 ATR
  buffer owns invalidation;
* that invalidation never moves away while waiting;
* crossing it before confirmation kills the interpretation rather than widening
  future risk.

Predicted trade-level change before evaluation:

* V2 reward-space rejections caused by the context extreme should become valid
  responses when the original interaction remains intact;
* immediate continuation through the interaction stop should be rejected before
  entry;
* target-before-retest and TTL-expired episodes should remain non-trades;
* if responses still have non-positive cost-after expectancy, or almost no
  independent responses survive across periods, the second-touch state model is
  falsified rather than tuned.
"""
from __future__ import annotations

from dataclasses import replace
import importlib.util
import math
from pathlib import Path
import sys
from typing import Any

from router import ABSORPTION_STATE, RouteDecision, classify_absorption
from strategy_base import SYMBOLS


_BASE_PATH = (
    Path(__file__).resolve().parents[1]
    / "candidate-55"
    / "strategy_inventory_release_second_touch.py"
)
_SPEC = importlib.util.spec_from_file_location(
    "candidate55_inventory_release_second_touch_v2_for_v4",
    _BASE_PATH,
)
if _SPEC is None or _SPEC.loader is None:
    raise RuntimeError(f"cannot load second-touch v2 policy: {_BASE_PATH}")
_BASE = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = _BASE
_SPEC.loader.exec_module(_BASE)


Candidate35Config = _BASE.Candidate35Config
_cost_after_net_r = _BASE._cost_after_net_r
_ArmedSecondTouch = _BASE._ArmedSecondTouch


class Candidate35Strategy(_BASE.Candidate35Strategy):
    """The frozen V2 policy with owner and invalidation geometry separated."""

    def __init__(self, config: Candidate35Config) -> None:
        super().__init__(config)
        self.diagnostics.update(
            {
                "candidate": "candidate-55-inventory-release-second-touch-v4",
                "state_model": (
                    "inventory release + premium turn -> real-flow sweep/reclaim "
                    "-> intact frozen interaction -> defended boundary retest response"
                ),
                "second_touch_owner_extreme_separated": True,
                "second_touch_frozen_stop_invalidations": 0,
                "second_touch_stop_target_ambiguities": 0,
                "risk_geometry": (
                    "frozen-interaction-extreme-plus-frozen-0.15-atr; "
                    "completed-hour-extreme-timestamp-owns-independence-only"
                ),
            }
        )

    def _event(self, *args: Any, **kwargs: Any) -> None:
        """Remove only the duplicated diagnostic timestamp present in V2."""
        if len(args) >= 2 and "ts_event" in kwargs:
            duplicate = int(kwargs.pop("ts_event"))
            positional = int(args[1])
            if duplicate != positional:
                raise RuntimeError(
                    "diagnostic timestamp disagrees with positional event timestamp: "
                    f"{duplicate} != {positional}"
                )
        super()._event(*args, **kwargs)

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
            if age > int(self.config.second_touch_ttl_minutes):
                self._expire_arm(key, ts_event, "TTL_EXPIRED")
                continue

            bar = self.bars[arm.symbol][-1]
            buffer = float(self.config.microauction_stop_buffer_atr) * float(
                arm.frozen_atr
            )
            frozen_stop = (
                float(arm.episode_extreme) - buffer
                if arm.side > 0
                else float(arm.episode_extreme) + buffer
            )
            if arm.side > 0:
                invalidated = float(bar.low) <= frozen_stop
                touched = float(bar.low) <= float(arm.reference)
                target_touched = float(bar.high) >= float(arm.target)
                close_back = float(bar.close) > float(arm.reference)
            else:
                invalidated = float(bar.high) >= frozen_stop
                touched = float(bar.high) >= float(arm.reference)
                target_touched = float(bar.low) <= float(arm.target)
                close_back = float(bar.close) < float(arm.reference)

            if invalidated and target_touched:
                self.diagnostics["second_touch_stop_target_ambiguities"] += 1
                self._expire_arm(
                    key,
                    ts_event,
                    "FROZEN_STOP_AND_TARGET_SAME_BAR_BEFORE_RESPONSE",
                )
                continue
            if invalidated:
                self.diagnostics["second_touch_frozen_stop_invalidations"] += 1
                self._expire_arm(
                    key,
                    ts_event,
                    "FROZEN_INTERACTION_INVALIDATED_BEFORE_RESPONSE",
                )
                continue
            if target_touched and touched:
                self.diagnostics["second_touch_intrabar_ambiguity"] += 1
                self._expire_arm(key, ts_event, "TARGET_AND_RETEST_SAME_BAR")
                continue
            if target_touched:
                self._expire_arm(key, ts_event, "TARGET_REACHED_BEFORE_RETEST")
                continue
            if not touched:
                continue
            self.diagnostics["second_touch_reference_touches"] += 1
            if not close_back:
                self.diagnostics["second_touch_close_rejections"] += 1
                continue

            observation = observations.get(arm.symbol)
            if observation is None or not observation.ready:
                self.diagnostics["feature_stale_episodes"] += 1
                continue
            directional_flow = arm.side * float(observation.flow_15s)
            directional_depth = arm.side * float(observation.depth_imbalance_1)
            if directional_flow <= 0.0:
                self.diagnostics["second_touch_flow_rejections"] += 1
                continue
            if directional_depth < float(
                self.config.second_touch_min_directional_depth
            ):
                self.diagnostics["second_touch_depth_rejections"] += 1
                continue

            entry = float(bar.close)
            stop = frozen_stop
            target = float(arm.target)
            planned_loss, net_reward, target_net_r = _cost_after_net_r(
                entry=entry,
                stop=stop,
                target=target,
                side=arm.side,
                fee_bps_each_side=float(
                    self.config.all_in_cost_bps_each_side
                ),
                slippage_bps_each_side=float(
                    self.config.adverse_slippage_bps_each_side
                ),
                funding_reserve_bps=float(self.config.funding_reserve_bps),
            )
            if (
                not math.isfinite(target_net_r)
                or target_net_r + 1e-12
                < float(self.config.second_touch_min_net_r)
            ):
                self.diagnostics["second_touch_reward_space_rejections"] += 1
                self._event(
                    "SECOND_TOUCH_REWARD_SPACE_REJECTED",
                    ts_event,
                    symbol=arm.symbol,
                    side=arm.side,
                    episode_key=f"{key[0]}:{key[1]}:{key[2]}",
                    entry=entry,
                    stop=stop,
                    target=target,
                    target_net_r=target_net_r,
                )
                continue

            diagnostics = dict(arm.decision.diagnostics)
            diagnostics.update(arm.context)
            diagnostics.update(
                {
                    "second_touch_arm_ts": int(arm.armed_ts),
                    "second_touch_response_ts": int(ts_event),
                    "second_touch_age_minutes": age,
                    "second_touch_reference": float(arm.reference),
                    "second_touch_entry_close": entry,
                    "second_touch_flow_15s": float(observation.flow_15s),
                    "second_touch_directional_flow_15s": directional_flow,
                    "second_touch_depth_imbalance_1": float(
                        observation.depth_imbalance_1
                    ),
                    "second_touch_directional_depth": directional_depth,
                    "frozen_interaction_extreme": float(arm.episode_extreme),
                    "frozen_interaction_stop": stop,
                    "frozen_interaction_atr": float(arm.frozen_atr),
                    "planned_loss_per_unit_reference": planned_loss,
                    "structural_target_net_reward_per_unit": net_reward,
                    "structural_target_net_r": target_net_r,
                    "structural_target": target,
                    "inventory_episode_key": f"{key[0]}:{key[1]}:{key[2]}",
                }
            )
            adapted = replace(
                arm.decision,
                entry_reference=entry,
                stop_reference=stop,
                objective_reference=target,
                reasons=(
                    *arm.decision.reasons,
                    "COMPLETED_HOUR_EXTREME_OWNS_EPISODE_ONLY",
                    "FROZEN_INTERACTION_INVALIDATION_RETAINED",
                    "RECLAIMED_BOUNDARY_RETESTED",
                    "COMPLETED_CLOSE_BACK_INSIDE_BALANCE",
                    "TERMINAL_AGGRESSOR_FLOW_FLIPPED",
                    "DIRECTIONAL_DEPTH_RETAINED",
                    "OPPOSITE_BALANCE_BOUNDARY_COST_AFTER_TARGET",
                ),
                diagnostics=diagnostics,
            )
            response_record = {
                "ts_event": int(ts_event),
                "symbol": arm.symbol,
                "side": int(arm.side),
                "episode_key": f"{key[0]}:{key[1]}:{key[2]}",
                "age_minutes": age,
                "entry": entry,
                "reference": float(arm.reference),
                "stop": stop,
                "target": target,
                "target_net_r": target_net_r,
                "directional_flow_15s": directional_flow,
                "directional_depth": directional_depth,
            }
            responses.append((adapted, key, response_record))

        return responses

    def _arm_new_interactions(
        self,
        ts_event: int,
        observations: dict[str, Any],
    ) -> int:
        if not all(item.ready for item in observations.values()):
            return 0
        lookback = int(self.config.inventory_context_lookback_minutes)
        returns = {
            symbol: self._hour_return(self.bars[symbol], lookback)
            for symbol in SYMBOLS
        }
        if not all(math.isfinite(value) for value in returns.values()):
            self._reject_context("HOURLY_RETURN_NOT_READY")
            return 0
        market_return = sum(returns.values()) / len(returns)
        contexts = {
            symbol: self._context_observation(symbol, ts_event)
            for symbol in SYMBOLS
        }
        armed_now = 0
        route_counts = self.diagnostics["route_counts"]
        reason_counts = self.diagnostics["unresolved_reason_counts"]

        for symbol in SYMBOLS:
            decision = classify_absorption(
                symbol,
                tuple(self.bars[symbol]),
                observations[symbol],
                self.route_config,
            )
            route_counts[decision.state] = int(
                route_counts.get(decision.state, 0)
            ) + 1
            if not decision.actionable:
                for reason in decision.reasons:
                    reason_counts[reason] = int(
                        reason_counts.get(reason, 0)
                    ) + 1
                continue
            self.diagnostics["base_absorption_states"] += 1

            context_observation = contexts[symbol]
            if not context_observation.ready:
                self.diagnostics["inventory_context_not_ready"] += 1
                self._reject_context("OI_OR_PREMIUM_CONTEXT_NOT_READY")
                continue
            side = int(decision.side)
            side_relative_return = side * (returns[symbol] - market_return)
            oi_release = float(context_observation.oi_change_15m)
            premium_recovery = side * float(
                context_observation.premium_change_5m
            )
            context = {
                "symbol_return_60m": returns[symbol],
                "market_return_60m": market_return,
                "side_relative_return_60m": side_relative_return,
                "oi_change_15m": oi_release,
                "side_premium_change_5m": premium_recovery,
            }
            if not side_relative_return < 0.0:
                self._reject_context("NO_IDIOSYNCRATIC_ADVERSE_DISPLACEMENT")
                continue
            if not oi_release < 0.0:
                self._reject_context("NO_OPEN_INTEREST_RELEASE")
                continue
            if not premium_recovery > 0.0:
                self._reject_context("NO_PREMIUM_RECOVERY")
                continue

            owner_extreme_ts, _ = self._episode_extreme(
                self.bars[symbol],
                side,
                int(self.config.second_touch_episode_lookback_minutes),
            )
            key = (symbol, side, owner_extreme_ts)
            if key in self.used_second_touch_keys:
                self.diagnostics["second_touch_used_episode_rejections"] += 1
                continue
            if key in self.seen_arm_keys:
                self.diagnostics["second_touch_seen_episode_rejections"] += 1
                continue
            if any(
                arm.symbol == symbol and arm.side == side
                for arm in self.armed_second_touches.values()
            ):
                continue

            diagnostics = dict(decision.diagnostics)
            prior_low = float(diagnostics.get("prior_balance_low", math.nan))
            prior_high = float(diagnostics.get("prior_balance_high", math.nan))
            atr = float(diagnostics.get("atr", math.nan))
            stop_reference = float(decision.stop_reference)
            if not all(
                math.isfinite(value)
                for value in (prior_low, prior_high, atr, stop_reference)
            ):
                self._reject_context("ARM_GEOMETRY_NOT_FINITE")
                continue
            buffer = float(self.config.microauction_stop_buffer_atr) * atr
            interaction_extreme = (
                stop_reference + buffer if side > 0 else stop_reference - buffer
            )
            reference = prior_low if side > 0 else prior_high
            target = prior_high if side > 0 else prior_low
            if side > 0 and not (0.0 < stop_reference < interaction_extreme <= reference < target):
                self._reject_context("ARM_LONG_INTERACTION_GEOMETRY_INVALID")
                continue
            if side < 0 and not (0.0 < target < reference <= interaction_extreme < stop_reference):
                self._reject_context("ARM_SHORT_INTERACTION_GEOMETRY_INVALID")
                continue

            for old_key, old_arm in list(self.armed_second_touches.items()):
                if old_arm.symbol == symbol and old_arm.side != side:
                    self.diagnostics["second_touch_same_symbol_replaced"] += 1
                    self._expire_arm(
                        old_key,
                        ts_event,
                        "OPPOSITE_INTERACTION_REPLACED_ARM",
                    )

            context.update(
                {
                    "inventory_owner_extreme_ts": int(owner_extreme_ts),
                    "frozen_interaction_extreme": interaction_extreme,
                    "frozen_interaction_stop": stop_reference,
                }
            )
            arm = _ArmedSecondTouch(
                key=key,
                decision=decision,
                symbol=symbol,
                side=side,
                armed_ts=int(ts_event),
                armed_minute=int(self.minute_index),
                reference=reference,
                target=target,
                episode_extreme=interaction_extreme,
                frozen_atr=atr,
                context=context,
            )
            self.armed_second_touches[key] = arm
            self.seen_arm_keys.add(key)
            self.diagnostics["second_touch_arms"] += 1
            armed_now += 1
            record = {
                "ts_event": int(ts_event),
                "symbol": symbol,
                "side": side,
                "episode_key": f"{key[0]}:{key[1]}:{key[2]}",
                "reference": reference,
                "target": target,
                "owner_extreme_ts": int(owner_extreme_ts),
                "interaction_extreme": interaction_extreme,
                "frozen_stop": stop_reference,
                "frozen_atr": atr,
                **context,
            }
            self.diagnostics["second_touch_arm_records"].append(record)
            self._event("SECOND_TOUCH_ARMED", ts_event, **record)

        return armed_now


__all__ = [
    "Candidate35Config",
    "Candidate35Strategy",
    "_cost_after_net_r",
]
