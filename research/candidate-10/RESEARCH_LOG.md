# Candidate 10 Research Log

This log separates implementation failures from strategy-logic results. It is updated only with reproducible workflow evidence.

## 2026-08-06 — Environment/API probe

### Implementation error I-001: prebuilt executable not found

- Run: GitHub Actions `31084417605`.
- Symptom: `smc4: command not found`.
- Controlled diagnosis: the image declared `/opt/smc4/.venv/bin` in `PATH`, but `bash -lc` replaced the image path through login-shell initialization.
- Fix: use `bash -c` so the pinned image environment is preserved.
- Strategy logic affected: no.

### Implementation error I-002: reflection stopped at Cython type

- Run: GitHub Actions `31084605339`.
- Symptom: `inspect.signature(FillModel)` raised `ValueError`.
- Controlled diagnosis: Cython/PyO3 built-in classes do not always expose a Python signature.
- Fix: isolate reflection failures and read constructor documentation without aborting the remaining probe.
- Strategy logic affected: no.

### Probe success

- Run: GitHub Actions `31084732028`.
- Confirmed: Python 3.13.5, NautilusTrader 1.230.0, bracket orders, contingent order-list submission, portfolio equity, deterministic fill model, bar construction, and BTC perpetual metadata.

## 2026-08-06 — Candidate v0, first BTC gate

### Initial full run before ablation completion

- Run: GitHub Actions `31086398270`, commit `673170ae184533d0fc3eaa865fc32683e452037b`.
- Week: `2023-10-16` through `2023-10-22`, selected before performance was viewed.
- Data: 11,520 verified Binance 1-minute bars including one warm-up day; zero gaps and zero duplicate timestamps.
- Full result: 12 trades, 4 wins, 8 losses, NAV `100,000 → 89,434.54418106`, net `-10.5655%`, geometric daily `-1.5825%`, intraday MDD `17.8373%`.
- Interpretation status at that point: provisional because the required ablation had not completed.

### Implementation error I-003: second Nautilus engine logger initialization

- Run: GitHub Actions `31086398270`.
- Symptom: after the full variant completed, the process aborted before the ablation with `attempted to set a logger after the logging system was already initialized`.
- Controlled diagnosis: NautilusTrader 1.230 initializes a process-global Rust logger. Creating the ablation engine in the same Python process attempted a second logger installation.
- Fix: run every variant in a fresh child process while preserving the same week, data, parameters, seed, cost model, and execution assumptions.
- Strategy logic affected: no.
- Required rerun: the same `2023-10-16` week for both variants.

### Controlled rerun and required ablation

- Run: GitHub Actions `31086615230`.
- Commit: `ac6ffbb2e79dfe32997572fb9438a73d632c2791`.
- Workflow conclusion: success.
- Unit tests: 4/4 passed.
- Data integrity: 11,520 bars, zero gaps, zero duplicates, all source checksums verified.

Full candidate:

- 12 closed trades; 4 wins, 8 losses; no order errors.
- NAV `100,000 → 89,434.54418106`.
- Net return `-10.5655%`.
- Geometric daily growth `-1.5825%`.
- Intraday MDD `17.8373%`.
- Acceptance net PnL `-8,778.36678844 USDT`.
- Rejection net PnL `-1,787.08903050 USDT`.
- Price PnL after modeled slippage, before commissions: `+11,932.6572 USDT`.
- Reported commissions/cost reserve: `22,498.1130 USDT`.

One-variable `ablation-no-acceptance`:

- Changed only `enable_acceptance=False`.
- 12 closed trades; 3 wins, 9 losses; no order errors.
- NAV `100,000 → 85,513.52615303`.
- Net return `-14.4865%`.
- Geometric daily growth `-2.2108%`.
- Intraday MDD `14.4865%`.
- Rejection net PnL `-14,486.47384697 USDT`.
- Price PnL after modeled slippage, before commissions: `+5,501.1057 USDT`.
- Reported commissions/cost reserve: `19,987.5795 USDT`.

### Logic conclusion L-001: v0 discarded

The acceptance path was not the sole failure because removing it worsened the result. v0 had some aggregate directional information before commissions, but its economic implementation was not tradeable.

Dominant causes:

1. confirmation was chased with a market parent;
2. event-extreme stops were too narrow relative to BTC price and executable round-trip cost, creating excessive quantity and turnover;
3. target eligibility used raw price reward/risk rather than net executable reward/risk; and
4. every previous fixed four-hour high/low was assumed to be meaningful liquidity without structural confirmation.

Valid part retained:

- The causal raid → rejection/acceptance → displacement sequence produced positive aggregate price PnL before commissions in both variants.
- That evidence supports retaining the state-transition framework for one structural execution revision, but it does not qualify as candidate success.

Full evidence is preserved in `V0_FAILURE.md`.

## 2026-08-06 — Candidate v1 structural revision

### Predeclared change

v1 changes execution grammar rather than adding a fitted filter:

- rejection parent rests at the 61.8% displacement retrace;
- acceptance parent rests at the accepted boundary;
- parent and target are post-only limits; stop is stop-market;
- pending parent expires or cancels on structural invalidation and scheduled flat windows;
- stop buffer is outside both robust event noise and an executable maker-entry/taker-stop cost floor;
- target eligibility uses declared costs and tick reserve in net reward/risk;
- data, week, seed, 3% NAV risk, source checksums, Nautilus engine, and one-variable acceptance ablation remain controlled.

### Decision rule

- An implementation or callback failure is fixed and rerun on `2023-10-16`.
- A completed v1 run is judged as strategy logic.
- If v1 still lacks strong cost-after expectancy, the fixed four-hour pool generator is discarded rather than parameter-tuned. The next structural hypothesis must use causally confirmed swing/equal-high/equal-low liquidity while preserving the tested execution grammar.

## Current gate status

v0 is discarded. v1 is under controlled first-week execution. No promotion claim has been made.
