---
name: rpi-fix
description: Synthesize code review feedback and apply fixes to uncommitted changes. Triggered only by the rpi.py script.
allowed-tools: Read, Edit, Write, Glob, Grep, Bash
---

# RPI Code Fix

Apply fixes from code review feedback. You receive reviewer findings as arguments and must synthesize, apply, and verify them.

## Arguments

The rpi.py script provides reviewer feedback as part of the prompt. This includes issues found and suggested changes, optionally attributed to multiple independent reviewers.

## Workflow

### Step 1: Understand the Feedback

Read the reviewer feedback carefully. If multiple reviewers provided input:

1. **Merge** semantically identical issues from different reviewers into one fix.
2. **Resolve contradictions** by siding with the suggestion that has stronger justification, not just the majority.
3. **Drop** suggestions that lack justification or are purely stylistic preferences.
4. **Prioritize** by severity: compile/type errors first, then logic bugs, then convention violations.

If a single reviewer provided input, take the issues and suggestions as given.

### Step 2: Apply Fixes

For each synthesized issue:

1. Read the relevant file to understand surrounding context.
2. Make the minimal edit that addresses the issue.
3. Do not refactor or add features beyond what is needed to fix the issue.
4. Do not add scope -- no new error handling, new tests, or new abstractions unless the reviewer specifically called for it.

Process files in a logical order: fix foundational/dependency changes first so downstream fixes see the correct state.

### Step 2.5: Check Transitive References

After applying each fix:

1. If you changed a function signature, grep for callers and update them.
2. If you changed an export or type name, check importers.
3. If you renamed a concept or endpoint, search the codebase for all other references.

A fix that updates one location but leaves stale references elsewhere creates a new issue.

### Step 3: Verify

Run the project's verification commands:

- Ur/Web files: `make typecheck`
- TypeScript files: `cd ide && npx tsc --noEmit`
- Python tests: `uv run pytest -vv` (if Python files were changed)

If verification fails:

1. Read the error output.
2. Fix the issue (it may be a consequence of your earlier fix).
3. Re-run verification.
4. If still failing after 2 attempts, report the remaining errors -- do not keep retrying.

### Step 4: Report

Your output will be constrained to a JSON schema. Report:

- **changes_applied**: The number of distinct fixes you made.
- **summary**: A brief description of what was fixed and whether verification passed.

## Guardrails

- **Be precise**: Make the specific fixes suggested by reviewers. Do not interpret loosely.
- **Minimal changes**: Touch only what is needed to fix the reported issues.
- **No scope creep**: Do not add features, refactor surrounding code, or "improve" things not flagged by reviewers.
- **Preserve style**: Match the existing code style in each file you edit.
- **Verify**: Always run type checks or tests after applying fixes.
