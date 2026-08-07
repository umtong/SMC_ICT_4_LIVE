from __future__ import annotations

from pathlib import Path


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one match, found {count}")
    return text.replace(old, new, 1)


def main() -> None:
    path = Path(__file__).resolve().parent / "futures_metrics_data.py"
    text = path.read_text(encoding="utf-8")
    text = replace_once(
        text,
        "from dataclasses import dataclass\n",
        "from dataclasses import dataclass, replace\n",
        "dataclass import",
    )
    helper = '''\n\nFIVE_MINUTE_NS = 5 * 60 * 1_000_000_000\nONE_MINUTE_NS = 60 * 1_000_000_000\nMAX_NOMINAL_TIMESTAMP_OFFSET_NS = 1_000_000_000\n\n\ndef _causal_metric_timestamp(source_ts_ns: int) -> tuple[int, int, int]:\n    \"\"\"Return nominal five-minute slot, causal observable minute and offset.\n\n    Binance has a small number of official metrics rows stamped one second away\n    from the nominal five-minute boundary. The row is never shifted earlier. A\n    non-minute source timestamp becomes observable at the next completed minute.\n    \"\"\"\n\n    lower = (source_ts_ns // FIVE_MINUTE_NS) * FIVE_MINUTE_NS\n    upper = lower + FIVE_MINUTE_NS\n    nominal = lower if source_ts_ns - lower <= upper - source_ts_ns else upper\n    offset = source_ts_ns - nominal\n    if abs(offset) > MAX_NOMINAL_TIMESTAMP_OFFSET_NS:\n        raise ValueError(\n            f\"metrics timestamp too far from five-minute boundary: \"\n            f\"source={source_ts_ns}, nominal={nominal}, offset_ns={offset}\",\n        )\n    observable = ((source_ts_ns + ONE_MINUTE_NS - 1) // ONE_MINUTE_NS) * ONE_MINUTE_NS\n    if observable < source_ts_ns:\n        raise AssertionError(\"metric observation timestamp moved before source timestamp\")\n    return nominal, observable, offset\n'''
    text = replace_once(
        text,
        "\n\ndef _read_archive(path: Path) -> list[FuturesMetric]:\n",
        helper + "\n\ndef _read_archive(path: Path) -> list[FuturesMetric]:\n",
        "timestamp helper insertion",
    )
    old = '''    observations: dict[int, FuturesMetric] = {}\n    for day in dates:\n        for item in _read_archive(downloaded[day][0]):\n            if item.ts_ns in observations:\n                raise ValueError(f\"duplicate metrics timestamp: {item.ts_ns}\")\n            observations[item.ts_ns] = item\n    observations = dict(sorted(observations.items()))\n    if not observations:\n        raise ValueError(f\"no metrics observations for {symbol} over {dates}\")\n    timestamps = list(observations)\n    gaps = [\n        (left, right)\n        for left, right in zip(timestamps, timestamps[1:])\n        if right - left != 5 * 60 * 1_000_000_000\n    ]\n    if gaps:\n        raise ValueError(f\"non-five-minute metrics gaps: count={len(gaps)}, first={gaps[:5]}\")\n'''
    new = '''    observations: dict[int, FuturesMetric] = {}\n    nominal_slots: dict[int, int] = {}\n    adjustments: list[dict[str, int]] = []\n    for day in dates:\n        for item in _read_archive(downloaded[day][0]):\n            source_ts_ns = item.ts_ns\n            nominal_ts_ns, observable_ts_ns, offset_ns = _causal_metric_timestamp(source_ts_ns)\n            if nominal_ts_ns in nominal_slots:\n                raise ValueError(\n                    f\"duplicate nominal metrics slot: nominal={nominal_ts_ns}, \"\n                    f\"sources=({nominal_slots[nominal_ts_ns]}, {source_ts_ns})\",\n                )\n            if observable_ts_ns in observations:\n                raise ValueError(f\"duplicate observable metrics timestamp: {observable_ts_ns}\")\n            nominal_slots[nominal_ts_ns] = source_ts_ns\n            observations[observable_ts_ns] = replace(item, ts_ns=observable_ts_ns)\n            if offset_ns != 0 or observable_ts_ns != source_ts_ns:\n                adjustments.append(\n                    {\n                        \"source_ts_ns\": source_ts_ns,\n                        \"nominal_ts_ns\": nominal_ts_ns,\n                        \"observable_ts_ns\": observable_ts_ns,\n                        \"nominal_offset_ns\": offset_ns,\n                        \"observation_delay_ns\": observable_ts_ns - source_ts_ns,\n                    },\n                )\n    observations = dict(sorted(observations.items()))\n    nominal_timestamps = sorted(nominal_slots)\n    if not observations:\n        raise ValueError(f\"no metrics observations for {symbol} over {dates}\")\n    gaps = [\n        (left, right)\n        for left, right in zip(nominal_timestamps, nominal_timestamps[1:])\n        if right - left != FIVE_MINUTE_NS\n    ]\n    if gaps:\n        raise ValueError(f\"missing nominal five-minute metrics slots: count={len(gaps)}, first={gaps[:5]}\")\n    timestamps = list(observations)\n    if any(right <= left for left, right in zip(timestamps, timestamps[1:])):\n        raise ValueError(\"observable metrics timestamps are not strictly increasing\")\n'''
    text = replace_once(text, old, new, "load_dates timestamp block")
    old_quality = '''        "first_observed_utc_ns": timestamps[0],\n        "last_observed_utc_ns": timestamps[-1],\n        "cadence_minutes": 5,\n        "missing_intervals": len(gaps),\n        "timestamp_contract": "published five-minute metric timestamp is used only when its completed snapshot is observable",\n        "fields": list(COLUMNS),\n'''
    new_quality = '''        "first_observed_utc_ns": timestamps[0],\n        "last_observed_utc_ns": timestamps[-1],\n        "first_nominal_slot_utc_ns": nominal_timestamps[0],\n        "last_nominal_slot_utc_ns": nominal_timestamps[-1],\n        "cadence_minutes": 5,\n        "missing_intervals": len(gaps),\n        "timestamp_adjustments": len(adjustments),\n        "max_abs_nominal_offset_ns": max((abs(item["nominal_offset_ns"]) for item in adjustments), default=0),\n        "max_observation_delay_ns": max((item["observation_delay_ns"] for item in adjustments), default=0),\n        "timestamp_contract": (\n            "source timestamp is validated against the nearest five-minute nominal slot; "\n            "a non-minute source timestamp becomes observable only at the next completed minute and is never shifted earlier"\n        ),\n        "fields": list(COLUMNS),\n'''
    text = replace_once(text, old_quality, new_quality, "quality timestamp block")
    path.write_text(text, encoding="utf-8")
    print("causal futures-metrics timestamp normalization applied")


if __name__ == "__main__":
    main()
