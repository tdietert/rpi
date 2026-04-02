"""RPI: Review-Plan-Implement-Fix automation.

Orchestrates plan-review, implement, and review-fix loops by calling
the Claude CLI with structured JSON responses via --json-schema.

Usage:
    rpi --prompt "build the feature"
    rpi --plan .claude/plans/2026-03-08-my-feature.md
    rpi --spec spec.md --prompt "build the feature"
    DRY_RUN=1 rpi --prompt "build the feature"
"""

from __future__ import annotations

import argparse
import atexit
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from rich.console import Console
from rich.table import Table

from .config import Config
from .display import Display
from .plan import Plan, PlanMetadata, parse_plan_metadata, validate_plan_file
from .process import cleanup_children, sigint_handler
from .progress import SnapshotStageProgress
from .snapshot import (
    create_snapshot_dir,
    load_snapshot,
    restore_from_snapshot,
    save_snapshot,
)
from .stage_name import StageName
from .stages import (
    PIPELINE,
    PipelineContext,
    print_summary,
    skips_stage,
)


@dataclass
class _RunState:
    """Mutable container for interrupt-safe snapshot."""
    snap_dir: Path | None = None
    config: Config | None = None
    progress: SnapshotStageProgress | None = None
    plan: Plan | None = None
    work_dir: Path | None = None
    display: Display | None = None


_run_state = _RunState()


def _try_save_exit_snapshot(label: str) -> None:
    """Best-effort snapshot save on any exit path."""
    disp = _run_state.display
    if (
        _run_state.snap_dir is not None
        and _run_state.config is not None
        and _run_state.progress is not None
        and _run_state.work_dir is not None
    ):
        try:
            save_snapshot(
                _run_state.snap_dir,
                _run_state.config,
                _run_state.progress,
                _run_state.plan,
                _run_state.work_dir,
            )
            if disp is not None:
                disp.info(f"{label} Snapshot saved to: {_run_state.snap_dir}")
        except Exception:
            if disp is not None:
                disp.info(f"{label} (snapshot save failed)")
    else:
        if disp is not None:
            disp.info(label)


def _parse_args() -> argparse.Namespace:
    """Build and parse CLI arguments."""
    parser = argparse.ArgumentParser(
        description="RPI: Review-Plan-Implement-Fix automation",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Subcommands:\n"
        "  rpi install-skills [--force]   Install skills to ~/.claude/skills/ (symlinks)\n"
        "  rpi uninstall-skills            Remove installed skill symlinks\n\n"
        "Environment variables: MIN_SCORE, MAX_REVIEW_ITERS, MAX_FIX_ITERS, "
        "REVIEW_QUORUM, SKIP_PLAN_REVIEW, SKIP_IMPLEMENT, SKIP_FIX, SKIP_COMMIT, SKIP_PR, PUSH, WORKTREE, DRY_RUN",
    )
    parser.add_argument("--plan", type=Path, dest="plan_path", default=None, help="Path to an existing plan file (starts at plan review)")
    parser.add_argument("--min-score", type=int, default=None, help="Minimum review score (0-10, default: 8)")
    parser.add_argument("--max-review-iters", type=int, default=None, help="Max plan review iterations (default: 3)")
    parser.add_argument("--max-fix-iters", type=int, default=None, help="Max review-fix iterations (default: 3)")
    parser.add_argument("--quorum", type=int, default=None, help="Number of parallel reviewers (default: 3)")
    parser.add_argument("--skip-plan-review", action="store_true", default=None, help="Skip plan review stage")
    parser.add_argument("--skip-implement", action="store_true", default=None, help="Skip implementation stage (jump straight to review-fix)")
    parser.add_argument("--skip-fix", action="store_true", default=None, help="Skip review-fix stage")
    parser.add_argument("--skip-commit", action="store_true", default=None, help="Skip commit stage")
    parser.add_argument("--skip-pr", action="store_true", default=None, help="Skip PR creation stage")
    parser.add_argument("--push", action="store_true", default=None, help="Push to current branch after commit (skips PR creation)")
    parser.add_argument("--worktree", action="store_true", default=None, help="Run in an isolated git worktree (new branch from main)")
    parser.add_argument("--worktree-name", default=None, help="Custom branch/slug name for the worktree (implies --worktree). E.g. --worktree-name my-feature creates branch rpi/my-feature.")
    parser.add_argument("--worktree-base", default=None, help="Base branch for the new worktree (default: main). E.g. --worktree-base develop")
    parser.add_argument("--worktree-path", default=None, help="Path to an existing git worktree to reuse (e.g. from a previous run). Skips worktree creation.")
    parser.add_argument("--dry-run", action="store_true", default=None, help="Print commands without executing")
    parser.add_argument("--resume", type=Path, default=None, help="Resume from a snapshot directory (e.g. ~/.claude/snapshots/rpi-my-feature-20260322-143000)")
    parser.add_argument("--list-snapshots", action="store_true", default=False, help="List available snapshots and exit")
    parser.add_argument("--prompt", type=str, default=None, help="Task description (optional additional context when used with --plan)")
    parser.add_argument("--research", type=Path, default=None, help="Existing research file (skips research stage)")
    parser.add_argument("--spec", type=Path, default=None, help="Existing spec file (skips research + spec stages)")
    parser.add_argument("--verbose", "-v", action="store_true", default=False, help="Verbose output (show streaming details)")
    return parser.parse_args()


