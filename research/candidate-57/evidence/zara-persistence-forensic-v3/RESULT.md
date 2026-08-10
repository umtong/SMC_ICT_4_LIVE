# ZaratustraV5 persistent-thesis forensic v3

The run exactly reproduces the frozen policy and records persistence only.

- baseline identical: True
- trades: 214
- wins/losses: 144/70
- PF: 0.6810251307578566

| outcome | trades | mean R | streak2 before boundary | streak3 before boundary | streak6 before boundary | two-TF failure before boundary | 30m failure before boundary | median max streak |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| winner | 144 | 0.17596339410465567 | 0.4236111111111111 | 0.3819444444444444 | 0.3541666666666667 | 0.2569444444444444 | 0.1597222222222222 | 12.0 |
| partial_loss | 44 | -0.2949621055681772 | 0.8409090909090909 | 0.7727272727272727 | 0.7045454545454546 | 0.7045454545454546 | 0.5454545454545454 | 68.0 |
| full_stop | 26 | -0.9878337253527738 | 0.6538461538461539 | 0.5384615384615384 | 0.4230769230769231 | 0.3076923076923077 | 0.15384615384615385 | 24.0 |

## Predeclared interpretation

- persistence hypothesis supported: False
- reason: winner streak3-before-activation=0.382; full-stop streak3-before--0.50R=0.538; full-stop median mark at streak3=-0.368R

A policy experiment is justified only when a three-check failure is uncommon before winner activation, common before full-stop -0.50R, and still exits materially before the source stop. Otherwise the source-entry family rather than its lifecycle management remains the primary problem.