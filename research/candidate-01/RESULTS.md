# Recorded results

## Discovery week — RUNTIME FAILURE

- implementation commit: `b97fe0998b5693d332a027dd0acf1e95d229eead`
- workflow: https://github.com/umtong/SMC_ICT_4_LIVE/actions/runs/30986585515

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
  File "/workspace/research/candidate-01/nautilus_backtest.py", line 603, in run_nautilus_backtest
    raise RuntimeError("wrangle frame unexpectedly exposes a read-only values buffer")
RuntimeError: wrangle frame unexpectedly exposes a read-only values buffer
```
