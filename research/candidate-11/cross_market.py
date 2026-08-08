"""Dynamic cross-market price-discovery and follower convergence.

The detector consumes synchronized completed one-minute observations for the four
allowed perpetual markets.  It never sizes positions, simulates execution, or
calculates PnL.  A trade requires:

1. a unique residual price-discovery leader after removing the contemporaneous
   peer factor with a beta estimated only from bars preceding the shock;
2. aligned leader aggressor flow and at least two peers moving with the shock;
3. a follower that is still behind its frozen beta-implied fair value;
4. the follower's own aggressor-flow flip, local structure break, and positive
   residual catch-up before an order is emitted;
5. a structural stop and a still-live, frozen fair-value target whose post-cost
   R is at least the project's existing minimum.

No symbol is hard-coded as leader.  All baselines exclude the bars they score.
"""
from __future__ import annotations

from collections import Counter, deque
from dataclasses import dataclass
from math import exp, isfinite, log, sqrt
from statistics import median
from typing import Literal, Mapping

MINUTE_NS = 60_000_000_000


@dataclass(frozen=True, slots=True)
class CrossObservation:
    ts_ns: int
    open: float
    high: float
    low: float
    close: float
    volume: float
    quote_volume: float
    taker_buy_volume: float

    @property
    def signed_quote_flow(self) -> float:
        if self.volume <= 0 or self.quote_volume <= 0:
            return 0.0
        imbalance = 2.0 * self.taker_buy_volume / self.volume - 1.0
        return self.quote_volume * max(-1.0, min(1.0, imbalance))


@dataclass(frozen=True, slots=True)
class CrossMarketPlan:
    symbol: str
    leader: str
    scenario_id: str
    direction: Literal["LONG", "SHORT"]
    observed_ts_ns: int
    expected_entry: float
    stop_price: float
    target_price: float
    loss_per_unit: float
    net_r: float
    expire_ts_ns: int
    signal_score: float
    details: dict[str, object]


@dataclass(slots=True)
class _Shock:
    shock_id: str
    leader: str
    direction: Literal["LONG", "SHORT"]
    detected_ts_ns: int
    base_ts_ns: int
    base_prices: dict[str, float]
    betas: dict[str, float]
    residual_rms: dict[str, float]
    flow_rms: dict[str, float]
    leader_initial_move: float
    leader_peak_move: float
    leader_score: float
    age: int = 0


