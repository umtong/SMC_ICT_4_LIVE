"""Late source patch applied after the immutable candidate-02 bundle.

This temporary bootstrap helper keeps runtime-compatibility edits reviewable while
GitHub Actions validates the candidate. It is removed once the permanent source
and workflow are committed.
"""

from pathlib import Path


backtest = Path("research/candidate-02/backtest.py")
text = backtest.read_text(encoding="utf-8")
text = text.replace(
    '"stats_general": _json_safe(result_obj.stats_general),',
    '"stats_general": _json_safe(getattr(result_obj, "stats_general", {})),',
)
backtest.write_text(text, encoding="utf-8")
