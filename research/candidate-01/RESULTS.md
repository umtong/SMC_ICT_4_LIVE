# Recorded results

## Discovery week — RUNTIME FAILURE

- implementation commit: `b8b0f7b6fc2213ad0fbb8680de241d8e7b25734a`
- workflow: https://github.com/umtong/SMC_ICT_4_LIVE/actions/runs/30985868113

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
  File "/workspace/research/candidate-01/nautilus_backtest.py", line 277, in run_nautilus_backtest
    from nautilus_trader.model import Bar, BarType, CryptoPerpetual
ImportError: cannot import name 'CryptoPerpetual' from 'nautilus_trader.model' (/opt/smc4/.venv/lib/python3.13/site-packages/nautilus_trader/model/__init__.py)
```
