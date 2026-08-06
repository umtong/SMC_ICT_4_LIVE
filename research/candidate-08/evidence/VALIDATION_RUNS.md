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
| 31077410620 | 54de04b6f158e3481d67dd546e429ed370fe63cd | invalid timestamp-scale run | cache reports were generated, but artifact inspection proved pandas retained a millisecond-resolution index and raw `.asi8` values were passed to Nautilus as nanoseconds. Bars appeared in 1970, the 2024 evaluation gate blocked all entries, and the zero-trade output is invalid performance evidence |
| 31077616228 | 85cd5654e215aea9ddfeac1fece60621a2afa205 | patch-infrastructure failure | the timestamp patch was first tested outside the pinned image where pandas was absent; source was not committed |
| 31077694294 | e7fb2081afe40a65a18fcbc76b364168136cf565 | validated patch, commit failure | the pinned image passed `smc4 doctor` and all eight causal/risk/timestamp tests; only Git safe-directory configuration prevented the already validated workspace changes from being committed |
| 31077856942 | b75dc3f7854631f16630cdb494d0ed31acb55171 | patch success | the same pinned-image checks and eight tests passed and the explicit epoch-nanosecond conversion plus optional result-statistics handling were committed as `474ff3ae5f9203ee0c021cb4ef6cae6243bedacc` |
| 31077987176 | a98827259323cf8d897f3dd737431c39e29717d5 | first real signal, OrderList adapter failure | the valid 2024 clock produced the first confirmed setup on 2024-04-09 16:08:59.999 UTC; the strategy then treated Nautilus's bracket `OrderList` object as an iterable tuple before any order submission |
| 31078147847 | 635da6f60fa7b2bd8b3cf132631b994eb1c02745 | OrderList patch success | the adapter was changed to unpack `OrderList.orders` while preserving the bracket object passed to `submit_order_list`; the fixed source was committed as `95e7f45b2b4381d7091d27f4ab1ed7873bb8ea60` |

The execution models use the pinned official `nautilus_trader.backtest.models` API. The data adapter
constructs the official Nautilus `Bar` type directly from an owned float64 array, converts any pandas
datetime resolution explicitly to epoch nanoseconds, and retains source close time as both event and
observation time. Reports are generated with `ReportProvider` from the engine cache. The loaded
legacy runtime does not expose the direct liquidation switch present in its type stub, so liquidation
is not silently claimed as active; realized loss and margin paths remain promotion diagnostics.
Subsequent rows are added only after their exact result is known.
