"""Causal Nautilus adaptation of the public Freqtrade ``ichiV2`` policy.

The external system is not trusted for its reported performance.  Candidate 47
reuses only its executable decision mechanism:

* five-minute Heikin-Ashi open/high/low with raw close;
* shifted multi-horizon EMA fan (5m, 15m, 30m, 1h, 1.5h, 8h);
* price above a shifted Ichimoku cloud;
* three consecutive fan-magnitude accelerations;
* exit when the shifted 5m close crosses below the shifted 1.5h EMA.

All indicator inputs are completed bars and shifted by one five-minute bar.
NautilusTrader owns orders, fills, fees, slippage, positions and account NAV.
The original 10% emergency stop and 30% remote objective are retained while the
trend-cross exit normally closes much earlier.  Quantity is still current NAV
x 3% divided by the full adverse stop loss including costs.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import importlib.util
import math
from pathlib import Path
import sys
from typing import Any, Sequence

HERE = Path(__file__).resolve().parent
BASE35 = HERE.parent / "candidate-35"
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import router as _router

# Candidate 35 imports ``router`` by module name.  Bind Candidate 47's causal
# data contracts before loading the verified execution/account shell.
sys.modules["router"] = _router
_spec = importlib.util.spec_from_file_location(
    "_candidate47_ichifan_reused_candidate35_strategy",
    BASE35 / "strategy.py",
)
if _spec is None or _spec.loader is None:
    raise ImportError(f"cannot load Candidate 35 shell from {BASE35 / 'strategy.py'}")
_base = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _base
_spec.loader.exec_module(_base)

Candidate47IchiFanConfig = _base.Candidate35Config
Candidate35Config = Candidate47IchiFanConfig
SYMBOLS = _base.SYMBOLS
_FIVE_MINUTES_NS = 5 * 60 * 1_000_000_000
_ONE_MINUTE_NS = 60 * 1_000_000_000


@dataclass(frozen=True, slots=True)
class FiveMinuteBar:
    ts_event: int
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass(frozen=True, slots=True)
class FanState:
    ts_event: int
    ready: bool
    entry: bool
    exit_cross_down: bool
    fan_magnitude: float = math.nan
    fan_gain: float = math.nan
    trend_close_5m: float = math.nan
    trend_close_90m: float = math.nan
    trend_close_8h: float = math.nan
    cloud_a: float = math.nan
    cloud_b: float = math.nan
    score: float = 0.0


def aggregate_five_minute(
    bars: Sequence[_router.BarObservation],
) -> list[FiveMinuteBar]:
    """Aggregate exact contiguous completed one-minute bars; skip partial bins."""
    output: list[FiveMinuteBar] = []
    bucket: list[_router.BarObservation] = []
    bucket_key: int | None = None

    def flush() -> None:
        nonlocal bucket
        if len(bucket) != 5:
            bucket = []
            return
        times = [item.ts_event for item in bucket]
        if any(times[index] - times[index - 1] != _ONE_MINUTE_NS for index in range(1, 5)):
            bucket = []
            return
        output.append(
            FiveMinuteBar(
                ts_event=times[-1],
                open=float(bucket[0].open),
                high=max(float(item.high) for item in bucket),
                low=min(float(item.low) for item in bucket),
                close=float(bucket[-1].close),
                volume=sum(max(float(item.volume), 0.0) for item in bucket),
            )
        )
        bucket = []

    for item in bars:
        key = int(item.ts_event // _FIVE_MINUTES_NS)
        if bucket_key is None:
            bucket_key = key
        elif key != bucket_key:
            flush()
            bucket_key = key
        bucket.append(item)
    flush()
    return output


def _ema(values: Sequence[float], period: int) -> list[float]:
    result = [math.nan] * len(values)
    seed: list[float] = []
    previous = math.nan
    alpha = 2.0 / (period + 1.0)
    for index, raw in enumerate(values):
        value = float(raw)
        if not math.isfinite(value):
            continue
        if not math.isfinite(previous):
            seed.append(value)
            if len(seed) < period:
                continue
            previous = sum(seed[-period:]) / period
            result[index] = previous
            continue
        previous = alpha * value + (1.0 - alpha) * previous
        result[index] = previous
    return result


def _midpoint(
    highs: Sequence[float],
    lows: Sequence[float],
    index: int,
    period: int,
) -> float:
    start = index - period + 1
    if start < 0:
        return math.nan
    selected_high = [float(value) for value in highs[start : index + 1]]
    selected_low = [float(value) for value in lows[start : index + 1]]
    if not all(math.isfinite(value) for value in (*selected_high, *selected_low)):
        return math.nan
    return (max(selected_high) + min(selected_low)) / 2.0


def fan_states(bars: Sequence[FiveMinuteBar]) -> list[FanState]:
    """Return one causal state per completed five-minute bar."""
    count = len(bars)
    if not count:
        return []

    raw_close = [float(item.close) for item in bars]
    ha_open: list[float] = []
    ha_close: list[float] = []
    ha_high: list[float] = []
    ha_low: list[float] = []
    for item in bars:
        close = (item.open + item.high + item.low + item.close) / 4.0
        opening = (
            (item.open + item.close) / 2.0
            if not ha_open
            else (ha_open[-1] + ha_close[-1]) / 2.0
        )
        ha_open.append(opening)
        ha_close.append(close)
        ha_high.append(max(item.high, opening, close))
        ha_low.append(min(item.low, opening, close))

    # Exact causal intent of the source policy: every indicator sees t-1 or older.
    shifted_close = [math.nan, *raw_close[:-1]]
    shifted_open = [math.nan, *ha_open[:-1]]
    shifted_high = [math.nan, *ha_high[:-1]]
    shifted_low = [math.nan, *ha_low[:-1]]

    close_15 = _ema(shifted_close, 3)
    close_30 = _ema(shifted_close, 6)
    close_60 = _ema(shifted_close, 12)
    close_90 = _ema(shifted_close, 18)
    close_8h = _ema(shifted_close, 96)
    open_15 = _ema(shifted_open, 3)
    open_30 = _ema(shifted_open, 6)
    open_60 = _ema(shifted_open, 12)

    fan = [math.nan] * count
    gain = [math.nan] * count
    for index in range(count):
        if math.isfinite(close_60[index]) and math.isfinite(close_8h[index]) and close_8h[index] > 0.0:
            fan[index] = close_60[index] / close_8h[index]
        if index and math.isfinite(fan[index]) and math.isfinite(fan[index - 1]) and fan[index - 1] > 0.0:
            gain[index] = fan[index] / fan[index - 1]

    leading_a = [math.nan] * count
    leading_b = [math.nan] * count
    for index in range(count):
        conversion = _midpoint(shifted_high, shifted_low, index, 20)
        base = _midpoint(shifted_high, shifted_low, index, 60)
        span_b = _midpoint(shifted_high, shifted_low, index, 120)
        if math.isfinite(conversion) and math.isfinite(base):
            leading_a[index] = (conversion + base) / 2.0
        leading_b[index] = span_b

    cloud_a = [math.nan] * count
    cloud_b = [math.nan] * count
    displacement = 30
    for index in range(displacement, count):
        cloud_a[index] = leading_a[index - displacement]
        cloud_b[index] = leading_b[index - displacement]

    states: list[FanState] = []
    for index, bar in enumerate(bars):
        required = (
            shifted_close[index],
            shifted_open[index],
            close_15[index], close_30[index], close_60[index], close_90[index], close_8h[index],
            open_15[index], open_30[index], open_60[index],
            fan[index], gain[index], cloud_a[index], cloud_b[index],
        )
        ready = index >= 3 and all(math.isfinite(value) for value in required)
        entry = False
        score = 0.0
        if ready:
            accelerating = all(fan[index] > fan[index - shift] for shift in (1, 2, 3))
            entry = (
                shifted_close[index] > cloud_a[index]
                and shifted_close[index] > cloud_b[index]
                and shifted_close[index] > shifted_open[index]
                and close_15[index] > open_15[index]
                and close_30[index] > open_30[index]
                and close_60[index] > open_60[index]
                and gain[index] >= 1.0013
                and fan[index] > 1.0
                and accelerating
            )
            cloud_top = max(cloud_a[index], cloud_b[index])
            score = (
                10_000.0 * max(gain[index] - 1.0, 0.0)
                + 100.0 * max(fan[index] - 1.0, 0.0)
                + 10.0 * max(shifted_close[index] / max(cloud_top, 1e-12) - 1.0, 0.0)
            )

        exit_cross = False
        if index > 0:
            current_fast = shifted_close[index]
            current_slow = close_90[index]
            prior_fast = shifted_close[index - 1]
            prior_slow = close_90[index - 1]
            exit_cross = (
                all(math.isfinite(value) for value in (current_fast, current_slow, prior_fast, prior_slow))
                and current_fast < current_slow
                and prior_fast >= prior_slow
            )

        states.append(
            FanState(
                ts_event=bar.ts_event,
                ready=ready,
                entry=entry,
                exit_cross_down=exit_cross,
                fan_magnitude=fan[index],
                fan_gain=gain[index],
                trend_close_5m=shifted_close[index],
                trend_close_90m=close_90[index],
                trend_close_8h=close_8h[index],
                cloud_a=cloud_a[index],
                cloud_b=cloud_b[index],
                score=score,
            )
        )
    return states


class Candidate47IchiFanStrategy(_base.Candidate35Strategy):
    """One-slot, four-symbol implementation of the external ichiV2 mechanism."""

    def __init__(self, config: Candidate47IchiFanConfig) -> None:
        super().__init__(config)
        self._seen_episodes: set[str] = set()
        self.diagnostics.update(
            {
                "ichifan_five_minute_decisions": 0,
                "ichifan_ready_symbol_states": 0,
                "ichifan_entry_candidates": 0,
                "ichifan_rising_edge_candidates": 0,
                "ichifan_duplicate_episode_rejections": 0,
                "ichifan_exit_signals": 0,
                "ichifan_trailing_exits": 0,
                "ichifan_source": "public-freqtrade-ichiV2-causal-adaptation",
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
                self._event("ENTRY_EXPIRED", ts_event, reason="ICHIFAN_MARKET_PARENT_NOT_FILLED")
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
        if any(len(self.bars[symbol]) < 800 for symbol in SYMBOLS):
            return

        self.diagnostics["ichifan_five_minute_decisions"] += 1
        candidates: list[tuple[float, str, FanState]] = []
        for symbol in SYMBOLS:
            five = aggregate_five_minute(tuple(self.bars[symbol]))
            states = fan_states(five)
            if len(states) < 2 or not states[-1].ready:
                continue
            self.diagnostics["ichifan_ready_symbol_states"] += 1
            current = states[-1]
            previous = states[-2]
            if current.entry:
                self.diagnostics["ichifan_entry_candidates"] += 1
            if not current.entry or previous.entry:
                continue
            self.diagnostics["ichifan_rising_edge_candidates"] += 1
            candidates.append((current.score, symbol, current))

        if not candidates:
            self.diagnostics["unresolved_episodes"] += 1
            return
        candidates.sort(key=lambda item: (item[0], item[1] == "BTCUSDT", item[1]), reverse=True)
        score, symbol, state = candidates[0]
        episode_id = f"{symbol}:{state.ts_event}:ICHIFAN_LONG"
        if episode_id in self._seen_episodes:
            self.diagnostics["ichifan_duplicate_episode_rejections"] += 1
            return

        entry = float(self.bars[symbol][-1].close)
        decision = _router.RouteDecision(
            symbol=symbol,
            state="ICHIMOKU_FAN_ACCELERATION_LONG",
            side=1,
            score=score,
            expected_target_r=3.0,
            atr=math.nan,
            entry_reference=entry,
            stop_reference=entry * 0.90,
            objective_reference=entry * 1.30,
            episode_ts=state.ts_event,
            reasons=(
                "SHIFTED_PRICE_ABOVE_ICHIMOKU_CLOUD",
                "BULLISH_5M_TO_1H_HEIKIN_ASHI_FAN",
                "THREE_STEP_FAN_ACCELERATION",
                "PUBLIC_ICHIV2_ENTRY_MECHANISM",
            ),
            diagnostics={
                "causal_episode_id": episode_id,
                "fan_magnitude": state.fan_magnitude,
                "fan_gain": state.fan_gain,
                "trend_close_5m": state.trend_close_5m,
                "trend_close_90m": state.trend_close_90m,
                "trend_close_8h": state.trend_close_8h,
                "cloud_a": state.cloud_a,
                "cloud_b": state.cloud_b,
                "external_policy_stop_fraction": 0.10,
                "external_policy_remote_objective_fraction": 0.30,
            },
        )
        before = int(self.diagnostics["entry_submissions"])
        super()._submit_decision(decision, ts_event)
        if int(self.diagnostics["entry_submissions"]) == before:
            return
        self._seen_episodes.add(episode_id)
        if self.current_scenario is not None:
            self.current_scenario.update(
                {
                    "scenario_id": f"c47-ichifan-{before + 1:07d}",
                    "candidate": "candidate-47-public-ichiv2-adaptation",
                    "causal_episode_id": episode_id,
                    "entry_policy": "MARKET_BRACKET",
                    "external_dynamic_exit": "5M_CLOSE_CROSS_BELOW_90M_EMA",
                    "external_trailing_activation": 0.08,
                    "external_trailing_distance": 0.06,
                    "peak_price": entry,
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
        latest = self.bars[symbol][-1]
        scenario = self.current_scenario or {}
        peak = max(float(scenario.get("peak_price", latest.high)), float(latest.high))
        scenario["peak_price"] = peak
        self.current_scenario = scenario
        entry = float(scenario.get("entry_reference", latest.close))

        moment = datetime.fromtimestamp(ts_event / 1_000_000_000, tz=timezone.utc)
        if moment.minute % 5 == 4:
            states = fan_states(aggregate_five_minute(tuple(self.bars[symbol])))
            if states and states[-1].exit_cross_down:
                self.diagnostics["ichifan_exit_signals"] += 1
                self._request_exit(
                    ts_event,
                    "ICHIFAN_TREND_CROSS_EXIT",
                    shifted_close_5m=states[-1].trend_close_5m,
                    shifted_close_90m=states[-1].trend_close_90m,
                )
                return

        trailing_active = entry > 0.0 and peak / entry - 1.0 >= 0.08
        trailing_hit = trailing_active and float(latest.close) <= peak * (1.0 - 0.06)
        if trailing_hit:
            self.diagnostics["ichifan_trailing_exits"] += 1
            self._request_exit(
                ts_event,
                "ICHIFAN_TRAILING_EXIT",
                entry=entry,
                peak=peak,
                close=float(latest.close),
            )
            return

        super()._manage_open_position(ts_event)


Candidate35Strategy = Candidate47IchiFanStrategy
