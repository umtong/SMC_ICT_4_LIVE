"""Inventory-release failed-auction second-touch specialist.

The immediate-entry absorption policy was falsified in both development and an
untouched week: most losses stopped within minutes even though the completed
minute had already swept and reclaimed the local balance.  That means the
snapshot described an interaction, not a completed state transition.

This policy keeps every upstream fact from the frozen v1 experiment and reuses
the Candidate-05 retest-response idea instead of tuning thresholds:

context
    idiosyncratic completed-hour displacement, aggregate OI release, and a
    premium turn in the proposed reversal direction;
interaction
    real Binance aggressor-flow crowding, low price-path efficiency, a sweep of
    the completed ten-minute balance, reclaim, and defended displayed depth;
transition
    during the next ten completed minutes, price must revisit the reclaimed
    balance boundary and close back on the scenario side while final-15-second
    aggressor flow and top-level depth are aligned;
entry
    next executable market fill after that completed retest response;
invalidation
    the most adverse price reached in the same armed episode through
    confirmation, plus the already frozen 0.15 ATR buffer;
objective
    the opposite boundary of the frozen pre-sweep balance.  No trade is taken
    unless that structural target retains at least the inherited 1.25R after
    the exact fee, adverse-slippage and funding reserves used for sizing.

The ten-minute response life is not a fitted value: it is the same completed
balance horizon whose failed auction created the setup.  One symbol/side/
completed-hour extreme can arm once, and all live arms are discarded when the
single global account slot is consumed.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
import importlib.util
import math
from pathlib import Path
import sys
from typing import Any

from router import ABSORPTION_STATE, RouteDecision, classify_absorption
from strategy_base import SYMBOLS


_BASE_PATH = Path(__file__).resolve().parents[1] / "candidate-55" / "strategy_inventory_release_absorption.py"
_SPEC = importlib.util.spec_from_file_location(
    "candidate55_inventory_release_absorption_base",
    _BASE_PATH,
)
if _SPEC is None or _SPEC.loader is None:
    raise RuntimeError(f"cannot load inventory-release v1 base: {_BASE_PATH}")
_BASE = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = _BASE
_SPEC.loader.exec_module(_BASE)


@dataclass(slots=True)
class _ArmedSecondTouch:
    key: tuple[str, int, int]
    decision: RouteDecision
    symbol: str
    side: int
    armed_ts: int
    armed_minute: int
    reference: float
    target: float
    episode_extreme: float
    frozen_atr: float
    context: dict[str, float]


class Candidate35Config(_BASE.Candidate35Config, frozen=True):
    second_touch_ttl_minutes: int = 10
    second_touch_episode_lookback_minutes: int = 60
    second_touch_min_directional_depth: float = 0.0
    second_touch_min_net_r: float = 1.25


def _cost_after_net_r(
    *,
    entry: float,
    stop: float,
    target: float,
    side: int,
    fee_bps_each_side: float,
    slippage_bps_each_side: float,
    funding_reserve_bps: float,
) -> tuple[float, float, float]:
    """Return planned loss, target reward and target R under shell costs."""
    values = (
        entry,
        stop,
        target,
        fee_bps_each_side,
        slippage_bps_each_side,
        funding_reserve_bps,
    )
    if side not in (-1, 1) or not all(math.isfinite(float(v)) for v in values):
        return math.nan, math.nan, math.nan
    if entry <= 0.0 or stop <= 0.0 or target <= 0.0:
        return math.nan, math.nan, math.nan
    if side > 0 and not (stop < entry < target):
        return math.nan, math.nan, math.nan
    if side < 0 and not (target < entry < stop):
        return math.nan, math.nan, math.nan

    fee = float(fee_bps_each_side) / 10_000.0
    slip = float(slippage_bps_each_side) / 10_000.0
    funding = float(funding_reserve_bps) / 10_000.0
    adverse_entry = entry * (1.0 + side * slip)
    adverse_stop = stop * (1.0 - side * slip)
    adverse_target = target * (1.0 - side * slip)
    planned_loss = (
        abs(adverse_entry - adverse_stop)
        + fee * (abs(adverse_entry) + abs(adverse_stop))
        + funding * abs(entry)
    )
    net_reward = (
        side * (adverse_target - adverse_entry)
        - fee * (abs(adverse_entry) + abs(adverse_target))
        - funding * abs(entry)
    )
    if planned_loss <= 0.0 or not math.isfinite(planned_loss):
        return math.nan, math.nan, math.nan
    return planned_loss, net_reward, net_reward / planned_loss


class Candidate35Strategy(_BASE.Candidate35Strategy):
    """One-account, response-confirmed failed-auction reversal policy."""

    def __init__(self, config: Candidate35Config) -> None:
        super().__init__(config)
        if str(config.microauction_mode).strip().lower() != "absorption":
            raise ValueError("second-touch specialist requires absorption mode")
        if int(config.second_touch_ttl_minutes) != int(
            config.microauction_balance_lookback
        ):
            raise ValueError(
                "second-touch life must equal the frozen balance lookback"
            )
        if int(config.second_touch_episode_lookback_minutes) != int(
            config.inventory_context_lookback_minutes
        ):
            raise ValueError(
                "episode lookback must equal the frozen inventory context hour"
            )
        if abs(float(config.second_touch_min_directional_depth)) > 1e-12:
            raise ValueError("directional depth boundary is the economic zero")
        if abs(float(config.second_touch_min_net_r) - 1.25) > 1e-12:
            raise ValueError("minimum cost-after target space is inherited 1.25R")

        self.armed_second_touches: dict[
            tuple[str, int, int], _ArmedSecondTouch
        ] = {}
        self.seen_arm_keys: set[tuple[str, int, int]] = set()
        self.used_second_touch_keys: set[tuple[str, int, int]] = set()
        self.diagnostics.update(
            {
                "candidate": "candidate-55-inventory-release-second-touch-v2",
                "state_model": (
                    "inventory release + premium turn -> real-flow sweep/reclaim "
                    "-> defended boundary retest response -> opposite balance boundary"
                ),
                "immediate_absorption_entries": 0,
                "second_touch_ttl_minutes": int(config.second_touch_ttl_minutes),
                "second_touch_min_net_r": float(config.second_touch_min_net_r),
                "second_touch_arms": 0,
                "second_touch_arm_expirations": 0,
                "second_touch_target_before_retest": 0,
                "second_touch_intrabar_ambiguity": 0,
                "second_touch_reference_touches": 0,
                "second_touch_close_rejections": 0,
                "second_touch_flow_rejections": 0,
                "second_touch_depth_rejections": 0,
                "second_touch_reward_space_rejections": 0,
                "second_touch_responses": 0,
                "second_touch_entries": 0,
                "second_touch_competing_responses": 0,
                "second_touch_same_symbol_replaced": 0,
                "second_touch_seen_episode_rejections": 0,
                "second_touch_used_episode_rejections": 0,
                "second_touch_arms_cleared_by_global_slot": 0,
                "second_touch_arm_records": [],
                "second_touch_response_records": [],
                "second_touch_expiration_records": [],
                "second_touch_selected_episode_keys": [],
                "target_geometry": "frozen-opposite-ten-minute-balance-boundary",
                "risk_geometry": (
                    "episode-extreme-through-confirmation-plus-frozen-0.15-atr"
                ),
            }
        )

    @staticmethod
    def _episode_extreme(
        bars: Any,
        side: int,
        lookback: int,
    ) -> tuple[int, float]:
        window = list(bars)[-(lookback + 1) :]
        if not window:
            return 0, math.nan
        if side > 0:
            bar = min(window, key=lambda item: (float(item.low), int(item.ts_event)))
            return int(bar.ts_event), float(bar.low)
        bar = max(window, key=lambda item: (float(item.high), -int(item.ts_event)))
        return int(bar.ts_event), float(bar.high)

    def _expire_arm(
        self,
        key: tuple[str, int, int],
        ts_event: int,
        reason: str,
    ) -> None:
        arm = self.armed_second_touches.pop(key, None)
        if arm is None:
            return
        if reason == "TTL_EXPIRED":
            self.diagnostics["second_touch_arm_expirations"] += 1
        elif reason == "TARGET_REACHED_BEFORE_RETEST":
            self.diagnostics["second_touch_target_before_retest"] += 1
        record = {
            "ts_event": int(ts_event),
            "symbol": arm.symbol,
            "side": int(arm.side),
            "episode_key": f"{key[0]}:{key[1]}:{key[2]}",
            "reason": reason,
            "age_minutes": int(self.minute_index - arm.armed_minute),
            "reference": float(arm.reference),
            "target": float(arm.target),
            "episode_extreme": float(arm.episode_extreme),
        }
        self.diagnostics["second_touch_expiration_records"].append(record)
        self._event("SECOND_TOUCH_ARM_EXPIRED", ts_event, **record)

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
            if arm.side > 0:
                arm.episode_extreme = min(
                    float(arm.episode_extreme),
                    float(bar.low),
                )
                touched = float(bar.low) <= float(arm.reference)
                target_touched = float(bar.high) >= float(arm.target)
                close_back = float(bar.close) > float(arm.reference)
            else:
                arm.episode_extreme = max(
                    float(arm.episode_extreme),
                    float(bar.high),
                )
                touched = float(bar.high) >= float(arm.reference)
                target_touched = float(bar.low) <= float(arm.target)
                close_back = float(bar.close) < float(arm.reference)

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
            buffer = float(self.config.microauction_stop_buffer_atr) * float(
                arm.frozen_atr
            )
            stop = (
                float(arm.episode_extreme) - buffer
                if arm.side > 0
                else float(arm.episode_extreme) + buffer
            )
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
                    "episode_extreme_through_confirmation": float(
                        arm.episode_extreme
                    ),
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
                    "RECLAIMED_BOUNDARY_RETESTED",
                    "COMPLETED_CLOSE_BACK_INSIDE_BALANCE",
                    "TERMINAL_AGGRESSOR_FLOW_FLIPPED",
                    "DIRECTIONAL_DEPTH_RETAINED",
                    "EPISODE_EXTREME_INVALIDATION_THROUGH_CONFIRMATION",
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
            side_relative_return = side * (
                returns[symbol] - market_return
            )
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
                self._reject_context(
                    "NO_IDIOSYNCRATIC_ADVERSE_DISPLACEMENT"
                )
                continue
            if not oi_release < 0.0:
                self._reject_context("NO_OPEN_INTEREST_RELEASE")
                continue
            if not premium_recovery > 0.0:
                self._reject_context("NO_PREMIUM_RECOVERY")
                continue

            extreme_ts, extreme_price = self._episode_extreme(
                self.bars[symbol],
                side,
                int(self.config.second_touch_episode_lookback_minutes),
            )
            key = (symbol, side, extreme_ts)
            if key in self.used_second_touch_keys:
                self.diagnostics[
                    "second_touch_used_episode_rejections"
                ] += 1
                continue
            if key in self.seen_arm_keys:
                self.diagnostics[
                    "second_touch_seen_episode_rejections"
                ] += 1
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
            if not all(
                math.isfinite(value)
                for value in (prior_low, prior_high, atr, extreme_price)
            ):
                self._reject_context("ARM_GEOMETRY_NOT_FINITE")
                continue
            reference = prior_low if side > 0 else prior_high
            target = prior_high if side > 0 else prior_low
            if not (0.0 < reference < target) and side > 0:
                self._reject_context("ARM_LONG_BALANCE_INVALID")
                continue
            if not (0.0 < target < reference) and side < 0:
                self._reject_context("ARM_SHORT_BALANCE_INVALID")
                continue

            # A new opposite interaction on the same symbol invalidates the old
            # pending interpretation before either can consume account risk.
            for old_key, old_arm in list(
                self.armed_second_touches.items()
            ):
                if old_arm.symbol == symbol and old_arm.side != side:
                    self.diagnostics[
                        "second_touch_same_symbol_replaced"
                    ] += 1
                    self._expire_arm(
                        old_key,
                        ts_event,
                        "OPPOSITE_INTERACTION_REPLACED_ARM",
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
                episode_extreme=extreme_price,
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
                "episode_extreme": extreme_price,
                "frozen_atr": atr,
                **context,
            }
            self.diagnostics["second_touch_arm_records"].append(record)
            self._event("SECOND_TOUCH_ARMED", ts_event, **record)

        return armed_now

    def _on_complete_universe_minute(self, ts_event: int) -> None:
        self.minute_index += 1
        self.diagnostics["complete_universe_minutes"] += 1
        self._record_equity(ts_event)

        open_symbols = [
            symbol
            for symbol in SYMBOLS
            if not self.portfolio.is_flat(self.instrument_ids[symbol])
        ]
        self.diagnostics["max_open_positions_observed"] = max(
            int(self.diagnostics["max_open_positions_observed"]),
            len(open_symbols),
        )
        if len(open_symbols) > 1:
            self.diagnostics["global_position_violations"] += 1
            for symbol in open_symbols:
                self.cancel_all_orders(self.instrument_ids[symbol])
                self.close_all_positions(self.instrument_ids[symbol])
            return
        if open_symbols:
            self.current_symbol = open_symbols[0]
            self._manage_open_position(ts_event)
            return
        if self.entry_pending:
            self.diagnostics["max_simultaneous_entry_intents"] = max(
                int(self.diagnostics["max_simultaneous_entry_intents"]),
                1,
            )
            if self.minute_index - self.entry_pending_minute > 2:
                if self.current_symbol is not None:
                    self.cancel_all_orders(
                        self.instrument_ids[self.current_symbol]
                    )
                self.diagnostics["entry_expirations"] += 1
                self._event(
                    "ENTRY_EXPIRED",
                    ts_event,
                    reason="NOT_FILLED_WITHIN_TWO_COMPLETE_MINUTES",
                )
                self._clear_trade_state()
            return
        if not (
            self.config.evaluation_start_ns
            <= ts_event
            <= self.config.evaluation_end_ns
        ):
            return

        minimum = max(
            int(self.route_config.microauction_atr_period) + 2,
            int(self.route_config.microauction_balance_lookback) + 1,
            int(self.config.inventory_context_lookback_minutes) + 1,
        )
        if any(len(self.bars[symbol]) < minimum for symbol in SYMBOLS):
            return

        observations = {
            symbol: self._observation(symbol, ts_event)
            for symbol in SYMBOLS
        }
        stale = self.diagnostics["feature_stale_by_symbol"]
        for symbol, observation in observations.items():
            if not observation.ready:
                stale[symbol] = int(stale.get(symbol, 0)) + 1

        self.diagnostics["quarter_hour_decisions"] += 1
        responses = self._response_candidates(ts_event, observations)
        if responses:
            self.diagnostics["second_touch_responses"] += len(responses)
            if len(responses) > 1:
                self.diagnostics[
                    "second_touch_competing_responses"
                ] += len(responses) - 1
            responses.sort(
                key=lambda item: (
                    -float(item[2]["target_net_r"]),
                    -float(item[2]["directional_flow_15s"]),
                    -float(item[2]["directional_depth"]),
                    -float(item[0].score),
                    item[0].symbol,
                    int(item[0].episode_ts),
                )
            )
            winner, key, response_record = responses[0]
            if not self._funding_blackout(ts_event) and (
                self.minute_index - self.last_entry_minute
                >= self.config.cooldown_minutes
            ):
                before = int(self.diagnostics["entry_submissions"])
                self._submit_decision(winner, ts_event)
                if int(self.diagnostics["entry_submissions"]) > before:
                    self.diagnostics["second_touch_entries"] += 1
                    self.used_second_touch_keys.add(key)
                    episode_text = f"{key[0]}:{key[1]}:{key[2]}"
                    self.diagnostics[
                        "second_touch_selected_episode_keys"
                    ].append(episode_text)
                    self.diagnostics[
                        "second_touch_response_records"
                    ].append(response_record)
                    cleared = max(
                        0,
                        len(self.armed_second_touches) - 1,
                    )
                    self.diagnostics[
                        "second_touch_arms_cleared_by_global_slot"
                    ] += cleared
                    self.armed_second_touches.clear()
                    if self.current_scenario is not None:
                        self.current_scenario.update(
                            {
                                "candidate": (
                                    "candidate-55-inventory-release-second-touch-v2"
                                ),
                                "state_family": ABSORPTION_STATE,
                                "inventory_episode_key": episode_text,
                                "risk_geometry": (
                                    "episode-extreme-through-confirmation-plus-frozen-atr-buffer"
                                ),
                                "management": (
                                    "opposite-frozen-balance-boundary-or-240-minute-timeout"
                                ),
                                "context": dict(
                                    self.armed_second_touches.get(
                                        key,
                                        _ArmedSecondTouch(
                                            key,
                                            winner,
                                            winner.symbol,
                                            int(winner.side),
                                            ts_event,
                                            self.minute_index,
                                            float(response_record["reference"]),
                                            float(response_record["target"]),
                                            float(winner.stop_reference),
                                            0.0,
                                            {},
                                        ),
                                    ).context
                                ),
                            }
                        )
                    return

        armed_now = self._arm_new_interactions(ts_event, observations)
        if not responses and armed_now == 0:
            self.diagnostics["unresolved_episodes"] += 1


__all__ = [
    "Candidate35Config",
    "Candidate35Strategy",
    "_cost_after_net_r",
]
