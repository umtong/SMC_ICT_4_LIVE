#!/usr/bin/env python3
"""Run a fast four-symbol continuous-account screen before Nautilus promotion."""
from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import date, timedelta
import json
from pathlib import Path
import sys
import pandas as pd

HERE=Path(__file__).resolve().parent
if str(HERE) not in sys.path: sys.path.insert(0,str(HERE))

from data import load_range, resample
from domain import Candle, CostAssumptions
from instrument_contracts import CONTRACTS
from market import EasyChartScenarioEngine, ScenarioConfig, confirmed_pivot
from simulator import ContinuousAccountSimulator, InstrumentSpec, MinuteBar

SYMBOLS=tuple(CONTRACTS)


def candles(frame: pd.DataFrame) -> list[Candle]:
    return [Candle(
        ts_open_ns=int(row.open_time_dt.value),
        ts_close_ns=int(row.close_time_dt.value),
        open=float(row.open), high=float(row.high), low=float(row.low), close=float(row.close), volume=float(row.volume),
    ) for row in frame.itertuples(index=False)]


def build_plans(symbol: str, one_minute: pd.DataFrame, config: ScenarioConfig):
    five_frame=resample(one_minute,5)
    fifteen_frame=resample(one_minute,15)
    sixty_frame=resample(one_minute,60)
    five=candles(five_frame)
    fifteen=candles(fifteen_frame)
    sixty=candles(sixty_frame)
    engine=EasyChartScenarioEngine(symbol,config)
    plans=[]

    # Higher-timeframe state is updated only when each 60m bar and any pivot it
    # confirms are fully observable.  It is a router, not another mandatory
    # copy of the 5m trigger.
    sixty_cursor=0
    fifteen_events=[]
    for i in range(len(fifteen)):
        pivot=confirmed_pivot(fifteen,i,config.pivot_span_15m)
        if pivot is not None:
            fifteen_events.append((fifteen[pivot.observed_index].ts_close_ns,pivot))
    event_cursor=0

    for i in range(len(five)):
        while sixty_cursor < len(sixty) and sixty[sixty_cursor].ts_close_ns <= five[i].ts_close_ns:
            context_pivot=confirmed_pivot(sixty,sixty_cursor,config.pivot_span_60m)
            if context_pivot is not None:
                engine.add_context_pivot(context_pivot)
            engine.update_context_close(sixty[sixty_cursor].close)
            sixty_cursor += 1
        while event_cursor<len(fifteen_events) and fifteen_events[event_cursor][0] <= five[i].ts_close_ns:
            _,pivot=fifteen_events[event_cursor]
            if config.use_15m_liquidity:
                engine.add_confirmed_pool(15,pivot,fifteen)
            event_cursor+=1
        pivot=confirmed_pivot(five,i,config.pivot_span_5m)
        if pivot is not None and config.use_5m_liquidity:
            engine.add_confirmed_pool(5,pivot,five)
        plans.extend(engine.on_five_minute_close(five,i))
    return plans,dict(engine.diagnostics),five_frame


