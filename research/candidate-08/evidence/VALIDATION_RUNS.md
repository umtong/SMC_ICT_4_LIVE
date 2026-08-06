# Validation run ledger

This ledger records immutable GitHub Actions run IDs and the reason for every rerun.

| run ID | commit | result | interpretation |
|---|---|---|---|
| 31076025364 | 1498a0bcfeb25908e91ab9ce56d293945923af80 | infrastructure failure | pinned container user could not write the GitHub temporary checkout directory; no candidate code ran |
| 31076099726 | bec1708115cd56eafb6c8fc0ba6b81c0208629a4 | implementation failure | `smc4 doctor`, compilation, and seven causal/risk tests passed; runner used an incorrect Nautilus 1.230.0 model import path before data replay |
| 31076483095 | 009cc09c1f9074de71dce03e8b80cf54f02ef380 | data-adapter failure | environment, compilation, seven tests, official Binance archive download, and published checksum verification passed; pandas exposed `DataFrame.values` read-only to the pinned Cython `BarDataWrangler`, so replay stopped before the first engine bar |
| 31076788907 | cb1c2efd3bb6af99fdca659c5667e8aaab332f65 | engine-config failure | official data was converted to Nautilus `Bar` objects; engine construction then rejected the unsupported `BacktestEngineConfig.shutdown_on_error` field before replay |
| 31076904149 | 3c54c673db3be4b89ece67e7c8a1287cf285cfdf | superseded duplicate | the patch trigger itself started validation before the write-enabled patch job committed the fix, so it exercised the same pre-fix source and is not performance evidence |
| 31076976101 | 0bb0d58454a8545c82c26887e42ad6ffd04be5a3 | venue-config failure | engine construction reached `add_venue`; the pinned legacy runtime rejected liquidation keywords exposed by the type stub but absent from the loaded Cython method |
| 31077172125 | bc197c73c8e2088fcf079688de803e79a7078274 | post-replay reporting failure | `smc4 doctor`, seven tests, checksum-verified data, engine/venue setup, and the complete first-window Nautilus replay all succeeded; only obsolete direct-engine report methods failed after `engine.run()` returned |

The execution models now use the pinned official `nautilus_trader.backtest.models` API. The data
adapter constructs the official Nautilus `Bar` type directly from an owned float64 array, retaining
source close time as both event and observation time. The runner uses only fields accepted by the
loaded pinned runtime, and `ReportProvider` now generates orders, fills, positions, and account
reports from the engine cache after replay. Liquidation equivalence must be diagnosed from every
realized position path before this candidate can be promoted; the unsupported direct-engine switch
is not silently claimed as active. Subsequent rows are added only after their exact result is known.
