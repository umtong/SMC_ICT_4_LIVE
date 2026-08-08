"""Candidate 33: cross-sectional idiosyncratic liquidity dislocation reversal.

The four allowed markets are observed on the same completed minute. A candidate
is armed only when it is the unique largest absolute ATR-normalized mover,
trades through a previously known five-minute internal pivot with aligned
aggressor flow and volume, while a majority of the other three markets does not
share the move's sign. This is an idiosyncratic liquidity dislocation, not a
market-wide directional signal.

A later completed bar must reclaim the pivot with opposite flow, body and close
location, and at least two peers must then align with the reversal direction.
The frozen pre-event close is the equilibrium target; the observed dislocation
extreme plus the existing ATR buffer is invalidation. Costs, minimum costed R,
exact 3% current-NAV risk and one global portfolio slot are unchanged.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Iterable

from logic import BarObs, CausalAuctionEngine, Direction, Scenario, Side, TradePlan
from semantic_execution import MARKET_ENTRY_SENTINEL_NS

SCENARIO_KIND = "CROSS_SECTIONAL_IDIOSYNCRATIC_LIQUIDITY_DISLOCATION"


@dataclass(slots=True)
class CrossSectionalState:
    scenario_id: str
    symbol: str
    swept_side: Side
    direction: Direction
    pivot_candidate_ts_ns: int
    pivot_known_ts_ns: int
    pivot_level: float
    event_ts_ns: int
    event_index: int
    expiry_index: int
    pre_event_close: float
    sweep_extreme: float
    candidate_standardized_move: float
    peer_standardized_moves: dict[str, float]
    event_relative_volume: float
    event_signed_flow: float
    state: str = "WAIT_RECLAIM"
    accepted_outside_streak: int = 0


def _market_economics(
    *,
    direction: Direction,
    entry: float,
    stop: float,
    target: float,
    taker_rate: float,
    maker_rate: float,
) -> tuple[float, float, float, float]:
    if direction == Direction.LONG:
        risk = entry - stop
        gross_gain = target - entry
    else:
        risk = stop - entry
        gross_gain = entry - target
    loss = risk + entry * taker_rate + stop * taker_rate
    net_gain = gross_gain - entry * taker_rate - target * maker_rate
    net_r = net_gain / loss if loss > 0.0 else float("-inf")
    return risk, loss, net_gain, net_r


def _consumed_keys(engine: CausalAuctionEngine) -> set[tuple[str, int, int, float]]:
    keys = getattr(engine, "_candidate33_consumed_internal_keys", None)
    if keys is None:
        keys = set()
        engine._candidate33_consumed_internal_keys = keys
    return keys


def _crossed_pivot(
    engine: CausalAuctionEngine,
    bar: BarObs,
    previous_close: float,
    side: Side,
) -> tuple[int, int, float] | None:
    points = engine.internal_highs if side == Side.HIGH else engine.internal_lows
    cutoff = bar.ts_ns - engine.config.event_expiry_bars * 60_000_000_000
    consumed = _consumed_keys(engine)
    eligible: list[tuple[int, int, float]] = []
    for candidate_ts_ns, known_ts_ns, level in points:
        key = (side.value, int(candidate_ts_ns), int(known_ts_ns), round(float(level), 10))
        if key in consumed or known_ts_ns >= bar.ts_ns or known_ts_ns < cutoff:
            continue
        crossed = (
            previous_close <= level < bar.high
            if side == Side.HIGH
            else previous_close >= level > bar.low
        )
        if crossed:
            eligible.append((int(candidate_ts_ns), int(known_ts_ns), float(level)))
    if not eligible:
        return None
    return min(eligible, key=lambda item: item[2]) if side == Side.HIGH else max(eligible, key=lambda item: item[2])


def _terminal(
    engine: CausalAuctionEngine,
    state: CrossSectionalState,
    bar: BarObs,
    reason: str,
) -> None:
    engine._event(
        state.scenario_id,
        "CROSS_SECTIONAL_DISLOCATION_TERMINAL",
        state.event_ts_ns,
        bar.ts_ns,
        state.state,
        "TERMINAL",
        reason,
        state.pivot_level,
        {
            "symbol": state.symbol,
            "swept_side": state.swept_side.value,
            "direction": state.direction.value,
            "pivot_level": state.pivot_level,
            "pre_event_close": state.pre_event_close,
            "sweep_extreme": state.sweep_extreme,
            "candidate_standardized_move": state.candidate_standardized_move,
            "peer_standardized_moves": state.peer_standardized_moves,
        },
    )
    engine.skips[reason] += 1
    engine._candidate33_cross_sectional_state = None


def _build_plan(
    engine: CausalAuctionEngine,
    state: CrossSectionalState,
    bar: BarObs,
    peer_moves: dict[str, float],
    atr: float,
) -> TradePlan | None:
    stop = (
        state.sweep_extreme - engine.config.stop_buffer_atr * atr
        if state.direction == Direction.LONG
        else state.sweep_extreme + engine.config.stop_buffer_atr * atr
    )
    entry = bar.close
    target = state.pre_event_close
    risk, loss, net_gain, net_r = _market_economics(
        direction=state.direction,
        entry=entry,
        stop=stop,
        target=target,
        taker_rate=engine.config.effective_taker_rate,
        maker_rate=engine.config.effective_maker_rate,
    )
    causal_order = (
        stop < entry < target
        if state.direction == Direction.LONG
        else target < entry < stop
    )
    if (
        not causal_order
        or risk <= 0.0
        or risk / atr < engine.config.min_stop_atr
        or net_gain <= 0.0
        or net_r < engine.config.min_net_r
    ):
        _terminal(engine, state, bar, "CROSS_SECTIONAL_DISLOCATION_INSUFFICIENT_COSTED_R")
        return None

    plan = TradePlan(
        scenario_id=state.scenario_id,
        scenario=Scenario.FAR,
        direction=state.direction,
        observed_ts_ns=bar.ts_ns,
        expected_entry=entry,
        stop_price=stop,
        target_price=target,
        atr=atr,
        loss_per_unit=loss,
        gain_per_unit=net_gain,
        net_r=net_r,
        reason_code="CROSS_SECTIONAL_DISLOCATION_PIVOT_RECLAIM_MARKET",
        expire_ts_ns=MARKET_ENTRY_SENTINEL_NS,
        entry_order_type="MARKET",
        entry_post_only=False,
        details={
            "scenario_kind": SCENARIO_KIND,
            "sweep_ts_ns": state.event_ts_ns,
            "pivot_candidate_ts_ns": state.pivot_candidate_ts_ns,
            "pivot_known_ts_ns": state.pivot_known_ts_ns,
            "pivot_level": state.pivot_level,
            "swept_side": state.swept_side.value,
            "pre_event_equilibrium": state.pre_event_close,
            "sweep_extreme": state.sweep_extreme,
            "candidate_standardized_move": state.candidate_standardized_move,
            "event_peer_standardized_moves": state.peer_standardized_moves,
            "reclaim_peer_standardized_moves": peer_moves,
            "event_relative_volume": state.event_relative_volume,
            "event_signed_flow": state.event_signed_flow,
            "entry_model": "IDIOSYNCRATIC_PIVOT_RECLAIM_MARKET",
            "stop_model": "DISLOCATION_EXTREME_INVALIDATION",
            "target_model": "PRE_EVENT_CLOSE_EQUILIBRIUM",
            "entry_cost_assumption": "TAKER",
            "market_parent_sentinel_ns": MARKET_ENTRY_SENTINEL_NS,
        },
    )
    engine._event(
        state.scenario_id,
        "TRADE_PLAN_CONFIRMED",
        state.event_ts_ns,
        bar.ts_ns,
        "WAIT_RECLAIM",
        "PLAN_CONFIRMED",
        plan.reason_code,
        entry,
        {
            "scenario": Scenario.FAR.value,
            "scenario_kind": SCENARIO_KIND,
            "direction": state.direction.value,
            "entry_order_type": "MARKET",
            "entry_post_only": False,
            "stop": stop,
            "target": target,
            "net_r": net_r,
        },
    )
    state.state = "PLAN_CONFIRMED"
    return plan


class CrossSectionalResidualDetector:
    def __init__(self, symbols: Iterable[str]) -> None:
        self.symbols = tuple(symbols)
        self.previous_close: dict[str, float] = {}

    @staticmethod
    def _standardized_move(
        engine: CausalAuctionEngine,
        bar: BarObs,
        previous_close: float,
    ) -> float | None:
        atr = engine.atr
        if atr is None or atr <= 0.0:
            return None
        return (bar.close - previous_close) / atr

    def _step_state(
        self,
        *,
        symbol: str,
        engine: CausalAuctionEngine,
        bar: BarObs,
        peer_moves: dict[str, float],
    ) -> TradePlan | None:
        state: CrossSectionalState | None = getattr(
            engine,
            "_candidate33_cross_sectional_state",
            None,
        )
        if state is None:
            return None
        atr = engine.atr
        if atr is None or atr <= 0.0:
            return None
        if engine._index > state.expiry_index:
            _terminal(engine, state, bar, "CROSS_SECTIONAL_DISLOCATION_EXPIRED")
            return None
        target_reached = (
            bar.low <= state.pre_event_close
            if state.direction == Direction.SHORT
            else bar.high >= state.pre_event_close
        )
        if target_reached:
            _terminal(engine, state, bar, "CROSS_SECTIONAL_EQUILIBRIUM_REACHED_BEFORE_ENTRY")
            return None

        if state.swept_side == Side.HIGH:
            state.sweep_extreme = max(state.sweep_extreme, bar.high)
            outside = bar.close >= state.pivot_level + engine.config.acceptance_close_atr * atr
            reclaimed = bar.close <= state.pivot_level - engine.config.rejection_reclaim_atr * atr
        else:
            state.sweep_extreme = min(state.sweep_extreme, bar.low)
            outside = bar.close <= state.pivot_level - engine.config.acceptance_close_atr * atr
            reclaimed = bar.close >= state.pivot_level + engine.config.rejection_reclaim_atr * atr
        state.accepted_outside_streak = state.accepted_outside_streak + 1 if outside else 0
        if state.accepted_outside_streak >= engine.config.acceptance_min_closes:
            _terminal(engine, state, bar, "CROSS_SECTIONAL_DISLOCATION_ACCEPTED")
            return None
        if engine._index <= state.event_index:
            return None

        sign = 1.0 if state.direction == Direction.LONG else -1.0
        peer_reversal_count = sum(sign * move > 0.0 for move in peer_moves.values())
        if state.direction == Direction.SHORT:
            flow = bar.signed_flow <= -engine.config.displacement_flow_min
            location = bar.close_location <= 1.0 - engine.config.acceptance_close_location
        else:
            flow = bar.signed_flow >= engine.config.displacement_flow_min
            location = bar.close_location >= engine.config.acceptance_close_location
        body = bar.body >= engine.config.displacement_body_atr * atr
        if not (reclaimed and flow and location and body and peer_reversal_count >= 2):
            return None
        engine._event(
            state.scenario_id,
            "CROSS_SECTIONAL_DISLOCATION_RECLAIMED",
            state.event_ts_ns,
            bar.ts_ns,
            "WAIT_RECLAIM",
            "PLAN_PENDING_COST_GATE",
            "PIVOT_RECLAIM_WITH_PEER_REVERSAL_MAJORITY",
            state.pivot_level,
            {
                "peer_reversal_count": peer_reversal_count,
                "peer_standardized_moves": peer_moves,
                "reclaim_signed_flow": bar.signed_flow,
                "reclaim_body_atr": bar.body / atr,
                "reclaim_close_location": bar.close_location,
            },
        )
        return _build_plan(engine, state, bar, peer_moves, atr)

    def _detect(
        self,
        *,
        ts_ns: int,
        observations: dict[str, BarObs],
        engines: dict[str, CausalAuctionEngine],
        moves: dict[str, float],
        blocked_symbols: set[str],
    ) -> None:
        if len(moves) != len(self.symbols):
            return
        absolute_order = sorted(
            moves,
            key=lambda symbol: (-abs(moves[symbol]), symbol),
        )
        if len(absolute_order) < 2:
            return
        symbol = absolute_order[0]
        if symbol in blocked_symbols:
            return
        if abs(moves[symbol]) <= abs(moves[absolute_order[1]]):
            return
        engine = engines[symbol]
        if (
            engine.active_trade_id is not None
            or getattr(engine, "_candidate16_failed_far_state", None) is not None
            or getattr(engine, "_candidate33_cross_sectional_state", None) is not None
        ):
            return
        bar = observations[symbol]
        previous_close = self.previous_close.get(symbol)
        atr = engine.atr
        median_volume = engine.median_volume
        if previous_close is None or atr is None or atr <= 0.0 or not median_volume:
            return
        move = moves[symbol]
        side = Side.HIGH if move > 0.0 else Side.LOW
        direction = Direction.SHORT if side == Side.HIGH else Direction.LONG
        peers = {key: value for key, value in moves.items() if key != symbol}
        same_sign = sum(move * peer > 0.0 for peer in peers.values())
        if same_sign > 1:
            return
        if abs(move) < engine.config.displacement_body_atr:
            return
        relative_volume = bar.volume / median_volume
        if relative_volume < engine.config.min_relative_volume:
            return
        flow_ok = (
            bar.signed_flow >= engine.config.absorption_flow_min
            if side == Side.HIGH
            else bar.signed_flow <= -engine.config.absorption_flow_min
        )
        if not flow_ok:
            return
        pivot = _crossed_pivot(engine, bar, previous_close, side)
        if pivot is None:
            return
        candidate_ts_ns, known_ts_ns, level = pivot
        key = (side.value, candidate_ts_ns, known_ts_ns, round(level, 10))
        _consumed_keys(engine).add(key)
        scenario_id = f"{engine.instrument_id}-CSD-{known_ts_ns}-{ts_ns}-{direction.value}"
        state = CrossSectionalState(
            scenario_id=scenario_id,
            symbol=symbol,
            swept_side=side,
            direction=direction,
            pivot_candidate_ts_ns=candidate_ts_ns,
            pivot_known_ts_ns=known_ts_ns,
            pivot_level=level,
            event_ts_ns=ts_ns,
            event_index=engine._index,
            expiry_index=engine._index + engine.config.retrace_expiry_bars,
            pre_event_close=previous_close,
            sweep_extreme=bar.high if side == Side.HIGH else bar.low,
            candidate_standardized_move=move,
            peer_standardized_moves=peers,
            event_relative_volume=relative_volume,
            event_signed_flow=bar.signed_flow,
        )
        engine._candidate33_cross_sectional_state = state
        engine._event(
            scenario_id,
            "CROSS_SECTIONAL_DISLOCATION_ARMED",
            candidate_ts_ns,
            ts_ns,
            "INTERNAL_POOL_ARMED",
            "WAIT_RECLAIM",
            "UNIQUE_ABSOLUTE_ATR_MOVE_WITH_PEER_SIGN_DISSENT",
            level,
            {
                "symbol": symbol,
                "swept_side": side.value,
                "direction": direction.value,
                "pivot_known_ts_ns": known_ts_ns,
                "pre_event_close": previous_close,
                "candidate_standardized_move": move,
                "peer_standardized_moves": peers,
                "same_sign_peer_count": same_sign,
                "relative_volume": relative_volume,
                "signed_flow": bar.signed_flow,
            },
        )

    def on_batch(
        self,
        ts_ns: int,
        observations: dict[str, BarObs],
        engines: dict[str, CausalAuctionEngine],
        blocked_symbols: set[str] | None = None,
    ) -> list[tuple[str, TradePlan]]:
        blocked = blocked_symbols or set()
        moves: dict[str, float] = {}
        for symbol in self.symbols:
            previous = self.previous_close.get(symbol)
            if previous is None:
                continue
            move = self._standardized_move(engines[symbol], observations[symbol], previous)
            if move is not None:
                moves[symbol] = move

        plans: list[tuple[str, TradePlan]] = []
        if len(moves) == len(self.symbols):
            for symbol in self.symbols:
                if symbol in blocked:
                    continue
                engine = engines[symbol]
                peers = {key: value for key, value in moves.items() if key != symbol}
                plan = self._step_state(
                    symbol=symbol,
                    engine=engine,
                    bar=observations[symbol],
                    peer_moves=peers,
                )
                if plan is not None:
                    plans.append((symbol, plan))
            self._detect(
                ts_ns=ts_ns,
                observations=observations,
                engines=engines,
                moves=moves,
                blocked_symbols=blocked | {symbol for symbol, _ in plans},
            )

        for symbol in self.symbols:
            self.previous_close[symbol] = observations[symbol].close
        return plans


BASE_MARK_SUBMITTED: Callable[..., None] | None = None
BASE_MARK_REJECTED: Callable[..., None] | None = None
BASE_MARK_TRADE_TERMINAL: Callable[..., None] | None = None


def candidate33_mark_submitted(
    self: CausalAuctionEngine,
    plan: TradePlan,
    quantity: Any,
    details: dict[str, Any] | None = None,
) -> None:
    if BASE_MARK_SUBMITTED is None:
        raise RuntimeError("Candidate 33 is not installed")
    state: CrossSectionalState | None = getattr(
        self,
        "_candidate33_cross_sectional_state",
        None,
    )
    if plan.details.get("scenario_kind") == SCENARIO_KIND:
        if state is None or state.scenario_id != plan.scenario_id or state.state != "PLAN_CONFIRMED":
            raise RuntimeError("submitted cross-sectional plan does not match state")
        if self.active_trade_id is not None:
            raise RuntimeError("global candidate slot already occupied")
        if self.active is not None and self.bars:
            self._terminal(
                self.active,
                self.bars[-1],
                "CROSS_SECTIONAL_PLAN_ALLOCATED_BEFORE_EXTERNAL_PLAN",
            )
        self._event(
            plan.scenario_id,
            "ENTRY_ORDER_LIST_SUBMITTED",
            plan.observed_ts_ns,
            plan.observed_ts_ns,
            "PLAN_CONFIRMED",
            "PENDING_ENTRY",
            plan.reason_code,
            plan.expected_entry,
            {
                "scenario": plan.scenario.value,
                "scenario_kind": SCENARIO_KIND,
                "direction": plan.direction.value,
                "quantity": str(quantity),
                "net_r": plan.net_r,
                **(details or {}),
            },
        )
        self.active_trade_id = plan.scenario_id
        self.active_trade_state = "PENDING_ENTRY"
        self._candidate33_cross_sectional_state = None
        self._candidate16_trade_kind = "OTHER"
        self._candidate16_submitted_far = None
        return
    if state is not None and self.bars:
        _terminal(self, state, self.bars[-1], "COMPETING_EXTERNAL_PLAN_ALLOCATED")
    BASE_MARK_SUBMITTED(self, plan, quantity, details)


def candidate33_mark_rejected(
    self: CausalAuctionEngine,
    plan: TradePlan,
    ts_ns: int,
    reason: str,
    details: dict[str, Any] | None = None,
) -> None:
    if BASE_MARK_REJECTED is None:
        raise RuntimeError("Candidate 33 is not installed")
    if plan.details.get("scenario_kind") == SCENARIO_KIND:
        state: CrossSectionalState | None = getattr(
            self,
            "_candidate33_cross_sectional_state",
            None,
        )
        if state is None or state.scenario_id != plan.scenario_id:
            return
        self._event(
            plan.scenario_id,
            "ENTRY_PLAN_REJECTED",
            plan.observed_ts_ns,
            ts_ns,
            state.state,
            "TERMINAL",
            reason,
            plan.expected_entry,
            details or {},
        )
        self.skips[reason] += 1
        self._candidate33_cross_sectional_state = None
        return
    BASE_MARK_REJECTED(self, plan, ts_ns, reason, details)


def candidate33_mark_trade_terminal(
    self: CausalAuctionEngine,
    ts_ns: int,
    reason: str,
    details: dict[str, Any] | None = None,
) -> None:
    if BASE_MARK_TRADE_TERMINAL is None:
        raise RuntimeError("Candidate 33 is not installed")
    BASE_MARK_TRADE_TERMINAL(self, ts_ns, reason, details)


def install() -> None:
    global BASE_MARK_SUBMITTED, BASE_MARK_REJECTED, BASE_MARK_TRADE_TERMINAL
    if CausalAuctionEngine.mark_submitted is candidate33_mark_submitted:
        return
    BASE_MARK_SUBMITTED = CausalAuctionEngine.mark_submitted
    BASE_MARK_REJECTED = CausalAuctionEngine.mark_rejected
    BASE_MARK_TRADE_TERMINAL = CausalAuctionEngine.mark_trade_terminal
    CausalAuctionEngine.mark_submitted = candidate33_mark_submitted
    CausalAuctionEngine.mark_rejected = candidate33_mark_rejected
    CausalAuctionEngine.mark_trade_terminal = candidate33_mark_trade_terminal
