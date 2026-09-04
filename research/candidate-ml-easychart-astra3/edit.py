from pathlib import Path
p=Path(__file__).with_name('research.py');s=p.read_text()
old="        if len(trades) and not engine.cache.positions_open() and abs((nav-starting_nav)-trades.pnl.sum())>.03:\n"
new="""        if len(trades) and abs((nav-starting_nav)-trades.pnl.sum())>.03:
            engine.trader.generate_order_fills_report().tail(20).to_json(path/'last_fills.json',orient='records',date_format='iso',default_handler=str)
            engine.trader.generate_positions_report().tail(8).to_json(path/'last_positions.json',orient='records',date_format='iso',default_handler=str)
            engine.trader.generate_account_report(VENUE).tail(12).to_json(path/'last_account.json',orient='records',date_format='iso',default_handler=str)
            (path/'active_execution.json').write_text(json.dumps({'open_positions':len(engine.cache.positions_open()),'closed_rows':len(trades),'active_plan':str(strategy.active_plan),'active_entry':str(strategy.active_entry_id)},indent=2))
        if len(trades) and not engine.cache.positions_open() and abs((nav-starting_nav)-trades.pnl.sum())>.03:
"""
assert s.count(old)==1;s=s.replace(old,new);p.write_text(s)
Path(__file__).unlink()
