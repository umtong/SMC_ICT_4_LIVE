# Candidate 14 evidence status

**NO_FRESH_V3_EVIDENCE**

The aggregate created in commit `b339cac7320d86adcb478cfff0fef421a16108b5` is invalidated.

The v3 static job stopped before any market evaluation because one inherited test still expected the old parent-order marker. The aggregate job nevertheless read the already committed development-v2 `results/` directory and relabeled its metrics with the v3 candidate name. The weekly `effective_config.json` files prove that their protocol remained `candidate-14-development-v2-protocol-v1`.

No v3 performance claim is made from those files. Development-v1 and development-v2 evidence remains separately preserved under `development-v1-*` and `development-v2-*`.

A replacement workflow now requires:

- static success;
- successful evaluation of all W10-W14 matrix jobs;
- exactly five freshly downloaded evidence artifacts;
- candidate, protocol schema, validation mode and session-module provenance equality for every interval;
- all independent safety-audit checks;
- a current branch SHA before and after aggregation.

Only provenance-verified fresh evidence may replace this status.
