---
name: rpi-plan
description: Create an implementation plan formatted for the automated rpi pipeline. The plan markdown is parsed deterministically and validated for structural correctness.
model: opus
allowed-tools: Task, Read, Glob, Grep, Bash, Write, Edit, AskUserQuestion
---

# Create RPI-Compatible Implementation Plan

You are tasked with creating a detailed implementation plan through an interactive process. The plan will be consumed by the **rpi.py automated pipeline** (plan-review-implement-fix). After you write the plan markdown, it is parsed deterministically into a structured representation, then validation runs on the result.

## How This Differs from /plan

Same interactive investigation process as `/plan`, but the output markdown must be structured for deterministic parsing. After parsing, `validate_plan()` runs deterministic checks (sequential phase numbering, unique task IDs, no cross-group file overlap). Failures block the pipeline.

---

## Initial Setup

When invoked, check if arguments were provided:

**If arguments include a file path** (e.g., `.claude/specs/...`, `.claude/research/...`, or `.claude/plans/...`):
- Read the file FULLY first
- Begin the planning process using it as context

**If arguments include a task description but no file path**:
- Check `.claude/specs/` for recent spec files first, then `.claude/research/` for research files
- If found, ask the user if they want to use any of them
- Begin the planning process

**If no arguments**:
```
I'll help create an rpi-compatible implementation plan. Please provide:

1. What you want to build or change
2. A spec or research file to reference (check .claude/specs/ and .claude/research/ for recent ones)

Example: /rpi-plan Add rate limiting to the API -- .claude/specs/2025-06-15-rate-limiting.md
```

## Process

### Step 1: Absorb Context

1. Read any provided spec or research files FULLY
2. Read CLAUDE.md and any relevant agent_docs/ files for project conventions
3. If a research file was provided, use its code references as starting points

**If a spec file was provided (`.claude/specs/...`):** The spec has already made the architectural decisions -- types, function signatures, control flow, interfaces, hard constraints, and scope boundaries. Your job is to turn those decisions into an implementation plan, not to re-decide them. Specifically:
- The spec's **hard constraints** must be respected in every phase of the plan
- The spec's **out of scope** items must not appear as plan tasks
- The spec's **function signatures and types** define the implementation targets
- The spec's **control flow** tells you the order of operations
- Reference the spec file in the plan's frontmatter (`spec:` field) and Research References section

### Step 2: Investigate the Codebase

Spawn parallel Task(Explore) agents to understand the implementation landscape:

```
Task(Explore, "Find all files that would need to change for [feature]. Return file paths, current content of relevant sections, and how they connect.")
Task(Explore, "Find existing patterns similar to [feature] in the codebase. Return examples with file:line references showing how similar things are implemented.")
Task(Explore, "Find tests related to [area]. What testing patterns are used? Return file paths and example test structures.")
```

If a research file was provided, make the agents' prompts MORE targeted using the file references from the research.

**If a spec was provided, investigation should be lighter.** The spec already covers architecture and interfaces. Focus investigation on: which specific files need to change, what existing test patterns to follow, and any implementation details the spec doesn't cover (it shouldn't -- specs are about architecture, not file-level changes).

### Step 3: Present Understanding and Ask Questions

After agents complete, present what you found and ask targeted questions:

```
Based on the research and my investigation, here's what I understand:

**Current state:**
- [Key finding with file:line reference]
- [Relevant pattern or constraint]

**Proposed approach:**
- [High-level description of what needs to change]

**Questions before I write the plan:**
- [Technical question requiring human judgment]
- [Design choice between approaches]
```

Only ask questions you genuinely cannot answer from the code. If everything is clear, say so and present the approach for approval.

### Step 4: Decompose into Vertical Slices

**Prefer vertical slices over horizontal layers.** Each phase should deliver a thin end-to-end slice of functionality — types, logic, wiring, and tests for one coherent capability — rather than building out one architectural layer at a time.

