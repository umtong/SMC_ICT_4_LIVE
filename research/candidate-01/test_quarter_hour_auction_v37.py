from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import importlib.util
from pathlib import Path
import sys
import types


class Side(Enum):
    LONG = "LONG"
    SHORT = "SHORT"

    @property
    def sign(self) -> int:
        return 1 if self is Side.LONG else -1


@dataclass(frozen=True, slots=True)
class ScenarioPlan:
    scenario_id: str
    response: str
    side: Side
    signal_bar_index: int
    signal_time_ns: int
    stop_price: float
    target_price: float
    confirmation_hold_price: float
    structure_high: float
    structure_low: float
    structure_midpoint: float
    pulse_high: float
    pulse_low: float
    pulse_flow_score: float
    pulse_move_atr: float
    pulse_path_efficiency: float
    pulse_close_location: float
    reason_code: str


agg = types.ModuleType("aggtrade_data")
agg.AggTrade = object
agg.AggTradeDownload = object
agg.iter_downloads = lambda rows: iter(())
sys.modules["aggtrade_data"] = agg
core = types.ModuleType("core")
core.Side = Side
sys.modules["core"] = core
impact = types.ModuleType("impact_regime_probe")
impact.ScenarioPlan = ScenarioPlan
sys.modules["impact_regime_probe"] = impact

path = Path(__file__).with_name("quarter_hour_auction_v37.py")
spec = importlib.util.spec_from_file_location("v37", path)
assert spec and spec.loader
v37 = importlib.util.module_from_spec(spec)
sys.modules["v37"] = v37
spec.loader.exec_module(v37)


def interval(start, end, *, o, h, l, c, signed):
    return v37.IntervalBar(
        start_time_ns=start,
        end_time_ns=end,
        open=o,
        high=h,
        low=l,
        close=c,
        quote_notional=1_000_000.0,
        signed_aggressive_quote=signed,
        trade_count=100,
        first_trade_time_ns=start + 1,
        last_trade_time_ns=end - 1,
    )


def minute(i: int, *, o=100.5, h=101.0, l=100.0, c=100.5, signed=0.0, impulse=None):
    start = i * v37.MINUTE_NS
    return v37.ClockMinute(
        start_time_ns=start,
        end_time_ns=start + v37.MINUTE_NS,
        minute=interval(
            start,
            start + v37.MINUTE_NS,
            o=o,
            h=h,
            l=l,
            c=c,
            signed=signed,
        ),
        first_ten_seconds=impulse,
    )


def prior_balance(start_index=0):
    result = []
    for offset in range(v37.BALANCE_MINUTES):
        index = start_index + offset
        close = 100.25 if offset % 2 == 0 else 100.75
        result.append(
            minute(
                index,
                o=close,
                h=101.0,
                l=100.0,
                c=close,
                signed=1000 if offset % 2 else -1000,
            ),
        )
    return result


def signal(index, *, boundary_minute, long=True):
    assert index % 60 == boundary_minute
    start = index * v37.MINUTE_NS
    if long:
        impulse = interval(
            start,
            start + v37.TEN_SECONDS_NS,
            o=100.5,
            h=101.25,
            l=100.4,
            c=101.15,
            signed=200_000,
        )
        return minute(
            index,
            o=100.5,
            h=101.30,
            l=100.4,
            c=101.18,
            signed=300_000,
            impulse=impulse,
        )
    impulse = interval(
        start,
        start + v37.TEN_SECONDS_NS,
        o=100.5,
        h=100.6,
        l=99.75,
        c=99.85,
        signed=-200_000,
    )
    return minute(
        index,
        o=100.5,
        h=100.6,
        l=99.70,
        c=99.82,
        signed=-300_000,
        impulse=impulse,
    )


def test_primary_pattern_and_exact_clock():
    data = prior_balance(0)
    data.append(signal(15, boundary_minute=15, long=True))
    pattern, reason = v37.detect_clock_auction_pattern(data, 15)
    assert reason is None and pattern is not None
    assert pattern.clock_class == v37.PRIMARY_RULE
    assert pattern.side is Side.LONG
    assert pattern.signal_time_ns == 16 * v37.MINUTE_NS
    assert pattern.dealing_range.end_time_ns == 15 * v37.MINUTE_NS