def run(args):
    output=args.output.resolve(); output.mkdir(parents=True,exist_ok=True)
    start=date.fromisoformat(args.start); end=date.fromisoformat(args.end)
    build_start=start-timedelta(days=args.warmup_days)
    config=ScenarioConfig(
        pivot_span_5m=args.pivot_span_5m,
        pivot_span_15m=args.pivot_span_15m,
        pivot_span_60m=args.pivot_span_60m,
        confirmation_window_bars=args.confirmation_window_bars,
        use_5m_liquidity=not args.no_5m_liquidity,
        use_15m_liquidity=not args.no_15m_liquidity,
        require_reclaim=not args.allow_touch_only,
        require_htf_alignment=args.require_htf_alignment,
        enable_sweep_reversal=not args.disable_sweep,
        enable_break_retest=not args.disable_break,
        enable_direct_sweep_retest=args.direct_sweep,
        enable_direct_break_retest=args.direct_break,
        min_body_ratio=args.min_body_ratio,
        min_gross_rr=1.0,
    )
    costs=CostAssumptions(
        entry_fee_bps=args.entry_fee_bps,
        stop_fee_bps=args.stop_fee_bps,
        target_fee_bps=args.target_fee_bps,
        entry_slippage_bps=args.entry_slippage_bps,
        stop_slippage_bps=args.stop_slippage_bps,
        target_slippage_bps=args.target_slippage_bps,
        expected_funding_bps=args.expected_funding_bps,
    )
    specs={s:InstrumentSpec(s,c.tick_size,c.size_increment,c.min_quantity,c.min_notional) for s,c in CONTRACTS.items()}
    sim=ContinuousAccountSimulator(
        starting_nav=args.starting_nav,
        specs=specs,
        costs=costs,
        leverage=1_000_000_000_000.0,
        default_funding_rate=args.default_funding_rate,
    )
    data={}; all_plans=[]; diagnostics={}
    for symbol in SYMBOLS:
        one=load_range(symbol,build_start,end,args.cache.resolve())
        data[symbol]=one
        symbol_config=ScenarioConfig(**{**asdict(config),"tick_size":CONTRACTS[symbol].tick_size})
        plans,diag,_=build_plans(symbol,one,symbol_config)
        plans=[p for p in plans if p.observed_time_ns >= int(pd.Timestamp(start,tz="UTC").value)]
        all_plans.extend(plans); diagnostics[symbol]=diag
    all_plans.sort(key=lambda p:(p.observed_time_ns,-p.gross_rr,p.symbol,p.plan_id))
    plan_cursor=0
    # Batch all four symbols by timestamp so a lexical symbol order cannot
    # decide a simultaneous entry conflict.
    grouped: dict[int, dict[str, MinuteBar]] = {}
    for symbol,frame in data.items():
        selected=frame[(frame.open_time_dt>=pd.Timestamp(start,tz="UTC")) & (frame.open_time_dt<pd.Timestamp(end+timedelta(days=1),tz="UTC"))]
        for row in selected.itertuples(index=False):
            close_ns=int(row.close_time_dt.value)
            grouped.setdefault(close_ns,{})[symbol]=MinuteBar(
                symbol=symbol,
                ts_open_ns=int(row.open_time_dt.value),
                ts_close_ns=close_ns,
                open=float(row.open),
                high=float(row.high),
                low=float(row.low),
                close=float(row.close),
            )
    for close_ns in sorted(grouped):
        batch=grouped[close_ns]
        earliest_open=min(bar.ts_open_ns for bar in batch.values())
        while plan_cursor<len(all_plans) and all_plans[plan_cursor].observed_time_ns < earliest_open:
            sim.add_plans([all_plans[plan_cursor]]); plan_cursor+=1
        sim.on_timestamp(batch)
    days=(end-start).days+1
    metrics=sim.metrics(days)
    metrics.update({
        "candidate":"candidate-easychart",
        "evaluation_start":str(start),"evaluation_end":str(end),
        "fixed_contract":{
            "risk_fraction":0.03,"min_gross_rr":1.0,"full_position_stop_market":True,"single_full_target":True,
            "partial_entries":False,"partial_stops":False,"partial_targets":False,"daily_loss_limit":None,"trade_count_limit":None,
            "global_entry_or_position_limit":1,
        },
        "scenario_config":asdict(config),"costs":asdict(costs),"scenario_diagnostics":diagnostics,"plans_generated":len(all_plans),
        "target_gate":{
            "min_geometric_daily_growth":0.01,
            "min_completed_trades":days,
            "passed":(
                metrics["geometric_daily_growth"]>=0.01
                and metrics["trades"]>=days
                and metrics["ending_nav"]>0
                and not metrics["open_position_at_end"]
            ),
        },
    })
    (output/"metrics.json").write_text(json.dumps(metrics,indent=2,sort_keys=True,allow_nan=False)+"\n")
    pd.DataFrame(sim.trade_rows()).to_csv(output/"trades.csv",index=False)
    pd.DataFrame(sim.equity).to_csv(output/"equity.csv",index=False)
    pd.DataFrame([asdict(p) for p in all_plans]).to_csv(output/"plans.csv",index=False)
    events=[]
    for plan in all_plans:
        events.append({
            "scenario_id":plan.causal_event_id,
            "instrument_id":plan.symbol,
            "event_type":"PLAN_CREATED",
            "event_time_ns":plan.observed_time_ns,
            "observed_time_ns":plan.observed_time_ns,
            "previous_state":"CONFIRMATION_FORMED",
            "next_state":"RETEST_ARMED",
            "reason_code":plan.family,
            "reference_price":str(plan.entry),
            "details":asdict(plan),
        })
    for trade in sim.trade_rows():
        events.append({
            "scenario_id":trade["causal_event_id"],
            "instrument_id":trade["symbol"],
            "event_type":"POSITION_CLOSED",
            "event_time_ns":trade["exit_time_ns"],
            "observed_time_ns":trade["exit_time_ns"],
            "previous_state":"POSITION_OPEN",
            "next_state":"CLOSED",
            "reason_code":trade["outcome"],
            "reference_price":str(trade["exit"]),
            "details":trade,
        })
    events.sort(key=lambda item:(item["observed_time_ns"],item["instrument_id"],item["event_type"]))
    (output/"scenario_events.jsonl").write_text(
        "".join(json.dumps(item,sort_keys=True,default=str)+"\n" for item in events),
        encoding="utf-8",
    )
    (output/"run.json").write_text(json.dumps({"candidate":"candidate-easychart","engine":"FAST_DIAGNOSTIC_NOT_AUTHORITATIVE","config":vars(args)},indent=2,sort_keys=True,default=str)+"\n")
    print(json.dumps(metrics,indent=2,sort_keys=True,allow_nan=False))
    return metrics