def _list_snapshots() -> None:
    """Print snapshot table and exit."""
    console = Console()
    snap_base = Path.home() / ".claude" / "snapshots"
    if not snap_base.is_dir():
        console.print("No snapshots found.")
        sys.exit(0)
    snap_dirs = sorted(snap_base.glob("rpi-*/snapshot.json"))
    if not snap_dirs:
        console.print("No snapshots found.")
        sys.exit(0)
    table = Table(title="RPI Snapshots")
    table.add_column("Plan", style="cyan", min_width=30)
    table.add_column("Timestamp", min_width=20)
    table.add_column("Progress")
    table.add_column("Path", style="dim")
    for snap_path in snap_dirs:
        try:
            snap = load_snapshot(snap_path.parent)
            p = snap.progress
            stages = []
            if p.research_done:
                stages.append("research")
            if p.spec_draft_done:
                stages.append("spec-draft")
            if p.plan_draft_done:
                stages.append("plan-draft")
            if p.plan_review_done:
                stages.append("review")
            if p.implementation_done:
                stages.append("impl")
            elif p.implementation and p.implementation.completed_phases:
                stages.append(f"impl({len(p.implementation.completed_phases)} phases)")
            if p.review_fix_done:
                stages.append("fix")
            if p.commit_done:
                stages.append("commit")
            if p.push_or_pr_done:
                stages.append("pr")
            progress_str = ", ".join(stages) if stages else "(none)"
            title = Path(snap.config.plan_path).stem[:38] if snap.config.plan_path else (snap.config.prompt[:38] if snap.config.prompt else snap_path.parent.name)
            table.add_row(title, snap.timestamp, progress_str, str(snap_path.parent))
        except Exception as e:
            table.add_row(snap_path.parent.name, "", f"(error: {e})", "")
    console.print(table)
    sys.exit(0)


def _collect_prompt(args: argparse.Namespace, disp: Display) -> str:
    """Resolve prompt from args or interactive input.

    Prompt is optional when an upstream artifact (--plan, --spec, or
    --research) is provided, since those artifacts already contain the
    task context.
    """
    prompt = args.prompt or ""
    has_artifact = (
        args.plan_path is not None
        or args.spec is not None
        or args.research is not None
    )
    if not has_artifact and not prompt:
        prompt = disp.collect_feedback("Task description", is_initial_input=True) or ""
        if not prompt:
            disp.error(
                "No task description provided. Usage:\n"
                "  rpi --plan <plan-file>   # run with existing plan\n"
                "  rpi --spec <spec-file>   # start from plan draft\n"
                "  rpi --prompt \"...\"        # start from research\n"
                "  rpi                       # interactive mode"
            )
            sys.exit(1)
    return prompt


