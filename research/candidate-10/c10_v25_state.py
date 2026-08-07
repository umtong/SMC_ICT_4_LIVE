"""Liquidity-shelf and top-of-book response state machine for candidate 10 v25.

The detector creates shelves from completed trade-flow auctions without quote
features.  The full trading scenario then requires causal top-of-book OFI and
replenishment evidence; the exact ablation removes only that quote response.
"""
from __future__ import annotations

from collections import Counter, deque
from math import log
from statistics import median
from typing import Iterable

from c10_v25_model import (
    LiquidityProbe,
    LiquidityResponseBar,
    LiquidityResponseParams,
    LiquidityResponsePlan,
    LiquidityResponseTransition,
    LiquidityShelf,
    NS_PER_SECOND,
)


def _sign(value: float) -> int:
    return 1 if value > 0.0 else -1 if value < 0.0 else 0


def _quantile(values: Iterable[float], probability: float) -> float | None:
    ordered = sorted(float(value) for value in values)
    if not ordered:
        return None
    if len(ordered) == 1:
        return ordered[0]
    probability = min(1.0, max(0.0, probability))
    location = (len(ordered) - 1) * probability
    lower = int(location)
    upper = min(len(ordered) - 1, lower + 1)
    weight = location - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


class LiquidityResponseStateMachine:
    """Trade failed auctions at previously demonstrated passive-liquidity shelves."""

    def __init__(
        self,
        params: LiquidityResponseParams,
        *,
        tick_size: float,
        instrument_id: str,
    ) -> None:
        if tick_size <= 0.0:
            raise ValueError("tick_size must be positive")
        if params.formation_seconds % params.bar_seconds:
            raise ValueError("formation_seconds must be divisible by bar_seconds")
        if params.approach_bars < 2:
            raise ValueError("approach_bars must be at least two")
        self.params = params
        self.tick_size = float(tick_size)
        self.instrument_id = str(instrument_id)
        self.sequence = 0
        self.shelf_sequence = 0
        self.event_sequence = 0
        self.shelves: list[LiquidityShelf] = []
        self.active_probe: LiquidityProbe | None = None
        self.recent_bars: deque[LiquidityResponseBar] = deque(
            maxlen=max(params.approach_bars + 2, params.formation_seconds + 2),
        )
        self.current_formation_id: int | None = None
        self.current_formation: list[LiquidityResponseBar] = []
        self.formation_abs_flow: deque[float] = deque(
            maxlen=params.feature_lookback_windows,
        )
        self.formation_efficiency: deque[float] = deque(
            maxlen=params.feature_lookback_windows,
        )
        self.formation_dominance: deque[float] = deque(
            maxlen=params.feature_lookback_windows,
        )
        history_seconds = params.feature_lookback_windows * params.formation_seconds
        self.abs_trade_quote_history: deque[float] = deque(maxlen=history_seconds)
        self.abs_ofi_history: deque[float] = deque(maxlen=history_seconds)
        self.range_history: deque[float] = deque(maxlen=history_seconds)
        self.spread_history: deque[float] = deque(maxlen=history_seconds)
        self.depth_history: deque[float] = deque(maxlen=history_seconds)
        self.notional_history: deque[float] = deque(maxlen=history_seconds)
        self.cooldown_active = False
        self.cooldown_normal_count = 0
        self.counters: Counter[str] = Counter()

    def diagnostics(self) -> dict[str, object]:
        return {
            "completed_rows": self.sequence,
            "active_probe": self.active_probe is not None,
            "cooldown_active": self.cooldown_active,
            "active_shelves": sum(1 for shelf in self.shelves if shelf.active),
            "total_shelves": len(self.shelves),
            "counts": dict(self.counters),
        }

    def _transition(
        self,
        *,
        scenario_id: str,
        bar: LiquidityResponseBar,
        event_type: str,
        previous_state: str,
        next_state: str,
        reason_code: str,
        reference_price: float | None = None,
        details: dict[str, object] | None = None,
    ) -> LiquidityResponseTransition:
        return LiquidityResponseTransition(
            scenario_id=scenario_id,
            event_type=event_type,
            event_time_ns=bar.ts_ns,
            observed_time_ns=bar.ts_ns,
            previous_state=previous_state,
            next_state=next_state,
            reason_code=reason_code,
            reference_price=reference_price,
            details=dict(details or {}),
        )

    def _feature_snapshot(self) -> dict[str, float] | None:
        minimum_seconds = self.params.minimum_feature_windows * self.params.formation_seconds
        if (
            len(self.abs_trade_quote_history) < minimum_seconds
            or len(self.formation_abs_flow) < self.params.minimum_feature_windows
        ):
            return None
        interaction_floor = _quantile(
            self.abs_trade_quote_history,
            self.params.interaction_flow_quantile,
        )
        confirmation_floor = _quantile(
            self.abs_trade_quote_history,
            self.params.confirmation_flow_quantile,
        )
        ofi_floor = _quantile(
            self.abs_ofi_history,
            self.params.quote_ofi_quantile,
        )
        return {
            "interaction_flow_floor": max(0.0, float(interaction_floor or 0.0)),
            "confirmation_flow_floor": max(0.0, float(confirmation_floor or 0.0)),
            "ofi_floor": max(self.tick_size, float(ofi_floor or 0.0)),
            "range_median": max(self.tick_size, float(median(self.range_history))),
            "spread_median": max(self.tick_size, float(median(self.spread_history))),
            "depth_median": max(self.tick_size, float(median(self.depth_history))),
            "causal_liquidity_notional": max(1.0, float(median(self.notional_history))),
        }

    def _formation_id(self, bar: LiquidityResponseBar) -> int:
        formation_ns = self.params.formation_seconds * NS_PER_SECOND
        return (int(bar.ts_ns) - 1) // formation_ns

    def _maybe_roll_formation(self, bar: LiquidityResponseBar) -> None:
        window_id = self._formation_id(bar)
        if self.current_formation_id is None:
            self.current_formation_id = window_id
            return
        if window_id == self.current_formation_id:
            return
        if self.current_formation:
            self._finalize_formation(self.current_formation)
        self.current_formation = []
        self.current_formation_id = window_id

    def _finalize_formation(self, bars: list[LiquidityResponseBar]) -> None:
        total = sum(bar.trade_quote_volume for bar in bars)
        signed = sum(bar.signed_trade_quote for bar in bars)
        direction = _sign(signed)
        dominance = abs(signed) / total if total > 0.0 else 0.0
        first = bars[0].mid_open
        last = bars[-1].mid_close
        directional_return = 0.0
        if first > 0.0 and last > 0.0 and direction:
            directional_return = max(0.0, direction * log(last / first))
        median_abs_flow = max(
            1.0,
            float(median(self.formation_abs_flow))
            if self.formation_abs_flow
            else abs(signed),
        )
        scaled_pressure = abs(signed) / median_abs_flow
        efficiency = directional_return / max(scaled_pressure, 1e-12)

        eligible_history = (
            len(self.formation_abs_flow) >= self.params.minimum_feature_windows
        )
        if eligible_history and direction and total > 0.0:
            flow_floor = _quantile(
                self.formation_abs_flow,
                self.params.formation_flow_quantile,
            )
            dominance_floor = _quantile(
                self.formation_dominance,
                self.params.formation_dominance_quantile,
            )
            efficiency_ceiling = _quantile(
                self.formation_efficiency,
                self.params.formation_efficiency_quantile,
            )
            qualifies = (
                abs(signed) >= float(flow_floor or 0.0)
                and dominance >= float(dominance_floor or 0.0)
                and efficiency <= float(efficiency_ceiling or float("inf"))
            )
            if qualifies:
                self._create_shelf(
                    bars=bars,
                    direction=direction,
                    dominance=dominance,
                    efficiency=efficiency,
                )
            else:
                self.counters["FORMATION_NOT_ABSORPTIVE"] += 1
        self.formation_abs_flow.append(abs(signed))
        self.formation_dominance.append(dominance)
        self.formation_efficiency.append(efficiency)
        self.counters["FORMATION_WINDOW_COMPLETED"] += 1

    def _create_shelf(
        self,
        *,
        bars: list[LiquidityResponseBar],
        direction: int,
        dominance: float,
        efficiency: float,
    ) -> None:
        price = (
            max(bar.mid_high for bar in bars)
            if direction > 0
            else min(bar.mid_low for bar in bars)
        )
        auction_range = max(bar.mid_high for bar in bars) - min(
            bar.mid_low for bar in bars
        )
        median_spread = max(
            self.tick_size,
            median(max(self.tick_size, bar.mean_spread) for bar in bars),
        )
        zone = max(
            self.tick_size * 2.0,
            self.params.shelf_zone_spread_multiple * median_spread,
            self.params.shelf_zone_range_fraction * auction_range,
        )
        for existing in self.shelves:
            if (
                existing.active
                and existing.side == direction
                and abs(existing.price - price) <= max(existing.zone, zone)
            ):
                self.counters["SHELF_DUPLICATE_SUPPRESSED"] += 1
                return
        self.shelf_sequence += 1
        side_name = "SUPPLY" if direction > 0 else "DEMAND"
        shelf = LiquidityShelf(
            shelf_id=(
                f"{self.instrument_id}:LRS:{side_name}:{self.shelf_sequence:08d}"
            ),
            side=direction,
            price=price,
            zone=zone,
            created_ns=bars[-1].ts_ns,
            formation_start_ns=bars[0].ts_ns,
            formation_end_ns=bars[-1].ts_ns,
            flow_dominance=dominance,
            impact_efficiency=efficiency,
        )
        self.shelves.append(shelf)
        if len(self.shelves) > self.params.max_shelves:
            removable = next(
                (
                    item
                    for item in self.shelves
                    if not item.active and not item.reserved
                ),
                None,
            )
            if removable is None:
                removable = next(
                    (item for item in self.shelves if not item.reserved),
                    None,
                )
            if removable is not None:
                self.shelves.remove(removable)
        self.counters[f"{side_name}_SHELF_CREATED"] += 1

    def _target_for_reversal(
        self,
        *,
        direction: int,
        entry: float,
        source_ids: set[str],
        observed_ns: int,
    ) -> LiquidityShelf | None:
        candidates = [
            shelf
            for shelf in self.shelves
            if shelf.active
            and not shelf.reserved
            and shelf.shelf_id not in source_ids
            and shelf.created_ns < observed_ns
            and shelf.side == direction
            and (
                (direction > 0 and shelf.price > entry)
                or (direction < 0 and shelf.price < entry)
            )
        ]
        if not candidates:
            return None
        return (
            min(candidates, key=lambda shelf: shelf.price)
            if direction > 0
            else max(candidates, key=lambda shelf: shelf.price)
        )

    def _crossed_shelves(
        self,
        bar: LiquidityResponseBar,
        previous_mid: float,
        move_direction: int,
    ) -> list[LiquidityShelf]:
        crossed: list[LiquidityShelf] = []
        for shelf in self.shelves:
            if not shelf.active or shelf.reserved or shelf.side != move_direction:
                continue
            if shelf.created_ns >= bar.ts_ns:
                continue
            if move_direction > 0:
                crossed_now = (
                    previous_mid <= shelf.price
                    and bar.mid_high >= shelf.price + shelf.zone
                )
            else:
                crossed_now = (
                    previous_mid >= shelf.price
                    and bar.mid_low <= shelf.price - shelf.zone
                )
            if crossed_now:
                crossed.append(shelf)
        return crossed

    def _detect_interaction(
        self,
        bar: LiquidityResponseBar,
        features: dict[str, float],
    ) -> tuple[list[LiquidityResponseTransition], LiquidityResponsePlan | None]:
        if not self.recent_bars or bar.trade_count <= 0:
            return [], None
        signed = bar.signed_trade_quote
        move_direction = _sign(signed)
        if (
            not move_direction
            or abs(signed) < features["interaction_flow_floor"]
        ):
            return [], None
        previous_mid = self.recent_bars[-1].mid_close
        crossed = self._crossed_shelves(
            bar,
            previous_mid=previous_mid,
            move_direction=move_direction,
        )
        if not crossed:
            return [], None
        source_ids = {shelf.shelf_id for shelf in crossed}
        source = (
            max(crossed, key=lambda shelf: shelf.price)
            if move_direction > 0
            else min(crossed, key=lambda shelf: shelf.price)
        )
        trade_direction = -move_direction
        target = self._target_for_reversal(
            direction=trade_direction,
            entry=bar.mid_close,
            source_ids=source_ids,
            observed_ns=bar.ts_ns,
        )
        for shelf in crossed:
            shelf.reserved = True
        self.event_sequence += 1
        scenario_id = f"{self.instrument_id}:LRA:{self.event_sequence:08d}"
        if target is None:
            for shelf in crossed:
                shelf.active = False
                shelf.reserved = False
            self.counters["INTERACTION_WITHOUT_PREEXISTING_TARGET"] += 1
            return [
                self._transition(
                    scenario_id=scenario_id,
                    bar=bar,
                    event_type="LIQUIDITY_RESPONSE_INTERACTION_REJECTED",
                    previous_state="NEUTRAL",
                    next_state="NO_TARGET",
                    reason_code="NO_PREEXISTING_OPPOSING_LIQUIDITY_SHELF",
                    reference_price=bar.mid_close,
                    details={"source_ids": sorted(source_ids)},
                ),
            ], None

        approach = list(self.recent_bars)[-self.params.approach_bars :]
        approach_low = min(item.mid_low for item in approach)
        approach_high = max(item.mid_high for item in approach)
        if move_direction > 0:
            attacked_remove = bar.ask_remove_qty
            aggressive_base = bar.taker_buy_base
        else:
            attacked_remove = bar.bid_remove_qty
            aggressive_base = bar.trade_base_volume - bar.taker_buy_base
        self.active_probe = LiquidityProbe(
            scenario_id=scenario_id,
            source_ids=tuple(sorted(source_ids)),
            source_side=source.side,
            source_price=source.price,
            source_zone=source.zone,
            initiated_sequence=self.sequence,
            initiated_ns=bar.ts_ns,
            move_direction=move_direction,
            trade_direction=trade_direction,
            raid_high=bar.mid_high,
            raid_low=bar.mid_low,
            approach_low=approach_low,
            approach_high=approach_high,
            target_id=target.shelf_id,
            target_price=target.price,
            # Replenishment starts strictly after the completed sweep second;
            # additions inside the detection second have unknowable ordering.
            cumulative_attacked_add=0.0,
            cumulative_attacked_remove=max(0.0, attacked_remove),
            cumulative_aggressive_base=max(0.0, aggressive_base),
        )
        self.counters["LIQUIDITY_SHELF_INTERACTION_DETECTED"] += 1
        if len(crossed) > 1:
            self.counters["MULTI_SHELF_SOURCE_CLUSTER"] += 1
        return [
            self._transition(
                scenario_id=scenario_id,
                bar=bar,
                event_type="LIQUIDITY_SHELF_SWEPT",
                previous_state="NEUTRAL",
                next_state="FAILED_AUCTION_WAIT",
                reason_code="AGGRESSIVE_FLOW_CROSSED_PREEXISTING_ABSORPTION_SHELF",
                reference_price=bar.mid_close,
                details={
                    "source_ids": sorted(source_ids),
                    "source_price": source.price,
                    "source_zone": source.zone,
                    "move_direction": move_direction,
                    "trade_direction": trade_direction,
                    "target_id": target.shelf_id,
                    "target_price": target.price,
                    "signed_trade_quote": signed,
                },
            ),
        ], None

    def _consume_probe_sources(self, probe: LiquidityProbe) -> None:
        source_ids = set(probe.source_ids)
        for shelf in self.shelves:
            if shelf.shelf_id in source_ids:
                shelf.active = False
                shelf.reserved = False

    def _release_probe(self, probe: LiquidityProbe) -> None:
        self._consume_probe_sources(probe)
        self.active_probe = None
        self.cooldown_active = True
        self.cooldown_normal_count = 0

    def _quote_response(
        self,
        bar: LiquidityResponseBar,
        probe: LiquidityProbe,
        features: dict[str, float],
    ) -> tuple[bool, float, bool]:
        denominator = max(
            probe.cumulative_attacked_remove,
            probe.cumulative_aggressive_base,
            features["depth_median"],
            1e-12,
        )
        replenishment = probe.cumulative_attacked_add / denominator
        ofi_flip = (
            probe.trade_direction * bar.ofi_qty >= features["ofi_floor"]
        )
        if not self.params.use_quote_response:
            return True, replenishment, ofi_flip
        return (
            replenishment >= self.params.replenishment_ratio and ofi_flip,
            replenishment,
            ofi_flip,
        )

    def _build_plan(
        self,
        *,
        bar: LiquidityResponseBar,
        probe: LiquidityProbe,
        features: dict[str, float],
        replenishment: float,
        ofi_flip: bool,
    ) -> LiquidityResponsePlan | None:
        direction = probe.trade_direction
        entry = bar.mid_close
        target = probe.target_price
        buffer = max(
            self.tick_size * self.params.execution_reserve_ticks,
            features["spread_median"],
            self.params.stop_range_multiple * features["range_median"],
        )
        stop = (
            probe.raid_low - buffer
            if direction > 0
            else probe.raid_high + buffer
        )
        valid = stop < entry < target if direction > 0 else target < entry < stop
        if not valid:
            self.counters["PLAN_INVALID_GEOMETRY"] += 1
            return None
        impact = max(
            self.tick_size * self.params.execution_reserve_ticks,
            bar.spread / 2.0,
            self.params.impact_range_fraction * features["range_median"],
            self.params.current_range_impact_fraction * bar.mid_range,
        )
        fee = self.params.taker_fee
        loss = abs(entry - stop) + fee * (entry + stop) + 2.0 * impact
        gross_reward = direction * (target - entry)
        net_reward = gross_reward - fee * (entry + target) - 2.0 * impact
        rr = net_reward / loss if loss > 0.0 else float("-inf")
        if net_reward <= 0.0 or rr < self.params.min_net_rr:
            self.counters["COST_ADJUSTED_RR_REJECTED"] += 1
            return None
        return LiquidityResponsePlan(
            scenario_id=probe.scenario_id,
            scenario="LIQUIDITY_REPLENISHMENT_FAILED_AUCTION_REVERSAL",
            direction=direction,
            observed_ns=bar.ts_ns,
            entry_estimate=entry,
            stop_price=stop,
            target_price=target,
            source_pool_id="|".join(probe.source_ids),
            target_pool_id=probe.target_id,
            expected_entry_impact=impact,
            expected_stop_impact=impact,
            cost_adjusted_net_rr=rr,
            details={
                "source_price": probe.source_price,
                "source_zone": probe.source_zone,
                "raid_high": probe.raid_high,
                "raid_low": probe.raid_low,
                "approach_low": probe.approach_low,
                "approach_high": probe.approach_high,
                "replenishment_ratio": replenishment,
                "ofi_flip": ofi_flip,
                "use_quote_response": self.params.use_quote_response,
                "signed_trade_quote": bar.signed_trade_quote,
                "ofi_qty": bar.ofi_qty,
                "atr": features["range_median"],
                "causal_liquidity_notional": features[
                    "causal_liquidity_notional"
                ],
                "base_impact_per_side": impact,
            },
        )

    def _process_probe(
        self,
        bar: LiquidityResponseBar,
        features: dict[str, float],
    ) -> tuple[list[LiquidityResponseTransition], LiquidityResponsePlan | None]:
        probe = self.active_probe
        assert probe is not None
        probe.raid_high = max(probe.raid_high, bar.mid_high)
        probe.raid_low = min(probe.raid_low, bar.mid_low)
        if probe.move_direction > 0:
            probe.cumulative_attacked_add += max(0.0, bar.ask_add_qty)
            probe.cumulative_attacked_remove += max(0.0, bar.ask_remove_qty)
            probe.cumulative_aggressive_base += max(0.0, bar.taker_buy_base)
            reclaimed = bar.mid_close < probe.source_price
        else:
            probe.cumulative_attacked_add += max(0.0, bar.bid_add_qty)
            probe.cumulative_attacked_remove += max(0.0, bar.bid_remove_qty)
            probe.cumulative_aggressive_base += max(
                0.0,
                bar.trade_base_volume - bar.taker_buy_base,
            )
            reclaimed = bar.mid_close > probe.source_price
        probe.reclaim_seen = probe.reclaim_seen or reclaimed
        age = self.sequence - probe.initiated_sequence

        target_reached = (
            bar.mid_high >= probe.target_price
            if probe.trade_direction > 0
            else bar.mid_low <= probe.target_price
        )
        if target_reached:
            event = self._transition(
                scenario_id=probe.scenario_id,
                bar=bar,
                event_type="SCENARIO_EXPIRED",
                previous_state="FAILED_AUCTION_WAIT",
                next_state="TARGET_REACHED_WITHOUT_ENTRY",
                reason_code="PREEXISTING_TARGET_REACHED_BEFORE_CONFIRMATION",
                reference_price=bar.mid_close,
                details={"age_bars": age, "target_price": probe.target_price},
            )
            self.counters["TARGET_REACHED_BEFORE_CONFIRMATION"] += 1
            self._release_probe(probe)
            return [event], None

        trade_flow_reversed = (
            probe.trade_direction * bar.signed_trade_quote
            >= features["confirmation_flow_floor"]
        )
        structure_shift = (
            bar.mid_close > probe.approach_high
            if probe.trade_direction > 0
            else bar.mid_close < probe.approach_low
        )
        quote_ok, replenishment, ofi_flip = self._quote_response(
            bar,
            probe,
            features,
        )
        confirmed = (
            probe.reclaim_seen
            and trade_flow_reversed
            and structure_shift
            and quote_ok
        )
        if confirmed:
            plan = self._build_plan(
                bar=bar,
                probe=probe,
                features=features,
                replenishment=replenishment,
                ofi_flip=ofi_flip,
            )
            if plan is None:
                event = self._transition(
                    scenario_id=probe.scenario_id,
                    bar=bar,
                    event_type="SCENARIO_INVALIDATED",
                    previous_state="FAILED_AUCTION_WAIT",
                    next_state="NO_EXECUTABLE_PLAN",
                    reason_code="OPPOSING_SHELF_NOT_COST_QUALIFIED",
                    reference_price=bar.mid_close,
                    details={
                        "age_bars": age,
                        "target_price": probe.target_price,
                        "replenishment_ratio": replenishment,
                        "ofi_flip": ofi_flip,
                    },
                )
                self._release_probe(probe)
                return [event], None
            event = self._transition(
                scenario_id=probe.scenario_id,
                bar=bar,
                event_type="LIQUIDITY_RESPONSE_REVERSAL_CONFIRMED",
                previous_state="FAILED_AUCTION_WAIT",
                next_state="ENTRY_READY",
                reason_code="RECLAIM_FLOW_REVERSAL_STRUCTURE_SHIFT_AND_REPLENISHMENT",
                reference_price=bar.mid_close,
                details={
                    "age_bars": age,
                    "direction": plan.direction,
                    "entry": plan.entry_estimate,
                    "stop": plan.stop_price,
                    "target": plan.target_price,
                    "cost_adjusted_net_rr": plan.cost_adjusted_net_rr,
                    "replenishment_ratio": replenishment,
                    "ofi_flip": ofi_flip,
                    "use_quote_response": self.params.use_quote_response,
                },
            )
            self.counters["TRADE_PLAN_CREATED"] += 1
            self._release_probe(probe)
            return [event], plan

        outside = (
            bar.mid_close >= probe.source_price + probe.source_zone
            if probe.move_direction > 0
            else bar.mid_close <= probe.source_price - probe.source_zone
        )
        same_flow = (
            probe.move_direction * bar.signed_trade_quote
            >= features["confirmation_flow_floor"]
        )
        same_ofi = (
            not self.params.use_quote_response
            or probe.move_direction * bar.ofi_qty >= features["ofi_floor"]
        )
        if age >= 3 and outside and same_flow and same_ofi:
            event = self._transition(
                scenario_id=probe.scenario_id,
                bar=bar,
                event_type="SCENARIO_INVALIDATED",
                previous_state="FAILED_AUCTION_WAIT",
                next_state="ACCEPTED_AUCTION",
                reason_code="SHELF_ACCEPTED_WITH_PERSISTENT_SAME_SIDE_FLOW",
                reference_price=bar.mid_close,
                details={"age_bars": age},
            )
            self.counters["ACCEPTED_AUCTION_NO_REVERSAL"] += 1
            self._release_probe(probe)
            return [event], None
        if age >= self.params.probe_max_bars:
            event = self._transition(
                scenario_id=probe.scenario_id,
                bar=bar,
                event_type="SCENARIO_EXPIRED",
                previous_state="FAILED_AUCTION_WAIT",
                next_state="EXPIRED",
                reason_code="NO_COMPLETE_LIQUIDITY_RESPONSE_SEQUENCE",
                reference_price=bar.mid_close,
                details={
                    "age_bars": age,
                    "reclaim_seen": probe.reclaim_seen,
                    "trade_flow_reversed": trade_flow_reversed,
                    "structure_shift": structure_shift,
                    "replenishment_ratio": replenishment,
                    "ofi_flip": ofi_flip,
                },
            )
            self.counters["PROBE_EXPIRED"] += 1
            self._release_probe(probe)
            return [event], None
        return [], None

    def _update_cooldown(
        self,
        features: dict[str, float],
        bar: LiquidityResponseBar,
    ) -> None:
        quiet = abs(bar.signed_trade_quote) <= features["confirmation_flow_floor"]
        if quiet:
            self.cooldown_normal_count += 1
        else:
            self.cooldown_normal_count = 0
        if self.cooldown_normal_count >= 3:
            self.cooldown_active = False
            self.cooldown_normal_count = 0
            self.counters["EVENT_COOLDOWN_RELEASED"] += 1

    def _append_histories(self, bar: LiquidityResponseBar) -> None:
        self.abs_trade_quote_history.append(abs(bar.signed_trade_quote))
        self.abs_ofi_history.append(abs(bar.ofi_qty))
        self.range_history.append(max(self.tick_size, bar.mid_range))
        self.spread_history.append(
            max(self.tick_size, bar.mean_spread, bar.spread),
        )
        self.depth_history.append(max(self.tick_size, bar.top_depth))
        self.notional_history.append(max(0.0, bar.trade_quote_volume))
        self.recent_bars.append(bar)
        self.current_formation.append(bar)

    def on_bar(
        self,
        bar: LiquidityResponseBar,
    ) -> tuple[list[LiquidityResponseTransition], LiquidityResponsePlan | None]:
        self.sequence += 1
        self.counters["BAR_COMPLETED"] += 1
        self._maybe_roll_formation(bar)
        features = self._feature_snapshot()
        events: list[LiquidityResponseTransition] = []
        plan: LiquidityResponsePlan | None = None
        if features is not None:
            if self.active_probe is not None:
                events, plan = self._process_probe(bar, features)
            elif self.cooldown_active:
                self._update_cooldown(features, bar)
            else:
                events, plan = self._detect_interaction(bar, features)
        self._append_histories(bar)
        return events, plan


__all__ = [
    "LiquidityResponseStateMachine",
    "_quantile",
]
