#!/usr/bin/env python3
"""Patch the suite runner so every Nautilus segment owns a fresh process."""

from pathlib import Path


def replace_once(text: str, old: str, new: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"expected one match but found {count}: {old[:100]!r}")
    return text.replace(old, new, 1)


path = Path(__file__).with_name("run_research.py")
text = path.read_text(encoding="utf-8")
text = replace_once(
    text,
    "import json\nfrom math import prod\n",
    "import json\nfrom math import prod\nimport subprocess\n",
)
marker = "    metric_rows: list[dict[str, Any]] = []\n    for label, start, end in segments:\n"
orchestrator = '''    # NautilusTrader 1.230.0 owns a process-global Rust logger.  Disposing an
    # engine releases market and portfolio resources, but intentionally does not
    # unregister that logger.  Multi-segment research therefore launches each
    # deterministic segment in a fresh Python process.  This is also the closest
    # research analogue to independent live sessions and prevents cross-segment
    # cache, clock, order-id, and logging state from leaking.
    if len(segments) > 1:
        metric_rows: list[dict[str, Any]] = []
        script = Path(__file__).resolve()
        for label, start, end in segments:
            command = [
                sys.executable,
                str(script),
                "--config",
                str(config_path),
                "--output",
                str(output_root),
                "--cache",
                str(cache_dir),
                "--suite",
                "discovery",
                "--start",
                start.date().isoformat(),
                "--end",
                end.date().isoformat(),
                "--label",
                label,
            ]
            subprocess.run(command, check=True)
            metrics_path = output_root / label / "metrics.json"
            if not metrics_path.is_file():
                raise RuntimeError(f"segment worker did not create {metrics_path}")
            metric_rows.append(json.loads(metrics_path.read_text(encoding="utf-8")))

        aggregate_metrics = aggregate(metric_rows)
        write_json_atomic(output_root / "aggregate_metrics.json", aggregate_metrics)
        write_json_atomic(
            output_root / "run.json",
            create_run_manifest(
                run_id=f"candidate-01-{args.suite}-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}",
                candidate="candidate-01-causal-liquidity-auction",
                config_path=config_path,
                extra={
                    "suite": args.suite,
                    "segment_labels": [label for label, _, _ in segments],
                    "candidate_success": aggregate_metrics["candidate_success"],
                    "segment_process_isolation": True,
                },
            ),
        )
        print(json.dumps(aggregate_metrics, indent=2, sort_keys=True), flush=True)
        return 0 if aggregate_metrics["candidate_success"] or args.suite != "full" else 2

    metric_rows: list[dict[str, Any]] = []
    for label, start, end in segments:
'''
text = replace_once(text, marker, orchestrator)
path.write_text(text, encoding="utf-8")