**Why:** Vertical slices produce testable, working behavior at each phase boundary. Integration issues surface early. If the plan is abandoned halfway, you have working features instead of half-built layers. Horizontal plans defer integration to the final phases, where problems are hardest to debug.

**How to slice:** Identify the distinct capabilities or user-facing behaviors the feature provides. Each phase delivers one capability fully — from types through logic through verification. A phase may touch multiple architectural layers (types file, service file, handler file, test file) and that's correct.

**Anti-pattern (horizontal):**
```
Phase 1: Define all types (SessionState, SSEEvent, Config, Broadcaster)
Phase 2: Implement all endpoint handlers
Phase 3: Wire entry point
Phase 4: Write all integration tests
```

**Correct (vertical):**
```
Phase 1: Start + status — types for session lifecycle, POST /start, GET /status, happy-path test
Phase 2: SSE streaming — Broadcaster type, GET /stream, replay logic, stream test
Phase 3: Inbox + waiting — waiting state, POST /inbox, steer/interrupt routing, test
Phase 4: Interrupts — interrupt state types, POST /resolve, interaction + confirmation, test
```

**When horizontal is acceptable:** When a type change cascades atomically through every layer and there is no meaningful intermediate slice (e.g., replacing five callbacks with a single EventSink interface that threads through the whole call chain). If the cascade can't be split without throwaway adapter code, one phase is fine. But this is the exception — default to vertical.

After determining slices, present the structure before writing details:

```
Here's my proposed plan structure:

## Phases:
1. [Phase name] - [end-to-end capability it delivers]
2. [Phase name] - [end-to-end capability it delivers]
3. [Phase name] - [end-to-end capability it delivers]

Each phase delivers a complete vertical slice with its own verification.
Does this phasing make sense?
```

Get feedback on structure before proceeding.

### Step 5: Write the Plan

Determine filename: `.claude/plans/YYYY-MM-DD-<description>.md`

Write the plan following the template below. The markdown is parsed deterministically — follow the template exactly.

### Step 6: Present the Plan

After writing:
- Print the file path
- Summarize the plan (phases, key decisions, estimated scope)
- Ask if anything needs adjustment

If the user requests changes, update the plan file using Edit and re-present.

---

## Plan Template

```markdown
---
date: YYYY-MM-DD
task: "<Task Description>"
spec: "<path to spec file, if used>"
research: "<path to research file, if used>"
status: draft
---

# <Feature/Task Name> Implementation Plan

## Overview

<1-3 sentences: what we're doing and why>

## Research References

<If a research file was used, reference it here with key findings that inform the plan.>
<If no research was used, note that and summarize what was discovered during planning.>

## Current State

<What exists now. Key files and their roles. Constraints to work within.>

## Desired End State

<What the system should look like when done. How to verify it works.>

## Implementation Phases

### Phase 1: <Name>

**Goal:** <What this phase accomplishes>

#### Tasks

##### Task 1.1: <Short descriptive name>
**Files:** `path/to/file.ext`, `path/to/other.ext`
**Group:** A
- [ ] <Specific change to make>
  - <sub-detail or field value>
  - <another sub-detail>
- [ ] <Another specific change>

##### Task 1.2: <Short descriptive name>
**Files:** `path/to/file.ext`
**Group:** B
- [ ] <Specific change to make>

**Verification:**
- [ ] <specific command or check to verify this phase works>
- [ ] <manual verification step if needed>

**Verification Commands:**
- `<shell command that exits 0 on success>`
- `<another shell command>`

### Phase 2: <Name>

**Goal:** <What this phase accomplishes>

#### Tasks

##### Task 2.1: <Short descriptive name>
**Files:** `path/to/file.ext`
**Group:** A
- [ ] <Specific change>

**Verification:**
- [ ] <verification command>

**Verification Commands:**
- `<shell command that exits 0 on success>`
- `<another shell command>`

## Testing Strategy

<How to verify the entire change works end-to-end. Specific commands to run.>

## Risks and Edge Cases

- <Risk 1> - <mitigation>
- <Risk 2> - <mitigation>

## Open Questions

<Anything unresolved, or "None">
```

