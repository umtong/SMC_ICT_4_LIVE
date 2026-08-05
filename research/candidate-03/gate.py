#!/usr/bin/env python3
"""Fail-fast stage gate; it never changes strategy parameters."""
from __future__ import annotations
import argparse,json
from pathlib import Path

def main()->int:
    p=argparse.ArgumentParser();p.add_argument('metrics',type=Path);p.add_argument('--minimum-trades',type=int,default=8)
    p.add_argument('--minimum-win-rate',type=float,default=0.45);p.add_argument('--minimum-daily-growth',type=float,default=0.005)
    p.add_argument('--require-target',action='store_true');args=p.parse_args();m=json.loads(args.metrics.read_text())
    checks={
      'enough_trades':m['trades']>=args.minimum_trades,
      'positive_expectancy':m['mean_net_r']>0,
      'win_rate':m['win_rate']>=args.minimum_win_rate,
      'after_cost_growth':m['daily_geometric_growth']>=args.minimum_daily_growth,
      'drawdown_recoverable':m['max_drawdown']<0.20,
    }
    if args.require_target:checks['target_met']=bool(m['target_met'])
    print(json.dumps({'checks':checks,'metrics':{k:m[k] for k in ('trades','win_rate','mean_net_r','daily_geometric_growth','max_drawdown','target_met')}},indent=2))
    return 0 if all(checks.values()) else 1
if __name__=='__main__':raise SystemExit(main())
