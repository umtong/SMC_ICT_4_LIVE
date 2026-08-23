# Live public inventory integration

`LiveBinanceInventoryCollector` is a read-only Binance USD-M public metrics
adapter. It does not require credentials and does not call trading or
top-trader endpoints.

## Strategy handoff

Create one collector for the four production symbols. Poll it from the
strategy/node event loop shortly after every completed five-minute boundary
(and retry during that slot because the two public endpoints can publish at
different times):

```python
from smc_ict_4.episode_policy_live.live_inventory import LiveInventoryCollector

inventory = LiveInventoryCollector()

results = inventory.poll_all()
for result in results:
    policy = strategy.coordinator.policies[result.symbol]
    # Assign every poll, including None. Never retain the previous timeline
    # when this poll is unavailable, unjoined, invalid, or stale.
    policy.inventory_timeline = result.timeline if result.ready else None
```

Do the assignment on the same event-loop thread that processes strategy bars;
do not mutate policy state from an unmanaged network thread. If fetching runs
in a worker, return the immutable `LiveInventoryPollResult` objects to that
event loop before assignment.

`current(symbol)` performs no network I/O and is safe as a pre-decision
freshness check. It stops exposing a previously ready timeline as soon as the
next completed five-minute slot is expected. `last_result(symbol)` and
`history(symbol)` are diagnostic evidence only and must not be used as a
readiness fallback.

The first successful poll requests eight recent points, enough to seed the
three-change `InventoryTimeline.evaluate()` window. Joined observations reuse
the same `InventoryMetric` and `InventoryTimeline` semantics as historical
replay. Unknown/gap status is evidence, not approval.
