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

## Gate result

Pending the first complete NautilusTrader run. No performance claim is made before `metrics.json`, order reports, event-log validation, and the required one-variable ablation are complete.
