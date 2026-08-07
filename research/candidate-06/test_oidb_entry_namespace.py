from __future__ import annotations

from test_open_interest_deleveraging_engine import seed_and_event, snap


def assert_entry_namespace(step, family: str) -> None:
    assert step.signal is not None
    assert step.signal.family == family
    assert step.signal.scenario_id.endswith(":ENTRY")
    entry = [transition for transition in step.transitions if transition.event_type == "OIDB_ENTRY_TRANSITION"]
    context = [
        transition
        for transition in step.transitions
        if transition.event_type == "OPEN_INTEREST_DELEVERAGING_TRANSITION"
    ]
    assert len(entry) == 1
    assert len(context) == 1
    assert entry[0].scenario_id == step.signal.scenario_id
    assert entry[0].previous_state == "IDLE"
    assert entry[0].next_state == "ENTRY_ARMED"
    assert context[0].scenario_id != step.signal.scenario_id
    assert entry[0].details["context_scenario_id"] == context[0].scenario_id


def main() -> None:
    reversal_engine = seed_and_event()
    reversal = reversal_engine.observe(snap(21, 99.8, open_=98.0, flow=0.4, location=0.9))
    assert_entry_namespace(reversal, "OIDB_R")

    continuation_engine = seed_and_event(continuation=True)
    for index in range(21, 25):
        continuation_engine.observe(
            snap(index, 97.0 - 0.2 * (index - 21), open_=97.2, flow=-0.3, location=0.1),
        )
    continuation = continuation_engine.observe(
        snap(25, 95.8, open_=96.4, flow=-0.4, location=0.1),
    )
    assert_entry_namespace(continuation, "OIDB_C")
    print("OIDB entry namespace contract passed")


if __name__ == "__main__":
    main()
