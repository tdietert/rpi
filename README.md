# RPI: Research-Plan-Implement

Automated orchestrator that takes a task description and drives it through research, specification, planning, review, implementation, code review, commit, and PR creation — all via the Claude CLI.

## Install

```bash
# Editable install (changes are live during development)
uv tool install -e ~/Code/rpi

# Or standard install
uv tool install ~/Code/rpi

# Install bundled skills and agents to ~/.claude/
rpi install
```

## Quick Start

```bash
# Full pipeline from a task description (research → spec → plan → implement → review → commit → PR)
rpi --prompt "Add retry logic to the HTTP client"

# Start from an existing research file
rpi --research .claude/research/2026-03-21-http-client.md --prompt "Add retry logic"

# Start from an existing spec
rpi --spec .claude/specs/http-retry.md --prompt "Add retry logic"

# Start from an existing plan
rpi --plan .claude/plans/http-retry.md

# Run in an isolated git worktree
rpi --prompt "Add retry logic" --worktree

# Use the caffeinate wrapper on macOS (prevents sleep during long runs)
rpi-caffeinate --prompt "Add retry logic" --worktree
```

## Prerequisites

- Python 3.11+
- [uv](https://docs.astral.sh/uv/)
- `claude` CLI in PATH
- Git

## How It Works

RPI orchestrates a 9-stage pipeline. Depending on what artifacts you provide, it starts at the appropriate stage:

```
Research → Spec → Plan → Preflight → Plan Review → Implementation → Review-Fix → Commit → PR
```

Each stage invokes the Claude CLI with structured JSON schemas or streaming output. Review stages use a parallel quorum of reviewers for reliability. Research, Spec, and Plan stages include interactive feedback loops where you can refine the output before proceeding.

### Stage 0: Research

Invokes the `/rpi-research` skill to explore the codebase and gather context about the task. Produces a structured research document in `.claude/research/`. You can provide feedback to refine the research before moving on.

Skip by providing `--research <path>` or `--spec <path>` or `--plan <path>`.

### Stage 1: Spec

Invokes the `/rpi-spec` skill to produce an architectural specification from the research findings. Defines types, control flow, interfaces, constraints, and scope boundaries. Supports interactive refinement.

Skip by providing `--spec <path>` or `--plan <path>`.

### Stage 2: Plan

Invokes the `/rpi-plan` skill to generate a structured implementation plan from the spec. The plan is parsed into phases, tasks, groups, and verification commands, with structural validation and auto-fix (up to 3 attempts). Supports interactive refinement.

Skip by providing `--plan <path>`.

### Stage 3: Preflight

Parses the plan file into a `Plan` with phases, tasks, groups, and verification commands. Validates structure (sequential phase numbering, unique task IDs, no cross-group file overlap).

### Stage 4: Plan Review Loop

Launches N parallel reviewers (default: 3) that score the plan on correctness, completeness, simplicity, and clarity (each 0-5, total 0-20). Feedback is synthesized and applied to the plan file. Iterates until the score meets the threshold or max iterations are exhausted.

### Stage 5: Implementation

Implements phases one at a time. Within each phase, tasks are grouped (A, B, C...) for parallelism hints. If the phase has verification commands, they're run via `subprocess` after each attempt.

On verification failure, RPI runs a triage step that classifies the failure:
- **command_wrong**: The verification command itself is broken — corrects and retries
- **code_fix**: A small, targetable code problem — spawns a fixer agent
- **fundamental**: The approach is wrong — runs implementation diagnosis

After all retries, prompts the user to continue or abort.

### Stage 6: Review-Fix Loop

Same quorum pattern as Plan Review, but reviewing uncommitted code changes. Reviewers score the code; if there are critical issues, a fix agent applies changes. Iterates until clean or max iterations reached.

### Stage 7: Commit

Invokes the `/rpi-commit` skill to group related changes into logical commits. Only commits changes relevant to the plan.

### Stage 8: Push / Create PR

Either pushes to the current branch (`--push`) or creates a GitHub PR via the `/rpi-create-pr` skill.

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
- The front matter `spec:` and `research:` fields point to supporting artifacts that are copied into the working directory

## CLI Reference

```
rpi [options]
rpi <subcommand> [options]
```

### Input Arguments

| Flag | Description |
|---|---|
| `--prompt TEXT` | Task description (optional if an artifact is provided) |
| `--plan PATH` | Path to existing plan file; starts at plan review |
| `--spec PATH` | Path to existing spec file; skips research + spec stages |
| `--research PATH` | Path to existing research file; skips research stage |

### Review Parameters

| Flag | Default | Description |
|---|---|---|
| `--min-score` | 8 | Minimum review score (0-10) to pass a review stage |
| `--max-review-iters` | 3 | Max plan review iterations before diagnosis |
| `--max-fix-iters` | 3 | Max review-fix iterations before diagnosis |
| `--quorum` | 3 | Number of parallel reviewers |

### Skip Flags

| Flag | Description |
|---|---|
| `--skip-plan-review` | Skip plan review stage |
| `--skip-implement` | Skip plan review + implementation stages |
| `--skip-fix` | Skip review-fix stage |
| `--skip-commit` | Skip commit stage |
| `--skip-pr` | Skip PR creation stage |

### Worktree Options

| Flag | Default | Description |
|---|---|---|
| `--worktree` | | Run in an isolated git worktree (new branch from main) |
| `--worktree-name NAME` | | Custom branch name for the worktree (implies `--worktree`) |
| `--worktree-base BRANCH` | main | Base branch for the worktree |
| `--worktree-path PATH` | | Reuse an existing worktree directory |
| `--worktree-clean` | | Remove existing worktree before creating a fresh one |

### Execution Control

| Flag | Description |
|---|---|
| `--push` | Push to current branch instead of creating a PR (implies `--skip-pr`) |
| `--dry-run` | Print commands without executing |
| `--verbose` | Show streaming output details |

### Snapshot & Resume

| Flag | Description |
|---|---|
| `--resume PATH` | Resume from a snapshot directory |
| `--list-snapshots` | List available snapshots and exit |

### Subcommands

| Command | Description |
|---|---|
| `rpi install [--force]` | Install skills + agents to `~/.claude/` (symlinks) |
| `rpi install-skills [--force]` | Install skills only |
| `rpi install-agents [--force]` | Install agents only |
| `rpi uninstall` | Remove all installed symlinks |
| `rpi uninstall-skills` | Remove skill symlinks only |
| `rpi uninstall-agents` | Remove agent symlinks only |

### Environment Variables

All review/skip/execution options can also be set via environment variables. CLI flags take precedence.

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

When resuming, RPI restores Config, Plan, and progress from the snapshot, skips completed stages, and continues from the exact point of interruption.

## Worktrees

For parallel development, RPI can run in an isolated git worktree:

```bash
# Auto-named worktree (derived from plan filename or prompt)
rpi --prompt "Add search" --worktree
# Creates: .claude/worktrees/add-search-1711234567/
# Branch: rpi/add-search

# Custom-named worktree
rpi --prompt "Add search" --worktree-name my-feature
# Branch: rpi/my-feature

# Worktree from a different base branch
rpi --prompt "Add search" --worktree --worktree-base develop

# Clean existing worktree before creating fresh
rpi --prompt "Add search" --worktree --worktree-clean

# Reuse a worktree from a previous run
rpi --prompt "Add search" --worktree-path .claude/worktrees/add-search-1711234567
```

Artifacts (research, spec, plan) are automatically mirrored back to the main repo from the worktree.

## Common Workflows

```bash
# Full pipeline from prompt
rpi --prompt "Add retry logic to HTTP client" --worktree

# Full pipeline from research
rpi --research .claude/research/2026-03-21-http-client.md --prompt "Add retry logic" --worktree

# Start from an existing plan
rpi --plan .claude/plans/http-retry.md --worktree

# Skip review, just implement and commit
rpi --plan .claude/plans/my-feature.md --skip-plan-review --skip-fix --push

# Code review only (implementation already done)
rpi --plan .claude/plans/my-feature.md --skip-implement

# Dry run to verify plan parsing
rpi --plan .claude/plans/my-feature.md --dry-run

# Resume after interruption
rpi --list-snapshots
rpi --resume ~/.claude/snapshots/rpi-my-feature-20260322-143000
```

## Skills & Agents

RPI ships bundled Claude skills and agent definitions that handle the actual work at each stage. Run `rpi install` to symlink them into `~/.claude/`.

**Skills** (invoked via `/rpi-*` slash commands):

| Skill | Used by |
|---|---|
| `rpi-research` | Research stage |
| `rpi-spec` | Spec stage |
| `rpi-plan` | Plan stage |
| `rpi-plan-review` | Plan Review stage (reviewers) |
| `rpi-implement` | Implementation stage |
| `rpi-review` | Review-Fix stage (reviewers) |
| `rpi-fix` | Review-Fix stage (fixer) |
| `rpi-commit` | Commit stage |
| `rpi-create-pr` | PR stage |
| `rpi-diagnosis` | Non-convergence diagnosis |

**Agents** (used by skills for specialized tasks):

| Agent | Purpose |
|---|---|
| `code-implementer` | Implements plan phases |
| `code-reviewer` | Reviews code changes |
| `code-fixer` | Applies targeted code fixes |

## Package Structure

| Module | Description |
|---|---|
| `rpi/__main__.py` | CLI entry point, argument parsing, pipeline orchestration |
| `rpi/config.py` | `Config` dataclass with all pipeline parameters |
| `rpi/stage_name.py` | `StageName` enum with stage metadata (labels, progress keys, skip keys) |
| `rpi/plan.py` | Plan types (`Plan`, `PlanPhase`, `PlanTask`), parsing, validation, serialization |
| `rpi/process.py` | Claude CLI wrappers (`ClaudeProcess`, `QuorumProcess`), signal handling |
| `rpi/review.py` | Review quorum logic, feedback synthesis, review-iterate-apply loop |
| `rpi/iteration.py` | `ReviewResult`, `IterationRecord`, iteration history formatting |
| `rpi/diagnosis.py` | Convergence diagnosis, verification triage, implementation diagnosis |
| `rpi/display.py` | `Display` class, `StreamActivity`, `QuorumActivity`, Rich formatting |
| `rpi/progress.py` | `SnapshotStageProgress` for tracking pipeline completion |
| `rpi/snapshot.py` | Snapshot types and I/O for resume |
| `rpi/worktree.py` | Git worktree creation and management |
| `rpi/banner.py` | Startup banner rendering |
| `rpi/skills.py` | Bundled skill management (install/uninstall symlinks) |
| `rpi/agents.py` | Bundled agent management (install/uninstall symlinks) |
| `rpi/install.py` | CLI handler for install/uninstall subcommands |
| `rpi/stages/` | Stage implementations |

**Stages:**

| Stage Module | Stage Name |
|---|---|
| `stages/research.py` | Research |
| `stages/spec.py` | Spec |
| `stages/plan.py` | Plan |
| `stages/preflight.py` | Preflight |
| `stages/plan_review.py` | Plan Review |
| `stages/implement.py` | Implement |
| `stages/review_fix.py` | Review-Fix |
| `stages/commit.py` | Commit |
| `stages/push_pr.py` | Push / PR |

## Development

```bash
# Run tests
uv run pytest

# Lint
uv run ruff check src/ tests/

# Editable install (already on PATH after uv tool install -e)
uv tool install -e ~/Code/rpi
```
