# Candidate 4t v3 — causal state ownership and commitment policy

Version 3 is the first Candidate 4t policy whose continuation models are suitable for evidence.

During implementation review, v1/v2 exposed a real error: their future continuation targets had names that the generic numeric encoder could treat as predictors. That would let the model see the later opportunity it was supposed to predict. Version 3 removes that route completely. Every training-only field is prefixed with `label_`, the encoder contract rejects such fields, fresh states never receive future-derived continuation columns, and the manifest asserts that no label or terminal-outcome field entered a model.

The trading logic remains the synthesis intended by Candidate 4t:

- one action-independent ownership label per causal auction state;
- family-aware but symbol-agnostic price/volume/structure ownership models;
- separate first-return fill and resolution models;
- persistent ownership evidence with reset on contradictory control transfer;
- exact structural stop and first unconsumed opposing-liquidity destination fixed before entry;
- same-episode continuation value and global one-slot commitment value learned from separated development periods;
- enter only when estimated post-cost log growth exceeds both alternatives;
- pending replacement only before fill; immutable TP/SL after fill;
- one continuous account, full-account collateral and approximately 3% NAV loss at structural stop.

The workflow may reuse v1's successfully harvested immutable action artifacts, but it never reuses v1/v2 route results.
