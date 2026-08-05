# Recorded results

## Discovery week — RUNTIME FAILURE

- implementation commit: `440d5fae2893dd621dcc90223ba561666905f456`
- workflow: https://github.com/umtong/SMC_ICT_4_LIVE/actions/runs/30987730612

The pinned NautilusTrader discovery run did not produce `aggregate_metrics.json`. No performance claim is made. The final log tail is preserved below and in the workflow artifact.

```text
Traceback (most recent call last):
  File "/workspace/research/candidate-01/run_research.py", line 254, in <module>
    raise SystemExit(run(build_parser().parse_args()))
                     ~~~^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/workspace/research/candidate-01/run_research.py", line 180, in run
    evidence = run_nautilus_backtest(
        label=label,
    ...<5 lines>...
        output_dir=destination,
    )
  File "/workspace/research/candidate-01/nautilus_backtest.py", line 646, in run_nautilus_backtest
    engine.add_venue(
    ~~~~~~~~~~~~~~~~^
        venue=venue,
        ^^^^^^^^^^^^
    ...<9 lines>...
        liquidation_cancel_open_orders=True,
        ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
    )
    ^
  File "nautilus_trader/backtest/engine.pyx", line 502, in nautilus_trader.backtest.engine.BacktestEngine.add_venue
TypeError: add_venue() got an unexpected keyword argument 'liquidation_enabled'
```
