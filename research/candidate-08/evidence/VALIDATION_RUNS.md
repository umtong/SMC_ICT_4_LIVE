# Validation run ledger

This ledger records immutable GitHub Actions run IDs and the reason for every rerun.

| run ID | commit | result | interpretation |
|---|---|---|---|
| 31076025364 | 1498a0bcfeb25908e91ab9ce56d293945923af80 | infrastructure failure | pinned container user could not write the GitHub temporary checkout directory; no candidate code ran |
| 31076099726 | bec1708115cd56eafb6c8fc0ba6b81c0208629a4 | implementation failure | `smc4 doctor`, compilation, and seven causal/risk tests passed; runner used an incorrect Nautilus 1.230.0 model import path before data replay |
| 31076483095 | 009cc09c1f9074de71dce03e8b80cf54f02ef380 | data-adapter failure | published Binance checksum verification passed; pandas exposed a read-only buffer to the pinned Cython wrangler before replay |
| 31076788907 | cb1c2efd3bb6af99fdca659c5667e8aaab332f65 | engine-config failure | official data reached engine construction; an unsupported `BacktestEngineConfig` field stopped replay |
| 31076904149 | 3c54c673db3be4b89ece67e7c8a1287cf285cfdf | superseded duplicate | patch trigger exercised the same pre-fix source and is not performance evidence |
| 31076976101 | 0bb0d58454a8545c82c26887e42ad6ffd04be5a3 | venue-config failure | loaded legacy runtime rejected liquidation keywords present in its type stub |
| 31077172125 | bc197c73c8e2088fcf079688de803e79a7078274 | post-replay reporting failure | complete first-window Nautilus replay succeeded; obsolete direct-engine report methods failed afterward |
| 31077410620 | 54de04b6f158e3481d67dd546e429ed370fe63cd | invalid timestamp-scale run | raw millisecond `.asi8` values appeared as 1970 nanoseconds, so zero trades were invalid evidence |
| 31077616228 | 85cd5654e215aea9ddfeac1fece60621a2afa205 | patch-infrastructure failure | timestamp patch was first tested outside the pinned image; source was not committed |
| 31077694294 | e7fb2081afe40a65a18fcbc76b364168136cf565 | validated patch, commit failure | pinned image and eight tests passed; Git safe-directory setup alone blocked the commit |
| 31077856942 | b75dc3f7854631f16630cdb494d0ed31acb55171 | patch success | explicit epoch-nanosecond conversion and optional result statistics committed as `474ff3ae5f9203ee0c021cb4ef6cae6243bedacc` |
| 31077987176 | a98827259323cf8d897f3dd737431c39e29717d5 | first real signal, OrderList adapter failure | first causal setup occurred at 2024-04-09 16:08:59.999 UTC; bracket `OrderList` was unpacked incorrectly before submission |
| 31078147847 | 635da6f60fa7b2bd8b3cf132631b994eb1c02745 | OrderList patch success | `OrderList.orders` adapter committed as `95e7f45b2b4381d7091d27f4ab1ed7873bb8ea60` |
| 31078218914 | ea5669f05cb1b1ed28c5da695c120aeb5da501aa | valid baseline screen-01; suite logger failure | first fixed week completed with real Nautilus orders/fills: 11 trades, 18.18% wins, NAV 100,000.00→82,611.57 USDT, daily geometric growth -2.69197%, max realized-equity drawdown 26.45%. All trades were acceptance setups. A second engine in the same process then hit the Rust logger singleton before screen-02 |
| 31078727960 | c489ef0689d1dc037d71efd272a2f9e85559f0c2 | controlled revision success | pinned image, `smc4 doctor`, compilation, and ten causal/risk/timestamp tests passed. Commit `28f469a6ad0a9e854fdb943fe992f2fabcc09f19` requires an established pool, a low-energy held retest, and a later independent continuation displacement; repeated-window logging is bypassed |

The execution models use the pinned official `nautilus_trader.backtest.models` API. The data adapter
constructs official Nautilus `Bar` objects from owned float64 arrays, explicitly converts pandas
indices to epoch nanoseconds, and retains source close time as event and observation time. Reports
come from `ReportProvider` over the engine cache. The loaded runtime does not expose the direct
liquidation switch found in its type stub, so liquidation is not claimed active; realized loss and
margin paths remain promotion diagnostics.
