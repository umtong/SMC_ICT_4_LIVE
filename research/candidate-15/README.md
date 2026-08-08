# Candidate 15 V5 — Timeframe-Consistent Cross-Market Response

Candidate 15 has iterated by removing structural errors rather than fitting PnL
thresholds:

- V1 showed that a resolved local auction state cannot be stored forever.
- V2 made the state a short causal decision lease and exposed a stop moved inside
  the original sweep invalidation.
- V3 enforced scenario-terminal sweep invalidation and exposed a faulty static
  cross-market role decision.
- V4 tested an independent repeated-common-flow continuation family. It supplied
  abundant activity but failed decisively because its five-minute impulse was
  normalized by one-minute ATR and repetition did not require price progress.

The rejected evidence is preserved in `V1_FAILURE.md` through `V4_FAILURE.md`.

## V5 hypothesis

A repeated flow label is useful only when it is measured on the correct timeframe
and converted into common price progress.

```text
first completed 5m common-flow impulse
  standardized by prior completed 5m ATR
                    ↓
              candidate only
                    ↓
second same-direction event within 4h
                    ↓
>=3 common markets + positive median signed progress
+ majority advances + majority holds first origins?
        ↙                                   ↘
      no                                     yes
 UNRESOLVED / NO TRADE          response-qualified initiative
                                                ↓
                                  horizon = event separation
                                                ↓
                           fresh post-activation 5m MSS
                           + displacement + strict FVG
                                                ↓
                               passive CE retracement
                                                ↓
                         protected structural invalidation
                         + live external 4H/day objective
```

The current five-minute range is added to the ATR history only after the event
decision. The state therefore cannot make its own displacement threshold easier.
Its lifetime is the observed time needed for the confirming response, rather than
a fixed four-hour label that refreshes indefinitely.

## Family isolation

V5 evaluates only the response-qualified continuation family. V1--V3 SCDAM
plans and `SESSION_I7` remain observed for causal diagnostics but receive an
explicit terminal no-trade transition. They are not mixed into V5 PnL.

The continuation itself is unchanged from V4 so the experiment isolates the
state-layer correction:

- only a five-minute bar completed after activation may form a leg;
- it must break protected structure with directional displacement and flow;
- a strict three-candle FVG provides a post-only consequent-encroachment entry;
- the stop remains beyond the protected swing or opposing bar;
- the target is the next causally live completed-4H or previous-day external
  liquidity pool;
- every distinct displacement identity is one opportunity and all four markets
  compete through the existing one-global-slot mutex.

## Execution and safety

- NautilusTrader exclusively owns orders, fills, fees, margin, positions and NAV.
- Quantity uses current whole-account NAV and the project 3% planned-loss budget.
- The four markets share at most one pending entry or open position.
- A post-only parent rejected while the account is still flat is recorded as
  unfilled passive non-execution, not an engine fault.
- Any rejection after a fill or while non-flat remains an engine error; remaining
  orders are canceled and exposure is immediately fail-closed.
- No leverage cap, score-based risk multiplier or custom simulator is added.

## Development protocol

E01--E06 are the same exposed diagnostic weeks as V4. Reusing them isolates the
mechanism change, but their result cannot support a success claim. The declared
gate requires sufficient activity and interval breadth, positive post-cost
geometric growth, adequate accuracy and payoff, bounded drawdown, non-concentrated
growth and complete safety.

```bash
for interval in E01 E02 E03 E04 E05 E06; do
  bash research/candidate-15/run_week.sh "$interval"
done
python research/candidate-15/aggregate.py
```

Only a promising exposed-development result permits a source freeze and newly
predeclared confirmation. Long-run success still requires one frozen continuous
account with realistic costs and no liquidation or unrecoverable NAV damage.
