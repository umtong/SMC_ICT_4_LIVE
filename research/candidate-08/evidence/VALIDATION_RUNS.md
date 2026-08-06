# Validation run ledger

This ledger records immutable GitHub Actions run IDs and the reason for every rerun.

| run ID | commit | result | interpretation |
|---|---|---|---|
| 31076025364 | 1498a0bcfeb25908e91ab9ce56d293945923af80 | infrastructure failure | pinned container user could not write the GitHub temporary checkout directory; no candidate code ran |
| 31076099726 | bec1708115cd56eafb6c8fc0ba6b81c0208629a4 | implementation failure | `smc4 doctor`, compilation, and seven causal/risk tests passed; runner used an incorrect Nautilus 1.230.0 model import path before data replay |

The source was then aligned to the pinned official API: `LatencyModel`, `MakerTakerFeeModel`, and
`OneTickSlippageFillModel` are imported from `nautilus_trader.backtest.models`. Subsequent rows are
added only after their exact result is known.
