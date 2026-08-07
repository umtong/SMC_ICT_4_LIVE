# v104 execution plumbing contract

## Current verified baseline

The repository's registered router workflow successfully executed v103 through the prebuilt environment and NautilusTrader 1.230.0 at commit `8652dca31f3113b12f801f66ac0a7e443c2b8165`. Therefore v103's negative first-week result is performance evidence, not a clone or registration failure.

## v104 trigger and exact-source contract

v104 reuses the already registered `.github/workflows/candidate-02-v77-terminal-router.yml` path. The workflow is changed last, and its own push is the only trigger path. It checks out the exact triggering `research/candidate-02` commit, verifies `git rev-parse HEAD == GITHUB_SHA`, then runs the prospectively locked source hashes. Compact evidence commits do not retrigger the workflow because only the router file is in the push path filter.

The job then:

1. verifies the prebuilt environment with `smc4 doctor`;
2. compiles the core, activation adapter, backtest wrapper and driver;
3. runs causal/activation tests before collecting v104 data;
4. downloads official public futures and spot archives for the locked window;
5. builds completed-minute causal features;
6. runs the baseline through NautilusTrader 1.230.0;
7. runs the one fixed equal-swing-family ablation only if the baseline fails;
8. writes signal-funnel, activation rejection, trade MFE/MAE, attribution, cost, risk and NAV diagnostics;
9. commits only compact evidence if the branch has not moved;
10. uploads the complete run artifact regardless of pass/fail.

Raw archives and bulky engine reports are not committed. Their URLs, sizes and SHA-256 values remain in data manifests and workflow artifacts.

## Bar execution timing correction

NautilusTrader processes a bar's OHLC execution path before dispatching `on_bar`; a zero-latency order submitted from `on_bar` can then settle against the close state. v104 therefore schedules its signal one full minute after the decision and treats the activation bar as already elapsed. Before submitting an order it rejects any activation bar which traversed the exchange-rounded stop, natural target, or old-range structural invalidation. Target eligibility must be no later than the prior decision close, expiry must cover activation, and boundary/delivery/cost-after-RR checks are repeated with the actual activation close and executable tick-rounded prices.

## Portfolio limit

The current prospective screens contain only BTC, so pending entry plus open position is at most one. Before adding ETH, SOL or XRP, the shared execution layer must implement one portfolio-wide gate; four independent strategy-local busy checks would violate the project contract.

## Dependency immutability

The prospective lock includes the exact Git blobs for the reused v53 NautilusTrader runner/strategy/core, the common risk-sizing core, the v75 futures collector/feature builder, and the v89 spot collector/cross-market augmenter. The driver verifies every dependency before collection so a later upstream edit cannot silently change the locked experiment.
