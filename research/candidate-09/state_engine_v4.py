"""Candidate 09 v4: post-acceptance resolution state engine.

Completed multi-horizon auction extremes are neutral liquidity events.  After an
approach, breach and outside acceptance, the engine does not assume that the first
retest will continue.  It waits for one of two causal resolutions:

* continuation: defended retest followed by renewed displacement through the
  retest swing (re-expansion);
* reversal: accepted breakout loses the level with opposite displacement/flow,
  trapping breakout participants and targeting the breached range equilibrium.

All observations are completed one-minute bars.  NautilusTrader remains the only
execution and accounting engine.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from decimal import Decimal, ROUND_FLOOR
from hashlib import sha256
from math import isfinite
from statistics import median
from typing import Any, Mapping

MINUTE_NS = 60_000_000_000


@dataclass(frozen=True, slots=True)
class FlowBar:
    ts_ns: int
    open: float
    high: float
    low: float
    close: float
    volume: float
    taker_buy_volume: float
    trade_count: int

    def __post_init__(self) -> None:
        values = (self.open, self.high, self.low, self.close, self.volume, self.taker_buy_volume)
        if self.ts_ns < 0 or any(not isfinite(value) for value in values):
            raise ValueError("bar contains an invalid timestamp or non-finite value")
        if self.low > min(self.open, self.close, self.high):
            raise ValueError("bar low is inconsistent")
        if self.high < max(self.open, self.close, self.low):
            raise ValueError("bar high is inconsistent")
        if self.volume < 0.0 or not 0.0 <= self.taker_buy_volume <= self.volume + 1e-9:
            raise ValueError("bar volume is inconsistent")
        if self.trade_count < 0:
            raise ValueError("trade_count must be non-negative")

    @property
    def signed_flow(self) -> float:
        return 2.0 * self.taker_buy_volume - self.volume

    @property
    def flow_imbalance(self) -> float:
        return self.signed_flow / self.volume if self.volume > 0.0 else 0.0


@dataclass(frozen=True, slots=True)
class EngineConfig:
    auction_horizons_minutes: tuple[int, ...] = (15, 60, 240, 1440)
    atr_period: int = 20
    volume_period: int = 60
    approach_period: int = 15
    maximum_active_levels_per_side: int = 96
    maximum_level_age_minutes: int = 10080
    minimum_breach_atr: float = 0.08
    cluster_tolerance_atr: float = 0.15
    acceptance_buffer_atr: float = 0.08
    acceptance_closes: int = 2
    acceptance_timeout_bars: int = 8
    retest_timeout_bars: int = 12
    post_retest_resolution_bars: int = 6
    retest_tolerance_atr: float = 0.20
    defended_close_buffer_atr: float = 0.02
    failure_close_buffer_atr: float = 0.06
    reexpansion_buffer_atr: float = 0.05
    stop_buffer_atr: float = 0.12
    minimum_approach_efficiency: float = 0.08
    minimum_approach_flow: float = 0.02
    directional_imbalance: float = 0.08
    maximum_adverse_retest_flow: float = 0.12
    minimum_volume_ratio: float = 1.00
    minimum_displacement_atr: float = 0.35
    minimum_excursion_atr: float = 0.22
    minimum_resolution_displacement_atr: float = 0.25
    minimum_net_reward_to_risk: float = 1.20
    composite_cost_per_fill: float = 0.00075
    cooldown_bars: int = 6
    use_flow_confirmation: bool = True
    require_acceptance_confirmation: bool = True
    require_reexpansion_confirmation: bool = True

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any], *, ablation: str = "baseline") -> "EngineConfig":
        allowed = {
            "baseline",
            "no-flow",
            "no-acceptance-confirmation",
            "no-reexpansion-confirmation",
        }
        if ablation not in allowed:
            raise ValueError(f"unknown ablation: {ablation}")
        structure = payload["structure"]
        breach = payload["breach"]
        flow = payload["flow"]
        trade = payload["trade"]
        risk = payload["risk"]
        horizons = tuple(int(value) for value in structure["auction_horizons_minutes"])
        if tuple(sorted(set(horizons))) != horizons or not horizons or any(value <= 0 for value in horizons):
            raise ValueError("auction horizons must be unique, positive, and ascending")
        return cls(
            auction_horizons_minutes=horizons,
            atr_period=int(structure["atr_period"]),
            volume_period=int(structure["volume_period"]),
            approach_period=int(structure["approach_period"]),
            maximum_active_levels_per_side=int(structure["maximum_active_levels_per_side"]),
            maximum_level_age_minutes=int(structure["maximum_level_age_minutes"]),
            minimum_breach_atr=float(breach["minimum_breach_atr"]),
            cluster_tolerance_atr=float(breach["cluster_tolerance_atr"]),
            acceptance_buffer_atr=float(breach["acceptance_buffer_atr"]),
            acceptance_closes=int(breach["acceptance_closes"]),
            acceptance_timeout_bars=int(breach["acceptance_timeout_bars"]),
            retest_timeout_bars=int(breach["retest_timeout_bars"]),
            post_retest_resolution_bars=int(breach["post_retest_resolution_bars"]),
            retest_tolerance_atr=float(breach["retest_tolerance_atr"]),
            defended_close_buffer_atr=float(breach["defended_close_buffer_atr"]),
            failure_close_buffer_atr=float(breach["failure_close_buffer_atr"]),
            reexpansion_buffer_atr=float(breach["reexpansion_buffer_atr"]),
            stop_buffer_atr=float(breach["stop_buffer_atr"]),
            minimum_approach_efficiency=float(flow["minimum_approach_efficiency"]),
            minimum_approach_flow=float(flow["minimum_approach_flow"]),
            directional_imbalance=float(flow["directional_imbalance"]),
            maximum_adverse_retest_flow=float(flow["maximum_adverse_retest_flow"]),
            minimum_volume_ratio=float(flow["minimum_volume_ratio"]),
            minimum_displacement_atr=float(flow["minimum_displacement_atr"]),
            minimum_excursion_atr=float(flow["minimum_excursion_atr"]),
            minimum_resolution_displacement_atr=float(flow["minimum_resolution_displacement_atr"]),
            minimum_net_reward_to_risk=float(trade["minimum_net_reward_to_risk"]),
            composite_cost_per_fill=float(risk["composite_taker_cost_per_fill"]),
            cooldown_bars=int(trade["cooldown_bars"]),
            use_flow_confirmation=ablation != "no-flow",
            require_acceptance_confirmation=ablation != "no-acceptance-confirmation",
            require_reexpansion_confirmation=ablation != "no-reexpansion-confirmation",
        )


@dataclass(slots=True)
class AuctionLevel:
    level_id: str
    kind: str
    price: float
    horizon_minutes: int
    range_start_ns: int
    range_end_ns: int
    range_high: float
    range_low: float
    range_midpoint: float
    range_width: float
    observed_index: int
    consumed: bool = False


@dataclass(slots=True)
class _RangeBuilder:
    horizon_minutes: int
    block_key: int
    start_ns: int
    end_ns: int
    high: float
    low: float
    close: float
    bars: int = 1


@dataclass(slots=True)
class PendingResolution:
    scenario_id: str
    level: AuctionLevel
    direction: str
    state: str
    start_index: int
    approach_efficiency: float
    approach_flow: float
    confluence_count: int
    extreme: float
    outside_closes: int = 0
    displacement_seen: bool = False
    directional_flow_seen: bool = False
    max_volume_ratio: float = 0.0
    post_signed_flow: float = 0.0
    post_volume: float = 0.0
    acceptance_index: int | None = None
    retest_index: int | None = None
    retest_high: float | None = None
    retest_low: float | None = None

    @property
    def post_flow_imbalance(self) -> float:
        return self.post_signed_flow / self.post_volume if self.post_volume > 0.0 else 0.0


@dataclass(frozen=True, slots=True)
class DiagnosticEvent:
    scenario_id: str
    event_type: str
    event_time_ns: int
    observed_time_ns: int
    previous_state: str
    next_state: str
    reason_code: str
    reference_price: float | None = None
    details: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Signal:
    scenario_id: str
    branch: str
    side: str
    observed_time_ns: int
    entry_reference: float
    stop_price: float
    target_price: float
    net_reward_to_risk: float
    reason_code: str
    details: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class EngineResult:
    events: tuple[DiagnosticEvent, ...]
    signal: Signal | None


@dataclass(frozen=True, slots=True)
class RiskSizing:
    quantity: Decimal
    loss_budget: Decimal
    per_unit_expected_loss: Decimal
    planned_loss: Decimal


def risk_based_quantity(
    *,
    nav: Decimal,
    risk_fraction: Decimal,
    entry_price: Decimal,
    stop_price: Decimal,
    cost_rate_per_fill: Decimal,
    quantity_increment: Decimal,
) -> RiskSizing:
    if nav <= 0 or not Decimal("0") < risk_fraction <= Decimal("0.03"):
        raise ValueError("NAV must be positive and risk_fraction must be in (0, 0.03]")
    if entry_price <= 0 or stop_price <= 0 or quantity_increment <= 0:
        raise ValueError("prices and quantity increment must be positive")
    if cost_rate_per_fill < 0:
        raise ValueError("cost rate cannot be negative")
    budget = nav * risk_fraction
    per_unit = abs(entry_price - stop_price) + entry_price * cost_rate_per_fill + stop_price * cost_rate_per_fill
    if per_unit <= 0:
        raise ValueError("per-unit expected loss must be positive")
    increments = ((budget / per_unit) / quantity_increment).to_integral_value(rounding=ROUND_FLOOR)
    quantity = increments * quantity_increment
    planned = quantity * per_unit
    if quantity <= 0:
        raise ValueError("risk budget is below one exchange quantity increment")
    if planned > budget:
        raise AssertionError("floored sizing exceeded the planned loss budget")
    return RiskSizing(quantity, budget, per_unit, planned)


class LiquidityStateEngine:
    def __init__(self, config: EngineConfig):
        self.config = config
        history_size = max(512, config.volume_period + 8, config.approach_period + 8)
        self._bars: deque[FlowBar] = deque(maxlen=history_size)
        self._true_ranges: deque[float] = deque(maxlen=config.atr_period)
        self._volumes: deque[float] = deque(maxlen=config.volume_period)
        self._levels: dict[str, list[AuctionLevel]] = {"HIGH": [], "LOW": []}
        self._builders: dict[int, _RangeBuilder] = {}
        self._pending: PendingResolution | None = None
        self._index = -1
        self._cooldown = 0
        self._atr = 0.0
        self._volume_median = 0.0
        self._last_timestamp = -1

    @property
    def active_pools(self) -> tuple[AuctionLevel, ...]:
        return tuple(level for kind in ("HIGH", "LOW") for level in self._levels[kind] if not level.consumed)

    @property
    def atr(self) -> float:
        return self._atr

    def on_bar(self, bar: FlowBar) -> EngineResult:
        if bar.ts_ns <= self._last_timestamp:
            raise ValueError("bars must be strictly increasing by observation timestamp")
        self._last_timestamp = bar.ts_ns
        self._index += 1
        previous_close = self._bars[-1].close if self._bars else bar.close
        self._true_ranges.append(max(bar.high - bar.low, abs(bar.high - previous_close), abs(bar.low - previous_close)))
        self._atr = sum(self._true_ranges) / len(self._true_ranges)
        self._volume_median = median(self._volumes) if self._volumes else max(bar.volume, 1e-12)
        self._bars.append(bar)
        events: list[DiagnosticEvent] = []
        self._update_completed_ranges(bar, events)
        self._prune_levels(bar.ts_ns)
        signal: Signal | None = None
        if self._cooldown > 0:
            self._cooldown -= 1
        elif self._pending is not None:
            signal = self._advance_pending(bar, events)
        elif self._ready:
            self._detect_breach(bar, events)
        self._volumes.append(bar.volume)
        return EngineResult(tuple(events), signal)

    @property
    def _ready(self) -> bool:
        return (
            len(self._bars) >= max(self.config.volume_period, self.config.approach_period + 1)
            and self._atr > 0.0
            and bool(self.active_pools)
        )

    def _update_completed_ranges(self, bar: FlowBar, events: list[DiagnosticEvent]) -> None:
        for horizon in self.config.auction_horizons_minutes:
            block_ns = horizon * MINUTE_NS
            key = (bar.ts_ns - 1) // block_ns
            start_ns = key * block_ns
            end_ns = (key + 1) * block_ns
            builder = self._builders.get(horizon)
            if builder is None:
                self._builders[horizon] = _RangeBuilder(horizon, key, start_ns, end_ns, bar.high, bar.low, bar.close)
                continue
            if key == builder.block_key:
                builder.high = max(builder.high, bar.high)
                builder.low = min(builder.low, bar.low)
                builder.close = bar.close
                builder.bars += 1
                continue
            if key < builder.block_key:
                raise ValueError("auction block key moved backward")
            self._finalize_range(builder, bar, events)
            self._builders[horizon] = _RangeBuilder(horizon, key, start_ns, end_ns, bar.high, bar.low, bar.close)

    def _finalize_range(self, builder: _RangeBuilder, observed_bar: FlowBar, events: list[DiagnosticEvent]) -> None:
        if builder.bars < max(2, builder.horizon_minutes // 2) or builder.high <= builder.low:
            return
        width = builder.high - builder.low
        midpoint = (builder.high + builder.low) / 2.0
        range_id = sha256(
            f"{builder.horizon_minutes}|{builder.block_key}|{builder.high:.10f}|{builder.low:.10f}".encode()
        ).hexdigest()[:16]
        for kind, price in (("HIGH", builder.high), ("LOW", builder.low)):
            level_id = sha256(f"{range_id}|{kind}".encode()).hexdigest()[:16]
            level = AuctionLevel(
                level_id, kind, price, builder.horizon_minutes, builder.start_ns, builder.end_ns,
                builder.high, builder.low, midpoint, width, self._index,
            )
            self._levels[kind].append(level)
            self._levels[kind] = self._levels[kind][-self.config.maximum_active_levels_per_side :]
            events.append(DiagnosticEvent(
                scenario_id=f"level-{level_id}", event_type="EXTERNAL_LIQUIDITY_LEVEL_CONFIRMED",
                event_time_ns=builder.end_ns, observed_time_ns=observed_bar.ts_ns,
                previous_state="FORMING", next_state="ARMED",
                reason_code=f"COMPLETED_{builder.horizon_minutes}M_AUCTION_{kind}",
                reference_price=price,
                details={"horizon_minutes": builder.horizon_minutes, "range_high": builder.high,
                         "range_low": builder.low, "range_midpoint": midpoint, "range_width": width},
            ))

    def _prune_levels(self, timestamp_ns: int) -> None:
        minimum_end = timestamp_ns - self.config.maximum_level_age_minutes * MINUTE_NS
        for kind in ("HIGH", "LOW"):
            self._levels[kind] = [level for level in self._levels[kind] if level.range_end_ns >= minimum_end][
                -self.config.maximum_active_levels_per_side :
            ]

    def _detect_breach(self, bar: FlowBar, events: list[DiagnosticEvent]) -> None:
        previous = list(self._bars)[-2]
        buffer = self.config.minimum_breach_atr * self._atr
        highs = [level for level in self._levels["HIGH"] if not level.consumed and level.observed_index < self._index
                 and previous.close <= level.price and bar.high >= level.price + buffer]
        lows = [level for level in self._levels["LOW"] if not level.consumed and level.observed_index < self._index
                and previous.close >= level.price and bar.low <= level.price - buffer]
        if highs and lows:
            events.append(DiagnosticEvent(
                scenario_id=f"ambiguous-{bar.ts_ns}", event_type="AMBIGUOUS_TWO_SIDED_BREACH",
                event_time_ns=bar.ts_ns, observed_time_ns=bar.ts_ns, previous_state="IDLE",
                next_state="NO_TRADE", reason_code="BOTH_EXTERNAL_SIDES_TOUCHED_IN_ONE_OBSERVATION",
                reference_price=bar.close, details={"high_levels": len(highs), "low_levels": len(lows)},
            ))
            return
        if not highs and not lows:
            return
        if highs:
            level = max(highs, key=lambda item: (item.price, item.horizon_minutes))
            direction, extreme = "UP", bar.high
        else:
            level = min(lows, key=lambda item: (item.price, -item.horizon_minutes))
            direction, extreme = "DOWN", bar.low
        efficiency, flow = self._approach_pressure(direction)
        confluence = self._level_confluence(level)
        approach_ok = efficiency >= self.config.minimum_approach_efficiency
        if self.config.use_flow_confirmation:
            approach_ok = approach_ok and (flow >= self.config.minimum_approach_flow if direction == "UP" else flow <= -self.config.minimum_approach_flow)
        self._consume_level_cluster(level)
        scenario_id = f"resolution-{level.level_id}-{direction.lower()}-{bar.ts_ns}"
        if not approach_ok:
            events.append(DiagnosticEvent(
                scenario_id=scenario_id, event_type="BREACH_REJECTED", event_time_ns=bar.ts_ns,
                observed_time_ns=bar.ts_ns, previous_state="ARMED", next_state="NO_TRADE",
                reason_code="NO_DIRECTIONAL_APPROACH_PRESSURE", reference_price=level.price,
                details={"direction": direction, "approach_efficiency": efficiency, "approach_flow": flow,
                         "horizon_minutes": level.horizon_minutes, "confluence_count": confluence},
            ))
            return
        pending = PendingResolution(
            scenario_id, level, direction, "BREACHED", self._index, efficiency, flow, confluence, extreme,
        )
        self._accumulate(pending, bar)
        pending.outside_closes = 1 if self._outside(bar, pending) else 0
        self._pending = pending
        events.append(self._event(pending, bar, "NEUTRAL_LIQUIDITY_BREACH", "ARMED", "BREACHED",
                                  "MULTI_HORIZON_AUCTION_EXTREME_TAKEN"))

    def _advance_pending(self, bar: FlowBar, events: list[DiagnosticEvent]) -> Signal | None:
        pending = self._pending
        assert pending is not None
        age = self._index - pending.start_index
        pending.extreme = max(pending.extreme, bar.high) if pending.direction == "UP" else min(pending.extreme, bar.low)
        self._accumulate(pending, bar)

        if pending.state == "BREACHED":
            if self._outside(bar, pending):
                pending.outside_closes += 1
            else:
                pending.outside_closes = 0
                self._expire(pending, bar, "BREACH_REENTERED_RANGE_BEFORE_ACCEPTANCE", events)
                return None
            if self._acceptance_ready(pending):
                pending.state = "ACCEPTED"
                pending.acceptance_index = self._index
                events.append(self._event(pending, bar, "OUTSIDE_ACCEPTANCE", "BREACHED", "ACCEPTED",
                                          "ORDERFLOW_DISPLACEMENT_ACCEPTED_OUTSIDE_AUCTION"))
            elif age > self.config.acceptance_timeout_bars:
                self._expire(pending, bar, "BREACH_DID_NOT_ACHIEVE_ACCEPTANCE", events)
                return None

        if pending.state in {"ACCEPTED", "RETESTED"} and pending.acceptance_index is not None:
            if self._index > pending.acceptance_index and self._failure_confirmed(pending, bar):
                signal = self._build_signal(pending, bar, branch="REVERSAL")
                return self._finish(pending, bar, signal, events)

        if pending.state == "ACCEPTED" and pending.acceptance_index is not None:
            if self._index > pending.acceptance_index and self._defended_retest(pending, bar):
                pending.state = "RETESTED"
                pending.retest_index = self._index
                pending.retest_high = bar.high
                pending.retest_low = bar.low
                events.append(self._event(pending, bar, "DEFENDED_RETEST", "ACCEPTED", "RETESTED",
                                          "FIRST_RETEST_CLOSED_OUTSIDE_ACCEPTED_LEVEL"))
                if not self.config.require_reexpansion_confirmation:
                    signal = self._build_signal(pending, bar, branch="CONTINUATION")
                    return self._finish(pending, bar, signal, events)
            elif self._index - pending.acceptance_index > self.config.retest_timeout_bars:
                self._expire(pending, bar, "ACCEPTED_BREAK_DID_NOT_RETEST_OR_FAIL", events)
                return None

        if pending.state == "RETESTED" and pending.retest_index is not None:
            if self._index > pending.retest_index and self._reexpansion_confirmed(pending, bar):
                signal = self._build_signal(pending, bar, branch="CONTINUATION")
                return self._finish(pending, bar, signal, events)
            if self._index - pending.retest_index > self.config.post_retest_resolution_bars:
                self._expire(pending, bar, "RETEST_DID_NOT_REEXPAND_OR_FAIL", events)
        return None

    def _accumulate(self, pending: PendingResolution, bar: FlowBar) -> None:
        pending.displacement_seen = pending.displacement_seen or self._aligned_displacement(bar, pending.direction, self.config.minimum_displacement_atr)
        pending.directional_flow_seen = pending.directional_flow_seen or self._aligned_flow(bar, pending.direction)
        pending.max_volume_ratio = max(pending.max_volume_ratio, self._volume_ratio(bar))
        pending.post_signed_flow += bar.signed_flow
        pending.post_volume += bar.volume

    def _acceptance_ready(self, pending: PendingResolution) -> bool:
        required = self.config.acceptance_closes if self.config.require_acceptance_confirmationr_filCume_us)

        if  xonfirmati)Ccg.acm}ti)Ccg.ac_finish(pending, bar, signal, events)
            if self._index - pending.retestdg.requireing, bar, signal, eve_referenc.accear) -poolsf.max_volume_rlrepD", "ACCEtorelf._bars)[-2]
        bxpire(pending, bad = on] sK "ACCEtore  bxpire(pending"ACCEtore see      bxpire(pAx - pending.renfig.require_acceptance_confirmationr_filCumding"ACCEtore see rgned_flow PTED", "RETESTED"} and penditance_ready(pending):
         acceptance_confirmationr_filCu_filCumdin(nce_readyrr) -poolsf.m,(mn:
   Cu_fi    pendin"rr) -poolsf.e_co rgnedelf._bars)[-2]
  lCumdin(nce_readyrr[-2]
  lCumdin(nce_readyrr[, bareadyUcg.ac_finish(pend"ACCEtore  bxpiiiii (n(nce_readyrr[, baMself._finish(pending, r))
        pence_read{._eventtJ    aEtore  dyrr[-2]
  lCumdin(nnm,  if pending.statfiMtcratio(:x - pending.:K "AC boself._event(pending, b cratio(:x - pending.:K "AC boself._event(pending, b creenndin"rr)Ent(pending, b craf"f._finish(   oending.directional, r=ctional, r=ct0:RN, b crafh(   o    )zAi)x_volAC boself._eNY_BREt(pending, bar):
                pending.state = "RETESTED"
}eadyUcg.ac_finish(pend"ACCEtore  bxpiiiii (n(ncAceptancet
6 eveccepting.retnc.acce(pending, , bet
6 evecce be}ti)Ccg.acding, , bet
6 evecing, eriod)
 .  oending.directional, r=ction(bar, pe:"t
6 es)[-2]
  lCumdin(nce_readyrr[-ng.acceptance_index)lie_closes se >= lafh(  kng,Tbe}ti)Ccg.acding, , bet
6 evecing, eriodU:ESTED"} and pendilder.low) / 2d             g,Tbe}(flow >=  g,Tg, eriodU:ESTED"}  buinch="CONT,l.,x)lie_closes se t  end_ns =ding.e_closes se t  eit) / quantio"]),
    )se = self._barsate: stAelf._barsate: stAelf._bar+  dyrr[-2]
  tr, "DEFENDED_RETEST"|tAelf._barsate: stAelf.inimum_displacement_atr)
        padyTEDiratio(:x - pendinnal, events)

        if pending.state ==)e seerNnU:ESTED"}  buinch="CONT,l.,x)lie_closes se t  endHc.end_ns,
           , events)

     cn[ the onl2{TED"}  bus)

  bu = self._barsate: stAelte: stAelte: stAelte: t
6 eve     ts_c
     -> Signal | None:
  N+  dyrpn"rr)(R:   cn[ the onl2{  -> Si["SlCume_us)

        iow_seen = pe.sP[kir[-2]
  tr, "DEFENDstAelnN "RETESTED       padyiow_seen = pe.sP[kBAelte: t
6 eve    t(pending, bEume_S)Zelf.l, r=ct0:pending, bEu = > Signal | None:
     iacding, , bet
6 evecing, eriod)
reme,
    =lafh(  kng,Tbe}ti)Db    RETESTED"} andNone
        self._index = -1
        self._cooldown = 0
        self._atr = 0.0
        self._volume_median = _, eveobse-lf.._index > pendilf._atr = 0.0
  Re: stAelte: t
6 eve     ts_c
BAee t  end_ns =ding.e_closes se t  eit) / -nt)_, e)c=ding.e_closesr_one:s0.0
    e       and accounting ee     ts_c
BAee: self._volumfBAee: send accounting ee     tse)c=ding.e_closesd at
    bar.e_closesr_one:s0.0
    e       and accountie_closesr:tance_iCg.retesOEounountie_closesr:, eriod)
  , bet
6 evecing, eriod)
rtie_cl accounnl ac None = None
    cl accounnl ac None te = "e  dinna
Elf._atrex > pending_E
Elf._atrex > pmax_Ood + 8, config.ap[-2]
  f self._index - pending.aevecing, eriod)lTION_EXT     rVR     self.2K_E
Elfg)lTION_EXT   Re = "e  Rnding_E
Elf._atrex > pmax_Ood + nl ac None te = "et.t_signed_,
Elfn2g.aNliPBAee t  end_ns = = "e  R[-2]
ex > pending.accnding.accndipproach_period=int(structure[     self._atr = 0.      midpoint = tr = 0.  f._atrex > pmax_Ood= 0.  f._atrex > pmax_Ood= 0.  f._02]
e  f._02]
e  f._0(9= _, eveobse-lf.._indexfig.minimum_approach_effio,tr"]),
 Eses se t  eit) / -nt)_, O._expire(pendiD-2]
  f self._index - pending.aevecing, eriod)lTION_EXT     rVR     self.2K_E
Elfg)lTION_EXT   Re = "e  Rnding_E
,x > pmax_Ood= 0.  f._atrex > pmax_Ood= 0.  fn-{_end = t/R,     sel> pmax_Ood= 0.  fn-nish_roach_efficiency": efficiency, "approach_  sel_inignal= t/R,    (,(
     : stAloses se t  end_ns =i2]
  f self.y0(9= _,_exp8= 0)(  -e: stAelf._barsatet/R,     sa   "DEFENDED_RETEST"|"ND_accumulate(selfFENDED_RETEST"|=_E
Elfg)sesr:tanc, r)=s  lCumdin(nnm, Fe_closes se t  ctional, r=ctarsatet/R,  , Fe_accndinsesq 0.  fn-{_end g"es: p,    "_RETEST"|"Cumdint  eit) / -niodU:ESTE_,UST"|"Cumdint  eit) / -niodU:ESTE_,UST"fficieniodU:ESTE_,U__a  f._atreconfirmation: boolnoun pending.accnding.acc+= bar.volume

    def _accd_signal(pending, bar,(pendinDs, "range_high": builder.high,
         f se,  , Fe_accndinsesq 0.  fn-{_Ntore  d-{_N  pending.direction ldint  eit) /eit) nce_indexd= 0./eitng, bag, bar)[}nt  eit)n(nce_re     ,PIn-{t0ymA[I  ,IL"er.high,
    "e_acce_indexd= 0./eitnFe_accndins>NETEST"|=_E
Elfg)ses4
    "e_accegDindins>NETEST"|=_E
Elfg)ses4
    "e_accegDindins>e(iodU:ESTE_,Ux_Ood= 0.  f._ency": efP,x)lie_clo: p, Lt  eit) / quantio"]),
efP,x)lie_clo: p, Lttp, Lt  ieobsegDindins>1}_      self._index = -tg.lf._l ieobsegDindins>1}_      self._index = -tg.lf._l ieobsegDindins>1}_      self._index = -tg.lf9= _,_exiflie_clo: p, Lttp, Lt  ieobsegDindins>1}_    c[s>1}Snimum_ap_,UST"|"CaT"|=,tg.lfogDind}_      send}_ ing, eriod)
rtie_cl accsend}_ ing, eriod)
rtie_cl accsend}_ ccsend}_ ccsend}_ ccsendion, "a,Ux_Ood= 0.  f._ency": efP          r_ fn-nish_roachn-nish_roat         self._levelsat       szigh,
         f sedvance_pending(b     ,PIn-{t0ymAg d ["olnoun pending.axpi  ,PIn-{t0ymAg drR_Faxpi  , fn-nish_roIgnal, et   id:
@dataclass(frozen=TAelf._barelf._inPcce be}ti)Ccg.at_volume > 0.0l    pending.axpi  ,Pi)Ccg.at_volume >  ,PiaclaniodU:ESTE_,UST"fficieniodU:ESTE_,U__a  f._atrbars: i ,PIn-{t0ymAg d ["olnoun penb-nish_roat         self.)Ccg.at_volumeu    self.)Ccg.d}_ ccsend}_ ccsend,Pi)Ccg.at_vo3}Sn   s: stAlos( p, Lttp, Lt  ieobsegDwf.)C
o
6 eve     ts_c
BAee t  end_dn   s: stAlos( p, Lttp, Lt  ihf.)Cc > pending.acdn  4de_closklf.)Ccg.at_volumeu   t  eieve     ts_c
BAolume > i:nish(pendi0nsE
      
BAolume olumind] = [lself.)CcgEST_DID_NSn   s: gax(self.config.voluCcgEpenb-nish_roe:
        pendingf.)CcEpenb-nish_roe:
-LED_RETEST"|tAelf.-   di    UrD > selses4
  L    ,PIn-{t0E4
  e_closes = 1(e4
  L    ,PIn-{t0E4
  e_closes = 1D_BREACH",
                          _sg, btre = 1(e4
 =one:
  N+  btre = 4
 =one:
)n(-> bool:
 _  r_ f 4
 owBadyrpn"rr)(R:sedvancmys(self, timestamp_ns: int) ->( key, start_ney,  "BREAy_ney,  "BREAy_ney,  ney,  "BREAy_ney,  ney,  "BREAy_ney,  ney,  "BR - pendinnancmysO,  =endinnbe}  "Bfone:
      ,,AkED"}  buinch="Coa}  buinch="Coa}  buinch= pending.axpi  ,Pachn-aclak:          ction": ="eturC_.  fn-{_Nto be f._atrex > pmax_Ood= t0E4
  e_closiiency, f bar.t0E4
  e_cs-{t1i.)Ccp_ns: XT     rVR   = sef.)C
o
6 eve  . > sePrt_nn)Ccp_ns:_ns: int) ->( key, start_ney,  "BREAy_ney,  "BRsh_r   builder = self._bu pDendin(__a  f._atrbars: i ,PIn-{t0ymAg d ["oskf._at i:nies)

     cn[ the onl2{TEDes)

     cn[ the otroIgnalo
6 eve oa}  buinch= pendins_ns,
  silder.high,
     Eear  ,PIn-{t0Eo
6 eve  .   builder.low = min(builder.low, bar.low)
   iUcg.ac_f 0.   p)
   iUcg.ac_f 0.   p)U > pmaxtng, bag, bar)[}nt  eit)n(nce_re     ,PIn-{tC,t  e_atrex > pending_E
Elf._atrex xpiiiii (acc 4de_closklf.)x xpi bt   iUcg.ac_f 0.   pAfg)sesr:tanc   -1er.loc  BEcg.ac_fh._a)n(nce_reesr:tanc   -1er.loc  BEcg.ac_uilder.l{m[iii (acc 4de_closkl][rIPns_ns,
re   6snding_ainDs, "range_high    ,(acc 4snding_ainDs, "range_high    ,(acc 4snding_ainDo, st)n(ncec(f p)U         g,Tbe}(flow2ignal = seoc  BEcg.ac_uilder.l{mt} and penditder.l{} and pendt)n(nri       g,ri  ll{mt} and penditder.l"qt_stw=float(flow[".ac_f      :ion, "approa*(l"qt_st       , bar)[}n4      sesr
    outsi, Lt  ieobsegDwf.)-1
        self.1
    outsi, Lt  ieobsegDwfnri     NC,t  e_atreobseg   NC,t  e_atreobscg.g   NC,t piAkr.l{m[iii ( = "R-1
        sZEENT     sZEEy1  ,Pt ase = self._bars[-1].close if self._bars else bar.close
     eario_id = f"_r   builder = self"):
        raise ValueEr[-1].be}ti)!TBaise ValueEHar.close
    -1].close if sektio,> )
       iUcg.aumdint  eit) / -niodU:ESTE_,UST"fficieniodU:EST-         _nri     NCnd zen=TAelfl{m[illfl{m,bR    NCnd zen=TAelfl{ms>1}_     elfl{m[illfl{m,bR  sesr:tanc   -1er.l: stAelte: t
6 eve     ts_c
 NCnd zen=TAelfl{m[illfl{m>inc, ncec(f p)U         g,Tbe}(flo       sZEENT tcc 4de_closkENT tc    raise Vadint  ei:_stw=float(flow[".ac_f      :ic "range_hi.low"])  :wei:_stw=float(flow[N, b crafh(   Are     ,PIn-{tc    raise Vadint  ,P.ac_f      :g     sesr
.ac_f      :g   e  g,T            pendinse )c
.acch_perioge_hi.lowk  NCnt
6 ec   ts_c
 NCnd zen=TAelfl keydins<)x NCvup
 b , f bar.t0E4
  e_cs-{t1i.)Ccp_ns: sZEE*"l | Np_ns: sZ )
       iUcg.aumvolAC None
           iUe t  endHcding_EorBAovolAC None
     ) ->( key, start_ney,  "BREAy_ney,  "BRsh_r      iUe t  endHcding_EorBAovolAC daumvolAC Nney,  "BREAy_ney, fl"._iEAy_eAC daymAg d ["osg.extreme, bar.lise ValueEr[-1].be}ti)!TBaise ValueEHar.cloHar.cloHar.cloHar.cloHar.cloHar.cloHar.cloHar.cloHar.cloHar.cloHar.cloHar.cloHar.cloHar.cloH.cloHar.cloHar.cloHar.          L(ins<)x pViyeri   ts_c
BAee: seder.l 