def _create_worktree(args: argparse.Namespace, prompt: str, disp: Display) -> str:
    """Resolve or create a git worktree. Returns worktree path (empty if none)."""
    if args.worktree_path:
        existing = Path(args.worktree_path).resolve()
        if not existing.is_dir():
            disp.error(f"Worktree path does not exist: {existing}")
            sys.exit(1)
        return str(existing)

    worktree_name = args.worktree_name
    use_worktree = (
        args.worktree or worktree_name is not None
        if args.worktree is not None or worktree_name is not None
        else os.environ.get("WORKTREE", "0") == "1"
    )
    if not use_worktree:
        return ""

    repo_root = Path(
        subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
    )

    ts = int(time.time())
    if worktree_name:
        slug = re.sub(r"[^a-zA-Z0-9_-]", "-", worktree_name).strip("-")
        if not slug:
            slug = f"rpi-{ts}"
    elif args.plan_path is not None:
        plan_stem = args.plan_path.stem
        slug = re.sub(r"^\d{4}-\d{2}-\d{2}-?", "", plan_stem)
        slug = re.sub(r"[^a-zA-Z0-9_-]", "-", slug).strip("-")
        if not slug:
            slug = f"rpi-{ts}"
    elif prompt:
        words = prompt.split()[:4]
        slug = "-".join(words) if words else ""
        slug = re.sub(r"[^a-zA-Z0-9_-]", "-", slug).strip("-")
        if not slug:
            slug = f"rpi-{ts}"
    elif args.spec is not None:
        slug = re.sub(r"[^a-zA-Z0-9_-]", "-", args.spec.stem).strip("-") or f"rpi-{ts}"
    elif args.research is not None:
        slug = re.sub(r"[^a-zA-Z0-9_-]", "-", args.research.stem).strip("-") or f"rpi-{ts}"
    else:
        slug = f"rpi-{ts}"

    branch_name = f"rpi/{slug}"
    base_branch = args.worktree_base or "main"
    worktree_name = f"{slug}-{ts}"
    worktree_dir = repo_root / ".claude" / "worktrees" / worktree_name
    worktree_dir.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        ["git", "worktree", "add", "-b", branch_name, str(worktree_dir), base_branch],
        capture_output=True, text=True, cwd=str(repo_root),
    )
    if result.returncode != 0:
        disp.error(f"Failed to create worktree: {result.stderr.strip()}")
        sys.exit(1)
    return str(worktree_dir)


# "If this arg has a value, the user provided the output of this stage"
_ARG_STAGE_OUTPUT: dict[str, StageName] = {
    "plan_path":     StageName.plan_draft,
    "spec":          StageName.spec_draft,
    "research":      StageName.research,
}


def _resolve_start_from(args: argparse.Namespace) -> StageName:
    """Determine which stage to start from based on artifact args."""
    provided = [
        stage for attr, stage in _ARG_STAGE_OUTPUT.items()
        if getattr(args, attr, None) is not None
    ]
    if not provided:
        return StageName.research
    stage_order = [S.name for S in PIPELINE]
    best = max(provided, key=lambda s: stage_order.index(s))
    idx = stage_order.index(best)
    return stage_order[idx + 1] if idx + 1 < len(stage_order) else stage_order[-1]


def _build_config(args: argparse.Namespace, prompt: str, worktree_path: str) -> Config:
    """Construct Config with CLI flags overriding env-var defaults."""
    push = (
        args.push
        if args.push is not None
        else os.environ.get("PUSH", "0") == "1"
    )
    return Config(
        plan_path=args.plan_path.resolve() if args.plan_path else None,
        min_score=args.min_score or int(os.environ.get("MIN_SCORE", "8")),
        max_review_iters=args.max_review_iters or int(os.environ.get("MAX_REVIEW_ITERS", "3")),
        max_fix_iters=args.max_fix_iters or int(os.environ.get("MAX_FIX_ITERS", "3")),
        review_quorum=args.quorum or int(os.environ.get("REVIEW_QUORUM", "3")),
        start_from=_resolve_start_from(args),
        skip_plan_review=args.skip_plan_review if args.skip_plan_review is not None else os.environ.get("SKIP_PLAN_REVIEW", "0") == "1",
        skip_implement=args.skip_implement if args.skip_implement is not None else os.environ.get("SKIP_IMPLEMENT", "0") == "1",
        skip_fix=args.skip_fix if args.skip_fix is not None else os.environ.get("SKIP_FIX", "0") == "1",
        skip_commit=args.skip_commit if args.skip_commit is not None else os.environ.get("SKIP_COMMIT", "0") == "1",
        skip_pr=(args.skip_pr if args.skip_pr is not None else os.environ.get("SKIP_PR", "0") == "1") or push,
        push=push,
        worktree=worktree_path,
        dry_run=args.dry_run if args.dry_run is not None else os.environ.get("DRY_RUN", "0") == "1",
        prompt=prompt,
        research_path=args.research.resolve() if args.research else None,
        spec_path=args.spec.resolve() if args.spec else None,
    )


