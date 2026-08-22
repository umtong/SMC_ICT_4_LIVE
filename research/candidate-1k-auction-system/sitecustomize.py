"""Bridge inherited v5/v6 module contracts during candidate-1k execution.

The sequential episode layer treats ``departure_first_return_harvest`` as its core
module, while the constants below are defined in that module's imported
``coherent_policy`` dependency.  Re-export them before the research entry point is
imported so the latest lineage can execute end to end.  This does not alter signal
logic or labels; it only restores the intended module interface.
"""
from __future__ import annotations

import coherent_policy as _coherent
import departure_first_return_harvest as _departure

for _name in (
    "MAX_RESPONSE_BARS",
    "MAX_RETURN_MINUTES",
    "MAX_DEPARTURE_MINUTES",
    "MAX_CONFIRM_MINUTES",
):
    setattr(_departure, _name, getattr(_coherent, _name))
