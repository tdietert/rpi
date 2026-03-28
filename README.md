# RPI: Review-Plan-Implement-Fix

Automated orchestrator that takes a structured implementation plan and drives it through review, implementation, code review, commit, and PR creation — all via the Claude CLI.

## Install

```bash
# Editable install (changes are live during development)
uv tool install -e ~/Code/rpi

# Or standard install
uv tool install ~/Code/rpi
```

## Quick Start

```bash
# Run a plan end-to-end
rpi .claude/plans/my-feature.md

# Run in an isolated git worktree
rpi .claude/plans/my-feature.md --worktree

# Use the caffeinate wrapper on macOS (prevents sleep during long runs)
rpi-caffeinate .claude/plans/my-feature.md --worktree
```

## Prerequisites

- Python 3.11+
- [uv](https://docs.astral.sh/uv/)
- `claude` CLI in PATH
- Git

## How It Works

RPI reads a markdown plan file, parses it into structured phases/tasks, then executes 5 stages sequentially:

```
Pre-flight → Stage 1: Plan Review → Stage 2: Implementation → Stage 3: Review-Fix → Stage 4: Commit → Stage 5: PR
```

Each stage invokes the Claude CLI with structured JSON schemas (`--json-schema`) to get typed responses. Review stages use a parallel quorum of reviewers for reliability.

### Pre-flight: Plan Parsing

Parses the plan file into a `ParsedPlan` with phases, tasks, groups, and verification commands. Validates structure (sequential phase numbering, unique task IDs, no cross-group file overlap). Auto-fixes structural issues up to 3 times before failing.

### Stage 1: Plan Review Loop

Launches N parallel reviewers (default: 3) that score the plan on correctness, completeness, simplicity, and clarity (each 0-5, total 0-20). Feedback is synthesized and applied to the plan file. Iterates until the score meets the threshold or max iterations are exhausted.

### Stage 2: Implementation

Implements phases one at a time. Within each phase, tasks are grouped (A, B, C...) for parallelism hints. If the phase has verification commands, they're run via `subprocess` after each attempt — max 3 attempts. Without verification commands, max 2 attempts.

On failure, the error context is passed to the next attempt. After all retries, prompts the user to continue or abort.

### Stage 3: Review-Fix Loop

Same quorum pattern as Stage 1, but reviewing uncommitted code changes. Reviewers score the code; if there are critical issues, a fix agent applies changes. Iterates until clean or max iterations reached.

### Stage 4: Commit

Groups related changes into logical commits.

### Stage 5: Push / Create PR

Either pushes to the current branch (`--push`) or creates a GitHub PR.

### Non-Convergence Diagnosis

If a review loop (plan review or review-fix) doesn't converge within the max iterations, RPI runs a diagnosis agent that identifies the pattern (circular, oscillating, structural disagreement, diminishing returns, fixer blind spot) and recommends next steps.

## Plan File Format

Plans live in `.claude/plans/` and use this structure:

```markdown
---
date: 2026-03-21
task: "Short description"
spec: ".claude/specs/my-feature.md"
research: "path/to/research.md, path/to/other.md"
status: draft
---

# Plan Title

## Overview
1-3 sentence summary.

## Current State
What exists now.

## Desired End State
What should exist after implementation.

## Phase 1: Phase Name

### Task 1.1: Task Name
- **Files:** `src/foo.ts`, `src/bar.ts`
- **Group:** A
- **Steps:**
  - [ ] First step
  - [ ] Second step

### Task 1.2: Another Task
- **Files:** `src/baz.ts`
- **Group:** B
- **Steps:**
  - [ ] A step

**Verification:**
- [ ] Types check cleanly
- [ ] Tests pass

**Verification Commands:**
- `npm run typecheck`
- `npm test`

## Phase 2: ...

## Testing Strategy
How to verify the full implementation.

## Risks
- Risk 1
- Risk 2
```

Key conventions:
- **Tasks** have an ID (`1.1`, `2.3`), files, a group label, and checkbox steps
- **Groups** within a phase indicate parallelism tiers — tasks in different groups can run concurrently, but must not share files
- **Verification Commands** are backtick-quoted shell commands run by RPI after each implementation attempt. Non-zero exit = failure.
- The front matter `spec:` and `research:` fields are used by snapshots to copy supporting files

## CLI Reference

```
rpi [plan_path] [options]
```

### Arguments

| Argument | Description |
|---|---|
| `plan_path` | Path to the plan file (required unless using `--resume` or `--list-snapshots`) |

### Options

| Flag | Default | Description |
|---|---|---|
| `--min-score` | 8 | Minimum review score (0-10) to pass a review stage |
| `--max-review-iters` | 5 | Max plan review iterations before diagnosis |
| `--max-fix-iters` | 5 | Max review-fix iterations before diagnosis |
| `--quorum` | 3 | Number of parallel reviewers |
| `--skip-plan-review` | | Skip Stage 1 |
| `--skip-implement` | | Skip Stages 1-2 (jump to review-fix) |
| `--skip-fix` | | Skip Stage 3 |
| `--skip-commit` | | Skip Stage 4 |
| `--skip-pr` | | Skip Stage 5 |
| `--push` | | Push to current branch instead of creating a PR (implies `--skip-pr`) |
| `--worktree` | | Run in an isolated git worktree (new branch from main) |
| `--worktree-name NAME` | | Custom branch name for the worktree (implies `--worktree`) |
| `--worktree-base BRANCH` | main | Base branch for the worktree |
| `--worktree-path PATH` | | Reuse an existing worktree directory |
| `--dry-run` | | Print commands without executing |
| `--resume PATH` | | Resume from a snapshot directory |
| `--list-snapshots` | | List available snapshots and exit |

### Environment Variables

All options can also be set via environment variables. CLI flags take precedence.

| Variable | Maps to |
|---|---|
| `MIN_SCORE` | `--min-score` |
| `MAX_REVIEW_ITERS` | `--max-review-iters` |
| `MAX_FIX_ITERS` | `--max-fix-iters` |
| `REVIEW_QUORUM` | `--quorum` |
| `SKIP_PLAN_REVIEW=1` | `--skip-plan-review` |
| `SKIP_IMPLEMENT=1` | `--skip-implement` |
| `SKIP_FIX=1` | `--skip-fix` |
| `SKIP_COMMIT=1` | `--skip-commit` |
| `SKIP_PR=1` | `--skip-pr` |
| `PUSH=1` | `--push` |
| `WORKTREE=1` | `--worktree` |
| `DRY_RUN=1` | `--dry-run` |

## Snapshots & Resume

RPI saves progress to `~/.claude/snapshots/` after each stage and implementation phase. If a run is interrupted (Ctrl+C, verification failure, network error), you can resume from where it left off.

A snapshot directory contains:

```
~/.claude/snapshots/rpi-my-feature-20260322-143000/
    snapshot.json    # Full run state (config, progress, parsed plan)
    plan.md          # Copy of the plan file at snapshot time
    spec.md          # Copy of the spec (if referenced in front matter)
    research.md      # Copy of research files (if referenced)
    work/            # Copy of the shared workspace (history files, context)
```

```bash
# List all saved snapshots
rpi --list-snapshots

# Resume a specific snapshot
rpi --resume ~/.claude/snapshots/rpi-my-feature-20260322-143000
```

When resuming, RPI restores Config, ParsedPlan, and work_dir from the snapshot, skips completed stages, and continues from the exact point of interruption.

## Worktrees

For parallel development, RPI can run in an isolated git worktree:

```bash
# Auto-named worktree (derived from plan filename)
rpi .claude/plans/add-search.md --worktree
# Creates: .claude/worktrees/add-search-1711234567/
# Branch: rpi/add-search

# Custom-named worktree
rpi plan.md --worktree-name my-feature
# Branch: rpi/my-feature

# Worktree from a different base branch
rpi plan.md --worktree --worktree-base develop

# Reuse a worktree from a previous run
rpi plan.md --worktree-path .claude/worktrees/add-search-1711234567
```

## Common Workflows

```bash
# Full run with review
rpi .claude/plans/my-feature.md --worktree

# Skip review, just implement and commit
rpi .claude/plans/my-feature.md --skip-plan-review --skip-fix --push

# Code review only (implementation already done)
rpi .claude/plans/my-feature.md --skip-implement

# Dry run to verify plan parsing
rpi .claude/plans/my-feature.md --dry-run

# Resume after interruption
rpi --list-snapshots
rpi --resume ~/.claude/snapshots/rpi-my-feature-20260322-143000
```

## Package Structure

| Module | Description |
|---|---|
| `rpi/types.py` | `Config` dataclass |
| `rpi/display.py` | `Display` class, stage tracker bar, Rich formatting |
| `rpi/process.py` | Claude CLI wrappers, signal handling |
| `rpi/plan.py` | Plan types (`ParsedPlan`, `PlanPhase`, `PlanTask`), validation, parsing |
| `rpi/snapshot.py` | Snapshot types and I/O for resume |
| `rpi/review.py` | Review types, quorum logic, feedback application |
| `rpi/diagnosis.py` | Convergence and verification diagnosis |
| `rpi/stages/` | Stage classes: Preflight, PlanReview, Implement, ReviewFix, Commit, PushPr |
| `rpi/__main__.py` | CLI entry point |

## Development

```bash
# Run tests
cd ~/Code/rpi && uv run --extra dev pytest -v

# Editable install (already on PATH after uv tool install -e)
uv tool install -e ~/Code/rpi
```
