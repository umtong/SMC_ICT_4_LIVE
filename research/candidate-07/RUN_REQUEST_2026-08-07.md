# Candidate-07 validation request — 2026-08-07

GitHub Actions throughput has recovered after the 2026-08-06 incident. This commit intentionally re-triggers the frozen BTC Week-1 event-time tournament without changing market logic, thresholds, risk, data contracts, or evaluation periods.

Required order:

1. `smc4 doctor`, compile, and causal unit tests.
2. Fixed-time aggregate-trade impact-resilience baseline.
3. Remove only the OI-release condition after an implementation-clean logic failure.
4. Volume-time impact-resilience baseline and its single OI ablation only if prior variants fail.
5. A structural pass is permission for immediate NautilusTrader execution implementation, not a success claim.

No Week-2, Week-3, or long evaluation is authorized before a complete Week-1 project-gate pass.
