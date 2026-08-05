# Recorded results

## Discovery week — RUNTIME FAILURE

- implementation commit: `89667e338679293ad6fc6096aac45d266f04c04d`
- workflow: https://github.com/umtong/SMC_ICT_4_LIVE/actions/runs/30986265960

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
  File "/workspace/research/candidate-01/nautilus_backtest.py", line 591, in run_nautilus_backtest
    bars = BarDataWrangler(bar_type, instrument).process(wrangle)
  File "nautilus_trader/persistence/wranglers.pyx", line 771, in nautilus_trader.persistence.wranglers.BarDataWrangler.process
  File "nautilus_trader/persistence/wranglers.pyx", line 779, in nautilus_trader.persistence.wranglers.BarDataWrangler._build_bar
  File "<stringsource>", line 673, in View.MemoryView.memoryview_cwrapper
  File "<stringsource>", line 360, in View.MemoryView.memoryview.__cinit__
ValueError: buffer source array is read-only
```
