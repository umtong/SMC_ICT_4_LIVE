# VCIB research ledger

## Predeclared hypothesis

A bucket closes only after a volume budget fixed at bucket start from prior completed one-minute volume. Two consecutive same-direction aggressive-flow buckets form one event:

- retained marginal price impact plus extension creates a continuation context;
- collapsing marginal impact without extension creates an exhaustion context.

Neither state is traded immediately. Continuation requires a later midpoint-defending retest and a separate same-direction response. Exhaustion requires a later opposite response through the combined midpoint.

## Fixed comparison

The full candidate uses prior-only marginal-impact quantiles. One ablation removes only impact-efficiency classification while preserving the volume clock, sequential flow, entry response, stop, target, costs, fill model and 3% NAV planned-loss sizing.

BTC week `2024-02-26` is evaluated first through NautilusTrader. The frozen weeks `2024-09-23` and `2024-04-22` open unchanged only after the full first-week gate passes. Valid negative or insufficient Nautilus metrics are a logic failure; syntax, clock, runner and order errors are implementation failures and require the identical week to be rerun.
