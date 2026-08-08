"""Materialize Candidate 13 V5 multi-market session-auction runner."""
from __future__ import annotations

from pathlib import Path

import market_leadership as _market_leadership
from portfolio_materializer_v5 import materialize_multisymbol_session_source
from runner_materializer_v5 import materialize_runner_source
from semantic_market_leadership_v5 import SemanticMarketLeadershipGate

ROOT = Path(__file__).resolve().parent
BASE_RUNNER = ROOT / "run_leadership_scdam_base.py"

# The base runner imports MarketLeadershipGate at materialization time. Replace
# only that module binding; all measurements remain in the frozen base class.
_market_leadership.MarketLeadershipGate = SemanticMarketLeadershipGate

_source = BASE_RUNNER.read_text(encoding="utf-8")
_source = materialize_runner_source(_source)
_source = materialize_multisymbol_session_source(_source)
_namespace = {
    "__name__": "candidate13_v5_multisymbol_session_materialized",
    "__file__": str(BASE_RUNNER),
}
exec(compile(_source, str(BASE_RUNNER), "exec"), _namespace)
run = _namespace["run"]
