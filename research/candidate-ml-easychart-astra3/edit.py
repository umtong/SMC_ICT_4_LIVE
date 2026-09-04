from pathlib import Path
p=Path(__file__).with_name('research.py');s=p.read_text()
old='''        engine.run()
        trades=pd.DataFrame(strategy.closed);payments=pd.DataFrame(funding.payments)
'''
new='''        engine.run()
        # Nautilus does not dispatch strategy callbacks after on_stop. A terminal
        # market close still executes; use that actual cached fill, not a made-up
        # mark-to-market trade, and keep it out of natural completed-trade counts.
        pending=strategy.active_plan
        if pending is not None and pending.plan_id not in {x['plan_id'] for x in strategy.closed}:
            actual=[x for x in engine.cache.positions_closed() if x.opening_order_id==strategy.active_entry_id]
            if len(actual)!=1:raise RuntimeError('terminal position has no unique actual completed execution')
            pos=actual[0];row=pending.record();row.update(strategy.open_context[pending.plan_id])
            row.update(position_id=str(pos.id),opened=int(pos.ts_opened),closed=int(pos.ts_closed),
                       entry_fill=float(pos.avg_px_open),exit_fill=float(pos.avg_px_close),quantity=float(pos.peak_qty),
                       pnl_ex_funding=pos.realized_pnl.as_double(),holding_minutes=(pos.ts_closed-pos.ts_opened)/MINUTE,
                       evaluation_censored=True)
            strategy.closed.append(row)
        trades=pd.DataFrame(strategy.closed);payments=pd.DataFrame(funding.payments)
'''
assert s.count(old)==1;s=s.replace(old,new);p.write_text(s)
Path(__file__).unlink()
