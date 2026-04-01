---
name: rpi-plan-review
description: Review an implementation plan for correctness, completeness, simplicity, architecture, spec alignment, and clarity. Tailored for the automated rpi pipeline -- output goes to a synthesizer agent, not a human. Triggered only by the rpi.py script.
allowed-tools: Read, Glob, Grep
---

# RPI Plan Review

Review an implementation plan for the automated review-implement-fix pipeline. Your output will be consumed by a synthesizer agent that decides which suggestions to apply, so precision and justification matter more than presentation.

## Arguments

- `<path>`: the plan file to review (always provided by the rpi script)

## Workflow

### Step 1: Read the Plan and Context

1. Read the plan file fully.
2. Parse YAML frontmatter for `spec` -- if present, read the spec file fully. The spec is the architectural authority.
3. Parse YAML frontmatter for `research` -- if present, read the research file fully.
4. Read `CLAUDE.md` at the project root.

### Step 2: Lightweight Codebase Verification

Verify the plan's key claims directly (do NOT spawn subagents). Focus on the highest-risk claims:

- Spot-check 3-5 file paths the plan references -- do they exist and contain the constructs described?
- Check 2-3 type signatures or function names the plan depends on -- are they accurate?
- If the plan proposes changing an export, grep for importers to check for missing downstream updates.

Skip exhaustive verification. The goal is to catch obvious stale references, not to re-do the research phase.

### Step 3: Evaluate

Score each dimension 1-5. Only report findings that have concrete consequences.

#### Scoring Scale

- **5** - No issues found.
- **4** - Minor issues that will not block implementation.
- **3** - Issues that should be addressed before implementing.
- **2** - Significant gaps or errors that will cause implementation failures.
- **1** - Fundamental problems requiring rethinking.

#### Correctness

Does the plan accurately describe the current codebase?

- Stale file references (moved, renamed, deleted)
- Wrong type signatures or function names
- Incorrect assumptions about existing behavior
- Phase ordering issues (phase N depends on phase N+1 output)
- Missing downstream consumers that would break
- Do NOT flag line number drift -- line numbers are navigation hints, not correctness concerns

#### Completeness

Does the plan cover everything needed to go from current state to desired end state?

- Files that need to change but are not mentioned
- Missing import updates when exports change
- Missing test updates when behavior changes
- Missing verification steps (a phase with no way to check it worked)
- Unresolved open questions that block implementation
- Could an implementer in a fresh context follow this without re-exploring?
- Does every phase have `verification_commands` with executable shell commands that would catch a broken build?
- **Do verification commands work in a bare shell?** The rpi pipeline runs them via `subprocess.run(cmd, shell=True, cwd=worktree)` with no virtual environment or project shell setup. Check that commands use the same invocation the project uses (e.g., `make`, `npx`, `uv run`, `poetry run`) rather than bare interpreter calls that would resolve to the system default.

#### Simplicity

Is the plan as simple as it could be while achieving the goal?

- Unnecessary abstraction layers or indirection
- Phases that could be merged without increasing risk
- Over-engineering: configurability, backward compatibility, or feature flags not requested
- Unnecessary type complexity
- Code that could be deleted instead of refactored
- Phase count should match actual complexity

#### Architecture

Does the technical approach fit the problem and the existing system?

- **Horizontal vs vertical slicing**: Are phases decomposed as horizontal layers (all types, then all logic, then all tests) when they could be vertical slices (each phase delivers end-to-end functionality for one capability)? Horizontal decomposition defers integration to the final phases where problems are hardest to debug. Vertical slices are preferred unless an atomic type cascade makes horizontal unavoidable (e.g., replacing a type that threads through every call site in one shot). Flag horizontal plans that could be sliced vertically — this is a critical issue since it affects the implementer's ability to verify progress incrementally.
- Approaches that fight existing patterns instead of using them
- Missing concurrency, state management, or data flow considerations
- New coupling between currently-independent modules
- Performance concerns (e.g., O(n^2) where O(n) is straightforward)
- Missing error handling for new failure modes

Architecture findings should be reflected in the Simplicity and Completeness scores.

#### Clarity

Can an implementer follow this plan without ambiguity?

- Vague change descriptions ("update the handler" vs "add a null check before the database call")
- Missing context for implementation
- Inconsistencies between overview, end state, and phase details

#### Convention Compliance

