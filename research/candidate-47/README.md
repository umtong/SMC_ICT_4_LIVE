# Candidate 47 — leader transfer and failed-reentry router

Candidate 47 reuses, rather than rewrites, three existing layers:

1. Candidate 35's four-symbol, one-account NautilusTrader execution/account shell;
2. Candidate 39's passive structural-retest LIMIT entry and cost-after reward-space gate;
3. Candidate 41's unexecuted cross-asset leader-continuation and mature failed-reentry router.

The inherited Candidate 35c alpha is rejected and receives no performance credit.
Candidate 47 must earn evidence through its own four-symbol continuous-account runs.

The two independent scenario families are:

- `LEADER_FIRST_PULLBACK_CONTINUATION`: a fresh 15-minute cross-asset leader reprices,
  survives its first shallow six-minute pullback, and resumes with new initiative;
- `MATURE_EXTENSION_FAILED_REENTRY`: a mature one-hour trend probes external value,
  cracks back inside, fails its re-entry attempt, and resumes away from the failed boundary.

Both use one stable causal-episode identifier, one global pending-entry/position slot,
current-NAV 3% planned-loss sizing, realistic costs, and NautilusTrader orders/fills/NAV.

Current status: implementation adopted; no Candidate 47 performance claim until the
branch workflows produce reproducible metrics.