---

## Structural Validation Rules

The plan parser runs three cross-item validation checks after parsing. All three MUST pass. Violations will block the pipeline.

### Rule 1: Sequential Phase Numbering

Phases must be numbered sequentially starting from 1, with no gaps or repeats.

**Valid:** Phase 1, Phase 2, Phase 3
**Invalid:** Phase 1, Phase 3, Phase 4 (gap)
**Invalid:** Phase 1, Phase 1, Phase 2 (duplicate)

### Rule 2: Unique Task IDs Within Each Phase

Within a single phase, every task must have a unique ID. Task IDs follow the pattern `X.Y` where X is the phase number and Y is the task number within that phase.

**Valid (Phase 2):** Task 2.1, Task 2.2, Task 2.3
**Invalid (Phase 2):** Task 2.1, Task 2.1 (duplicate)
**Invalid (Phase 2):** Task 1.1 (wrong phase prefix)

### Rule 3: No Cross-Group File Overlap

Within a single phase, tasks in **different** groups must NOT share any files. Tasks in different groups are assumed to be parallelizable, so file overlap would cause race conditions.

**Valid:**
```
##### Task 1.1: Update schema
**Files:** `src/schema.ts`
**Group:** A

##### Task 1.2: Update tests
**Files:** `src/schema.test.ts`
**Group:** B
```

**Invalid:**
```
##### Task 1.1: Update schema types
**Files:** `src/schema.ts`
**Group:** A

##### Task 1.2: Update schema validation
**Files:** `src/schema.ts`
**Group:** B
```
(Both groups touch `src/schema.ts` -- these tasks must be in the same group.)

**Fix:** If tasks share files, put them in the same group. If all tasks in a phase share files, put them all in Group A.

---

## Phase Boundary Rule

Every phase must leave the codebase in a **type-checkable state**. A phase may internally break types (e.g., change a signature then update callers), but when the phase is complete, `make typecheck` (or the project's equivalent) must pass. If a change cannot be split into type-safe phases, it belongs in a single phase.

## Plan Quality Checklist

Before considering the plan done, verify:

- [ ] The plan references specific code patterns from the codebase (not generic advice)
- [ ] Each phase leaves the codebase in a type-checkable state
- [ ] An implementer in a fresh session can follow this without re-exploring the codebase
- [ ] Each task is specific enough that a code-implementer agent can implement it without further exploration
- [ ] Every task specifies WHAT to change (not vague directions)
- [ ] Phases are vertical slices (each delivers end-to-end functionality), not horizontal layers -- unless an atomic type cascade makes horizontal unavoidable
- [ ] Phases are ordered so each builds on the last (no forward dependencies)
- [ ] Testing strategy uses the project's actual test commands

## Important Notes

- The plan must be self-contained -- an implementer reading it in a fresh context window should have everything they need
- Be specific about file paths and what to change in each file
- Use checkboxes (- [ ]) for all actionable items so the implementer can track progress
- Reference actual code patterns from the codebase, not generic approaches
- If the research file had open questions, try to resolve them during planning or carry them forward
- The plan markdown is parsed deterministically -- follow the template exactly so parsing succeeds
- **Verification commands must use relative paths, never absolute paths.** The rpi pipeline may execute inside a git worktree, not the main repo checkout. Absolute paths like `cd /Users/foo/project && npm test` will run against the main repo (missing the worktree's changes). Use relative commands like `npm test` or `cd subdir && npm test` — rpi.py sets the working directory automatically.
- **Verification commands run in a bare shell.** The rpi pipeline runs verification commands via `subprocess.run(cmd, shell=True, cwd=worktree)` — no virtual environment activated, no project-specific shell setup. Before writing verification commands, check how the project actually invokes its toolchain (Makefile, package.json scripts, build tool wrappers, etc.) and use the same invocation. If the project uses a runtime wrapper (e.g., `uv run`, `poetry run`, `npx`, `nix run`), verification commands must use it too.

ARGUMENTS: the user's task description follows
