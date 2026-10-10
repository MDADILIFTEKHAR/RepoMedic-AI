"""Command Line Interface (CLI) for RepoMedic AI.

Provides subcommands for local repository repair, benchmark execution,
skill validation, and launching the FastAPI server.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import sys
from typing import Optional

# Ensure UTF-8 output across Windows and POSIX shells
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
if hasattr(sys.stderr, "reconfigure"):
    try:
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from repomedic import __version__
from repomedic.config import Settings, get_settings
from repomedic.evaluation.benchmark import BenchmarkRunner
from repomedic.models import create_model_client
from repomedic.orchestrator import EvidenceGatedOrchestrator, RepairState
from repomedic.sandbox.runner import create_sandbox_runner


def cmd_repair(args: argparse.Namespace) -> int:
    """Execute repair on a repository path or fixture."""
    repo_path = Path(args.repo).resolve()
    if not repo_path.exists() or not repo_path.is_dir():
        print(f"[ERROR] Target path '{repo_path}' is not a valid directory.", file=sys.stderr)
        return 1

    settings = get_settings()
    model_client = create_model_client(settings)
    sandbox_runner = create_sandbox_runner(timeout_seconds=settings.test_timeout_seconds)

    print("=================================================================")
    print(f"RepoMedic AI v{__version__} — Evidence-Gated Repair Harness")
    print("=================================================================")
    print(f"Target Repository : {repo_path}")
    print(f"Model Provider    : {settings.model_provider}")
    print(f"Docker Isolation  : {'Available' if settings.is_docker_available else 'Unavailable (Restricted)'}")
    print(f"Max Attempts      : {settings.max_repair_attempts}")
    print("-----------------------------------------------------------------")
    print(f"Issue: {args.issue}")
    print("-----------------------------------------------------------------")

    def on_state(state: RepairState, msg: str):
        print(f"[{state.value:^26}] {msg}")

    orchestrator = EvidenceGatedOrchestrator(
        model_client=model_client,
        settings=settings,
        sandbox_runner=sandbox_runner,
        on_state_change=on_state,
    )

    report = asyncio.run(
        orchestrator.run_repair(
            repo_path=repo_path,
            issue_description=args.issue,
            is_predefined_fixture=args.is_fixture,
        )
    )

    print("\n=================================================================")
    print(f"Final Outcome: {report.terminal_state.value}")
    print(f"Verified Pass: {report.success}")
    print(f"Attempts Used: {report.attempts_count} of {report.max_attempts}")
    print(f"Elapsed Time : {report.total_elapsed_seconds}s")
    print("=================================================================")

    if report.verified_patch:
        print("\n--- VERIFIED UNIFIED DIFF PATCH ---")
        print(report.verified_patch)
        print("-----------------------------------")

        if args.output_patch:
            # Prompt user approval before exporting patch
            if not args.yes:
                resp = input(f"Export verified patch to '{args.output_patch}'? [y/N]: ").strip().lower()
                if resp != "y":
                    print("Patch export cancelled by user.")
                    return 0

            out_file = Path(args.output_patch)
            out_file.write_text(report.verified_patch, encoding="utf-8")
            print(f"[SUCCESS] Patch written to {out_file}")

    if args.json_report:
        json_path = Path(args.json_report)
        json_path.write_text(json.dumps(report.model_dump(), indent=2), encoding="utf-8")
        print(f"[INFO] Audit report saved to {json_path}")

    return 0 if report.success else 2


def cmd_benchmark(args: argparse.Namespace) -> int:
    """Run reproducible benchmark evaluation across all 10 deterministic tasks."""
    settings = get_settings()
    fixtures_dir = Path(__file__).resolve().parent.parent / "benchmarks" / "fixtures"

    if not fixtures_dir.exists():
        print(f"[ERROR] Fixtures directory '{fixtures_dir}' not found.", file=sys.stderr)
        return 1

    model_client = create_model_client(settings)
    runner = BenchmarkRunner(fixtures_root=fixtures_dir, settings=settings)

    print("=================================================================")
    print(f"Running RepoMedic AI Benchmark Suite (Mode: {args.mode.upper()})")
    print("=================================================================")

    async def _run():
        if args.mode in ("one_shot", "both"):
            rep_one = await runner.run_suite(model_client, max_attempts=1, mode_label="one_shot")
            print("\n" + rep_one.summary_markdown)
            if args.output:
                p = Path(f"{args.output}_oneshot.json")
                p.write_text(json.dumps(rep_one.model_dump(), indent=2), encoding="utf-8")
                print(f"Wrote one-shot results to {p}")

        if args.mode in ("loop", "both"):
            rep_loop = await runner.run_suite(model_client, max_attempts=3, mode_label="bounded_repair_loop")
            print("\n" + rep_loop.summary_markdown)
            if args.output:
                p = Path(f"{args.output}_loop.json")
                p.write_text(json.dumps(rep_loop.model_dump(), indent=2), encoding="utf-8")
                print(f"Wrote repair-loop results to {p}")

    asyncio.run(_run())
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    """Launch the FastAPI server."""
    import uvicorn

    settings = get_settings()
    port = args.port or settings.port
    host = args.host or settings.host

    print(f"Starting RepoMedic AI Web API on http://{host}:{port}")
    uvicorn.run("repomedic.api:app", host=host, port=port, reload=args.reload)
    return 0


def cmd_check_skill(args: argparse.Namespace) -> int:
    """Validate skills/repo-repair/SKILL.md compliance with Agent Skill Open Standard."""
    skill_file = Path(__file__).resolve().parent.parent / "skills" / "repo-repair" / "SKILL.md"
    if not skill_file.exists():
        print(f"[FAIL] Skill file not found: {skill_file}", file=sys.stderr)
        return 1

    content = skill_file.read_text(encoding="utf-8")
    if not content.startswith("---"):
        print("[FAIL] SKILL.md missing opening YAML frontmatter delimiter '---'", file=sys.stderr)
        return 1

    parts = content.split("---", 2)
    if len(parts) < 3:
        print("[FAIL] SKILL.md does not have closed YAML frontmatter '---'", file=sys.stderr)
        return 1

    import yaml

    try:
        meta = yaml.safe_load(parts[1])
    except Exception as ex:
        print(f"[FAIL] Invalid YAML in SKILL.md: {ex}", file=sys.stderr)
        return 1

    for field in ["name", "description", "version", "author", "license"]:
        if field not in meta:
            print(f"[FAIL] Missing mandatory metadata field '{field}'", file=sys.stderr)
            return 1

    print("[PASS] Agent Skill 'repo-repair' is valid and complies with Agent Skill Open Standard.")
    print(f"  Name        : {meta['name']}")
    print(f"  Version     : {meta['version']}")
    print(f"  Author      : {meta['author']}")
    print(f"  Description : {meta['description']}")
    return 0


def main() -> int:
    """CLI entrypoint."""
    parser = argparse.ArgumentParser(
        prog="repomedic",
        description="RepoMedic AI — Open-Weight AI-Powered Python Bug-Repair Harness",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    subparsers = parser.add_subparsers(dest="subcommand", help="Available subcommands")

    # repair
    p_repair = subparsers.add_parser("repair", help="Run repair harness on a target repository")
    p_repair.add_argument("--repo", required=True, help="Path to repository")
    p_repair.add_argument("--issue", required=True, help="Bug issue description")
    p_repair.add_argument("--is-fixture", action="store_true", help="Flag as safe predefined benchmark fixture")
    p_repair.add_argument("--output-patch", help="Path to write verified unified diff patch")
    p_repair.add_argument("--json-report", help="Path to save machine-readable JSON session report")
    p_repair.add_argument("-y", "--yes", action="store_true", help="Skip approval prompt before writing patch")

    # benchmark
    p_bench = subparsers.add_parser("benchmark", help="Run benchmark suite")
    p_bench.add_argument(
        "--mode",
        choices=["one_shot", "loop", "both"],
        default="both",
        help="Evaluation mode (default: both)",
    )
    p_bench.add_argument("--output", help="Prefix path for saving benchmark results JSON")

    # serve
    p_serve = subparsers.add_parser("serve", help="Start FastAPI application server")
    p_serve.add_argument("--host", default="0.0.0.0", help="Host interface (default: 0.0.0.0)")
    p_serve.add_argument("--port", type=int, default=8000, help="Port (default: 8000)")
    p_serve.add_argument("--reload", action="store_true", help="Enable auto-reload for development")

    # check-skill
    subparsers.add_parser("check-skill", help="Validate Agent Skill Open Standard compliance")

    args = parser.parse_args()
    if not args.subcommand:
        parser.print_help()
        return 1

    if args.subcommand == "repair":
        return cmd_repair(args)
    elif args.subcommand == "benchmark":
        return cmd_benchmark(args)
    elif args.subcommand == "serve":
        return cmd_serve(args)
    elif args.subcommand == "check-skill":
        return cmd_check_skill(args)

    return 0


if __name__ == "__main__":
    sys.exit(main())
