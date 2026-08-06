#!/usr/bin/env python3
"""Make v14 long-run evidence complete without changing economic logic.

The original pooled-screen patch evaluated the long run correctly in memory but left the
long run's trades, fills, and diagnostic events out of the persisted evidence files. This
controlled implementation fix selects only the frozen gate baseline plus the optional
long-BTC baseline for evidence output; fixed-week ablation rows remain confined to
``outcomes.csv``. No signal, order, cost, target, stop, or sizing code is modified.
"""

from __future__ import annotations

from pathlib import Path


path = Path(__file__).resolve().parent / "run.py"
text = path.read_text(encoding="utf-8")

helper = '''

def evidence_details_for_output(
    baseline: list[DetailedRun],
    all_details: list[DetailedRun],
) -> list[DetailedRun]:
    selected = list(baseline)
    selected_ids = {id(detail) for detail in selected}
    for detail in all_details:
        if (
            id(detail) not in selected_ids
            and detail.outcome.variant == "baseline"
            and detail.outcome.segment == "long-btc"
        ):
            selected.append(detail)
            selected_ids.add(id(detail))
    return selected
'''
anchor = "\ndef diagnose_failure("
if "def evidence_details_for_output(" not in text:
    if anchor not in text:
        raise SystemExit("diagnosis anchor not found")
    text = text.replace(anchor, helper + anchor, 1)

main_anchor = "    baseline_events = [\n"
selection = "    evidence_details = evidence_details_for_output(baseline_details, all_details)\n"
if selection not in text:
    if main_anchor not in text:
        raise SystemExit("baseline evidence anchor not found")
    text = text.replace(main_anchor, selection + main_anchor, 1)

replacements = (
    (
        "        for detail in baseline_details\n        for event in detail.events\n",
        "        for detail in evidence_details\n        for event in detail.events\n",
    ),
    (
        "        for detail in baseline_details\n        for trade in detail.trades\n",
        "        for detail in evidence_details\n        for trade in detail.trades\n",
    ),
    (
        "        for detail in baseline_details\n        for fill in detail.fills\n",
        "        for detail in evidence_details\n        for fill in detail.fills\n",
    ),
)
for old, new in replacements:
    if new in text:
        continue
    if old not in text:
        raise SystemExit(f"evidence comprehension not found: {old!r}")
    text = text.replace(old, new, 1)

required = (
    "def evidence_details_for_output(",
    "evidence_details = evidence_details_for_output(baseline_details, all_details)",
    "for detail in evidence_details\n        for event in detail.events",
    "for detail in evidence_details\n        for trade in detail.trades",
    "for detail in evidence_details\n        for fill in detail.fills",
)
missing = [snippet for snippet in required if snippet not in text]
if missing:
    raise SystemExit(f"incomplete evidence fix: {missing}")

path.write_text(text, encoding="utf-8")
