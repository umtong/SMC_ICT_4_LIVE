"""Causal Nautilus adaptation of the public Freqtrade E0V1E entry policy.

The public source contributes two completed-five-minute oversold mechanisms:

``ewo``
    RSI(4) < 45, close < EMA(8) * 0.942, EWO(50, 200) > -5.585,
    close < EMA(16) * 1.084 and RSI(14) < 35.

``buy_1``
    RSI(20) is still falling, RSI(4) < 46, RSI(14) > 19,
    close < SMA(15) * 0.942 and CTI(20) < -0.86.

The source's disabled 99% stop and any DCA behavior are deliberately not
reused.  Candidate 47 turns the observation into a complete day-trading
scenario: enter only on a causal rising edge, invalidate below the lowest low
of the causal twenty-bar interaction (bounded by the source-independent 10%
emergency distance), target the source branch's short-term mean, and translate
the source RSI/Stoch profit-protection conditions into market exits.  All
orders, fills, costs, positions and account NAV remain NautilusTrader-owned.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import math
from typing import Any, Sequence

import ichifan_strategy as _shared

_router = _shared._router
_base = _shared._base
FiveMinuteBar = _shared.FiveMinuteBar
aggregate_five_minute = _shared.aggregate_five_minute

Candidate47E0V1EConfig = _base.Candidate35Config
Candidate35Config = Candidate47E0V1EConfig
SYMBOLS = _base.SYMBOLS
_FIVE_MINUTES_NS = 5 * 60 * 1_000_000_000


@dataclass(frozen=True, slots=True)
class IndicatorSnapshot:
    close: float
    ema8: float
    ema16: float
    ema50: float
    ema200: float
    sma15: float
    rsi_fast: float
    rsi: float
    rsi_slow: float
    prior_rsi_slow: float
    cti: float
    ewo: float
    fastk: float


@dataclass(frozen=True, slots=True)
class E0V1EState:
    ts_event: int
    ready: bool
    raw_entry: bool
    entry: bool
    branch: str
    is_ewo: bool
    buy_1: bool
    close: float = math.nan
    signal_low: float = math.nan
    support_low: float = math.nan
    stop: float = math.nan
    target: float = math.nan
    reward_risk: float = math.nan
    score: float = 0.0
    ema8: float = math.nan
    ema16: float = math.nan
    ema50: float = math.nan
    ema200: float = math.nan
    sma15: float = math.nan
    rsi_fast: float = math.nan
    rsi: float = math.nan
    rsi_slow: float = math.nan
    prior_rsi_slow: float = math.nan
    cti: float = math.nan
    ewo: float = math.nan
    fastk: float = math.nan


# Exact public defaults from E0V1E.py.
_EWO_RSI_FAST = 45.0
_EWO_RSI = 35.0
_EWO_MIN = -5.585
_EWO_EMA8_FACTOR = 0.942
_EWO_EMA16_FACTOR = 1.084
_BUY1_RSI_FAST = 46.0
_BUY1_RSI_MIN = 19.0
_BUY1_SMA15_FACTOR = 0.942
_BUY1_CTI = -0.86
_SOURCE_FASTK_EXIT = 75.0


def _ema(values: Sequence[float], period: int) -> list[float]:
    """TA-Lib-compatible EMA seed followed by recursive smoothing."""
    result = [math.nan] * len(values)
    if period <= 0 or len(values) < period:
        return result
    seed = [float(value) for value in values[:period]]
    if not all(math.isfinite(value) for value in seed):
        return result
    previous = sum(seed) / period
    result[period - 1] = previous
    alpha = 2.0 / (period + 1.0)
    for index in range(period, len(values)):
        value = float(values[index])
        if not math.isfinite(value):
            previous = math.nan
            continue
        if not math.isfinite(previous):
            return result
        previous = alpha * value + (1.0 - alpha) * previous
        result[index] = previous
    return result


def _sma(values: Sequence[float], period: int) -> list[float]:
    result = [math.nan] * len(values)
    if period <= 0:
        return result
    running = 0.0
    invalid = 0
    for index, raw in enumerate(values):
        value = float(raw)
        if math.isfinite(value):
            running += value
        else:
            invalid += 1
        if index >= period:
            old = float(values[index - period])
            if math.isfinite(old):
                running -= old
            else:
                invalid -= 1
        if index >= period - 1 and invalid == 0:
            result[index] = running / period
    return result


def _wilder_rsi(values: Sequence[float], period: int) -> list[float]:
    """Wilder RSI with the same initial arithmetic average used by TA-Lib."""
    count = len(values)
    result = [math.nan] * count
    if period <= 0 or count <= period:
        return result
    selected = [float(value) for value in values]
    if not all(math.isfinite(value) and value > 0.0 for value in selected):
        return result

    gains = 0.0
    losses = 0.0
    for index in range(1, period + 1):
        change = selected[index] - selected[index - 1]
        gains += max(change, 0.0)
        losses += max(-change, 0.0)
    average_gain = gains / period
    average_loss = losses / period

    def value() -> float:
        if average_loss <= 0.0:
            return 100.0 if average_gain > 0.0 else 0.0
        if average_gain <= 0.0:
            return 0.0
        relative = average_gain / average_loss
        return 100.0 - 100.0 / (1.0 + relative)

    result[period] = value()
    for index in range(period + 1, count):
        change = selected[index] - selected[index - 1]
        gain = max(change, 0.0)
        loss = max(-change, 0.0)
        average_gain = (average_gain * (period - 1) + gain) / period
        average_loss = (average_loss * (period - 1) + loss) / period
        result[index] = value()
    return result


def _cti(values: Sequence[float], period: int) -> list[float]:
    """Pandas-TA CTI: rolling Pearson r against x=[1, ..., period]."""
    result = [math.nan] * len(values)
    if period <= 1:
        return result
    x = [float(index) for index in range(1, period + 1)]
    x_mean = sum(x) / period
    x_centered = [value - x_mean for value in x]
    x_squared = sum(value * value for value in x_centered)
    for index in range(period - 1, len(values)):
        y = [float(value) for value in values[index - period + 1 : index + 1]]
        if not all(math.isfinite(value) for value in y):
            continue
        y_mean = sum(y) / period
        y_centered = [value - y_mean for value in y]
        denominator = math.sqrt(
            x_squared * sum(value * value for value in y_centered)
        )
        if denominator <= 0.0:
            continue
        numerator = sum(
            left * right for left, right in zip(x_centered, y_centered)
        )
        result[index] = numerator / denominator
    return result


def _fastk(
    highs: Sequence[float],
    lows: Sequence[float],
    closes: Sequence[float],
    period: int = 5,
) -> list[float]:
    result = [math.nan] * len(closes)
    for index in range(period - 1, len(closes)):
        selected_high = [float(value) for value in highs[index - period + 1 : index + 1]]
        selected_low = [float(value) for value in lows[index - period + 1 : index + 1]]
        close = float(closes[index])
        if not all(
            math.isfinite(value)
            for value in (*selected_high, *selected_low, close)
        ):
            continue
        highest = max(selected_high)
        lowest = min(selected_low)
        width = highest - lowest
        result[index] = 0.0 if width <= 0.0 else 100.0 * (close - lowest) / width
    return result


def e0v1e_branches(snapshot: IndicatorSnapshot) -> tuple[bool, bool, str]:
    """Apply the two public entry branches without execution assumptions."""
    values = tuple(float(getattr(snapshot, field)) for field in snapshot.__dataclass_fields__)
    if not all(math.isfinite(value) for value in values):
        return False, False, "NONE"
    is_ewo = (
        snapshot.rsi_fast < _EWO_RSI_FAST
        and snapshot.close < snapshot.ema8 * _EWO_EMA8_FACTOR
        and snapshot.ewo > _EWO_MIN
        and snapshot.close < snapshot.ema16 * _EWO_EMA16_FACTOR
        and snapshot.rsi < _EWO_RSI
    )
    buy_1 = (
        snapshot.rsi_slow < snapshot.prior_rsi_slow
        and snapshot.rsi_fast < _BUY1_RSI_FAST
        and snapshot.rsi > _BUY1_RSI_MIN
        and snapshot.close < snapshot.sma15 * _BUY1_SMA15_FACTOR
        and snapshot.cti < _BUY1_CTI
    )
    branch = "BOTH" if is_ewo and buy_1 else "EWO" if is_ewo else "BUY_1" if buy_1 else "NONE"
    return is_ewo, buy_1, branch


def causal_e0v1e_state(
    bars: Sequence[FiveMinuteBar],
    *,
    current_ts: int,
) -> E0V1EState:
    """Return the exact state at the latest completed five-minute observation."""
    timestamp = int(current_ts)
    if timestamp <= 0:
        raise ValueError("current_ts must be positive")
    if len(bars) < 200:
        return E0V1EState(
            ts_event=timestamp,
            ready=False,
            raw_entry=False,
            entry=False,
            branch="NONE",
            is_ewo=False,
            buy_1=False,
        )

    selected = list(bars[-240:])
    if int(selected[-1].ts_event) != timestamp:
        relation = "future" if int(selected[-1].ts_event) > timestamp else "stale"
        raise ValueError(
            f"{relation} latest five-minute bar: {int(selected[-1].ts_event)} != {timestamp}"
        )
    for index in range(1, len(selected)):
        if int(selected[index].ts_event) - int(selected[index - 1].ts_event) != _FIVE_MINUTES_NS:
            raise ValueError("non-contiguous E0V1E five-minute history")

    opens = [float(item.open) for item in selected]
    highs = [float(item.high) for item in selected]
    lows = [float(item.low) for item in selected]
    closes = [float(item.close) for item in selected]
    volumes = [float(item.volume) for item in selected]
    if not all(
        math.isfinite(value) and value > 0.0
        for value in (*opens, *highs, *lows, *closes)
    ):
        raise ValueError("invalid E0V1E price history")
    if not all(math.isfinite(value) and value >= 0.0 for value in volumes):
        raise ValueError("invalid E0V1E volume history")

    ema8 = _ema(closes, 8)
    ema16 = _ema(closes, 16)
    ema50 = _ema(closes, 50)
    ema200 = _ema(closes, 200)
    sma15 = _sma(closes, 15)
    rsi_fast = _wilder_rsi(closes, 4)
    rsi = _wilder_rsi(closes, 14)
    rsi_slow = _wilder_rsi(closes, 20)
    cti = _cti(closes, 20)
    fastk = _fastk(highs, lows, closes, 5)
    index = len(selected) - 1
    required = (
        ema8[index],
        ema16[index],
        ema50[index],
        ema200[index],
        sma15[index],
        rsi_fast[index],
        rsi[index],
        rsi_slow[index],
        rsi_slow[index - 1],
        cti[index],
        fastk[index],
    )
    ready = all(math.isfinite(value) for value in required) and volumes[index] > 0.0
    if not ready:
        return E0V1EState(
            ts_event=timestamp,
            ready=False,
            raw_entry=False,
            entry=False,
            branch="NONE",
            is_ewo=False,
            buy_1=False,
        )

    close = closes[index]
    ewo = (ema50[index] - ema200[index]) / lows[index] * 100.0
    snapshot = IndicatorSnapshot(
        close=close,
        ema8=ema8[index],
        ema16=ema16[index],
        ema50=ema50[index],
        ema200=ema200[index],
        sma15=sma15[index],
        rsi_fast=rsi_fast[index],
        rsi=rsi[index],
        rsi_slow=rsi_slow[index],
        prior_rsi_slow=rsi_slow[index - 1],
        cti=cti[index],
        ewo=ewo,
        fastk=fastk[index],
    )
    is_ewo, buy_1, branch = e0v1e_branches(snapshot)
    raw_entry = is_ewo or buy_1

    targets: list[float] = []
    if is_ewo and ema8[index] > close:
        targets.append(ema8[index])
    if buy_1 and sma15[index] > close:
        targets.append(sma15[index])
    target = min(targets) if targets else math.nan
    support_low = min(lows[index - 19 : index + 1])
    stop = max(close * 0.90, support_low)
    valid_geometry = (
        raw_entry
        and math.isfinite(stop)
        and math.isfinite(target)
        and stop < close < target
    )
    reward_risk = (
        (target - close) / (close - stop)
        if valid_geometry and close > stop
        else math.nan
    )
    mean_room = max(target / close - 1.0, 0.0) if math.isfinite(target) else 0.0
    oversold = (
        max(_EWO_RSI_FAST - rsi_fast[index], 0.0) / _EWO_RSI_FAST
        + max(_EWO_RSI - rsi[index], 0.0) / _EWO_RSI
        + max(-cti[index], 0.0)
    )
    score = (
        (reward_risk if math.isfinite(reward_risk) else 0.0)
        + 100.0 * mean_room
        + oversold
    )

    return E0V1EState(
        ts_event=timestamp,
        ready=True,
        raw_entry=raw_entry,
        entry=valid_geometry,
        branch=branch,
        is_ewo=is_ewo,
        buy_1=buy_1,
        close=close,
        signal_low=lows[index],
        support_low=support_low,
        stop=stop,
        target=target,
        reward_risk=reward_risk,
        score=score,
        ema8=ema8[index],
        ema16=ema16[index],
        ema50=ema50[index],
        ema200=ema200[index],
        sma15=sma15[index],
        rsi_fast=rsi_fast[index],
        rsi=rsi[index],
        rsi_slow=rsi_slow[index],
        prior_rsi_slow=rsi_slow[index - 1],
        cti=cti[index],
        ewo=ewo,
        fastk=fastk[index],
    )


class Candidate47E0V1EReversionStrategy(_base.Candidate35Strategy):
    """One-slot four-symbol oversold-to-mean day-trading policy."""

    def __init__(self, config: Candidate47E0V1EConfig) -> None:
        super().__init__(config)
        self._seen_episodes: set[str] = set()
        self.diagnostics.update(
            {
                "e0v1e_five_minute_decisions": 0,
                "e0v1e_ready_symbol_states": 0,
                "e0v1e_raw_candidates": 0,
                "e0v1e_ewo_candidates": 0,
                "e0v1e_buy1_candidates": 0,
                "e0v1e_both_candidates": 0,
                "e0v1e_rising_edge_candidates": 0,
                "e0v1e_invalid_geometry": 0,
                "e0v1e_duplicate_episode_rejections": 0,
                "e0v1e_source_profit_exits": 0,
                "e0v1e_ewo_five_percent_exits": 0,
                "e0v1e_fastk_exits": 0,
                "e0v1e_rsi80_exits": 0,
                "e0v1e_rsi90_exits": 0,
                "e0v1e_source": "public-freqtrade-E0V1E-exact-entry-defaults",
                "e0v1e_geometry": "twenty-bar-causal-low-stop-to-source-short-term-mean",
            }
        )

    def _on_complete_universe_minute(self, ts_event: int) -> None:
        self.minute_index += 1
        self.diagnostics["complete_universe_minutes"] += 1
        self._record_equity(ts_event)

        open_symbols = [
            symbol for symbol in SYMBOLS
            if not self.portfolio.is_flat(self.instrument_ids[symbol])
        ]
        self.diagnostics["max_open_positions_observed"] = max(
            int(self.diagnostics["max_open_positions_observed"]), len(open_symbols)
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
                int(self.diagnostics["max_simultaneous_entry_intents"]), 1
            )
            if self.minute_index - self.entry_pending_minute > 2:
                assert self.current_symbol is not None
                self.cancel_all_orders(self.instrument_ids[self.current_symbol])
                self.diagnostics["entry_expirations"] += 1
                self._event("ENTRY_EXPIRED", ts_event, reason="E0V1E_MARKET_PARENT_NOT_FILLED")
                self._clear_trade_state()
            return

        if not (self.config.evaluation_start_ns <= ts_event <= self.config.evaluation_end_ns):
            return
        if self._funding_blackout(ts_event):
            return
        if self.minute_index - self.last_entry_minute < self.config.cooldown_minutes:
            return

        moment = datetime.fromtimestamp(ts_event / 1_000_000_000, tz=timezone.utc)
        if moment.minute % 5 != 4:
            return
        if any(len(self.bars[symbol]) < 1_000 for symbol in SYMBOLS):
            return

        self.diagnostics["e0v1e_five_minute_decisions"] += 1
        candidates: list[tuple[float, str, E0V1EState]] = []
        for symbol in SYMBOLS:
            five = aggregate_five_minute(tuple(self.bars[symbol]))
            if len(five) < 200:
                continue
            current = causal_e0v1e_state(five, current_ts=five[-1].ts_event)
            if not current.ready:
                continue
            self.diagnostics["e0v1e_ready_symbol_states"] += 1
            if current.raw_entry:
                self.diagnostics["e0v1e_raw_candidates"] += 1
                if current.is_ewo:
                    self.diagnostics["e0v1e_ewo_candidates"] += 1
                if current.buy_1:
                    self.diagnostics["e0v1e_buy1_candidates"] += 1
                if current.is_ewo and current.buy_1:
                    self.diagnostics["e0v1e_both_candidates"] += 1
            if current.raw_entry and not current.entry:
                self.diagnostics["e0v1e_invalid_geometry"] += 1
                continue
            if not current.entry:
                continue
            previous = causal_e0v1e_state(
                five[:-1],
                current_ts=five[-2].ts_event,
            )
            if previous.ready and previous.raw_entry:
                continue
            self.diagnostics["e0v1e_rising_edge_candidates"] += 1
            candidates.append((current.score, symbol, current))

        if not candidates:
            self.diagnostics["unresolved_episodes"] += 1
            return
        candidates.sort(
            key=lambda item: (item[0], item[1] == "BTCUSDT", item[1]),
            reverse=True,
        )
        score, symbol, state = candidates[0]
        episode_id = f"{symbol}:{state.ts_event}:E0V1E_{state.branch}"
        if episode_id in self._seen_episodes:
            self.diagnostics["e0v1e_duplicate_episode_rejections"] += 1
            return

        decision = _router.RouteDecision(
            symbol=symbol,
            state=f"E0V1E_{state.branch}_MEAN_REVERSION_LONG",
            side=1,
            score=score,
            expected_target_r=state.reward_risk,
            atr=math.nan,
            entry_reference=state.close,
            stop_reference=state.stop,
            objective_reference=state.target,
            episode_ts=state.ts_event,
            reasons=(
                "PUBLIC_E0V1E_COMPLETED_5M_ENTRY",
                f"SOURCE_BRANCH_{state.branch}",
                "TWENTY_BAR_INTERACTION_LOW_INVALIDATION",
                "SOURCE_SHORT_TERM_MEAN_OBJECTIVE",
                "RISING_EDGE_CAUSAL_EPISODE",
            ),
            diagnostics={
                "causal_episode_id": episode_id,
                "branch": state.branch,
                "is_ewo": state.is_ewo,
                "buy_1": state.buy_1,
                "signal_low": state.signal_low,
                "support_low": state.support_low,
                "ema8": state.ema8,
                "ema16": state.ema16,
                "ema50": state.ema50,
                "ema200": state.ema200,
                "sma15": state.sma15,
                "rsi_fast": state.rsi_fast,
                "rsi": state.rsi,
                "rsi_slow": state.rsi_slow,
                "prior_rsi_slow": state.prior_rsi_slow,
                "cti": state.cti,
                "ewo": state.ewo,
                "fastk": state.fastk,
                "reward_risk": state.reward_risk,
            },
        )
        before = int(self.diagnostics["entry_submissions"])
        self._submit_decision(decision, ts_event)
        if int(self.diagnostics["entry_submissions"]) == before:
            return
        self._seen_episodes.add(episode_id)
        if self.current_scenario is not None:
            self.current_scenario.update(
                {
                    "scenario_id": f"c47-e0v1e-{before + 1:07d}",
                    "candidate": "candidate-47-public-e0v1e-structural-reversion",
                    "causal_episode_id": episode_id,
                    "branch": state.branch,
                    "entry_policy": "MARKET_BRACKET_AFTER_COMPLETED_5M_SOURCE_SIGNAL",
                    "dynamic_management": "SOURCE_RSI_STOCHF_PROFIT_PROTECTION_AS_MARKET_EXIT",
                }
            )

    def _request_exit(self, ts_event: int, reason: str, **details: Any) -> None:
        if self.current_symbol is None:
            return
        instrument_id = self.instrument_ids[self.current_symbol]
        self.cancel_all_orders(instrument_id)
        self.close_all_positions(instrument_id)
        self._event(reason, ts_event, **details)

    def _manage_open_position(self, ts_event: int) -> None:
        symbol = self.current_symbol
        if symbol is None or not self.bars[symbol]:
            return
        moment = datetime.fromtimestamp(ts_event / 1_000_000_000, tz=timezone.utc)
        if moment.minute % 5 == 4:
            five = aggregate_five_minute(tuple(self.bars[symbol]))
            if len(five) >= 200:
                try:
                    state = causal_e0v1e_state(five, current_ts=five[-1].ts_event)
                except ValueError:
                    state = None
                if state is not None and state.ready:
                    scenario = self.current_scenario or {}
                    entry = float(scenario.get("entry_reference", math.nan))
                    close = float(five[-1].close)
                    profit = close / entry - 1.0 if math.isfinite(entry) and entry > 0.0 else math.nan
                    branch = str(scenario.get("branch", ""))
                    reason = None
                    if "EWO" in branch and math.isfinite(profit) and profit >= 0.05:
                        reason = "E0V1E_EWO_FIVE_PERCENT_PROTECTION_EXIT"
                        self.diagnostics["e0v1e_ewo_five_percent_exits"] += 1
                    elif math.isfinite(profit) and profit > 0.01 and state.fastk > _SOURCE_FASTK_EXIT:
                        reason = "E0V1E_FASTK_PROFIT_PROTECTION_EXIT"
                        self.diagnostics["e0v1e_fastk_exits"] += 1
                    elif math.isfinite(profit) and profit > 0.01 and state.rsi > 80.0:
                        reason = "E0V1E_RSI80_PROFIT_PROTECTION_EXIT"
                        self.diagnostics["e0v1e_rsi80_exits"] += 1
                    elif math.isfinite(profit) and profit < 0.01 and state.rsi > 90.0:
                        reason = "E0V1E_RSI90_PROTECTION_EXIT"
                        self.diagnostics["e0v1e_rsi90_exits"] += 1
                    if reason is not None:
                        self.diagnostics["e0v1e_source_profit_exits"] += 1
                        self._request_exit(
                            ts_event,
                            reason,
                            branch=branch,
                            close=close,
                            profit=profit,
                            fastk=state.fastk,
                            rsi=state.rsi,
                        )
                        return
        super()._manage_open_position(ts_event)


Candidate35Strategy = Candidate47E0V1EReversionStrategy

__all__ = [
    "Candidate35Config",
    "Candidate35Strategy",
    "Candidate47E0V1EConfig",
    "Candidate47E0V1EReversionStrategy",
    "E0V1EState",
    "IndicatorSnapshot",
    "causal_e0v1e_state",
    "e0v1e_branches",
]