def test_five_minute_nonquarter_control():
    data = prior_balance(50)
    data.append(signal(65, boundary_minute=5, long=True))
    pattern, reason = v37.detect_clock_auction_pattern(data, 15)
    assert reason is None and pattern is not None
    assert pattern.clock_class == v37.CONTROL_RULE


def test_no_pattern_if_impulse_opens_outside():
    data = prior_balance(0)
    start = 15 * v37.MINUTE_NS
    impulse = interval(
        start,
        start + v37.TEN_SECONDS_NS,
        o=101.1,
        h=101.3,
        l=101.05,
        c=101.2,
        signed=200_000,
    )
    data.append(
        minute(
            15,
            o=101.1,
            h=101.3,
            l=101.05,
            c=101.2,
            signed=300_000,
            impulse=impulse,
        ),
    )
    pattern, reason = v37.detect_clock_auction_pattern(data, 15)
    assert pattern is None
    assert reason == "BOUNDARY_IMPULSE_DID_NOT_OPEN_INSIDE_RANGE"


def test_liquidity_requires_confirmation_and_remains_unswept():
    bars = []
    highs = [101, 102, 105, 103, 102, 104, 103]
    lows = [99, 99, 100, 99.5, 99.2, 99.4, 99.3]
    for index, (high, low) in enumerate(zip(highs, lows)):
        bars.append(
            v37.FiveMinuteBar(
                index * v37.FIVE_MINUTES_NS,
                (index + 1) * v37.FIVE_MINUTES_NS,
                high,
                low,
                (high + low) / 2,
            ),
        )
    level = v37.confirmed_unswept_liquidity(
        bars,
        signal_start_ns=8 * v37.FIVE_MINUTES_NS,
        signal_high=104.5,
        signal_low=100,
        side=Side.LONG,
    )
    assert level is not None
    assert level.price == 105
    assert level.confirmation_time_ns == 5 * v37.FIVE_MINUTES_NS
    bars.append(
        v37.FiveMinuteBar(
            7 * v37.FIVE_MINUTES_NS,
            8 * v37.FIVE_MINUTES_NS,
            105.1,
            100,
            104,
        ),
    )
    assert v37.confirmed_unswept_liquidity(
        bars,
        signal_start_ns=9 * v37.FIVE_MINUTES_NS,
        signal_high=104.5,
        signal_low=100,
        side=Side.LONG,
    ) is None


def test_router_uses_midpoint_invalidation_and_external_target():
    data = prior_balance(0)
    data.append(signal(15, boundary_minute=15, long=True))
    pattern, _ = v37.detect_clock_auction_pattern(data, 15)
    assert pattern is not None
    bars = []
    highs = [101.0, 101.2, 103.0, 101.5, 101.3, 101.7, 101.4]
    lows = [99.8, 99.9, 100.1, 100.0, 99.9, 100.1, 100.0]
    for index, (high, low) in enumerate(zip(highs, lows), start=-7):
        start = index * v37.FIVE_MINUTES_NS
        bars.append(
            v37.FiveMinuteBar(
                start,
                start + v37.FIVE_MINUTES_NS,
                high,
                low,
                (high + low) / 2,
            ),
        )
    plan, diagnostic = v37.route_pattern(pattern, five_minute_bars=bars)
    assert plan is not None
    assert plan.side is Side.LONG
    assert abs(plan.stop_price - 100.5 * (1 - v37.COST_PER_SIDE)) < 1e-9
    assert plan.target_price == 103.0
    assert plan.confirmation_hold_price == 101.0
    assert diagnostic.liquidity_confirmation_time_ns < pattern.signal_time_ns
    assert diagnostic.signal_close_price_risk_fraction > 0.65
    assert diagnostic.signal_close_net_reward_risk > 1.35


def test_short_is_symmetric():
    data = prior_balance(0)
    data.append(signal(15, boundary_minute=15, long=False))
    pattern, reason = v37.detect_clock_auction_pattern(data, 15)
    assert reason is None and pattern is not None
    assert pattern.side is Side.SHORT
    assert pattern.boundary_price == 100.0


if __name__ == "__main__":
    test_primary_pattern_and_exact_clock()
    test_five_minute_nonquarter_control()
    test_no_pattern_if_impulse_opens_outside()
    test_liquidity_requires_confirmation_and_remains_unswept()
    test_router_uses_midpoint_invalidation_and_external_target()
    test_short_is_symmetric()
    print("v37 synthetic causal state-machine tests: PASS")
