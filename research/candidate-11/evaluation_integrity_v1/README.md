# Candidate 11 evaluation integrity

This directory records the evidence-level reason isolated random-week results did not transfer and installs a binding protocol that prevents recurrence.

Run:

```bash
python audit_random_week_bias.py
python -m unittest discover -s . -p 'test_*.py' -v
```

The audit is not a strategy and does not replace NautilusTrader. It decides whether existing evidence is eligible to support a generalization claim.
