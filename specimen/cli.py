"""Command line interface for Specimen."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .contracts import RunConfig
from .core import reduce_csv

_ARTIFACT_NAMES = ("specimen.csv", "repro.py", "report.json", "REPORT.md")


def _examples_root() -> Path:
    candidates = [Path(__file__).resolve().parent.parent / "examples",
                  Path(__file__).resolve().parent / "examples"]
    for candidate in candidates:
        if candidate.is_dir():
            return candidate
    raise FileNotFoundError("bundled examples are unavailable")


def _sensitive(values: list[str] | None) -> list[str]:
    return [part.strip() for value in (values or []) for part in value.split(",") if part.strip()]


def _output_overlaps_source(config: RunConfig) -> bool:
    destinations = {(config.output_dir / name).resolve() for name in _ARTIFACT_NAMES}
    return config.input_path.resolve() in destinations or config.script_path.resolve() in destinations


def _model_policy(config: RunConfig) -> None:
    """Load the optional policy adviser only when requested."""
    try:
        from .model import advise
        result = advise(config)
        if isinstance(result, dict):
            config.policy = result
    except Exception:
        # An adviser is never part of the reduction or verification path.
        print("Model adviser unavailable; continuing with deterministic policy.", file=sys.stderr)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="specimen", description="Reduce failing CSV inputs into verified specimens.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    demo = subparsers.add_parser("demo", help="run a bundled synthetic example")
    demo.add_argument("example", nargs="?", choices=("unicode", "relations", "conflict"), default="unicode")
    demo.add_argument("--output", type=Path, default=Path("specimen-output"))
    demo.add_argument("--runner", choices=("local", "docker"), default="local")
    demo.add_argument("--model-assist", action="store_true")

    reduce = subparsers.add_parser("reduce", help="reduce a CSV and supplied Python reproducer")
    reduce.add_argument("input_csv", type=Path)
    reduce.add_argument("--script", required=True, type=Path)
    reduce.add_argument("--sensitive", nargs="*", default=[], metavar="COLUMN[,COLUMN]")
    reduce.add_argument("--output", type=Path, default=Path("specimen-output"))
    reduce.add_argument("--runner", choices=("docker", "local"), default="docker")
    reduce.add_argument("--model-assist", action="store_true")

    serve = subparsers.add_parser("serve", help="serve the local dashboard")
    serve.add_argument("--port", type=int, default=8765)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "serve":
        if not 1 <= args.port <= 65535:
            print("Port must be between 1 and 65535.", file=sys.stderr)
            return 2
        try:
            import uvicorn
            from .server import create_app
        except ImportError:
            print("The web dependencies are unavailable.", file=sys.stderr)
            return 2
        uvicorn.run(create_app(), host="127.0.0.1", port=args.port, log_level="info")
        return 0

    if args.command == "demo":
        root = _examples_root() / args.example
        try:
            metadata = json.loads((root / "metadata.json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            print("The selected bundled demo is unavailable.", file=sys.stderr)
            return 2
        input_path, script_path = root / "data.csv", root / "repro.py"
        if not input_path.is_file() or not script_path.is_file():
            print("The selected bundled demo is unavailable.", file=sys.stderr)
            return 2
        config = RunConfig(input_path=input_path, script_path=script_path, output_dir=args.output,
                           sensitive_columns=metadata.get("sensitive_columns", []), runner=args.runner)
        if args.runner == "local":
            print("Notice: local mode executes the trusted bundled fixture on this host.", file=sys.stderr)
        if args.model_assist:
            _model_policy(config)
    else:
        config = RunConfig(input_path=args.input_csv, script_path=args.script, output_dir=args.output,
                           sensitive_columns=_sensitive(args.sensitive), runner=args.runner)
        if args.runner == "local":
            print("WARNING: --runner local executes your script without a sandbox.", file=sys.stderr)
        if args.model_assist:
            _model_policy(config)

    if _output_overlaps_source(config):
        print("Choose an output directory that does not overwrite the input CSV or script.", file=sys.stderr)
        return 2
    report = reduce_csv(config, on_event=lambda event: _print_event(event))
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report.get("status") == "verified" else 1


def _print_event(event: dict) -> None:
    # Event messages are engine-authored and must remain free of source values.
    message = event.get("message", "Progress")
    print(f"[{event.get('type', 'event')}] {message}", file=sys.stderr)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
