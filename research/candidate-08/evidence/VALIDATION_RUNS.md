# Validation run ledger

This ledger records immutable GitHub Actions run IDs and the reason for every rerun.

| run ID | commit | result | interpretation |
|---|---|---|---|
| 31076025364 | 1498a0bcfeb25908e91ab9ce56d293945923af80 | infrastructure failure | pinned container user could not write the GitHub temporary checkout directory; no candidate code ran |
| 31076099726 | bec1708115cd56eafb6c8fc0ba6b81c0208629a4 | implementation failure | `smc4 doctor`, compilation, and seven causal/risk tests passed; runner used an incorrect Nautilus 1.230.0 model import path before data replay |
| 31076483095 | 009cc09c1f9074de71dce03e8b80cf54f02ef380 | data-adapter failure | environment, compilation, seven tests, official Binance archive download, and published checksum verification passed; pandas exposed `DataFrame.values` read-only to the pinned Cython `BarDataWrangler`, so replay stopped before the first engine bar |

The execution models now use the pinned official `nautilus_trader.backtest.models` API. The data
adapter now constructs the same official Nautilus `Bar` type directly from an owned float64 array,
retaining source close time as both event and observation time and avoiding a pandas writable-buffer
assumption. Subsequent rows are added only after their exact result is known.
