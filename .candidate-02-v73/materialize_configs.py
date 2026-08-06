"""Materialize prelocked v73 boundary-horizon configs."""
from __future__ import annotations
from pathlib import Path
import argparse,json

def main()->None:
    p=argparse.ArgumentParser();p.add_argument("--lock",type=Path,required=True);p.add_argument("--output",type=Path,required=True);a=p.parse_args();lock=json.loads(a.lock.read_text())
    if lock["status"]!="LOCKED_REVEALED_WEEK_INVENTORY_DEPTH_FAMILY_BEFORE_NAUTILUSTRADER_RUN" or lock["custom_backtest_engine"] is not False:raise ValueError("invalid v73 lock")
    a.output.mkdir(parents=True,exist_ok=True)
    for v in lock["prelocked_boundary_horizons"]:
        s=dict(lock["base_scenario"]);s["boundary_minutes"]=v["boundary_minutes"]
        cfg={"candidate":f"candidate-02-v73-{v['label'].replace('_','-')}","scenario":s,
             "risk":{"starting_nav_usdt":lock["fixed_risk"]["starting_nav_usdt"],"risk_fraction":lock["fixed_risk"]["risk_fraction"],"quantity_rule":"current NautilusTrader account NAV times 3% divided by expected per-unit stop loss including entry and stop fees, slippage, impact and funding","maximum_notional_cap":None,"score_risk_multiplier":None},
             "costs":dict(lock["fixed_costs"]),
             "validation":{"first_week_start":"2024-10-28","selection_seed":2026080673,"selection_stage":"revealed v66 week; prelocked v73 inventory-depth family","warmup_days":2,"exit_buffer_minutes":60,"log_level":"ERROR","pass_criteria":{"minimum_trades_per_day":lock["pass_criteria"]["minimum_trades_per_day"],"minimum_win_rate":lock["pass_criteria"]["minimum_win_rate"],"minimum_profit_factor":lock["pass_criteria"]["minimum_profit_factor_after_cost"],"minimum_geometric_daily_growth":lock["pass_criteria"]["minimum_geometric_daily_growth_after_cost"],"maximum_drawdown":lock["pass_criteria"]["maximum_mark_to_market_drawdown"]}}}
        path=a.output/f"{v['label']}.json";path.write_text(json.dumps(cfg,indent=2));print(path)
if __name__=="__main__":main()