def _render_banner(
    config: Config,
    meta: PlanMetadata | None,
    snap_dir: Path,
    work_dir: Path,
    disp: Display,
    *,
    progress: SnapshotStageProgress | None = None,
    resume_completed_phases: set[int] | None = None,
) -> None:
    """Print startup banner for both fresh and resume paths."""
    if progress is not None and resume_completed_phases is not None:
        # Resume path
        fields = [("Plan", meta.title if meta else config.prompt[:60])]
        if config.plan_path:
            fields.append(("File", str(config.plan_path)))
        if meta and meta.num_phases:
            fields.append(("Phases", str(meta.num_phases)))
        fields.append(("Snapshot", str(snap_dir)))
        if resume_completed_phases:
            fields.append(("Resuming", f"skipping phases {sorted(resume_completed_phases)}"))
        completed_stages = []
        if progress.research_done:
            completed_stages.append("research")
        if progress.spec_draft_done:
            completed_stages.append("spec-draft")
        if progress.plan_draft_done:
            completed_stages.append("plan-draft")
        if progress.plan_review_done:
            completed_stages.append("plan-review")
        if progress.implementation_done:
            completed_stages.append("implement")
        if progress.review_fix_done:
            completed_stages.append("review-fix")
        if progress.commit_done:
            completed_stages.append("commit")
        if progress.push_or_pr_done:
            completed_stages.append("push/pr")
        if completed_stages:
            fields.append(("Done", ", ".join(completed_stages)))
        fields.append(("Work", str(work_dir)))
    elif meta:
        fields = [
            ("Plan", meta.title),
            ("File", str(config.plan_path)),
            ("Phases", str(meta.num_phases)),
            ("Config", f"min_score={config.min_score}, max_review={config.max_review_iters}, "
             f"max_fix={config.max_fix_iters}, quorum={config.review_quorum}"),
        ]
    else:
        fields = []
        if config.prompt:
            fields.append(("Prompt", config.prompt))
        fields.append(
            ("Config", f"min_score={config.min_score}, max_review={config.max_review_iters}, "
             f"max_fix={config.max_fix_iters}, quorum={config.review_quorum}"),
        )
        if config.research_path:
            fields.append(("Research", str(config.research_path)))
        if config.spec_path:
            fields.append(("Spec", str(config.spec_path)))

    if progress is None:
        # Fresh-run extras
        if config.worktree:
            fields.append(("Worktree", config.worktree))
        skips = [
            S.name.value.replace("_", "-") for S in PIPELINE
            if skips_stage(config, S.name)
        ]
        if skips:
            fields.append(("Skip", ", ".join(skips)))
        if config.push:
            fields.append(("Push", "yes (commit + push, no PR)"))
        fields.append(("Snapshot", str(snap_dir)))
        fields.append(("Work", str(work_dir)))

    disp.banner("Review-Implement-Fix", fields)


