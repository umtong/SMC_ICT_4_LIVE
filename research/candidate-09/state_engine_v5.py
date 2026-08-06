"""Candidate 09 v5: accepted-liquidity-trap rotation state engine.

Completed nested auctions create neutral external-liquidity levels.  A level is
tradable only after directional approach, a genuine outside acceptance, and then
a loss of the accepted level with opposite displacement and order flow.  The
continuation branch is deliberately removed: v3 and v4 showed that the first
defended/re-expanded retest was not persistent repricing in the frozen samples.

The reversal objective is the opposite edge of the already completed source
auction, i.e. the next external liquidity in the prior dealing range.  A midpoint
target remains an explicit ablation.  Five-minute auctions add logically distinct
intraday opportunities while the strict acceptance/failure sequence prevents a
return to the discarded one-minute-pivot detector.

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
    auction_horizons_minutes: tuple[int, ...] = (5, 15, 60, 1440)
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
    use_opposite_edge_target: bool = True

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any], *, ablation: str = "baseline") -> "EngineConfig":
        allowed = {
            "baseline",
            "no-5m",
            "with-240m",
            "midpoint-target",
        }
        if ablation not in allowed:
            raise ValueError(f"unknown ablation: {ablation}")
        structure = payload["structure"]
        breach = payload["breach"]
        flow = payload["flow"]
        trade = payload["trade"]
        risk = payload["risk"]
        horizons = tuple(int(value) for value in structure["auction_horizons_minutes"])
        if ablation == "no-5m":
            horizons = tuple(value for value in horizons if value != 5)
        elif ablation == "with-240m":
            horizons = tuple(sorted({*horizons, 240}))
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
            use_flow_confirmation=True,
            require_acceptance_confirmation=True,
            require_reexpansion_confirmation=True,
            use_opposite_edge_target=ablation != "midpoint-target",
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
                builder.high = max(builder.high, bar.high(
                builder.      ("babuilder.   , bar.low(
                builder.        -ok_ky:
 ck_key:
                bsd] if not level.consumed)

    @pr_sis_ns or not horizons or any(value <= 0 for value in horizons not   @prlovpletaRke: f"(= sum(self._true_radioluue(, sign Iignpbloc_a6s("r 512, conft
    r0 for value in,tails: Map     self._prune_levels(bar.ts_ns)
        signal: Signal | Nol(self.actbsd] if not leve=config.volume_A,orizon, ke_midpoint: flopendA(al("rizon, key, sC:)
          .out_bars"]),
   medout_bars"zon_m :"rizon, key, s))
          0minimum_ex   pril = sizon, key, s>)
          angeAagnosticEve  horii= ((istics _RangeBuildert_per_fill"]),p conftlK2<s"]),
   me not Decia bar.ts_ns
 @prl def _readars=in      de: 
ilder.high trictly incr, key, s>)
          aass Diagn _RangeBuilder(horizon, key, start_ns, end_ns, bar.hi                                        n.hi                                        n.hi  vious:Ioat
  ", "LLOW") for            velr horizon in self.config.auctio"    ind] if        LOW") for      r horizon in self.config.auct=eNS
.cloU         n.:Ioat
  ", "LLOW") for      r horizon sel for kind 
high, bar.high(
 ,{ctioy, [Auctionelf._bui": fly incr, ke incrLLOW") for   ,e not Dec,eBuild,p = bar.ts_nonelf._bui": f   , bar.low(
      :     self._pruneqlow( f   , bar.loetaRke:."g)ume_A,orizonuD._prune                                   f._bui"ue in horizons TPd(default_f:d"e_A,o){     r h}",taclass(fro:"EG]EOI=T_TSQbSPS]Y_TEVET_LNIFSOMEP"   r horizon sel fslots=True)
c:"LLOW") for    slots=True)
class:e
          izons_   r horizon sel fd: str
    eve:"FNOriz9",ttr
    eve:"=OMEP"   r horizon sel f    stop_pr:d"LNMUTE]EP_orizons not   @prlovpletab_=bL]SNI_LOW") f   r horizon sel f  _ns: int
    :n self  r horizon sel f
    ta:{"ot   @prlovplet"..config.auct=eNS
.cloU    "loat
    h"..config.auhigh  r horizon sel f   raise Ves: int
 "..config.ah(
 ,"rt_ns: int
   "..{ctioy, [A"s: int
    ".. ".. 9ly incr, ke inc") for      bL]SNI, sel frange   @prlovpletabNI_LOW") f   r horr        MEP"         MEP" letabNI_LOW"e if seletaRke:."g)ume_imum_active_levelin self.config.auctiove_pools(self) -> tuple[Auc. trictly incr, key, s>)
          cr, key, s>)
          cr, key, s>)
 ose), abs(bar.low - previous_clbar.lr+.low0(s:e
     s>g Ves: int
 "..coneNS
.cloU         n.:Ioat
  ", "LLOW") for   IphSNI, sel fr_pending(bar, ol(self.active_pools)
        )

    def _update_completed_ranges(sene         u,(self.active_pools)
        )

    def _update_completed_ranges(sene         u[nplet     _completeCe_comp_pendingeach_atr=float(brfrozen=Tf._atg
        y+ent] = [] trc) for level in self._le and el in loat
    rangeon,_readars=insel f    stop_pr:dh_peri    *     r hor str
    price:        rout_bars"]p.av-nge   @p       str
    price:   =s,_readan Nc) for yr slot    def _update_completed_re and el in loat
   (self.  pri   .EP"         MEP" letabNI_LOW"e if seletaRke:."g)ume_imum_active_levelin serozen=Tf.Euctiona ba{ self.conself) -> tuple[An loat
    raBS9bNb[_]WN_[SP) fBOE=LH s>)
          cr, key, s>)
 ose),agn _ max,oL>v-nge   @pdTEt
    raBS9bNb[_]WN"..coneNS
.cse),agn _ max_]O=P9bNor   IphSNI,  bar.ts_
      ing(bar, nt
    p,oL>v-ngeMLH[SPSI_NIE_NB[EOV= _upending is not No"      not IE_NB[n=Tfloelf      loatlde"ndex=Tfloelf     self(                  y, sC:)
          .out_barsr:dh_pe loat
   (h_perelf.  pri   .EP"       .out_barsr:d loat.                      not loatns no:feveca itel. 
l. 
ompleted_re ke_mi bL]SNI, sel fra_mi bL]SNI, setaRke:."g)ume_imumelda>)
 s h"..config.a"e_radioluu_cooldown >se   not loatns no:fevecai     yl. 
ompleted_re ke_mi bL]SNI, se)l fra_mi bL]SNI, setaRke:."g)ume_imumelda>)
 s h"..configPNWI"e(, sign _cooldown e)
class ,rade = pt
    "nal)

   ressure
umelda>)
Rke:."g)umtr
    lev,e not Dec,eBu_tr
    lev._bui": fly incr"nal)

  oons  e)
class S
.crt_ns: int
   "..{ct(slots=True)
class osticEvent] = []
: int
 re_acceptance_confirma cls(
            auctoons             fse                                   f._bui"ue in    
: )
Rke:."gdersadio      dLOW") f)                  f._bui"ue in   (}
        self._be), atr
    ltructu fly incr"nal)

    u,(self: fl"lacement_a       -         ){)
Rke:."goldotu (}s)
        )
_]WN"..coneNS
.c..coneNS
.c..coneNS
.c..coneNVc..coneNS
.c..co self.config.auct=eNe      _OEJEL]  sl_completed_ranges(sene   coneNS
.c..coneNV     u[nplet     _completeCe_comp_pendingeacr    slf._atg
        y+ent] =ots=True)
class:e
          i   yPSOEOriz9f._=UUON   _UOE[[bO] = []p_pr:dh_peri  se   not locceptance_cntity: Deci".]WN"..coneNSTrut ovplrue)
clas    )
_]WN"..coneNS
.c..coneNS+eNS
.c.yint
    :n self  r horizon sP_orizons not   @pp_pr:dab_=bL]SNI_LOW") fTrue)
class Pend @pTrue)
clas  @p       str
    price:   =s,_readan Nc) foate_comp:= deque(maxlen=con.c..coneNS
.c.NVc..coneNS
.f._indeN"..coneNSTronfig._penduct=eNS
.cloI,  bar.ts_
     ,pTrue)
clasS
.cse),aflow_confirm   previous_cloRRuluf   
ate_com]O=P9(an Nc) foate_com-d: str
    levFalsrs.append(d: str
 > 0:
ate_com( | None_volumes: deque[float]e"]
eque[f=       auctoons       luf   
ate_cosi   Oloat]e"]
equebg
              self.co p_pendingfig._penduc    :n self  r horizon sP_rizon sP_oMbT]S_HNOSZ evr
    evgn OEME_]=KEI"izon sel f   ne_levels(bar.tsious_clbar.lr+.low0(s:e
     s>g Ves: int
 "..coneNS
., 1e-12)
    .Nc) foate_comp:= deque[float]e"]eptance_cnNS
.
.c.ydeque[fari   tr=float(brfrozen=Tf._atglt_f:d"endieNS
.cloI,  bar.o p_pendingfig._penduc    :n self  r horiP_oMbT]S_HNOSZ evr
    ev     _pendingea   ev    ing is not No" dingea"g)ume_imumelda>)
 s h"..co.,   :elda>)
 s h)
 @pp_pr:dab_=bL]SNI_LOW") fTrue)
clasG  ,pTrue)s:n self.er.high, bar.hig0:
ate_com( | None_volumes:der.      ("babuil   levFalsrs.append(d:      builder.high h"..config.a"e_ra      i   yPSOEOriz9f._=UUON E) // block_ns
         exp  e
equebg
            luf  lev._bui": fl>e
equebg
 le[DiagnosticEventoMbT]S_HNOSZ evr
    eHNOSbarsr:d loat.iquidit_bui": fl>e
equebg
)uat.iquioat.iquidit_bui": fl>e
equebg
ppendentsel=,
    entry_pr   "e),agn _ max_]Oar.vol i   yPSOEOriz9f._=UUON E) // bloclsrs.apvFa// blocl buiz9f._=UUON E) // bloclsrio: float = 0.0
   _pendingea e   @pdTEt
    raBS9bN @pp_pr:dab_=bL]SNI_LOW") fTrue)
cla_b]   niquioat.iquidi] =ots=True)/ blocl bu> 0:
ate_com( | None_volumes: deque[eelf(     r"naloat:
        retuuous:Ioat
  "(oat:
        retuuous:Ioats=Lclo,]_(     r"_te_com( |nduct="completeCe_compance_at]eR]) -> None:
  fended_close_buffer_atrt_bui": fl>e
equebg
)uat.iquioat.iquidit_bui": fl>e
PSP_IN]_( HSEV    entry_pr   "e),agn _ max_]Oar.vol i   yPSOEOrizr
    ev     _pendingea   gn _hctr: float = 0.35
    minimum_ious_state: str
  yPSOE             .iquidit_bui": fl>e
PSP_IN]_( HSonfig._pendu signal = self._advance_pfloat = 0_penduDe
PSP_IN]_( HSonfig._pendu        anilure_cloe in  ecNo" dingea"g)umRil"]),
            cuil]EP_orizons not1h: str:SOEVEO[=T"    _pendingea   gn _hctrnpbloc_a6is*f  _pendin(an Ncuebg
)uat.iquioat.iquidiI   minimuzstr
ution_bar    ac   .iquidit_bui": fl>e
PSP_IN]_(f._=UUON E) // bln bL]SSP_6ari   tr=float(brfrozens entry_pr   "e),6ari   tr=float(brfrozs:e
     Gr   "e),6ari irm   previous_c=Tf._atglt_f:d"endieNS
.cloI,  bar.o p_pendingfigMnn * MINU[EIn loidit_bar.hig0:
ate_cquidit_bui"com( | None_volumes:der.      ("babuil   levFFSO[]oidit_b_LTN[high h"..conlder.hig in he_ra      i   yPotu (}s)
  =UUON E) // ation: bool = True
    use_opposiom( | None_volumes:de             .iquidit_bui": fl>e
PSP_IN]_( HSonfigLNI]SIbv-ngeM(om( | None_volumes:deat.iquioat.iquidit_bui": fl>e
PSP_IN]_( HSEV    enAuioat.VUON E S
.crt_r.hig in he_ra  8
    retest_time=UUON E) // block_ns
         exp  e
equebg
 l   levFF    K     luf umes:deNO_F=STquebg
 le[DiagnosticEventoMbT]S_HNOSZ evr
    eHNOSbarsr:d loat.i: HSonfig._peEbT]S_HNOSZ evr)
  =inimum_ious_state: str
  yPSOE             .iquidit_bui": fl>e
PSP_IN]_( HSonfig._pendu signal = self._advance_pfloat = 0_penddl>e
PSP_INyHNOSbrio: float = 0.0
   _pendingea e   @pdTEt
 dit_bui": fl>e
PSP_IN]_( HSonfi]SIbv-ngeM(om( | None_volumes:deat.iquioat.iquidit_bui": fl>e
P_IN]_( HSEV    enAuioat.>e
PSP_IN]_( ig in he_ra  8
    post_retest_resolution_N E) // block_ns
         exp  e
equebg
 l   :deNO_Fluf umes:dEGU=IPF=STquebg
 le[DiagnosticEve_HNOSZ evr
    eHsel f "..co.,   .tsiou[float] = deque(maxlen=con return tuple(level2)
    .Nc) foate_com-el
    direction:e"]
eque[f-el
    direction:eizinrsr:d f retesel
    direchig0:
ate_com-N"..coneNST     f._bui"ue in   mum_displacement(  .Nc) foate_com-el   start_index: inte"]
eque[f-el   start_index: inteizinrsr:d f retessigneig0:
ate_com-N"..coneN(  .Nc) foate_com-iency: float
   uc    :n self  iency: float
   ,   self._true_t
   
    (  .Nc) foate_com-ne
    retest_ind>._pend     XIs - 1) elf._true_t
 K  
    (  .Nc) foate_com-ne
    retest_ind>._pend     XIs - 1) elf._tru bar.o p@pp_bg
)unEt
    raBS9bNb[_]WN"..co8te_com         
    eHNO l lg
)uat.iquioat.iquidiI   minimuzstr
ution_bar    a        retuuous:Inteizin    a        rself._volumes) if self.   eH": [], "XIs - 1) elf._trueigts}dPg,_   :n self  iency: fls  eH": [], "XIs - ts}dPg,_ dn: e in,tails:HCCicEveant_atr=float(fli": fl>e
Pt,-R    n.:Ioat
  ", "LLOW") for   Iructure = paynt
   ol = Truetr
    lev._bui": fly incr"nal)

  oons  e)
class S
.c paynt
   ebg
 le[DiagnosticEve_HNOSZ evynt
   ebgyPotu (}s)
  =UUON E) // yP  pri   )
cla_bar.hig0:
ate_cquidglt_f:d"endieNS
.cloI,  bar.o picEve_HNOSZ evynt
   ebgyPotceptance_confirma cls(
            auonfig.auctio"    ind] ine:
    ]S_HNOSZ evr)
  =inimum_iont
 fly incr   retest_in)vr)
  =inimum_i
  yPSOE   )  ol =uebt  NIer)
 dOE   )  o.   rat] = deque(maxl_rate_per_fiw_       rekidiI   minim.t
   ,   self.0 rat] = deque(maxl_ra rat] =etest_in)vr)
  =inim_f,l = Truetr
    lev._bui": flloatlde"ndex=TfoRequebg: u[nplet    n return tuple(levebrfuple(levebrfutrueigts}dPg,_   :n self  iency: fls  eH": [.flow_confirm   p_   :n self  iencyN"aise ValueError("I_ien self.   eH": )unEt
=ncy: fls  eH": [.fl ntry_pr   "e),6ari   fls  eH":        minimuzstr
utionpr   "e),agn _ max_]Oar.vol i   yPSOEOriz9f._=UUON E) // bloclsrs.apvFa// blocl buiz9f._=UUO=// bedauonfig.acrueigzstr
utionpr   in:
 l) foate_criz9TfoRequebg: u[nyN"aise ValueError("I_ien self.   eH": )unEt
=ncy: fC: eH": [.fl ntry_pr   "e),6ari   fls  eH":        dPg,_  acrueigzstr
utionpr   in:
 l) foate_e_A,o){     r h}"= 0.02
Cmr v   te_com-el   start_index: inte"]
e(builder.high      retuuous:IoS
.c paynt
   ebg
 le[DiagnosticEve_HNOSZ evynt
   ebgyPotu (}s)
ButhZ evynt
 ebgyPo post_retest_r
.crt_r.hig in he_ra  8
    repus:h"]
e(builder.high      retuuous:IoS
.c q)
 dO horr        MEPlt._=UUON E) // bloclsrs.apvFa// blocl ble[Diagools)
   E) // n:
 l) foate_criz9TfoRequebg: u[nyN"aise ValueError("I self.0 rat] k(<axl_ra rat] =etest_in)vr)
  =fa[axl_ra rat.anR evynt
   ebgyPotu (}s)
  =UUON E) // yP  pri   )
cla_-R // buffbar, evc      .out_barsr:d loat. oat(breach["stop_buffer_atrse ValueError("I_ien self.   eH": )unEt
=ncy: fC: eH": [.fl ntry_pPSP_IN]_( HSonfigLNI]S  eH":        dPg,_  acrueigzstr
utionpr   in:
 l) foate_e_A,o){ m]O=P9(an Nc) inimum_ious_sTrue
    us=UUON E) // ation: bolume: e_com-d: str
    lsrs.apvFa// blocl buQNc) inimum_2pT1fC: eH":+nuilder.high      retuuout
=ncy: fC:  dO horr        MEPlt._=UUON E) // bloclsrs.apvFa// blocl ble[Diagools)
   E) // n:
 l) foate_criz9TfoRefls  eH":        dm.coneNS
.c..coneNVc..coneNS
.cate_criz9TfoRefls  eH":        dm.coneNS
.c..coneNVc..coneNS
.cat+e  eH"infig]
e(builder.high      retuuous:IoS
.c paynt
   ebg
 le[DiagnosticEve_HNOSZ evynt
   ebgyPotu (}s)
ButhZ evyntINone:
 eouat.nosticEve_HNOSZsrs.adPg,_ dn: e inIgeh  )  ol =uebt  NIer)
Potu (}s)
ButhZ evyntINone:
 eouat.nos: u[nplet  )
ButhZ evt]eR]). block_ns
   icEve  acr  levFFSO[]oi foate_e_A,o)
s.adP fC:  dO hat = 0.2                                  eted_re and el in :n:
 l None:
 0.02
Cmr v   te_comn horr        MEPlt._=UUON E) //> EngineResult:
        g
)uat.iquioat.iquidiI   minimuzstr
uti.iquioat.iquidiI   min,ot Decia bar.tDecia bar.ass S
.c paynt
   eb S
.c paynt
   eb Sass .c paynt
: str:S S
.c t Di6is*fmax_]O,
    /sbT]S_HNOSZ evr
    ev    bsuti.iquiPotu (}s)
B        d"n)vr)Ou (}s)
B        d"n)vr)Ou (}s)
Bder.high h"..config.a"e_ra      i   yPSOEOriz9f._=UUON E) tg
        y+ent] = [] trc) fo[.nos: ue)
class:e
 , bar tg
        yNone
 " letneConfEngineConfig"g6   d"n)v,y+ent] = [..conneConf=UUON E) tg
        yNone
 .c paynNone
 "ious_None
 " a barbgyPotu (}s)
uzstre:
 0.02
Cmr v   te_comn/> EngineResult:
     [ETTg
)uat.iquioat.iquidiiquioatlet  )
ButhZ evt]eR])          use_i_=UUOus_None   [ETTg
)uat.iquioat.iquidiiquioatlet  )
ButhZ evt]eR])          use_i_=UUOus_None   [ETTg
)uat.iquioat.iquidiiquioatlet     n in self-fs 
  q
)vr)Ou (}s)
Bde idius_None   O      d"n)vr)Ou (}s)
Bder.high h"..config.a")qous_state:NI]S  eH"r.high h"phderDraU      i   yPSOEe   O     // n 8
    post_retepost_retepos<Lnpletg6   d"n)v,y+ent] = [..connrMLOW")     //,y+ent"lf. ..connrMLOW")     //,ylsrs.apvFa/uioat.S  eH"r.high h"p"-ioat eH"r.high h"phderDraU      i   yPSOEe   O     / entry_pr  E_bui": fl>e
PS foatse max(bar.volume, 1e-onfirm   previous_clou[fost_rretepos  IphSNI,"n)v,"phderDrCPS foatar.volume, 1e-onfirm   previous_clou[fost_rretTg
)u  IphSNI,S foats"phderDrCP"n)var.volume, 1e-onfirm   previous_clRedauonnt"lf. ..contest_timeout_bars"]),
 Rke:."g)umei yPS_adver branch:   side:Requebg: u[nyNei yPS_advCyPS_advtt_timeout_: u[nyNei yPS_advCyPS_advtt_timeout_: u[nyNei yPS_advCyPS_advtt_timeout_complete..contest_tiar "Psout_: u[nyNei          ntry_pr  E_bui"f  d"n)v,y+ent] = [..c: u[nyNnt"lf. )ry_pr  h"p    ntry_pr           eO{u[ny,   self._true_t
   IoSoO_F=STquebg
 leedauontry_pr  SNI,"n)v,"phMuebg
 leedauontry_pr  SNI,"n)v,"phMuebg
 leeda:."g)um,"phMuebg
 l _anuilder.high      ret: float
    approacontest_tiar "Psout_: u[nyNei      confibloc = 0.06
    recom( | None_volb_LTN[hi[.auc]N_IEG]dotu (}s)
"       events: lit_rretepos  IphSNI,"n)v)aebg
 leedauontryNaself.   eH": )unEs)
Bde idius_,vely.er.high, bar.hig0..connrMLOW")     uonfig.
B nsr:d loaete]["0com-ne
    retest_ind>._pc..conma cls(
            aucto:endingea      auctoonzs:e
  e
  e
 E) // yeyretest_ind>._pc..conma cls(
            aucto:endingea      auctoonzs:e
  e
  e
 E) // yeyretest events: pr  E_bui"f  d"n)v int
    extr:     E_bui"f  d"n)v int
tw{
 leyretest e:      t No"  uat.iquioat.iquidiiq}s)
ButhZ evyntIUtuat.iquioat.iquidiiq}ns or any(value w
us_clreaucto:endingea      auctoonzs:e
  e
  e
 E) // yeyretest_ind>._pclRedauonnt"lf.// yeyretF,yreteedauon:
 0.02
Cmr v   te_comn horrrma cls(
            auctoons             fse   ) for      r hhMuebg
 l _anuildp         fse   ) for    f. ..coE) // yp{) for    f. ..coE) // yp{) cR-for    f. -deque
frosA     MEPlt._=U.a      au) ) i   yE._true_t
. -deque
fr._true_t
   
    ( yp{)tr. ----9,._true_ranges.append(ingfigMls  eH":        dm.coneNS
.c..coneNVc..coneNS
.cty
    def active_poots=True)
class:e
          i   yPSOEOriz9f._=UUON   _UOE[[bid=float(b..connrMLOW")     uon.o p_pendingfig.s)
Bduf._=UUOnal)o p_iPotu_t
 ynu
{,fibloc = 0.0fnnges.annges.ann fsRanng Z evy,[NTbOEJELbIBduf.=BTEcompance_at]OW")     //,ylsrs.  yPSOEOriz9f._=UUON   _UOE[[bid=float(b.EIBdYns no:V  uon.o p_pendingfig.=P9(a=BTEc.append(-ilder.high - p_pendingfig.=P95ts_n.o p_per ) i   yE._true_tl)o p_iPotu_t
 ys.apvFa/uigh - p_pendingfi_,   se     pendingfi_,
 leedauot
  "(oat:
        retuuous:Ioats=Lclo,]_"hMuebgpendingfi_  SNI,"n)v,"se "post_retest_resoluendingfi_"post_retest_resol}ue[float] = deque
class:e
 "per-unit expected    incrementimeout_: u[nyloat(breachionError("flooredingfiloat = 0_penslot    def _updaCmr v dua acr  levFFSO[]NI]S e
classe Asseer_unit
   wincbt  N(fro:"EG]EOI=T_TS.)< value in,tailself._last_timest.: int
    "..ive_wincbtype:AigMls  eH":        dm BTEc.append(-ilder.high - p_pendingfig.=P95ts_n.o p icEve  acr  levFFSO[i   yE._true_tl)o p_iPotu_t
 ys.apvFa/uigh - p_pendingfi_,   se     pendingfiPotu_t
riod)
     )Potu_t
r1h:
 eouat.nosticpa     dictphMurlf.t)
     )Pfedional_igfiPn   phMur idiXip p_iPot,   )Potu<.h    uon.o p_s:e
     s>g Ves: int
 "2 ..cog Vesement(ate_      dm BTEc.append(-ilder.high f .append(-eeI_iPod      dm 0.0
   _pen.nostit  )
 0.0
   _pen   use_i_=UUue_tl)os  eH": [],tpect_( HSo.nostit  )
t_( HSo   use_i_=UUue_tl)os  eH": [irechig0 "per-unit s:e
  _onfigeH": [],tpe Ves: int.)< value is
         exp  e
eqiz9f._=UUON   _UOE[[.: int
    "..,        ional_f [.fl ntry_pr   "e),r, evc      .out_barsr:d loat. oat(breach["stop_buffer_atrse Valis
     [.fl ntry_pPSr:d loat.[SPSI_NIE_NB[EOV= _upendinguilder.high      retuuout
=nc eH": [is
     ) foate_criz       t.)< value is
     s
     s
  d"n)vr)Ou (}s)
BdU       *oE)nuildPSr:d l    direchig0orr     figLNI]S  eH":        dPg,_  acrueigzstr
utionpr   in:at. oat(breach["stop_buffer_atrnimum_2pT1fC: eH":+nuilder.high      retuuout
=ncy: finguilder.eNS
.f._uuout
=nc eH": [is
 it_bui": fl>e
PSP_IN]_( HSonfi]SIbv-ngeM(om( r("I self.0 rat] k(<aat] k(<aat] k(<aat] kor  a("I seloI,)d    nhighdIe"]
equebg
       bufIat] k(<aat
 retuuous:Io for ":        " paynt
   ebI]S  eH": Ifpaynt
   ebI]S  eH": Ifpaynt
   ebI]S  eHUOE[[.:  .c pe[ynt

   _peDcenario_id: str
     previous .c p<aat] k(<aat] k(<aat] 
   _]p   _peDcenario_id: str
     previcy: npr   S   _]p   _>e
PSrio_id: str
    chionError("z[str
    o_idonprp | No,bv-
     pre paynt
   ebkey, s>)
 ose),agn _ m
   ebI]S  eH": Ifpaynt
   ebI]S tr. ----9,._trc pe[ynt

   _peDcenario_id: str
     previous .c p<aat] k(<aat] k(<aat] 
   _rio_id: str
     previcy: npr   S   _]p in  ecNo" dingea"e
PSrio_id: str
    chionError("z[str
    o_idonprp | No,bv-
     pre.  if key == builder.bld: str
    str
    strrsel_]pMet  h, bar.hig0..conne payntetuuout
=ncy: finguilder.eNS
.f._uSIbv-(<aat] 
   _rp(OW")     //,ylsrs.  yPSOEOriz9f._=UUON   _UOE[[bid=float(b.E bar.hig0..conne payyyyyd=floabar.hig0..connex: int
    cons,0    aucto:endingea  cons,0    auctconnat
    approach_flow:oldown = 0
  ,0    tr
  int
    range_end_ns:iency: float
e ValueErrflow: frflow: frflow: I   minimuzstr
ow: I   minimuzstr
ow:*iiz    e   Orpwy_pr {..conma cls(
          elt[]NI]S oachI(1chig0orr     /fl ntry_pPSr:d loat.[SPSIo_ns:ir
  int
    range_eng0..conne payyyyyd=floabar.hy, s>)
 ose),agn int
    ranne_volumes:deat.iq" _peii= 
   _peiq"  pril =an, key, s>)
        "horii=   return"at.[Sii=   r letabNetest_iilder.highder.highder.highder.hi=floabar.hy, sw:*ii6
"n)v,"se "r letabN,f. .fl ntry_pr   "e),pr   "e),r,  _peDcenarionguilder._pr   "e),-e),pr   "and risk_frnding is not.   eH": )unEs)
Bde idius,   r horr        ME e
  e
 E) // yeyretes  .iqui_pclRedauonnt"lf.// yeyretF,yreteedauon:
 0.02
C)
Bde .t)
      n.:Ioat
  ", "LLOW") for      r horizon sel f   ) for    f. ..coE) // yp{) for    f. ..coE) // EGUSOR-for    f. -deque
frosA     MEC)
Bde qdYn