class CausalLeaderFollowerEngine:
    """Detect dynamic price discovery, then wait for a follower's own catch-up."""

    def __init__(
        self,
        symbols: tuple[str, ...],
        *,
        effective_maker_rate: float = 0.0004,
        effective_taker_rate: float = 0.0008,
        minimum_net_r: float = 1.25,
    ) -> None:
        if len(symbols) < 3 or len(set(symbols)) != len(symbols):
            raise ValueError("cross-market logic requires at least three unique symbols")
        self.symbols = tuple(symbols)
        self.effective_maker_rate = float(effective_maker_rate)
        self.effective_taker_rate = float(effective_taker_rate)
        self.minimum_net_r = max(1.25, float(minimum_net_r))
        self.history = {symbol: deque(maxlen=480) for symbol in self.symbols}
        self.active: _Shock | None = None
        self.sequence = 0
        self.cooldown_until_ns = -1
        self.pending_plan_id: str | None = None
        self.position_open = False
        self.events: list[dict[str, object]] = []
        self.skips: Counter[str] = Counter()
        self._last_ts_ns = -1

    def _record(self, event_type: str, ts_ns: int, **details: object) -> None:
        self.events.append({
            "type": event_type,
            "observed_ts_ns": int(ts_ns),
            **details,
        })
        if len(self.events) > 20_000:
            self.events = self.events[-20_000:]

    def _validate_batch(
        self,
        ts_ns: int,
        observations: Mapping[str, CrossObservation],
    ) -> None:
        if ts_ns <= self._last_ts_ns:
            raise ValueError("cross-market batches must be strictly increasing")
        if set(observations) != set(self.symbols):
            missing = sorted(set(self.symbols) - set(observations))
            extra = sorted(set(observations) - set(self.symbols))
            raise ValueError(f"incomplete synchronized batch: missing={missing} extra={extra}")
        for symbol, bar in observations.items():
            if bar.ts_ns != ts_ns:
                raise ValueError(f"asynchronous observation for {symbol}")
            numeric = (
                bar.open, bar.high, bar.low, bar.close, bar.volume,
                bar.quote_volume, bar.taker_buy_volume,
            )
            if not all(isfinite(value) for value in numeric):
                raise ValueError(f"non-finite observation for {symbol}")
            if min(bar.open, bar.high, bar.low, bar.close) <= 0:
                raise ValueError(f"non-positive price for {symbol}")
            if bar.high < max(bar.open, bar.close) or bar.low > min(bar.open, bar.close):
                raise ValueError(f"invalid OHLC ordering for {symbol}")
            if min(bar.volume, bar.quote_volume, bar.taker_buy_volume) < 0:
                raise ValueError(f"negative flow for {symbol}")
            if bar.taker_buy_volume > bar.volume + max(1e-9, 1e-9 * bar.volume):
                raise ValueError(f"taker-buy volume exceeds total volume for {symbol}")

    def _returns(self, symbol: str) -> list[float]:
        bars = list(self.history[symbol])
        return [log(curr.close / prev.close) for prev, curr in zip(bars, bars[1:])]

    def _factor_returns(self, excluded_symbol: str) -> list[float]:
        series = {symbol: self._returns(symbol) for symbol in self.symbols}
        length = min(len(values) for values in series.values())
        result: list[float] = []
        for index in range(length):
            result.append(median(
                series[symbol][-length + index]
                for symbol in self.symbols
                if symbol != excluded_symbol
            ))
        return result

    @staticmethod
    def _beta_and_rms(y: list[float], x: list[float]) -> tuple[float, float] | None:
        if len(y) != len(x) or len(y) < 60:
            return None
        mean_y = sum(y) / len(y)
        mean_x = sum(x) / len(x)
        variance_x = sum((value - mean_x) ** 2 for value in x)
        if variance_x <= 1e-18:
            return None
        covariance = sum((left - mean_y) * (right - mean_x) for left, right in zip(y, x, strict=True))
        raw_beta = covariance / variance_x
        # Only positive common-factor relationships can define catch-up.  A
        # negative estimate is evidence of idiosyncratic behavior, not a follower.
        beta = max(0.0, raw_beta)
        residuals = [left - beta * right for left, right in zip(y, x, strict=True)]
        rms = sqrt(sum(value * value for value in residuals) / len(residuals))
        if rms <= 1e-12:
            return None
        return beta, rms

    def _flow_rms(self, symbol: str, before_last: int, lookback: int = 120) -> float | None:
        bars = list(self.history[symbol])
        end = len(bars) - before_last
        start = end - lookback
        if start < 0:
            return None
        values = [bar.signed_quote_flow for bar in bars[start:end]]
        if len(values) != lookback:
            return None
        rms = sqrt(sum(value * value for value in values) / len(values))
        return rms if rms > 1e-12 else None

    def _atr(self, symbol: str, lookback: int = 30) -> float | None:
        bars = list(self.history[symbol])
        # Current confirmation bar is excluded from its structural scale.
        sample = bars[-(lookback + 2):-1]
        if len(sample) != lookback + 1:
            return None
        values: list[float] = []
        previous_close = sample[0].close
        for bar in sample[1:]:
            values.append(max(
                bar.high - bar.low,
                abs(bar.high - previous_close),
                abs(bar.low - previous_close),
            ))
            previous_close = bar.close
        atr = sum(values) / len(values)
        return atr if atr > 0 else None

    def _shock_statistics(self) -> dict[str, dict[str, float]] | None:
        if any(len(self.history[symbol]) < 125 for symbol in self.symbols):
            return None
        result: dict[str, dict[str, float]] = {}
        shock_bars = 3
        baseline = 120
        for symbol in self.symbols:
            returns = self._returns(symbol)
            factors = self._factor_returns(symbol)
            y = returns[-(baseline + shock_bars):-shock_bars]
            x = factors[-(baseline + shock_bars):-shock_bars]
            model = self._beta_and_rms(y, x)
            flow_rms = self._flow_rms(symbol, before_last=shock_bars, lookback=baseline)
            if model is None or flow_rms is None:
                return None
            beta, residual_rms = model
            shock_residual = sum(
                left - beta * right
                for left, right in zip(returns[-shock_bars:], factors[-shock_bars:], strict=True)
            )
            flow = sum(bar.signed_quote_flow for bar in list(self.history[symbol])[-shock_bars:])
            result[symbol] = {
                "beta": beta,
                "residual_rms": residual_rms,
                "flow_rms": flow_rms,
                "residual_move": shock_residual,
                "flow": flow,
                "factor_move": sum(factors[-shock_bars:]),
            }
        return result

    def _detect_shock(self, ts_ns: int) -> None:
        if self.active is not None or ts_ns < self.cooldown_until_ns:
            return
        if self.pending_plan_id is not None or self.position_open:
            return
        statistics = self._shock_statistics()
        if statistics is None:
            return
        scored: list[tuple[float, str, Literal["LONG", "SHORT"], float, float]] = []
        for symbol, values in statistics.items():
            for direction, sign in (("LONG", 1.0), ("SHORT", -1.0)):
                move_z = sign * values["residual_move"] / max(values["residual_rms"] * sqrt(3), 1e-12)
                flow_z = sign * values["flow"] / max(values["flow_rms"] * sqrt(3), 1e-12)
                score = min(move_z, flow_z)
                scored.append((score, symbol, direction, move_z, flow_z))
        scored.sort(key=lambda value: (-value[0], value[1], value[2]))
        best = scored[0]
        second = scored[1]
        if best[0] < 1.25 or best[0] - second[0] < 0.20:
            return
        _, leader, direction, move_z, flow_z = best
        sign = 1.0 if direction == "LONG" else -1.0
        bars_by_symbol = {symbol: list(self.history[symbol]) for symbol in self.symbols}
        base_prices = {symbol: bars[-4].close for symbol, bars in bars_by_symbol.items()}
        peer_moves = [
            sign * log(bars_by_symbol[symbol][-1].close / base_prices[symbol])
            for symbol in self.symbols
            if symbol != leader
        ]
        if sum(value > 0.0 for value in peer_moves) < 2 or median(peer_moves) <= 0.0:
            self.skips["LEADER_SHOCK_WITHOUT_BROAD_PEER_SUPPORT"] += 1
            return
        leader_move = sign * log(bars_by_symbol[leader][-1].close / base_prices[leader])
        if leader_move <= 0.0:
            return
        self.sequence += 1
        shock_id = f"{leader}-{direction}-{ts_ns}-{self.sequence:07d}"
        self.active = _Shock(
            shock_id=shock_id,
            leader=leader,
            direction=direction,
            detected_ts_ns=ts_ns,
            base_ts_ns=bars_by_symbol[leader][-4].ts_ns,
            base_prices=base_prices,
            betas={symbol: values["beta"] for symbol, values in statistics.items()},
            residual_rms={symbol: values["residual_rms"] for symbol, values in statistics.items()},
            flow_rms={symbol: values["flow_rms"] for symbol, values in statistics.items()},
            leader_initial_move=leader_move,
            leader_peak_move=leader_move,
            leader_score=best[0],
        )
        self._record(
            "CROSS_MARKET_PRICE_DISCOVERY_SHOCK",
            ts_ns,
            shock_id=shock_id,
            leader=leader,
            direction=direction,
            residual_move_z=move_z,
            flow_z=flow_z,
            leader_score=best[0],
            second_score=second[0],
            peer_signed_moves=peer_moves,
            betas=self.active.betas,
        )

    def _costed_plan(
        self,
        *,
        symbol: str,
        bar: CrossObservation,
        shock: _Shock,
        entry: float,
        stop: float,
        target: float,
        atr: float,
        signal_score: float,
        details: dict[str, object],
    ) -> CrossMarketPlan | None:
        direction = shock.direction
        valid = stop < entry < target if direction == "LONG" else target < entry < stop
        if not valid:
            self.skips["CROSS_MARKET_NON_CAUSAL_PRICE_ORDER"] += 1
            return None
        stop_distance = abs(entry - stop)
        if not 0.08 * atr <= stop_distance <= 1.50 * atr:
            self.skips["CROSS_MARKET_STOP_GEOMETRY"] += 1
            return None
        target_live = target > bar.high + 0.02 * atr if direction == "LONG" else target < bar.low - 0.02 * atr
        if not target_live:
            self.skips["CROSS_MARKET_TARGET_REACHED_BEFORE_CONFIRMATION"] += 1
            return None
        loss_per_unit = (
            stop_distance
            + entry * self.effective_maker_rate
            + stop * self.effective_taker_rate
        )
        reward = (
            abs(target - entry)
            - entry * self.effective_maker_rate
            - target * self.effective_maker_rate
        )
        net_r = reward / max(loss_per_unit, 1e-12)
        if reward <= 0.0 or net_r < self.minimum_net_r:
            self.skips["CROSS_MARKET_INSUFFICIENT_COSTED_R"] += 1
            return None
        scenario_id = f"{symbol}-CMLF-{bar.ts_ns}-{self.sequence:07d}"
        return CrossMarketPlan(
            symbol=symbol,
            leader=shock.leader,
            scenario_id=scenario_id,
            direction=direction,
            observed_ts_ns=bar.ts_ns,
            expected_entry=entry,
            stop_price=stop,
            target_price=target,
            loss_per_unit=loss_per_unit,
            net_r=net_r,
            expire_ts_ns=bar.ts_ns + 8 * MINUTE_NS,
            signal_score=signal_score,
            details=details,
        )

    def _evaluate_followers(self, ts_ns: int) -> list[CrossMarketPlan]:
        shock = self.active
        if shock is None:
            return []
        shock.age += 1
        sign = 1.0 if shock.direction == "LONG" else -1.0
        current = {symbol: self.history[symbol][-1] for symbol in self.symbols}
        leader_move = sign * log(current[shock.leader].close / shock.base_prices[shock.leader])
        shock.leader_peak_move = max(shock.leader_peak_move, leader_move)
        if (
            leader_move <= 0.0
            or leader_move < 0.40 * max(shock.leader_peak_move, shock.leader_initial_move)
        ):
            self.skips["PRICE_DISCOVERY_LEADER_REVERSED"] += 1
            self._record(
                "CROSS_MARKET_SHOCK_TERMINATED",
                ts_ns,
                shock_id=shock.shock_id,
                reason="PRICE_DISCOVERY_LEADER_REVERSED",
            )
            self.active = None
            return []
        if shock.age > 8:
            self.skips["FOLLOWER_CONFIRMATION_EXPIRED"] += 1
            self._record(
                "CROSS_MARKET_SHOCK_TERMINATED",
                ts_ns,
                shock_id=shock.shock_id,
                reason="FOLLOWER_CONFIRMATION_EXPIRED",
            )
            self.active = None
            return []

        plans: list[CrossMarketPlan] = []
        for symbol in self.symbols:
            if symbol == shock.leader:
                continue
            bars = list(self.history[symbol])
            if len(bars) < 35:
                continue
            atr = self._atr(symbol)
            if atr is None:
                continue
            peer_moves = [
                log(current[peer].close / shock.base_prices[peer])
                for peer in self.symbols
                if peer != symbol
            ]
            factor_move = median(peer_moves)
            signed_factor_move = sign * factor_move
            if signed_factor_move <= 0.0:
                continue
            beta = shock.betas[symbol]
            if beta <= 0.0:
                self.skips["FOLLOWER_NONPOSITIVE_CAUSAL_BETA"] += 1
                continue
            fair_log = log(shock.base_prices[symbol]) + beta * factor_move
            fair_price = exp(fair_log)
            lag_log = sign * (fair_log - log(bars[-1].close))
            if lag_log <= 0.15 * atr / bars[-1].close:
                continue
            latest_return = log(bars[-1].close / bars[-2].close)
            peer_latest = median(
                log(self.history[peer][-1].close / self.history[peer][-2].close)
                for peer in self.symbols
                if peer != symbol
            )
            residual_latest = latest_return - beta * peer_latest
            flow_z = sign * bars[-1].signed_quote_flow / max(shock.flow_rms[symbol], 1e-12)
            previous = bars[-4:-1]
            structure = (
                bars[-1].close > max(value.high for value in previous)
                if shock.direction == "LONG"
                else bars[-1].close < min(value.low for value in previous)
            )
            own_catchup = sign * latest_return > 0.0 and sign * residual_latest > 0.0
            if not (structure and own_catchup and flow_z >= 0.50):
                continue
            entry = (bars[-1].open + bars[-1].close) / 2.0
            stop = (
                min(value.low for value in bars[-4:]) - 0.08 * atr
                if shock.direction == "LONG"
                else max(value.high for value in bars[-4:]) + 0.08 * atr
            )
            residual_z = sign * residual_latest / max(shock.residual_rms[symbol], 1e-12)
            lag_z = lag_log / max(shock.residual_rms[symbol], 1e-12)
            signal_score = min(flow_z, max(0.0, residual_z)) + max(0.0, lag_z)
            plan = self._costed_plan(
                symbol=symbol,
                bar=bars[-1],
                shock=shock,
                entry=entry,
                stop=stop,
                target=fair_price,
                atr=atr,
                signal_score=signal_score,
                details={
                    "source": "CAUSAL_CROSS_MARKET_LEADER_FOLLOWER",
                    "shock_id": shock.shock_id,
                    "leader": shock.leader,
                    "leader_direction": shock.direction,
                    "shock_detected_ts_ns": shock.detected_ts_ns,
                    "shock_base_ts_ns": shock.base_ts_ns,
                    "causal_beta": beta,
                    "peer_factor_move": factor_move,
                    "frozen_fair_price": fair_price,
                    "lag_log": lag_log,
                    "lag_z": lag_z,
                    "confirmation_residual_z": residual_z,
                    "confirmation_flow_z": flow_z,
                    "leader_current_signed_move": leader_move,
                    "entry_cost_assumption": "MAKER",
                    "target_method": "FROZEN_BETA_IMPLIED_PEER_EQUILIBRIUM",
                },
            )
            if plan is not None:
                plans.append(plan)
        plans.sort(key=lambda value: (-value.signal_score, -value.net_r, value.symbol))
        if plans:
            winner = plans[0]
            self._record(
                "CROSS_MARKET_FOLLOWER_PLAN_EMITTED",
                ts_ns,
                shock_id=shock.shock_id,
                scenario_id=winner.scenario_id,
                leader=winner.leader,
                symbol=winner.symbol,
                direction=winner.direction,
                signal_score=winner.signal_score,
                net_r=winner.net_r,
            )
            for rejected in plans[1:]:
                self.skips["LOWER_CROSS_MARKET_PRIORITY"] += 1
                self._record(
                    "CROSS_MARKET_FOLLOWER_PLAN_REJECTED",
                    ts_ns,
                    scenario_id=rejected.scenario_id,
                    reason="LOWER_CROSS_MARKET_PRIORITY",
                )
            self.active = None
            return [winner]
        return []

    def on_batch(
        self,
        ts_ns: int,
        observations: Mapping[str, CrossObservation],
    ) -> list[CrossMarketPlan]:
        self._validate_batch(ts_ns, observations)
        for symbol in self.symbols:
            self.history[symbol].append(observations[symbol])
        self._last_ts_ns = ts_ns
        plans = self._evaluate_followers(ts_ns)
        if plans:
            return plans
        self._detect_shock(ts_ns)
        return []

    def mark_submitted(self, plan: CrossMarketPlan) -> None:
        self.pending_plan_id = plan.scenario_id
        self._record(
            "CROSS_MARKET_PLAN_SUBMITTED",
            plan.observed_ts_ns,
            scenario_id=plan.scenario_id,
            leader=plan.leader,
            symbol=plan.symbol,
        )

    def mark_rejected(self, plan: CrossMarketPlan, ts_ns: int, reason: str) -> None:
        self.pending_plan_id = None
        self.position_open = False
        self.cooldown_until_ns = int(ts_ns) + 10 * MINUTE_NS
        self.skips[str(reason)] += 1
        self._record(
            "CROSS_MARKET_PLAN_REJECTED",
            ts_ns,
            scenario_id=plan.scenario_id,
            reason=str(reason),
        )

    def mark_entry_filled(self, ts_ns: int) -> None:
        if self.pending_plan_id is None:
            return
        self.position_open = True
        self._record(
            "CROSS_MARKET_ENTRY_FILLED",
            ts_ns,
            scenario_id=self.pending_plan_id,
        )

    def mark_terminal(self, ts_ns: int, reason: str) -> None:
        scenario_id = self.pending_plan_id
        self.pending_plan_id = None
        self.position_open = False
        self.cooldown_until_ns = int(ts_ns) + 15 * MINUTE_NS
        self._record(
            "CROSS_MARKET_TRADE_TERMINAL",
            ts_ns,
            scenario_id=scenario_id,
            reason=str(reason),
        )
