# Candidate 11 market-leadership research

## Evidence that motivated the hypothesis

The unchanged four-market SCDAM completed three frozen NautilusTrader weeks:

- W1: five closed trades, four wins, one loss, +3.1313% daily geometric NAV growth.
- W2: eleven closed trades, five wins, six losses, -2.2826% daily geometric NAV growth.
- W3: one closed trade, one loss, -3.0106% daily geometric NAV growth.

Those results reject durable generalization of the original portfolio candidate.
The synchronized complex candidate also failed: seventeen positions, three wins,
fourteen losses, and -9.5998% net NAV return on W1.

The seventeen actual original-portfolio fills were joined to their source sweep,
plan confirmation, peer-market bars, Nautilus orders, positions, and account NAV.
There were nine wins and eight losses. Six losses had less than 0.46R favorable
excursion and were direction failures; two reached more than 1R before stopping.
Lowering structural R, widening stops, using nearer weak targets, fixed response
confirmation, and static breadth thresholds did not separate winners from losses.

A causal diagnostic did separate the historical fills:

1. determine the price-discovery leader at the sweep from trailing completed
   24-hour quote notional, without hard-coding BTC;
2. allow the leader to express idiosyncratic FAR or AAC;
3. require a follower FAR to have all three peers move in the proposed reversal
   direction between the sweep and plan confirmation;
4. reject follower AAC, because acceptance should originate in the current
   price-discovery leader.

Applied only as a retrospective diagnostic, this retained seven historical wins
and rejected all eight historical losses. This is not validation: W1-W3 informed
the hypothesis and are in-sample diagnostics.

## Controlled change

The SCDAM sweep, session map, FAR/AAC state machines, structural target, stop,
post-only GTD entry, stop-market protection, fees, slippage assumptions, exact
3% current-NAV loss sizing, and global one-entry/one-position mutex are unchanged.
The new layer only approves or abstains after a complete causal plan. It never
changes quantity or risk.

Leadership uses the completed one-minute observations available at the original
sweep. Peer movement uses exact synchronized completed closes at the sweep and
confirmation. Missing history, missing peer snapshots, asynchronous timestamps,
or disagreement fail closed.

## Precommitted evaluation

W1-W3 are diagnostic and cannot validate this hypothesis. Before downloading new
market data, `random.Random(2026080711)` selected the first three non-overlapping
seven-day starts in 2023-01-01 through 2025-12-25, excluding W1-W3:

- W4: 2023-11-18 through 2023-11-25 exclusive;
- W5: 2024-07-24 through 2024-07-31 exclusive;
- W6: 2025-04-05 through 2025-04-12 exclusive.

W1 is rerun only for implementation regression. W4 is the first untouched test.
W5 is unlocked only if W4 passes the existing promising gate; W6 is unlocked only
if W5 passes. Nautilus account NAV is authoritative. A normally completed weak
week is a logic failure and is not repaired by changing the fixed intervals.
