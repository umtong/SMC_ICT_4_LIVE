# Validation run ledger

This ledger records immutable GitHub Actions run IDs and the reason for every rerun.

| run ID | commit | result | interpretation |
|---|---|---|---|
| 31076025364 | 1498a0bcfeb25908e91ab9ce56d293945923af80 | infrastructure failure | pinned container user could not write the GitHub temporary checkout directory; no candidate code ran |
| 31076099726 | bec1708115cd56eafb6c8fc0ba6b81c0208629a4 | implementation failure | environment and seven tests passed; runner used the wrong pinned model import path |
| 31076483095 | 009cc09c1f9074de71dce03e8b80cf54f02ef380 | data-adapter failure | published Binance checksum verification passed; pandas exposed a read-only buffer to the Cython wrangler |
| 31076788907 | cb1c2efd3bb6af99fdca659c5667e8aaab332f65 | engine-config failure | official data reached engine construction; an unsupported config field stopped replay |
| 31076904149 | 3c54c673db3be4b89ece67e7c8a1287cf285cfdf | superseded duplicate | pre-fix patch-trigger run, not performance evidence |
| 31076976101 | 0bb0d58454a8545c82c26887e42ad6ffd04be5a3 | venue-config failure | loaded runtime rejected liquidation keywords present only in its type stub |
| 31077172125 | bc197c73c8e2088fcf079688de803e79a7078274 | post-replay reporting failure | complete first-window replay succeeded; obsolete report methods failed afterward |
| 31077410620 | 54de04b6f158e3481d67dd546e429ed370fe63cd | invalid timestamp-scale run | millisecond `.asi8` values appeared as 1970 nanoseconds, so the zero-trade result was invalid |
| 31077616228 | 85cd5654e215aea9ddfeac1fece60621a2afa205 | patch-infrastructure failure | timestamp patch was tested outside the pinned image and not committed |
| 31077694294 | e7fb2081afe40a65a18fcbc76b364168136cf565 | validated patch, commit failure | pinned image and eight tests passed; Git safe-directory setup blocked only the commit |
| 31077856942 | b75dc3f7854631f16630cdb494d0ed31acb55171 | patch success | explicit epoch-nanosecond conversion committed as `474ff3ae5f9203ee0c021cb4ef6cae6243bedacc` |
| 31077987176 | a98827259323cf8d897f3dd737431c39e29717d5 | first real signal, OrderList adapter failure | first causal setup occurred at 2024-04-09 16:08:59.999 UTC; bracket `OrderList` was unpacked incorrectly |
| 31078147847 | 635da6f60fa7b2bd8b3cf132631b994eb1c02745 | OrderList patch success | `OrderList.orders` adapter committed as `95e7f45b2b4381d7091d27f4ab1ed7873bb8ea60` |
| 31078218914 | ea5669f05cb1b1ed28c5da695c120aeb5da501aa | valid baseline screen-01; suite logger failure | 11 trades, 18.18% wins, NAV 100,000.00→82,611.57 USDT, daily geometric growth -2.69197%, max realized-equity drawdown 26.45%; all trades were acceptance setups |
| 31078727960 | c489ef0689d1dc037d71efd272a2f9e85559f0c2 | controlled revision success | ten tests passed; commit `28f469a6ad0a9e854fdb943fe992f2fabcc09f19` requires established liquidity, a contracted held retest, and later continuation displacement |
| 31078940798 | e055fec757c7ec9d52b9d931aa6474baf5fd2e61 | revised screen-01 failed; diagnostic chain stopped suite | four trades, zero wins, NAV 100,000.00→77,993.28 USDT, daily geometric growth -3.48838%. Three rejection trades lost 19,279.51 USDT and one acceptance trade lost 2,727.21 USDT. Screen-02 replay also ran, but `RETEST_HELD→CONFIRMED` was logged with stale `previous_state=ARMED`, so strict event validation stopped before its metrics were written |
| 31079160379 | fad4305fb872aa7c7240983fd3cbe7a42105b821 | diagnostic state patch success | cancellation and confirmation events now preserve `RETEST_HELD`; no trading rule, order, fill, or sizing behavior changed |
| 31114849538 | e28a0f244690f1e41b594f8aa849a896b19fc13e | native production adapter success | pinned PyO3 strategy allocation, instrument/data conversions, reports, one shared margin account, market bracket, funding/mark data, liquidation, and zero residual exposure passed; bot committed `02978fca21260fbeabf566ba9d155c7a6e94ef63` |
| 31115021104 | 55c9b9f6d6f75a67ba7e68a708ad55dd8bebf5ef | valid clean first-week logic failure | native replay completed with 3 trades, 0 wins, NAV 100,000.00→93,949.88433884 USDT, daily geometric growth -0.887590%; all causality, funding, three-percent risk, liquidation, and residual-exposure checks passed |
| 31115693854 | 1d41d9cbef33121dc5ea390d3e0a2c4f82290be9 | valid single-variable diagnostic ablation | removing only retest contraction produced 3 trades, 1 win, NAV 104,182.48422935 USDT, daily geometric growth +0.587057%; one ETH trade supplied all positive PnL, so the predeclared non-promotable ablation did not meet the project target or independence requirement |
| 31117471234 | 851f41878d37a06b700c7bb7ea1ebc20b8dd876a | workflow patch implementation failure | a workflow-time string replacement for scenario metadata was non-unique and stopped before tests or replay; no performance evidence was created |
| 31117765098 | d809e76589c377c9a545533597f5337d839224e1 | external runner-queue blocker | source-corrected auction-router v2 remained queued without job start; no candidate code ran and the marker remains only a pending request |
| 31119226020 | 8fd348c8b4ed11d92777973c3762e54f477daeec | external runner-queue blocker | source-stable auction-router v3 removes runtime strategy rewriting and stages first-week then three-week replay, but remained queued without job start at the latest check; no performance conclusion is permitted |

The earlier execution models used the pinned official `nautilus_trader.backtest.models` API and
progressively exposed Cython/PyO3 boundary problems.  Commit
`02978fca21260fbeabf566ba9d155c7a6e94ef63` supersedes that mixed adapter with the verified native
PyO3 production path.  Current performance evidence must therefore come from the native runner and
must include official checksum-verified data manifests, account/fill/order/position reports,
causal funding and mark-price state, native liquidation settings, exact three-percent planned-loss
checks, and zero residual exposure.