def _run() -> None:
    args = _parse_args()
    start_time = time.monotonic()

    if args.list_snapshots:
        _list_snapshots()

    # Temporary display for early error paths (replaced after snapshot dir is created)
    disp = Display(verbose=args.verbose, log_dir=Path(tempfile.gettempdir()) / "rpi-early-logs")

    resume_completed_phases: set[int] = set()
    snap_dir: Path | None = None
    progress = SnapshotStageProgress()
    plan = None

    if args.resume:
        config, plan_restored, work_dir, progress = restore_from_snapshot(
            args.resume.resolve(), display=disp
        )
        snap_dir = args.resume.resolve()
        plan = plan_restored
        disp = Display(verbose=args.verbose, log_dir=snap_dir / "logs")

        # Set start_from for completed early stages
        if progress.plan_draft_done:
            config.start_from = StageName.preflight
        elif progress.spec_draft_done:
            config.start_from = StageName.plan_draft
        elif progress.research_done:
            config.start_from = StageName.spec_draft

        # Set skip flags for completed later stages
        if progress.plan_review_done:
            config.skip_plan_review = True
        if progress.implementation_done:
            config.skip_implement = True
        if progress.review_fix_done:
            config.skip_fix = True
        if progress.commit_done:
            config.skip_commit = True
        if progress.push_or_pr_done:
            config.skip_pr = True

        if progress.implementation and not progress.implementation_done:
            resume_completed_phases = set(progress.implementation.completed_phases)

        if config.plan_path:
            meta = parse_plan_metadata(config.plan_path)
        elif progress.plan_draft_done:
            meta = PlanMetadata(title=config.prompt[:60], num_phases=0, completed_phases=0)
        else:
            meta = None

        _run_state.snap_dir = snap_dir
        _run_state.config = config
        _run_state.progress = progress
        _run_state.plan = plan
        _run_state.work_dir = work_dir
        _run_state.display = disp

        _render_banner(config, meta, snap_dir, work_dir, disp,
                       progress=progress, resume_completed_phases=resume_completed_phases)

    else:
        prompt = _collect_prompt(args, disp)
        worktree_path = _create_worktree(args, prompt, disp)
        config = _build_config(args, prompt, worktree_path)

        if config.plan_path is not None:
            if not config.plan_path.is_file():
                disp.error(f"Plan file not found: {config.plan_path}")
                sys.exit(1)
            validation_errors = validate_plan_file(config.plan_path)
            if validation_errors:
                disp.error(f"{config.plan_path} does not look like a plan file:")
                for err in validation_errors:
                    disp.info(f"  - {err}")
                disp.info(
                    "RPI expects a plan file from .claude/plans/ with implementation phases, "
                    "task definitions, group labels, and verification commands. "
                    "If this is a spec, run '/plan' on it first."
                )
                sys.exit(1)

        work_dir = Path(tempfile.mkdtemp(prefix="rpi-"))
        atexit.register(shutil.rmtree, str(work_dir))
        snap_dir = create_snapshot_dir(config)
        disp = Display(verbose=args.verbose, log_dir=snap_dir / "logs")

        _run_state.snap_dir = snap_dir
        _run_state.config = config
        _run_state.progress = progress
        _run_state.work_dir = work_dir
        _run_state.display = disp

        meta = parse_plan_metadata(config.plan_path) if config.plan_path is not None else None
        _render_banner(config, meta, snap_dir, work_dir, disp)

    ctx = PipelineContext(
        config=config,
        work_dir=work_dir,
        snap_dir=snap_dir,
        progress=progress,
        display=disp,
        meta=meta,
        plan=plan,
        resume_completed_phases=resume_completed_phases,
    )

    if config.research_path:
        ctx.research_path = config.research_path
    if config.spec_path:
        ctx.spec_path = config.spec_path

    _run_state.plan = plan

    stages = [S() for S in PIPELINE]
    for stage in stages:
        if skips_stage(config, stage.name):
            ctx.display.info(f"[dim]{stage.label} -- SKIPPED[/dim]")
            continue
        stage.execute(ctx)
        _run_state.progress = ctx.progress
        _run_state.plan = ctx.plan

    elapsed = time.monotonic() - start_time
    print_summary(ctx, total_elapsed=elapsed)


def _handle_subcommands() -> bool:
    """Check for subcommands that bypass the normal pipeline. Returns True if handled."""
    if len(sys.argv) < 2:
        return False

    cmd = sys.argv[1]
    console = Console()

    if cmd == "install-skills":
        from .skills import install_skills

        force = "--force" in sys.argv[2:]
        console.print("[bold]Installing RPI skills...[/bold]")
        for msg in install_skills(force=force):
            console.print(msg)
        console.print("[green]Done.[/green]")
        sys.exit(0)

    if cmd == "uninstall-skills":
        from .skills import uninstall_skills

        console.print("[bold]Uninstalling RPI skills...[/bold]")
        for msg in uninstall_skills():
            console.print(msg)
        console.print("[green]Done.[/green]")
        sys.exit(0)

    return False


def main() -> None:
    _handle_subcommands()
    signal.signal(signal.SIGINT, sigint_handler)
    try:
        _run()
    except KeyboardInterrupt:
        cleanup_children()
        _try_save_exit_snapshot("Interrupted.")
        sys.exit(130)
    except SystemExit as exc:
        if exc.code != 0:
            _try_save_exit_snapshot("Exited with error.")
        raise
    except Exception:
        _try_save_exit_snapshot("Crashed.")
        raise


if __name__ == "__main__":
    main()
