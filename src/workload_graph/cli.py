"""Command-line interface.

Status: partial
Purpose: ``workload-graph build`` and ``evaluate`` run offline; ``collect`` is not wired.
Inputs: argv.
Outputs: exit code. 0 on success, 2 on bad input.
Planned dependencies: argparse, pipeline, evaluation.
Notes: ``collect`` raises ``NotImplementedError`` until approved roles exist.
    # SKELETON: live wiring requires approved roles.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from pydantic import ValidationError

from workload_graph.evaluation.golden import load_golden
from workload_graph.evaluation.metrics import evaluate, placed_key_strings
from workload_graph.models import BuildResult, Workspace
from workload_graph.pipeline import run
from workload_graph.ruleset import Ruleset


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="workload-graph")
    sub = parser.add_subparsers(dest="command", required=True)
    build = sub.add_parser("build", help="build candidate workloads from stored snapshots")
    build.add_argument("--snapshots", required=True, type=Path)
    build.add_argument("--workspace", required=True, type=Path)
    build.add_argument("--ruleset", required=True, type=Path)
    build.add_argument("--out", required=True, type=Path)
    evaluate_cmd = sub.add_parser("evaluate", help="score a build result against a golden file")
    evaluate_cmd.add_argument("--result", required=True, type=Path)
    evaluate_cmd.add_argument("--golden", required=True, type=Path)
    sub.add_parser("collect", help="live collection is not wired")
    ui = sub.add_parser("ui", help="open the local workload graph page")
    ui.add_argument("--port", type=int, default=8765)
    ui.add_argument("--external-id", default=None,
                    help="deployed mode: assume TamReadOnly in up to 20 member accounts")
    ui.add_argument("--runs-dir", type=Path, default=Path("out/runs"),
                    help="where runs and decisions are kept (gitignored)")
    return parser


def _build(args: argparse.Namespace) -> int:
    workspace = Workspace.model_validate_json(args.workspace.read_text(encoding="utf-8"))
    ruleset = Ruleset.load(args.ruleset)
    result = run(args.snapshots, ruleset, workspace)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(result.to_json(), encoding="utf-8")
    return 0


def _evaluate(args: argparse.Namespace) -> int:
    result = BuildResult.model_validate_json(args.result.read_text(encoding="utf-8"))
    golden = load_golden(args.golden, known_keys=placed_key_strings(result))
    print(evaluate(result, golden).summary(), end="")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """Run the CLI. Returns 0 on success and 2 on bad input."""
    parser = _parser()
    try:
        args = parser.parse_args(None if argv is None else list(argv))
    except SystemExit as exc:
        code = exc.code
        return code if isinstance(code, int) else 2
    if args.command == "collect":
        # SKELETON: live wiring requires approved roles.
        raise NotImplementedError("SKELETON: live wiring requires approved roles")
    if args.command == "ui":
        from workload_graph.ui_server import serve

        serve(args.port, external_id=args.external_id, runs_dir=args.runs_dir)
        return 0
    try:
        if args.command == "build":
            return _build(args)
        if args.command == "evaluate":
            return _evaluate(args)
    except NotImplementedError:
        raise
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except ValidationError:
        print("error: invalid input", file=sys.stderr)
        return 2
    except OSError as exc:
        detail = exc.strerror or exc.__class__.__name__
        print(f"error: {detail}: {exc.filename}", file=sys.stderr)
        return 2
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
