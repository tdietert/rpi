"""RPI: Review-Plan-Implement-Fix automation.

Orchestrates plan-review, implement, and review-fix loops by calling
the Claude CLI with structured JSON responses via --json-schema.

Usage:
    rpi <plan-file>
    rpi .claude/plans/2026-03-08-my-feature.md
    DRY_RUN=1 rpi <plan-file>
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
from pathlib import Path

from rich.table import Table

from .display import display
from .plan import PlanMetadata, parse_plan_metadata, validate_plan_file
from .process import _run_state, _sigint_handler, cleanup_children
from .snapshot import (
    SnapshotPhaseProgress,
    SnapshotReviewProgress,
    SnapshotStageProgress,
    create_snapshot_dir,
    load_snapshot,
    restore_from_snapshot,
    save_snapshot,
)
from .stages import (
    CommitStage,
    ImplementStage,
    PipelineContext,
    PlanDraftStage,
    PlanReviewStage,
    PreflightStage,
    PushPrStage,
    ResearchStage,
    ReviewFixStage,
    print_summary,
)
from .types import Config


def _try_save_exit_snapshot(label: str) -> None:
    """Best-effort snapshot save on any exit path."""
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
            display.console.print(f"\n  {label} Snapshot saved to: {_run_state.snap_dir}")
        except Exception:
            display.console.print(f"\n  {label} (snapshot save failed)")
    else:
        display.console.print(f"\n  {label}")


def _run() -> None:
    parser = argparse.ArgumentParser(
        description="RPI: Review-Plan-Implement-Fix automation",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Environment variables: MIN_SCORE, MAX_REVIEW_ITERS, MAX_FIX_ITERS, "
        "REVIEW_QUORUM, SKIP_PLAN_REVIEW, SKIP_IMPLEMENT, SKIP_FIX, SKIP_COMMIT, SKIP_PR, PUSH, WORKTREE, DRY_RUN",
    )
    parser.add_argument("plan_path", type=Path, nargs="?", default=None, help="Path to the plan file")
    parser.add_argument(
        "--min-score",
        type=int,
        default=None,
        help="Minimum review score (0-10, default: 8)",
    )
    parser.add_argument(
        "--max-review-iters",
        type=int,
        default=None,
        help="Max plan review iterations (default: 5)",
    )
    parser.add_argument(
        "--max-fix-iters",
        type=int,
        default=None,
        help="Max review-fix iterations (default: 5)",
    )
    parser.add_argument(
        "--quorum",
        type=int,
        default=None,
        help="Number of parallel reviewers (default: 3)",
    )
    parser.add_argument(
        "--skip-plan-review",
        action="store_true",
        default=None,
        help="Skip plan review stage",
    )
    parser.add_argument(
        "--skip-implement",
        action="store_true",
        default=None,
        help="Skip implementation stage (jump straight to review-fix)",
    )
    parser.add_argument(
        "--skip-fix",
        action="store_true",
        default=None,
        help="Skip review-fix stage",
    )
    parser.add_argument(
        "--skip-commit",
        action="store_true",
        default=None,
        help="Skip commit stage",
    )
    parser.add_argument(
        "--skip-pr",
        action="store_true",
        default=None,
        help="Skip PR creation stage",
    )
    parser.add_argument(
        "--push",
        action="store_true",
        default=None,
        help="Push to current branch after commit (skips PR creation)",
    )
    parser.add_argument(
        "--worktree",
        action="store_true",
        default=None,
        help="Run in an isolated git worktree (new branch from main)",
    )
    parser.add_argument(
        "--worktree-name",
        default=None,
        help="Custom branch/slug name for the worktree (implies --worktree). "
        "E.g. --worktree-name my-feature creates branch rpi/my-feature.",
    )
    parser.add_argument(
        "--worktree-base",
        default=None,
        help="Base branch for the new worktree (default: main). "
        "E.g. --worktree-base develop",
    )
    parser.add_argument(
        "--worktree-path",
        default=None,
        help="Path to an existing git worktree to reuse (e.g. from a previous run). "
        "Skips worktree creation.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=None,
        help="Print commands without executing",
    )
    parser.add_argument(
        "--resume",
        type=Path,
        default=None,
        help="Resume from a snapshot directory (e.g. ~/.claude/snapshots/rpi-my-feature-20260322-143000)",
    )
    parser.add_argument(
        "--list-snapshots",
        action="store_true",
        default=False,
        help="List available snapshots and exit",
    )
    parser.add_argument(
        "--prompt",
        type=str,
        default=None,
        help="Task description — starts from research stage instead of requiring a plan file",
    )
    parser.add_argument(
        "--research",
        type=Path,
        default=None,
        help="Existing research file — skips research stage",
    )
    parser.add_argument(
        "--skip-research",
        action="store_true",
        default=False,
        help="Skip the research stage (go straight to plan draft)",
    )
    args = parser.parse_args()

    # -- Handle --list-snapshots early -----------------------------------------
    if args.list_snapshots:
        snap_base = Path.home() / ".claude" / "snapshots"
        if not snap_base.is_dir():
            display.stdout.print("No snapshots found.")
            sys.exit(0)
        snap_dirs = sorted(snap_base.glob("rpi-*/snapshot.json"))
        if not snap_dirs:
            display.stdout.print("No snapshots found.")
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
                title = Path(snap.plan_path).stem[:38] if snap.plan_path else (snap.prompt[:38] if snap.prompt else snap_path.parent.name)
                table.add_row(title, snap.timestamp, progress_str, str(snap_path.parent))
            except Exception as e:
                table.add_row(snap_path.parent.name, "", f"(error: {e})", "")
        display.stdout.print(table)
        sys.exit(0)

    # -- Resume or fresh run ---------------------------------------------------
    resume_completed_phases: set[int] = set()
    snap_dir: Path | None = None
    progress = SnapshotStageProgress()
    plan = None

    if args.resume:
        # Resume from snapshot
        config, plan_restored, work_dir, progress = restore_from_snapshot(
            args.resume.resolve()
        )
        snap_dir = args.resume.resolve()
        plan = plan_restored

        # Set skip flags for completed stages
        if progress.research_done:
            config.skip_research = True
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

        # Track which implementation phases to skip
        if progress.implementation and not progress.implementation_done:
            resume_completed_phases = set(progress.implementation.completed_phases)

        if config.plan_path:
            meta = parse_plan_metadata(config.plan_path)
        elif progress.plan_draft_done:
            # Prompt-mode with plan draft completed but plan_path not restored
            meta = PlanMetadata(title=config.prompt[:60], num_phases=0, completed_phases=0)
        else:
            # Prompt-mode interrupted before plan draft — PlanDraftStage will set meta
            meta = None

        # Populate _run_state for interrupt-safe snapshots
        _run_state.snap_dir = snap_dir
        _run_state.config = config
        _run_state.progress = progress
        _run_state.plan = plan
        _run_state.work_dir = work_dir

        # Startup banner
        fields = [
            ("Plan", meta.title if meta else config.prompt[:60]),
        ]
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
        display.banner("Review-Implement-Fix", "RESUMED", fields)

    else:
        # Determine invocation mode and prompt
        prompt = args.prompt or ""
        if args.plan_path is None and not prompt:
            # Interactive mode: collect task description
            from .feedback import collect_feedback
            prompt = collect_feedback("Task description", is_initial_input=True) or ""
            if not prompt:
                display.error(
                    "No task description provided. Usage:\n"
                    "  rpi <plan-file>          # run with existing plan\n"
                    "  rpi --prompt \"...\"        # start from research\n"
                    "  rpi                       # interactive mode"
                )
                sys.exit(1)

        # Build config: CLI flags override env vars, env vars override defaults
        push = (
            args.push
            if args.push is not None
            else os.environ.get("PUSH", "0") == "1"
        )
        worktree_name = args.worktree_name
        worktree_path = ""

        if args.worktree_path:
            # Reuse an existing worktree
            existing = Path(args.worktree_path).resolve()
            if not existing.is_dir():
                display.error(f"Worktree path does not exist: {existing}")
                sys.exit(1)
            worktree_path = str(existing)
        else:
            use_worktree = (
                args.worktree or worktree_name is not None
                if args.worktree is not None or worktree_name is not None
                else os.environ.get("WORKTREE", "0") == "1"
            )
            # Pre-create a shared git worktree so all claude instances use cwd into it
            if use_worktree:
                repo_root = Path(
                    subprocess.run(
                        ["git", "rev-parse", "--show-toplevel"],
                        capture_output=True,
                        text=True,
                        check=True,
                    ).stdout.strip()
                )

                ts = int(time.time())
                if worktree_name:
                    # Custom name provided: --worktree-name my-feature
                    slug = re.sub(r"[^a-zA-Z0-9_-]", "-", worktree_name).strip("-")
                    if not slug:
                        slug = f"rpi-{ts}"
                elif args.plan_path is not None:
                    # Auto-derive from plan filename: 2026-03-11-add-search.md -> add-search
                    plan_stem = args.plan_path.stem
                    slug = re.sub(r"^\d{4}-\d{2}-\d{2}-?", "", plan_stem)
                    slug = re.sub(r"[^a-zA-Z0-9_-]", "-", slug).strip("-")
                    if not slug:
                        slug = f"rpi-{ts}"
                else:
                    # Derive from prompt in prompt mode
                    words = prompt.split()[:4]
                    slug = "-".join(words) if words else ""
                    slug = re.sub(r"[^a-zA-Z0-9_-]", "-", slug).strip("-")
                    if not slug:
                        slug = f"rpi-{ts}"
                branch_name = f"rpi/{slug}"
                base_branch = args.worktree_base or "main"
                worktree_name = f"{slug}-{ts}"
                worktree_dir = repo_root / ".claude" / "worktrees" / worktree_name
                worktree_dir.parent.mkdir(parents=True, exist_ok=True)
                result = subprocess.run(
                    ["git", "worktree", "add", "-b", branch_name, str(worktree_dir), base_branch],
                    capture_output=True,
                    text=True,
                    cwd=str(repo_root),
                )
                if result.returncode != 0:
                    display.error(f"Failed to create worktree: {result.stderr.strip()}")
                    sys.exit(1)
                worktree_path = str(worktree_dir)

        config = Config(
            plan_path=args.plan_path.resolve() if args.plan_path else None,
            min_score=args.min_score or int(os.environ.get("MIN_SCORE", "8")),
            max_review_iters=args.max_review_iters
            or int(os.environ.get("MAX_REVIEW_ITERS", "5")),
            max_fix_iters=args.max_fix_iters
            or int(os.environ.get("MAX_FIX_ITERS", "5")),
            review_quorum=args.quorum
            or int(os.environ.get("REVIEW_QUORUM", "3")),
            skip_plan_review=args.skip_plan_review
            if args.skip_plan_review is not None
            else os.environ.get("SKIP_PLAN_REVIEW", "0") == "1",
            skip_implement=args.skip_implement
            if args.skip_implement is not None
            else os.environ.get("SKIP_IMPLEMENT", "0") == "1",
            skip_fix=args.skip_fix
            if args.skip_fix is not None
            else os.environ.get("SKIP_FIX", "0") == "1",
            skip_commit=args.skip_commit
            if args.skip_commit is not None
            else os.environ.get("SKIP_COMMIT", "0") == "1",
            skip_pr=(
                args.skip_pr
                if args.skip_pr is not None
                else os.environ.get("SKIP_PR", "0") == "1"
            ) or push,  # --push implies --skip-pr
            push=push,
            worktree=worktree_path,
            dry_run=args.dry_run
            if args.dry_run is not None
            else os.environ.get("DRY_RUN", "0") == "1",
            prompt=prompt,
            skip_research=args.skip_research or (args.research is not None),
            research_path=args.research.resolve() if args.research else None,
        )

        if config.plan_path is not None:
            if not config.plan_path.is_file():
                display.error(f"Plan file not found: {config.plan_path}")
                sys.exit(1)

            # Pre-flight: validate the file looks like a plan (not a spec/research doc)
            validation_errors = validate_plan_file(config.plan_path)
            if validation_errors:
                display.error(f"{config.plan_path} does not look like a plan file:")
                for err in validation_errors:
                    display.info(f"  - {err}")
                display.info(
                    "RPI expects a plan file from .claude/plans/ with implementation phases, "
                    "task definitions, group labels, and verification commands. "
                    "If this is a spec, run '/plan' on it first."
                )
                sys.exit(1)

        # Create shared workspace
        work_dir = Path(tempfile.mkdtemp(prefix="rpi-"))
        atexit.register(shutil.rmtree, str(work_dir))

        # Create snapshot directory
        snap_dir = create_snapshot_dir(config)

        # Populate _run_state for interrupt-safe snapshots
        _run_state.snap_dir = snap_dir
        _run_state.config = config
        _run_state.progress = progress
        _run_state.work_dir = work_dir

        # Parse plan metadata (deferred in prompt mode until plan draft completes)
        if config.plan_path is not None:
            meta = parse_plan_metadata(config.plan_path)
        else:
            meta = None

        # Startup banner
        if meta:
            fields = [
                ("Plan", meta.title),
                ("File", str(config.plan_path)),
                ("Phases", str(meta.num_phases)),
                ("Config", f"min_score={config.min_score}, max_review={config.max_review_iters}, "
                 f"max_fix={config.max_fix_iters}, quorum={config.review_quorum}"),
            ]
        else:
            fields = [
                ("Prompt", prompt),
                ("Config", f"min_score={config.min_score}, max_review={config.max_review_iters}, "
                 f"max_fix={config.max_fix_iters}, quorum={config.review_quorum}"),
            ]
            if config.research_path:
                fields.append(("Research", str(config.research_path)))
        if config.worktree:
            fields.append(("Worktree", config.worktree))
        skips = [
            name
            for name, flag in [
                ("plan-review", config.skip_plan_review),
                ("implement", config.skip_implement),
                ("fix", config.skip_fix),
                ("commit", config.skip_commit),
                ("pr", config.skip_pr),
            ]
            if flag
        ]
        if skips:
            fields.append(("Skip", ", ".join(skips)))
        if config.push:
            fields.append(("Push", "yes (commit + push, no PR)"))
        fields.append(("Snapshot", str(snap_dir)))
        fields.append(("Work", str(work_dir)))
        display.banner("Review-Implement-Fix", "", fields)

    # -- Build pipeline context and run stages ---------------------------------
    ctx = PipelineContext(
        config=config,
        work_dir=work_dir,
        snap_dir=snap_dir,
        progress=progress,
        meta=meta,
        plan=plan,
        resume_completed_phases=resume_completed_phases,
    )

    if config.research_path:
        ctx.research_path = config.research_path

    # Update _run_state for interrupt-safe snapshots
    _run_state.plan = plan

    stages = [
        ResearchStage(),
        PlanDraftStage(),
        PreflightStage(),
        PlanReviewStage(),
        ImplementStage(),
        ReviewFixStage(),
        CommitStage(),
        PushPrStage(),
    ]
    for stage in stages:
        stage.execute(ctx)
        # Keep _run_state in sync
        _run_state.progress = ctx.progress
        _run_state.plan = ctx.plan

    print_summary(ctx)


def main() -> None:
    signal.signal(signal.SIGINT, _sigint_handler)
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
