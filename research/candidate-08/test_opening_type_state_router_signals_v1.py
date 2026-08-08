"""Causal contracts for opening-type state router V1."""
from __future__ import annotations
import unittest
import numpy as np
import pandas as pd
from opening_initial_balance_failed_auction_signals_v1 import InitialBalance
from opening_type_state_router_signals_v1 import build_opening_type_state_router_signals, classify_opening_state
from range_fvg_logic import FiveMinuteBar
from session_value_migration_signals_v1 import DailyValueProfile


def bar(i, start, o, h, l, c, volume=100.0, atr=1.0):
    return FiveMinuteBar(i, int((start+pd.Timedelta(minutes=5)-pd.Timedelta(milliseconds=1)).value),
        o,h,l,c,volume,100.0,50.0,0.0,atr,1.0,1.0,0.0,0,
        str(start.floor('4h')), str(start.floor('1D')), str((start-pd.Timedelta(days=start.weekday())).floor('1D')))


def profile():
    start=pd.Timestamp('2025-08-17T00:00:00Z')
    return DailyValueProfile(int(start.value),'2025-08-17',int((start+pd.Timedelta(days=1)-pd.Timedelta(milliseconds=1)).value),
        100.0,1.0,99.0,101.0,2.0,97.0,103.0,97.5,102.5,1000.0)


def balance(rows):
    start=pd.Timestamp('2025-08-18T00:00:00Z')
    return InitialBalance('ASIA','UTC','2025-08-18',int(start.value),int((start+pd.Timedelta(minutes=30)).value),
        int((start+pd.Timedelta(hours=3,minutes=30)).value),max(x.high for x in rows),min(x.low for x in rows),0,5)


def opening(kind):
    start=pd.Timestamp('2025-08-18T00:00:00Z'); rows=[]
    for i in range(6):
        if kind=='drive': vals=(102.0 if i==0 else 102.5,104.0,101.2,103.0)
        elif kind=='test': vals=(102.0 if i==0 else 102.4,104.0,100.8 if i==1 else 101.2,103.0)
        elif kind=='reject': vals=(102.0 if i==0 else 100.4,102.4,99.4,100.2)
        else: vals=(100.0,101.0,99.0,100.2)
        rows.append(bar(i,start+pd.Timedelta(minutes=5*i),*vals))
    return tuple(rows)


def full_bars():
    starts=pd.date_range('2025-08-17T00:00:00Z','2025-08-18T00:35:00Z',freq='5min'); rows=[]
    for i,start in enumerate(starts):
        if start < pd.Timestamp('2025-08-18T00:00:00Z'):
            x=99.5 if i%2==0 else 100.5; rows.append(bar(i,start,x-.05,x+.2,x-.2,x+.05))
        elif start < pd.Timestamp('2025-08-18T00:30:00Z'):
            step = start.minute // 5
            base = 102.0 + 0.5 * step
            rows.append(bar(i, start, base, base + 0.6, base - 0.2, base + 0.4))
        else:
            rows.append(bar(i, start, 105.3, 106.2, 105.2, 106.0))
    return tuple(rows)


def execution(extra=0):
    idx=pd.date_range('2025-08-17T23:00:10Z',pd.Timestamp('2025-08-18T00:40:00Z')+pd.Timedelta(seconds=10*extra),freq='10s')
    close=np.full(len(idx),106.1)
    return pd.DataFrame({'open':close,'high':close+.05,'low':close-.05,'close':close,'volume':10.0},index=idx)


class Contracts(unittest.TestCase):
    def test_state_router_separates_opening_types(self):
        p=profile(); got=[]
        for kind in ('drive','test','reject','inside'):
            rows=opening(kind); state=classify_opening_state(rows,balance(rows),p); got.append(None if state is None else state[:2])
        self.assertEqual(got,[('OPEN_DRIVE',1),('OPEN_TEST_DRIVE',1),('OPEN_REJECTION_REVERSE',-1),None])

    def build(self,extra=0):
        bars=full_bars()
        return build_opening_type_state_router_signals(data=execution(extra),context_times=np.asarray([x.ts_event_ns for x in bars],dtype=np.int64),
            context_bars=bars,snapshots=tuple(() for _ in bars),symbol='BTCUSDT',instrument_id='BTCUSDT-PERP.BINANCE',
            tick=.1,fee_rate=.0006,minimum_net_reward_risk=1.2,router_config={})

    def test_signal_is_after_separate_m5_trigger_and_future_invariant(self):
        left=self.build(); right=self.build(30)
        self.assertEqual(left.diagnostics['TRADEABLE_OPENING_TYPE_SIGNAL'],1)
        a=next(iter(next(iter(left.signals_by_time_ns.values())))); b=next(iter(next(iter(right.signals_by_time_ns.values()))))
        trigger=int(full_bars()[-2].ts_event_ns)
        self.assertTrue(trigger < a.signal_time_ns <= trigger+11_000_000_000)
        self.assertEqual(a.details['opening_type'],'OPEN_DRIVE'); self.assertEqual(a.direction,1)
        self.assertGreaterEqual(a.net_reward_risk,1.2); self.assertFalse(a.details['ten_second_alpha_inputs'])
        self.assertEqual((a.signal_time_ns,a.entry_reference,a.structural_stop,a.external_target,a.net_reward_risk),
                         (b.signal_time_ns,b.entry_reference,b.structural_stop,b.external_target,b.net_reward_risk))

if __name__=='__main__': unittest.main(verbosity=2)
