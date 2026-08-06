# Candidate 10 v20.2 controlled execution trigger

This branch exists only to trigger the `pull_request`-scoped NautilusTrader gate against `research/candidate-10`.

- Signal source SHA256: `b481befeb701cedc13536f7924b3207cc46912aeb4ec4d688fee294db514dec7`
- Live-impact patch SHA256: `ad93590e75c6c68f0de2daaae1525bc5aa4503afa91795e829e787c58fb1dc6f`
- First preselected BTC week: `2023-10-16` through `2023-10-22` UTC
- Full versus exact ablation: remove only open-interest state
- Engine: pinned NautilusTrader 1.230.0
- Risk: current conservative whole-account NAV × 3% planned loss

No strategy parameter, week, seed, fee, entry, stop, target, or risk change is introduced by this trigger.
