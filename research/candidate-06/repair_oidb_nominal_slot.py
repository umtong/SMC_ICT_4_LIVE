from __future__ import annotations

from pathlib import Path


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one match, found {count}")
    return text.replace(old, new, 1)


def main() -> None:
    root = Path(__file__).resolve().parent
    source = root / "futures_metrics_data.py"
    text = source.read_text(encoding="utf-8")
    text = replace_once(
        text,
        "MAX_NOMINAL_TIMESTAMP_OFFSET_NS = 1_000_000_000\n",
        "MAX_NOMINAL_TIMESTAMP_OFFSET_NS = FIVE_MINUTE_NS // 2\n",
        "nominal offset bound",
    )
    text = replace_once(
        text,
        '''    Binance has a small number of official metrics rows stamped one second away
    from the nominal five-minute boundary. The row is never shifted earlier. A
    non-minute source timestamp becomes observable at the next completed minute.
''',
        '''    Official metrics rows may be published seconds after their nominal slot.
    The nominal slot is used only to verify that no five-minute observation is
    missing. The row itself is never shifted earlier and becomes observable at
    the first completed minute at or after its source timestamp.
''',
        "timestamp contract docstring",
    )
    text = replace_once(
        text,
        '''    if abs(offset) > MAX_NOMINAL_TIMESTAMP_OFFSET_NS:
        raise ValueError(
            f"metrics timestamp too far from five-minute boundary: "
            f"source={source_ts_ns}, nominal={nominal}, offset_ns={offset}",
        )
''',
        '''    if abs(offset) >= MAX_NOMINAL_TIMESTAMP_OFFSET_NS:
        raise ValueError(
            f"metrics timestamp is ambiguous between adjacent five-minute slots: "
            f"source={source_ts_ns}, nominal={nominal}, offset_ns={offset}",
        )
''',
        "timestamp ambiguity guard",
    )
    source.write_text(text, encoding="utf-8")

    test_path = root / "test_futures_metrics_timestamp_contract.py"
    test = test_path.read_text(encoding="utf-8")
    old = '''    try:
        _causal_metric_timestamp(boundary + 1_000_000_001)
    except ValueError as exc:
        assert "too far" in str(exc)
    else:
        raise AssertionError("timestamp outside one-second tolerance must fail")

'''
    new = '''    plus_two_seconds = boundary + 2_000_000_000
    nominal, observable, offset = _causal_metric_timestamp(plus_two_seconds)
    assert nominal == boundary
    assert observable == boundary + ONE_MINUTE_NS
    assert offset == 2_000_000_000
    assert observable >= plus_two_seconds

    late_but_unambiguous = boundary + (FIVE_MINUTE_NS // 2) - 1
    nominal, observable, offset = _causal_metric_timestamp(late_but_unambiguous)
    assert nominal == boundary
    assert observable >= late_but_unambiguous
    assert offset == (FIVE_MINUTE_NS // 2) - 1

    try:
        _causal_metric_timestamp(boundary + FIVE_MINUTE_NS // 2)
    except ValueError as exc:
        assert "ambiguous" in str(exc)
    else:
        raise AssertionError("half-slot timestamp must fail as ambiguous")

'''
    test = replace_once(test, old, new, "timestamp test tolerance")
    test_path.write_text(test, encoding="utf-8")
    print("OIDB nominal-slot validation generalized without moving observations earlier")


if __name__ == "__main__":
    main()