def main():
    p=argparse.ArgumentParser()
    p.add_argument("--start",required=True); p.add_argument("--end",required=True); p.add_argument("--cache",type=Path,required=True); p.add_argument("--output",type=Path,required=True)
    p.add_argument("--warmup-days",type=int,default=14); p.add_argument("--starting-nav",type=float,default=100000.0)
    p.add_argument("--pivot-span-5m",type=int,default=2); p.add_argument("--pivot-span-15m",type=int,default=2); p.add_argument("--pivot-span-60m",type=int,default=2); p.add_argument("--confirmation-window-bars",type=int,default=2); p.add_argument("--min-body-ratio",type=float,default=1.0)
    p.add_argument("--no-5m-liquidity",action="store_true"); p.add_argument("--no-15m-liquidity",action="store_true"); p.add_argument("--allow-touch-only",action="store_true"); p.add_argument("--require-htf-alignment",action="store_true"); p.add_argument("--disable-sweep",action="store_true"); p.add_argument("--disable-break",action="store_true"); p.add_argument("--direct-sweep",action="store_true"); p.add_argument("--direct-break",action="store_true")
    p.add_argument("--entry-fee-bps",type=float,default=7.5); p.add_argument("--stop-fee-bps",type=float,default=7.5); p.add_argument("--target-fee-bps",type=float,default=7.5)
    p.add_argument("--entry-slippage-bps",type=float,default=0.0); p.add_argument("--stop-slippage-bps",type=float,default=2.5); p.add_argument("--target-slippage-bps",type=float,default=0.0); p.add_argument("--expected-funding-bps",type=float,default=1.0); p.add_argument("--default-funding-rate",type=float,default=0.0001)
    args=p.parse_args(); run(args)
if __name__=="__main__": main()
