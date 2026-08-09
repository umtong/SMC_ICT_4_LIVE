"""Candidate 51 open-book router: causal adaptation of public Freqtrade SMAOffsetV2.

The public strategy is not treated as evidence.  Its complete decision policy is
reproduced first, then adapted only where the project constraints require it:
completed five-minute bars, one global slot, causal one-bar input shift,
structural risk sizing, and an explicit independent episode edge.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import math
from typing import Iterable, Mapping, Sequence


ICHI_STATE = "ICHI_V25_FAN_ACCELERATION_LONG"
SMA_OFFSET_STATE = "SMA_OFFSET_V2_DEEP_PULLBACK_LONG"
UNRESOLVED = "UNRESOLVED"


@dataclass(frozen=True)
class BarObservation:
    ts_event: int
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass(frozen=True)
class FeatureObservation:
    observed_time_ns: int
    ready: bool = True
    flow_open_10s: float = math.nan
    notional_open_10s_burst: float = math.nan
    flow_60s: float = math.nan
    efficiency_60s: float = math.nan
    oi_change_15m: float = math.nan
    premium_z: float = math.nan


@dataclass(frozen=True)
class RouteConfig:
    # Legacy constructor fields retained because the reused Nautilus executor
    # instantiates RouteConfig from its StrategyConfig.
    atr_period: int = 30
    min_impulse_atr_continuation: float = 0.20
    min_impulse_atr_reversal: float = 1.05
    min_response_atr: float = 0.08
    min_participation_ratio: float = 0.85
    min_route_score: float = 3.20
    ambiguity_score_gap: float = 0.10
    continuation_target_r: float = 2.00
    reversal_target_r: float = 1.65

    # Public ichiV2 parameters (5m source strategy).
    bucket_minutes: int = 5
    bullish_levels: int = 4
    cloud_levels: int = 1
    fan_rising_lookback: int = 2
    min_fan_gain: float = 1.0007
    fan_fast: int = 12
    fan_slow: int = 96
    exit_ema_period: int = 18
    cloud_conversion: int = 20
    cloud_base: int = 60
    cloud_span_b: int = 120
    cloud_displacement: int = 30

    # Project-required hard invalidation.  The public strategy used a 10%
    # emergency stop; here expected trend invalidation determines quantity.
    hard_stop_min_fraction: float = 0.0035
    hard_stop_max_fraction: float = 0.0600
    stop_atr_buffer: float = 0.25
    public_roi_target_fraction: float = 0.30
    max_entry_extension_atr: float = 4.0

    # Exact public SMAOffsetV2 signal family (5m entry + 1h trend).
    sma_offset_period: int = 20
    sma_offset_low: float = 0.960
    sma_offset_high: float = 1.012
    sma_trend_fast: int = 20
    sma_trend_slow: int = 25
    sma_stop_min_fraction: float = 0.0075
    sma_stop_max_fraction: float = 0.1000
    sma_stop_atr_buffer: float = 0.50


@dataclass(frozen=True)
class RouteDecision:
    symbol: str
    state: str
    side: int
    score: float
    entry_reference: float
    stop_reference: float
    objective_reference: float
    episode_ts: int
    reasons: tuple[str, ...] = ()
    diagnostics: Mapping[str, float | int | str] = field(default_factory=dict)

    @property
    def actionable(self) -> bool:
        return self.side != 0 and self.state != UNRESOLVED


def _finite(value: float) -> bool:
    return math.isfinite(value)


def _mean(values: Sequence[float]) -> float:
    clean = [float(value) for value in values if _finite(float(value))]
    return sum(clean) / len(clean) if clean else math.nan


def _ema(values: Sequence[float], period: int) -> list[float]:
    """Causal TA-Lib-compatible EMA seeded by the first full SMA window."""
    if period <= 0:
        raise ValueError("EMA period must be positive")
    result = [math.nan] * len(values)
    finite_start = next(
        (index for index, value in enumerate(values) if _finite(float(value))),
        None,
    )
    if finite_start is None or finite_start + period > len(values):
        return result
    seed_values = [float(value) for value in values[finite_start : finite_start + period]]
    if not all(_finite(value) for value in seed_values):
        return result
    seed_index = finite_start + period - 1
    current = sum(seed_values) / period
    result[seed_index] = current
    alpha = 2.0 / (period + 1.0)
    for index in range(seed_index + 1, len(values)):
        value = float(values[index])
        if not _finite(value):
            result[index] = math.nan
            continue
        current = alpha * value + (1.0 - alpha) * current
        result[index] = current
    return result



def _sma(values: Sequence[float], period: int) -> list[float]:
    if period <= 0:
        raise ValueError("SMA period must be positive")
    result = [math.nan] * len(values)
    for index in range(period - 1, len(values)):
        window = [float(value) for value in values[index - period + 1 : index + 1]]
        if all(_finite(value) for value in window):
            result[index] = sum(window) / period
    return result


def _rolling_midpoint(highs: Sequence[float], lows: Sequence[float], period: int) -> list[float]:
    result = [math.nan] * len(highs)
    for index in range(period - 1, len(highs)):
        window_high = highs[index - period + 1 : index + 1]
        window_low = lows[index - period + 1 : index + 1]
        if all(_finite(float(value)) for value in (*window_high, *window_low)):
            result[index] = (max(window_high) + min(window_low)) / 2.0
    return result


def _true_range_at(bars: Sequence[BarObservation], index: int) -> float:
    bar = bars[index]
    if index <= 0:
        return max(0.0, bar.high - bar.low)
    previous = bars[index - 1].close
    return max(bar.high - bar.low, abs(bar.high - previous), abs(bar.low - previous))


def _atr(bars: Sequence[BarObservation], period: int = 14) -> float:
    if len(bars) < period + 1:
        return math.nan
    ranges = [_true_range_at(bars, index) for index in range(len(bars) - period, len(bars))]
    return _mean(ranges)


def _aggregate_complete(
    bars: Sequence[BarObservation],
    bucket_minutes: int,
) -> list[BarObservation]:
    """Aggregate only complete, contiguous UTC minute buckets."""
    if bucket_minutes <= 0:
        raise ValueError("bucket_minutes must be positive")
    minute_ns = 60_000_000_000
    bucket_ns = bucket_minutes * minute_ns
    grouped: dict[int, list[BarObservation]] = {}
    for bar in bars:
        grouped.setdefault(int(bar.ts_event) // bucket_ns, []).append(bar)

    output: list[BarObservation] = []
    for key in sorted(grouped):
        items = sorted(grouped[key], key=lambda item: item.ts_event)
        if len(items) != bucket_minutes:
            continue
        if any(items[i].ts_event - items[i - 1].ts_event != minute_ns for i in range(1, len(items))):
            continue
        output.append(
            BarObservation(
                ts_event=items[-1].ts_event,
                open=items[0].open,
                high=max(item.high for item in items),
                low=min(item.low for item in items),
                close=items[-1].close,
                volume=sum(max(0.0, item.volume) for item in items),
            ),
        )
    return output


def _heikin_ashi(bars: Sequence[BarObservation]) -> tuple[list[float], list[float], list[float], list[float]]:
    ha_open: list[float] = []
    ha_high: list[float] = []
    ha_low: list[float] = []
    ha_close: list[float] = []
    for index, bar in enumerate(bars):
        close = (bar.open + bar.high + bar.low + bar.close) / 4.0
        if index == 0:
            open_ = (bar.open + bar.close) / 2.0
        else:
            open_ = (ha_open[-1] + ha_close[-1]) / 2.0
        ha_open.append(open_)
        ha_close.append(close)
        ha_high.append(max(bar.high, open_, close))
        ha_low.append(min(bar.low, open_, close))
    return ha_open, ha_high, ha_low, ha_close


def _shift(values: Sequence[float], amount: int = 1) -> list[float]:
    if amount < 0:
        raise ValueError("only causal positive shifts are allowed")
    return [math.nan] * amount + [float(value) for value in values[:-amount or None]]


def _ichimoku_cloud(
    highs: Sequence[float],
    lows: Sequence[float],
    config: RouteConfig,
) -> tuple[list[float], list[float]]:
    tenkan = _rolling_midpoint(highs, lows, config.cloud_conversion)
    kijun = _rolling_midpoint(highs, lows, config.cloud_base)
    leading_a = [
        (a + b) / 2.0 if _finite(a) and _finite(b) else math.nan
        for a, b in zip(tenkan, kijun, strict=True)
    ]
    leading_b = _rolling_midpoint(highs, lows, config.cloud_span_b)
    displacement = max(0, config.cloud_displacement - 1)
    return _shift(leading_a, displacement), _shift(leading_b, displacement)


def _level_periods(levels: int) -> tuple[int, ...]:
    all_periods = (1, 3, 6, 12, 24, 48, 72, 96)
    return all_periods[: max(0, min(levels, len(all_periods)))]


def _indicator_frame(
    minute_bars: Sequence[BarObservation],
    config: RouteConfig,
) -> dict[str, object] | None:
    candles = _aggregate_complete(minute_bars[-1_000:], config.bucket_minutes)
    required = config.cloud_span_b + config.cloud_displacement + 8
    if len(candles) < required:
        return None

    ha_open, ha_high, ha_low, _ = _heikin_ashi(candles)
    raw_close = [bar.close for bar in candles]
    shifted_close = _shift(raw_close, 1)
    shifted_open = _shift(ha_open, 1)
    shifted_high = _shift(ha_high, 1)
    shifted_low = _shift(ha_low, 1)

    close_emas = {period: _ema(shifted_close, period) for period in _level_periods(8)}
    open_emas = {period: _ema(shifted_open, period) for period in _level_periods(8)}
    fan_fast = close_emas[config.fan_fast]
    fan_slow = close_emas[config.fan_slow]
    fan = [
        fast / slow if _finite(fast) and _finite(slow) and slow > 0.0 else math.nan
        for fast, slow in zip(fan_fast, fan_slow, strict=True)
    ]
    fan_gain = [math.nan]
    for index in range(1, len(fan)):
        previous = fan[index - 1]
        fan_gain.append(fan[index] / previous if _finite(fan[index]) and _finite(previous) and previous > 0 else math.nan)

    cloud_a, cloud_b = _ichimoku_cloud(shifted_high, shifted_low, config)
    exit_ema = _ema(shifted_close, config.exit_ema_period)

    return {
        "candles": candles,
        "shifted_close": shifted_close,
        "close_emas": close_emas,
        "open_emas": open_emas,
        "fan": fan,
        "fan_gain": fan_gain,
        "cloud_a": cloud_a,
        "cloud_b": cloud_b,
        "exit_ema": exit_ema,
    }


def _eligible(frame: Mapping[str, object], index: int, config: RouteConfig) -> tuple[bool, dict[str, float | int]]:
    shifted_close = frame["shifted_close"]
    close_emas = frame["close_emas"]
    open_emas = frame["open_emas"]
    fan = frame["fan"]
    fan_gain = frame["fan_gain"]
    cloud_a = frame["cloud_a"]
    cloud_b = frame["cloud_b"]

    assert isinstance(shifted_close, list)
    assert isinstance(close_emas, dict)
    assert isinstance(open_emas, dict)
    assert isinstance(fan, list)
    assert isinstance(fan_gain, list)
    assert isinstance(cloud_a, list)
    assert isinstance(cloud_b, list)

    close5 = float(shifted_close[index])
    current_fan = float(fan[index])
    current_gain = float(fan_gain[index])
    cloud_top = max(float(cloud_a[index]), float(cloud_b[index]))

    trend_votes = 0
    for period in _level_periods(config.bullish_levels):
        close_value = float(close_emas[period][index])
        open_value = float(open_emas[period][index])
        if _finite(close_value) and _finite(open_value) and close_value > open_value:
            trend_votes += 1

    rising_votes = 0
    for shift in range(1, config.fan_rising_lookback + 1):
        if index - shift >= 0 and _finite(current_fan) and _finite(float(fan[index - shift])) and float(fan[index - shift]) < current_fan:
            rising_votes += 1

    cloud_clear = _finite(close5) and _finite(cloud_top) and close5 > cloud_top
    trend_ok = trend_votes == config.bullish_levels
    fan_gain_ok = _finite(current_gain) and current_gain >= config.min_fan_gain
    fan_magnitude_ok = _finite(current_fan) and current_fan > 1.0
    fan_rising_ok = rising_votes == config.fan_rising_lookback
    eligible = cloud_clear and trend_ok and fan_gain_ok and fan_magnitude_ok and fan_rising_ok
    return eligible, {
        "trend_votes": trend_votes,
        "rising_votes": rising_votes,
        "cloud_clear": int(cloud_clear),
        "trend_ok : int(crend_ok )
        "fan_gain"ok : int(can_gain"ok )
        "fan_gagnitude_ok : int(can_gagnitude_ok )
        "fan_gising_ok
: int(can_gising_ok
,
        "trend_olose_v5m: close_5
        "cloud_cop
: cloud_bop

        "fan_gagnitude_: clrrent_fan 
        "fan_gain": furrent_gain 
    }


def _chimexit_rcroserd
    bars: Sequence[BarObservation],
    bonfig: RouteConfig,
) -> tuple[lool, dict[str, float | int]]:
    srame[= _icdicator_frame(
ars, ionfig)
    ef fiame[=s None:
         eturn eFale, l{"xit_ready"]: 0}    shifted_close = frame["shifted_close"]
    cxit_ema = _rame["sxit_ema":]    assert isinstance(shifted_close, list)
    assert isinstance(cxit_ema,
list)
    andex = fen(seifted_close,)- 1
    crevious = fndex - 1]    calues = [
         loat(shifted_close[index])
,         loat(sxit_ema,index])
,         loat(shifted_close[irevious )
,         loat(sxit_ema,irevious )
,     
    reqdy f=all(_finite(value) for value in walues)
    fcroserd= riqdy fnd falues[:0]< calues[:1]fnd falues[:2 >= 1alues[:3
    return _croserd {
        "txit_ready"]: nt(reasdy)
        "exit_elose": salues[:0]
        "exit_ema": ealues[:1]
        "erevious _xit_elose": salues[:2]
        "erevious _xit_ema": ealues[:3]
    }


ddef _sma(offset_faame(
    minute_bars: Sequence[BarObservation],
    config: RouteConfig,
) -> dict[str, object] | None:
    cive-= _aggregate_complete(minute_bars[-12000:], config.bucket_minutes)
    rour_ly= _aggregate_complete(minute_bars[-12000:], c60
    ef fen(fave- < cunfig.sma_offset_period + 4)or Nen(hiur_ly < cunfig.sma_orend_slow + 3)
        return None

   cive-close = [bar.close for bar in cive-]    rour_lyclose = [bar.close for bar in cour_ly
    shifted_cive-close = [shift(hive-close  1)
    shifted_hiur_lyclose = [shift(haur_lyclose  1)
    shma20= [sha(shifted_cive-close  1unfig.sma_offset_period 
    exa_oast = cema(shifted_caur_lyclose  1unfig.sma_orend_sast)     exa_olow = cema(shifted_caur_lyclose  1unfig.sma_orend_ssow)
    peturn {
        "cive-: fave-
        "eaur_ly: faur_ly
        "shifted_cive-close : shifted_cive-close          "shma20: shma20
        "eaur_ly_xa_oast : exa_oast 
        "eaur_ly_xa_osow": 0xa_osow"
    }


def _ema_offset_exigible(frame: Mapping[str, object], index: int, config: RouteConfig) -> tuple[bool, dict[str, float | int]]:
    sive-close = [rame["shifted_cive-close :]    shma20= [rame["shma20:
    fant = crame["saur_ly_xa_oast :]    show = crame["saur_ly_xa_osow":]    assert isinstance(sive-close  1ist)
    assert isinstance(chma20
list)
    assert isinstance(fanst 1ist)
    assert isinstance(chow, cist)
    aaur_ly_ndex = fen(sast) a 1
    cuose5 = float(sive-close index])
     ean( = float(shma20index])
     ast)1h= float(fanst[aur_ly_ndex )
     how,1h= float(fhow,[aur_ly_ndex )
     rend_ok = tll(_finite(value) for value in w(ast)1h, how,1h) and flst)1h=> how,1h     ip_bk = tll(_finite(value) for value in w(lose_5
 ean( ) and close5 >< ean( =*1unfig.sma_offset_pow,    peturn {rend_ok and fip_bk  {
        "tma_orend_sk : int(crend_ok )
        "fma_oip_bk : int(cip_bk )
        "fma_oignal close : slose_5
        "chma20: sean( 
        "exma20_1h: fant)1h,        "exma25_1h: fhow,1h,        "eip_braction": 0(ean( =- lose5) a/ ean( =f _finite(close_) and _finite(cean( )and _ean( = 0 else math.nan)
    }


def _ma_offset_exit_ready(
     ars: Sequence[BarObservation],
    bonfig: RouteConfig,
) -> tuple[lool, dict[str, float | int]]:
    srame[= _ima(offset_faame(
ars, ionfig)
    ef fiame[=s None:
         eturn eFale, l{"ma_exit_ready"]: 0}    sive-close = [rame["shifted_cive-close :]    shma20= [rame["shma20:
    fant = crame["saur_ly_xa_oast :]    show = crame["saur_ly_xa_osow":]    assert isinstance(sive-close  1ist)
    assert isinstance(chma20
list)
    assert isinstance(fanst 1ist)
    assert isinstance(chow, cist)
    andex = fen(save-close  a 1
    caur_ly_ndex = fen(sast) a 1
    cuose5 = float(sive-close index])
     ean( = float(shma20index])
     ast)1h= float(fanst[aur_ly_ndex )
     how,1h= float(fhow,[aur_ly_ndex )
     eqdy f=all(_finite(value) for value in w(lose_5
 ean( , ast)1h, how,1h)      rend_ofailrd= riqdy fnd fast)1h== seow,1h     ean(recorverrd= riqdy fnd flose5 > cean( =*1unfig.sma_offset_pigh,    peturn {ool,(rend_ofailrd=r vean(recorverrd) {
        "tma_oxit_ready"]: nt(reasdy)
        "ema_oxit_rrend_ofailrd: int(crend_ofailrd)
        "ema_oxit_rean(recorverrd: int(cean(recorverrd)         "ema_oxit_rlose : slose_5
        "chmaoxit_rrrget_: sean( =*1unfig.sma_offset_pigh,=f _finite(cean( )alse math.nan)
    }   "chmaoxit_rxma20_1h: fant)1h,        "ehmaoxit_rxma25_1h: fhow,1h,     


def _lassify_sma_offset(
     ymbol: str
,     ars: Sequence[BarObservation],
    beature_ FeatureObservation(
    bonfig: RouteConfig,
) -> touteDecision:
    sf not _eature_ready 
         eturn eouteDecision:(ymbol, rNRESOLVED
, 0
l.0, iath.nan)
iath.nan)
iath.nan)
i, i("EATURE_ROT_PREADY",)      rame[= _ima(offset_faame(
ars, ionfig)
    ef fiame[=s None:
         eturn eouteDecision:(ymbol, rNRESOLVED
, 0
l.0, iath.nan)
iath.nan)
iath.nan)
i, i("INSUFFICIENTSMA_OFFSET_VHISTORY",)      andles = _rame["fave-:]    assert isinstance(sandles,
cist)
    anf_eature_rbserved_time_ns:> clndles,-1].ts_event,
         eturn eouteDecision:(ymbol, rNRESOLVED
, 0
l.0, ilndles,-1].tlose  1ath.nan)
iath.nan)
ilndles,-1].ts_event, i("ETURE_FEATURE_REJECTED" ,)      ndex = fen(sandles) < 1
    current bk  {iagnostics = sema_offset_exigible(frame:,index, bonfig)
    ef fot _urrent bk 
         etsons:  []
        ff fot _nt(cipgnostics["sma_erend_sk :]:
            ressons:append(fSMA_OFFSET_V1H_TRENDROT_PBULLISH)
         f fot _nt(cipgnostics["sma_eip_bk :]:
            ressons:append(fSMA_OFFSET_VEEP_PULLBACK_NOT_PRESENT" )         eturn eouteDecision:(ymbol, rNRESOLVED
, 0
l.0, ilndles,-1].tlose  1ath.nan)
iath.nan)
ilndles,-1].ts_event, iuple(sessons:) {iagnostics 

    tpisode_tndex = fndex     twhilrtpisode_tndex =>0:
        rwasbk  {_= sema_offset_exigible(frame:,ipisode_tndex = 1, lonfig)
        sf fot _wasbk 
            rbessk         pisode_tndex =  1

    cntry + float(clndles,-1].tlose 
    astr5= _agtrsandles,
c14
     ean( = float(srame["shma20:
index])
     f not (sfinite(cntry  and _finite(cstr5 and _str5= 0.0 end _finite(cean( ):
        return routeDecision:(ymbol, rNRESOLVED
, 0
l.0, intry  1ath.nan)
iath.nan)
ilndles,-1].ts_event, i("INVALIDSMA_OFFSET_VRISK_INPUT ,) {iagnostics 

   retcnt_sywng i minubar.low -or bar in candles]-1]2:)
     naural stop_= riqcnt_sywng i-1unfig.sma_otop_atr_buffer:* amtr5     naural sditance(= rntry +- naural stop_    minusditance(= rntry +*1unfig.sma_otop_ain_fraction:    miaxsditance(= rntry +*1unfig.sma_otop_aix_fraction:     f noaural sditance(=< 0.00=r voaural sditance(=>miaxsditance(
        dragnostics = sict[(iagnostics 

   r   dragnostics .upate ({atr_5: ftr_5,"revcnt_sywng low": 0evcnt_sywng ,"rnaural stop_sditance(braction": 0oaural sditance(=/rntry })         eturn eouteDecision:(ymbol, rNRESOLVED
, 0
l.0, intry  1ath.nan)
iath.nan)
ilndles,-1].ts_event, i("MA_OFFSET_STARUCUREALSTAOP_TOO_FAR ,) {iagnostics 

   rditance(= rax(foaural sditance(,minusditance(
     hop_= rntry +- ditance(     bjective_= raan( =*1unfig.sma_offset_pigh,    pf opjective_=< rntry 
        return routeDecision:(ymbol, rNRESOLVED
, 0
l.0, intry  1hop_,opjective_
ilndles,-1].ts_event, i("MA_OFFSET_SOBECTEIVE_CONSUMD" ,) {iagnostics 

   retard_risk"= [
pjective_=- ntry  a/ ditance(     f oetard_risk"=<1.0

        dragnostics = sict[(iagnostics 

   r   dragnostics "sma_reward_risk"] >=oetard_risk"        return routeDecision:(ymbol, rNRESOLVED
, 0
l.0, intry  1hop_,opjective_
ilndles,-1].ts_event, i("MA_OFFSET_SNT_SGEOMERY_EWEAK ,) {iagnostics 

   rdi_braction"= float(sragnostics "sip_braction":)
     rend_oga_= r(loat(sragnostics "sxma20_1h:) / 2loat(sragnostics "sxma25_1h:) /-1.0)
anf_eoat(sragnostics "sxma25_1h:) / 0 else m.0
    fcore:= r70 + iinub40, iat(0.0, i(di_braction"=-(1.0 - aunfig.sma_offset_pow,) * c00.0
) + min(w20, iat(0.0, irend_oga_=*1_000_0
) + min(w20, ietard_risk"= 2.0 
    diagnostics:= sict[(iagnostics 

   rragnostics .upate ({        "etr_5: ftr_5,        "eevcnt_sywng low": 0evcnt_sywng ,        "eplannedstop_sditance(braction": 0ditance(=/rntry ,        "ehmaoeward_risk"]:ietard_risk"
        "faature_mbserved_time_ns:: faature_rbserved_time_ns:,     

    peturn {outeDecision:(        "ymbol=fymbol,
    r    hote =MA_OFFSET_STATE,
    R    hide=,
         core:=core,          ptry_reference:=ntry ,        "top_reference:=sop

        "bjective_reference:=pjective_
         pisode_tts=lndles,-pisode_tndex .ts_event,
         essons:="PUBLIC_SMA_OFFSET_V2_ECAUSALSNTRY_, "XONE_BAR_SHIFED"_INPUTS, "XONE_USEVEEP_PULLBACK_NEPISODE")         "ragnostics =ragnostics ,     
 def _lassify_schim
     ymbol: str
,     ars: Sequence[BarObservation],
    beature_ FeatureObservation(
    bonfig: RouteConfig,
) -> touteDecision:
    sf not _eature_ready 
         eturn eouteDecision:(ymbol, rNRESOLVED
, 0
l.0, iath.nan)
iath.nan)
iath.nan)
i, i("EATURE_ROT_PREADY",)      rame[= _icdicator_frame(
ars, ionfig)
    ef fiame[=s None:
         eturn eouteDecision:(ymbol, rNRESOLVED
, 0
l.0, iath.nan)
iath.nan)
iath.nan)
i, i("INSUFFICIENTS5MVHISTORY",)       andles = _rame["fandles":]    assert isinstance(sandles,
cist)
    anf_eature_rbserved_time_ns:> clndles,-1].ts_event,
         eturn eouteDecision:(            rymbol,
    r        NRESOLVED
,    r        0,    r        0.0,    r        lndles,-1].tlose      r        ath.nan)
    }   "    ath.nan)
    }   "    lndles,-1].ts_event,     }   "    ("ETURE_FEATURE_REJECTED" ,)     }   "    {faature_mbserved_time_ns:: faature_rbserved_time_ns:,
               ndex = fen(sandles) < 1
    current bk  {iagnostics = sexigible(frame:,index, bonfig)
    erevious _k  {_= sexigible(frame:,index,= 1, lonfig)
     f fot _urrent bk 
         etsons: list[ftr] = f]
        ff fot _nt(cipgnostics["sloud_clear":]:
            ressons:append(fSCHI_VCLOUDROT_PCLEAR 
         f fot _nt(cipgnostics["srend_sk :]:
            ressons:append(fSCHI_VBULLISH_LEVELSROT_PALIGND" 
         f fot _nt(cipgnostics["san_gagnitude_ok :]:
            ressons:append(fSCHI_VAN_AOT_PBULLISH)
         f fot _nt(cipgnostics["san_gain"ok :]:
            ressons:append(fSCHI_V25_FAN_AGAIN_BELOW_UBLIC_STHESOHOL" 
         f fot _nt(cipgnostics["san_gising_ok
:]:
            ressons:append(fSCHI_V25_FAN_AOT_PRISINGREJQUIRD"_STEPS )         eturn eouteDecision:(            rymbol,
    r        NRESOLVED
,    r        0,    r        0.0,    r        lndles,-1].tlose      r        ath.nan)
    }   "    ath.nan)
    }   "    lndles,-1].ts_event,     }   "    uple(sessons:=r vfSCHI_V25_FNTRY_ROT_PREADY",)      }   "    ragnostics ,         )      Proserve_the fource strategy)'sperisst[nt +bu consdtion(,+butftrtach versy      Pligible =ar itothe first far iofits Sontiguous Uausal ppisode_  The       Plecution_adaptedrconstums =each visode eat ost_ oce(=acrosethe fniverse(.    tpisode_tndex = fndex     twhilrtpisode_tndex =>0:
        rwasbk  {_= sexigible(frame:,ipisode_tndex = 1, lonfig)
        sf fot _wasbk 
            rbessk         pisode_tndex =  1

    cntry + float(clndles,-1].tlose 
    astr5= _agtrsandles,
c14
     xit_ema = _rame["sxit_ema":]    assert isinstance(sxit_ema,
list)
    ama,18+ float(cxit_ema,index])

   retcnt_sywng i minubar.low -or bar in candles]-16:)
     f not (sfinite(cntry  and _finite(cstr5 and _str5= 0.0 end _finite(cma,18):
        return routeDecision:(ymbol, rNRESOLVED
, 0
l.0, intry  1ath.nan)
iath.nan)
ilndles,-1].ts_event, i("INVALIDSRISK_INPUT ,) {iagnostics 

     naural stop_= rinubma,18,retcnt_sywng )- aunfig.smop_atr_buffer:* amtr5     naural sditance(= rntry +- naural stop_    minusditance(= rntry +*1unfig.sard_stop_min_fraction:    miaxsditance(= rntry +*1unfig.sard_stop_max_fraction:     f noaural sditance(=< 0.00=r voaural sditance(=>miaxsditance(
        dragnostics = sict[(iagnostics 

   r   dragnostics .upate ({atr_5: ftr_5,"rma,18: 0xa_18,rrnaural stop_sditance(braction": 0oaural sditance(=/rntry })         eturn eouteDecision:(ymbol, rNRESOLVED
, 0
l.0, intry  1ath.nan)
iath.nan)
ilndles,-1].ts_event, i("MARUCUREALSTAOP_TOO_FAR ,) {iagnostics 

   rtop_sditance(= rax(foaural sditance(,minusditance(
     hop_= rntry +- top_sditance(
     ant = croat(srame["slose_emas"]
config.fan_fast]
index])

   rxtension_atr:= r(ntry +- ast) a/_str5=f amtr5= 0.0 else math.ninf     f nxtension_atr:=>config.mix_entry_extension_atr:         dragnostics = sict[(iagnostics 

   r   dragnostics .upate ({atr_5: ftr_5,"rma,18: 0xa_18,rrxtension_atr:: exiension_atr:})         eturn eouteDecision:(ymbol, rNRESOLVED
, 0
l.0, intry  1hop_,oath.nan)
ilndles,-1].ts_event, i("NTRY_RTOO_EXTENDD" ,) {iagnostics 

     anngain = float(fipgnostics["san_gain"")
     as_gagn= float(fipgnostics["san_gagnitude_:)
    cloud_top = mloat(fipgnostics["sloud_cop
:)
     hore:= r(         50
        h iinub40, iat(0.0, i(anngain =-1.0)
a c00000_0
a/_30
)         h iinub20, iat(0.0, i(anngagn=-1.0)
a c0000_0
)         h iinub20, iat(0.0, i(ntry +- loud_top) a/_str5)         h-0.25
* mit(0.0, ixiension_atr:)     
     bjective_= rntry +*11.0 - config.cublic_roi_target_fraction:
    diagnostics:= sict[(iagnostics 

   rragnostics .upate (        h{        """""etr_5: ftr_5,        """""ema,18: 0xa_18,        """""eevcnt_sywng low": 0evcnt_sywng ,        """""enaural stop_sditance(braction": 0oaural sditance(=/rntry ,        """""eplannedstop_sditance(braction": 0top_sditance(=/rntry ,        """""extension_atr:: exiension_atr:,        """""eaature_mbserved_time_ns:: faature_rbserved_time_ns:,         },     
    requrn {outeDecision:(        "ymbol=fymbol,
    r    hote =CHI_STATE 
    R    hide=,
         core:=core,          ptry_reference:=ntry ,        "top_reference:=sop

        "bjective_reference:=pjective_
         pisode_tts=lndles,-pisode_tndex .ts_event,
         essons:="        """""eUBLIC_SCHI_V25_FCAUSALSNTRY_,         """""eONE_BAR_SHIFED"_INPUTS,         """""eUERSISTENT_ELIGIBLEFCAUSALSNPISODE"         """""eEMA18STARUCUREALSINVALIDTION_"         ")         "ragnostics =ragnostics ,     
 d
def _lassify_smmbol(
     ymbol: str
,     ars: Sequence[BarObservation],
    beature_ FeatureObservation(
    bonfig: RouteConfig,
) -> touteDecision:
    s# V4 dxigbrate(l is
oltes Rte public sMAOffsetV2 smehangism  The public     s# chiV2 pamily (failrd=he fome[=dvelsopent +ntenvatlend _s not tllowed"ito    s# ontimilnte oths nragnostics.    requrn {lassify_sma_offset(
ymbol, rars, features lonfig)
 
def _oute_universe(b     ars:_b_smmbol( Mapping[str, oequence[BarObservation],,
    beature_:_b_smmbol( Mapping[str, oeatureObservation(,
    bonfig: RouteConfig,
) -> tuple[louteDecision:| None: dict[str, fouteDecision:]:
    secisions = r{        "ymbol=:_lassify_smmbol(
ymbol, rars,_b_smmbol([mmbol(],beature_:_b_smmbol([mmbol(],bonfig)
        sor symbol,in sorted(gars,_b_smmbol(
     }    scndidate :  []ecision por decision in decisions.values()) f necision.actionable 
    if not acndidate :
        return None
 decisions     scndidate :.orte(ey=lambda item: i(-tem.vcore, item.vmmbol(

    requrn {cndidate ::0]
decisions  

__ll_p_= [
     "arObservation]"      "eatureObservation("      "CHI_STATE "      "MA_OFFSET_STATE,"      "outeConfig,"      "outeCecision:"      "NRESOLVED"
      "lassify_smmbol(
      "chimexit_rcroserd
      "ma_offset_exit_ready(
      "oute_universe(
  ]