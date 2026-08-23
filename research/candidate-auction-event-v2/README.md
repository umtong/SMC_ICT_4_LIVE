# Candidate auction-event policy v2

A direct causal replacement for legacy plan filtering. It emits one independent episode
when a pre-existing 60/240-minute range or confirmed 5/15/60-minute pivot is either
swept and reclaimed or broken and held by the next completed minute. Derivatives open
interest and positioning metrics are lagged by one native sample. Exact one-minute path
features preserve effort/result and acceptance/rejection sequencing. Entry is the next
minute open; event invalidation and all targets are immutable before entry.
