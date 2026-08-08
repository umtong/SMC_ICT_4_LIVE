# Candidate 18 v3 explicit PARTIAL OTO failure

Candidate 18 v3 explicitly set `BacktestVenueConfig.oto_trigger_mode=PARTIAL`.
The frozen NautilusTrader 1.230.0 venue reported the requested mode, but the
same untouched IOC partial-fill failure remained. On 2023-10-16..22 the first
36.790 BTC parent filled 14.288 BTC; its order list contained no surviving stop
or target after the parent remainder canceled. The position again exited only
at the 180-minute forced close for a 9.0738% NAV loss.

The result proves that configuration intent is not execution evidence. v3 is
retained and retired. Candidate 18 v4 removes the parent/child dependency:
entry is standalone and each real fill creates independent reduce-only
protective orders.
