---
name: rpi-review
description: Review uncommitted code changes for the automated rpi pipeline. Read-only -- does not apply fixes. Triggered only by the rpi.py script.
allowed-tools: Read, Glob, Grep, Bash
---

# RPI Code Review

Review uncommitted code changes for the automated review-implement-fix pipeline. Your output will be consumed by a synthesizer agent that decides which fixes to apply, so precision and justification matter more than presentation.

## Scope: Uncommitted Changes Only

The implement stage leaves all changes uncommitted. Your review scope is:

1. Run `git diff` to see modified tracked files.
2. Run `git diff --cached` to see staged files.
3. Run `git status --short` to identify untracked files.

Review ALL of these changes. Do NOT compare branches -- the changes have not been committed yet.

If there are no uncommitted changes, report a perfect score and no issues.

## Workflow

### Step 1: Gather Changes and Context

1. Run the git commands above to identify all changed and new files.
2. Read `CLAUDE.md` at the project root for project conventions.
3. Check for subdirectory `CLAUDE.md` files relevant to the changed files.
4. Read each changed/new file fully (or the relevant sections for large files).

### Step 2: Gather Surrounding Context

For each changed file, spot-check integration points:

- If a function signature changed, grep for callers (2-3 callers is enough).
- If an export changed, check importers.
- If a type definition changed, check consumers.

Skip exhaustive tracing. The goal is to catch obvious breakage, not re-do the implementation.

### Step 3: Run Verification

Run the project's type-check and test commands where applicable:

- Ur/Web files: `make typecheck`
- TypeScript files: `cd ide && npx tsc --noEmit`
- Python files: `cd ide && npm test` or `uv run pytest` as appropriate

Report verification results in your findings. A failing type check or test is a high-confidence issue.

### Step 4: Evaluate

Score each dimension 1-5. Only report findings that have concrete consequences.

#### Scoring Scale

- **5** - No issues found.
- **4** - Minor issues that will not block correctness.
- **3** - Issues that should be addressed.
- **2** - Significant errors that will cause runtime or compile failures.
- **1** - Fundamental problems requiring rethinking.

#### Correctness

Does the code do what it is supposed to do?

- Logic errors, off-by-one, wrong conditions
- Type mismatches or missing type updates
- Missing null/undefined checks where needed
- Broken integration with existing code (wrong function signatures, missing imports)
- Compile or type-check failures

#### Completeness

Are all necessary changes present?

- Missing import updates when exports change
- Missing downstream updates (callers, consumers, tests)
- Partially implemented features (function declared but not wired up)
- Missing error handling for new failure modes

#### Simplicity

Is the code as simple as it could be while achieving the goal?

- Unnecessary abstraction layers or indirection
- Over-engineering: configurability, backward compatibility, or feature flags not requested
- Code that could be deleted instead of refactored
- Unnecessary type complexity

#### Clarity

Is the code readable and maintainable?

- Misleading variable or function names
- Complex logic without explanatory structure
- Inconsistent patterns within the same change

#### Convention Compliance

Check against CLAUDE.md rules:

- No emojis or unicode symbols
- No backward compatibility shims or historical comments
- No string matching on error messages
- Discriminated unions instead of bags of optional arguments
- I/O separated from business logic

Convention violations are correctness issues.

## Output Guidelines

Your output will be constrained to a JSON schema with `issues` and `suggested_changes` arrays.

### Issue Severity

Each issue must have a `severity` field. Choose carefully — the verdict is derived from your issues:

- **critical**: Code will not compile, will fail at runtime, or will produce incorrect results if this is not addressed.
- **note**: Code works correctly but could be improved. Style, naming, or non-blocking suggestions.

Any critical issue means the code needs fixes. If you only have notes, the code passes.

Report at most **3 critical issues** and **2 notes**. If you have more, keep only the highest-impact ones.

### Format

- **Issues**: State the problem and its consequence in the `description` field. Example: "`getTableData` in `client.ts` calls `/api/tableData` but the route handler exports POST, not GET. Consequence: 405 Method Not Allowed at runtime."

- **Suggested changes**: State the change and why it matters. Example: "Change `fetch('/api/tableData')` to use method POST, or change the route handler to accept GET. Why: method mismatch causes runtime failure."

Do NOT include suggestions that are:
- Purely stylistic preferences
- "Nice to have" improvements unrelated to the implementation goal
- Vague ("consider adding error handling" without specifying where and why)
- Scope additions beyond what was implemented

Every suggestion must pass this test: "If this is not addressed, what specifically breaks or degrades?" If you cannot answer that concretely, omit the suggestion.
