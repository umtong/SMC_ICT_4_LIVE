"""Command-line entry point for the shared foundation."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys


def _doctor(_: argparse.Namespace) -> int:
    from .doctor import checks, render

    results = checks()
    print(render(results))
    return 0 if all(item.ok for item in results) else 1


def _smoke(args: argparse.Namespace) -> int:
    from .smoke import run_smoke

    metrics = run_smoke(args.output)
    print(f"smoke passed: {metrics}")
    return 0


def _data_manifest(args: argparse.Namespace) -> int:
    from .manifest import build_data_manifest, write_data_manifest

    manifest = build_data_manifest(args.root, dataset=args.dataset)
    destination = write_data_manifest(args.output, manifest)
    print(f"wrote {destination} with {len(manifest.files)} files")
    return 0


def _validate_events(args: argparse.Namespace) -> int:
    from .event_log import validate_event_file

    events = validate_event_file(args.path)
    print(f"valid: {len(events)} events")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="smc4", description="SMC/ICT 4 shared research foundation")
    subparsers = parser.add_subparsers(dest="command", required=True)

    doctor = subparsers.add_parser("doctor", help="verify the pinned runtime")
    doctor.set_defaults(handler=_doctor)

    smoke = subparsers.add_parser("smoke", help="run a tiny real NautilusTrader backtest")
    smoke.add_argument("--output", type=Path, default=Path("artifacts/smoke"))
    smoke.set_defaults(handler=_smoke)

    data_manifest = subparsers.add_parser("data-manifest", help="hash a local dataset")
    data_manifest.add_argument("--root", type=Path, required=True)
    data_manifest.add_argument("--dataset", required=True)
    data_manifest.add_argument("--output", type=Path, required=True)
    data_manifest.set_defaults(handler=_data_manifest)

    validate = subparsers.add_parser("validate-events", help="validate a scenario event JSONL file")
    validate.add_argument("path", type=Path)
    validate.set_defaults(handler=_validate_events)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.handler(args))
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
