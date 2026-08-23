# Candidate 4t v4 — hardened causal feature contract

Version 4 uses the v3 policy and replaces its defensive feature audit with an exact-column contract. The first audit intentionally rejected future/outcome fields, but substring matching could confuse a causal market feature such as `swing_*` with the outcome column `win`. Version 4 rejects exact outcome columns and explicit `label_`, `future_`, `actual_` and `diagnostic_` prefixes while retaining legitimate structure features.

The model and trading decision are otherwise unchanged from v3. This is an implementation correction, not a new alpha claim.
