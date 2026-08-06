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

## 2026-08-06 — First BTC gate attempt

### Full candidate result before ablation completion

- Run: GitHub Actions `31086398270`, commit `673170ae184533d0fc3eaa865fc32683e452037b`.
- Week: `2023-10-16` through `2023-10-22`, selected before performance was viewed.
- Data: 11,520 verified Binance 1-minute bars including one warm-up day; zero gaps and zero duplicate timestamps.
- Trades: 12 closed, 4 winners, 8 losers, no order denial/rejection.
- Net NAV: `100,000 → 89,434.54418106` (`-10.5655%`).
- Net geometric daily growth: `-1.5825%`.
- Intraday NAV drawdown: `17.8373%`.
- Scenario attribution: acceptance `-8,778.3668 USDT`; rejection `-1,787.0890 USDT`.
- Interpretation status: provisional only. The required acceptance-path ablation did not complete, so this is not yet a strategy-logic conclusion.

### Implementation error I-003: second Nautilus engine logger initialization

- Run: GitHub Actions `31086398270`.
- Symptom: after the full variant completed, the process aborted before the ablation with `attempted to set a logger after the logging system was already initialized`.
- Controlled diagnosis: NautilusTrader 1.230 initializes a process-global Rust logger. Creating the ablation engine in the same Python process attempted a second logger installation.
- Fix: run every variant in a fresh child process while preserving the same week, data, parameters, seed, cost model, and execution assumptions.
- Strategy logic affected: no.
- Required rerun: the same `2023-10-16` week for both full and `ablation-no-acceptance`.

## Gate result

Pending the controlled rerun and completed one-variable ablation. No promotion or discard claim is made yet.
