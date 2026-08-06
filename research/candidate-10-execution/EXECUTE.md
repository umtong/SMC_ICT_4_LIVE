# Candidate 10 v20.2 isolated execution

This temporary branch contains only immutable candidate source, the live conservative impact patch, and a pull-request workflow used to obtain a clean pinned NautilusTrader result.

It is not intended to merge into `main`.

- source SHA256: `b481befeb701cedc13536f7924b3207cc46912aeb4ec4d688fee294db514dec7`
- patch SHA256: `ad93590e75c6c68f0de2daaae1525bc5aa4503afa91795e829e787c58fb1dc6f`
- first preselected BTC week: `2023-10-16` through `2023-10-22` UTC
- exact ablation: remove only open-interest state
- engine: NautilusTrader 1.230.0
- planned loss: current conservative NAV × 3%
