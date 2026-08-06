#!/usr/bin/env python3
"""Replace direct frozen ScenarioPlan mutation with dataclasses.replace."""
from pathlib import Path

PATH = Path(__file__).with_name("intrinsic_outside_acceptance_v9_nautilus_week.py")


def main() -> None:
    source = PATH.read_text(encoding="utf-8")
    old_import = "from dataclasses import asdict, dataclass"
    new_import = "from dataclasses import asdict, dataclass, replace"
    if source.count(old_import) != 1:
        raise RuntimeError("unexpected dataclasses import")
    source = source.replace(old_import, new_import, 1)
    old = '''        if plan is not None:
            plan.response = "OUTSIDE_VALUE_ACCEPTANCE_CONTINUATION"
            plan.reason_code = "OUTSIDE_VALUE_BOUNDARY_RETEST_CONTINUATION"
            plans.append(plan)
'''
    new = '''        if plan is not None:
            plan = replace(
                plan,
                response="OUTSIDE_VALUE_ACCEPTANCE_CONTINUATION",
                reason_code="OUTSIDE_VALUE_BOUNDARY_RETEST_CONTINUATION",
            )
            plans.append(plan)
'''
    if source.count(old) != 1:
        raise RuntimeError("unexpected acceptance plan mutation block")
    PATH.write_text(source.replace(old, new, 1), encoding="utf-8")
    print("patched v9 to preserve frozen ScenarioPlan semantics")


if __name__ == "__main__":
    main()
