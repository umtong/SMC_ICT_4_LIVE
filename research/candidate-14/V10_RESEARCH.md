# Candidate 14 v10 — Failure-Leg Leadership Window

## Retained state

V9 correctly reduced v8's 126 pseudo-failure trades to 60 completed accepted auctions, 52 later failures, 18 rescinds and 29 later reversal initiatives. It executed none of those 29 because the generic FAR market gate measured each candidate from the original source sweep.

That time window belongs mainly to the completed opposite-direction acceptance leg. It is not the causal window of the new reversal.

## One controlled change

```text
confirmed accepted-auction failure plan:
    market window start = acceptance_failure_ts_ns
    market window end   = later initiative / plan observation

all other plans:
    market window start = original sweep timestamp
```

The existing peer alignment, event efficiency, standardized displacement, event rank and trailing-auction rules are unchanged. The accepted-auction completion/failure/reversal state, entry, stop, target, costs, exact current-NAV 3% sizing, Session I7 and global slot are unchanged.

## Evidence role

The 84-day L1 path is already inspected and is used only to isolate the causal measurement boundary. No result from this branch can establish success. A surviving mechanism must be frozen before a new continuous interval is selected and collected.
