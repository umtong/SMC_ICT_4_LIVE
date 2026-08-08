# Candidate 14 v7 — Owned AAC Reacceleration Entry

## Retained result from v6

The 84-day controlled v6 replay isolated a real categorical defect. Requiring ordinary FAR to originate from an exclusively rejection-framed auction removed all seven mixed-origin FAR losses. The account changed from 15 trades, 3 wins and 12 losses with final NAV 80,737.94 USDT to 8 trades, 3 wins and 5 losses with final NAV 100,407.07 USDT. This is mechanism evidence, not a success claim: frequency and growth remained far below the project objective.

The remaining SCDAM structure was exact:

- exclusive-origin FAR: 3 trades, 3 wins;
- filled AAC: 3 trades, 3 losses;
- Session I7: 2 trades, 2 losses.

## One v7 change

The AAC detector already requires this completed sequence:

```text
external acceptance
→ at least two outside closes
→ causal defended pullback pivot
→ later reacceleration beyond the frozen impulse extreme
```

The inherited execution then places a second passive order at the same defended pullback. That order does not merely improve price. Its fill requires the market to undo the just-confirmed reacceleration and revisit a pivot which has already served its causal role. The fill population is therefore selected toward weakening or failed acceptance.

V7 assigns entry ownership to the reacceleration leg itself:

```text
completed reacceleration close
→ unchanged source-boundary / pullback invalidation
→ unchanged live external objective
→ unchanged complete taker-entry + taker-stop + maker-target economics
    ├─ costed structural R qualifies: Nautilus MARKET parent
    └─ does not qualify: terminal, no passive fallback
```

No new magnitude threshold, session, symbol, target, stop boundary, risk multiplier or execution simulator is introduced.

## Evidence role

The same `2026-05-11` through `2026-08-03` account path is intentionally reused to isolate the known AAC execution mechanism. It is development diagnostic data and is permanently ineligible for a success claim. Any surviving system must be frozen before a newly reserved continuous interval is collected.