Check proposed code snippets against CLAUDE.md rules:

- No emojis or unicode symbols
- No backward compatibility shims
- No historical comments
- No string matching on error messages
- Discriminated unions instead of bags of optional arguments
- I/O separated from business logic

Convention violations in code snippets are correctness issues.

#### Spec Alignment (only when a spec is referenced)

**This is the most important dimension when a spec exists.** The plan must be a pure translation of the spec into implementation steps. The spec is the architectural authority -- the plan decomposes it into ordered, testable phases, not extends or modifies it.

Flag as spec divergences:

- **New types** not defined in the spec (interfaces, type aliases, enums, discriminated unions)
- **New function signatures** not in the spec (public functions, API endpoints, module exports)
- **New modules or architectural boundaries** not in the spec
- **Altered type signatures** from what the spec defines
- **Architectural decisions** the spec left unspecified that the plan fills in (cross-module control flow, state management, error handling strategies)
- **Scope additions** that fall under the spec's "Out of Scope" section

For each divergence, classify as:

1. **Spec gap**: The spec was under-specified and the planner had to invent something. The fix belongs in the spec.
2. **Planner overreach**: The planner added something the spec intentionally omitted. Remove from the plan.
3. **Legitimate implementation detail**: Internal helpers, test utilities, or module structure that doesn't affect the spec's contracts. These are acceptable -- do NOT flag.

The test for (3): would changing this decision require updating the spec's function signatures, type definitions, or interface contracts? If no, it's implementation detail. If yes, it's an architectural decision that belongs in the spec.

Spec alignment divergences where the root cause is a spec gap should be reported as critical issues with a `spec_amendment` field describing what the spec should add. These are critical because the plan cannot be correct without spec changes -- the planner was forced to make architectural decisions that aren't its job.

## Output Guidelines

Your output will be constrained to a JSON schema with `issues` and `suggested_changes` arrays.

### Issue Severity

Each issue must have a `severity` field. Choose carefully — the verdict is derived from your issues:

- **critical**: Implementation will fail, produce incorrect results, or violate a spec constraint if this is not addressed. Examples: wrong file paths, missing imports, phase ordering errors, type mismatches, spec contradictions, plan introduces types/interfaces not defined in the spec (spec gap).
- **note**: Observation that could improve the plan but will not block a correct implementation. Examples: a phase could be simpler, a description could be clearer, a minor inconsistency the implementer can resolve on the fly.

Any critical issue means the plan needs revision. If you only have notes, the plan passes.

Report at most **3 critical issues** and **2 notes**. If you have more, keep only the highest-impact ones. This forces prioritization.

### Format

- **Issues**: State the problem, the evidence from the codebase, and the consequence. Every issue that makes a claim about the codebase MUST include an explicit code reference citing what was actually observed during Step 2 verification — the file path and the relevant construct (function name, type signature, export, etc.) as it actually exists. Example: "Phase 3 references `buildQuery()` in `search.ts` but that function does not exist — `search.ts` exports `executeQuery()` (verified: `src/search.ts`). Why: implementer will get a compile error and have to re-explore." If you cannot verify a claim, say so explicitly ("not verified — could not locate the file") rather than asserting it as fact. Never report a finding based on assumption alone.

- **Suggested changes**: State the change, cite the code evidence, and explain why it matters. "Phase 2: add `import { UserRole }` to the list of changes in `auth.ts`. Evidence: `UserRole` is exported from `src/types/auth.ts` and is not currently imported in `src/middleware/auth.ts`. Why: the new guard clause uses `UserRole` but it is not currently imported, causing a compile error."

- **Spec amendments** (for spec gap divergences): When the plan introduces architecture because the spec was under-specified, include a `spec_amendment` field on the issue describing what the spec should define. Example: "Plan introduces a `CacheEntry` type in Phase 2 that isn't in the spec. spec_amendment: The spec's Core Types section should define `CacheEntry` with its shape and relationship to `SessionState`. The planner shouldn't be deciding type structures."

Do NOT include suggestions that are:
- Purely stylistic preferences
- "Nice to have" improvements unrelated to the plan's stated goal
- Vague ("consider adding error handling" without specifying where and why)
- Scope additions beyond what the plan set out to do

Every suggestion must pass this test: "If this is not addressed, what specifically breaks or degrades?" If you cannot answer that concretely, omit the suggestion.
