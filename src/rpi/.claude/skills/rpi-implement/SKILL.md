---
name: rpi-implement
description: Implement a single phase of an implementation plan for the automated rpi pipeline. Non-interactive -- no human review pauses. Triggered only by the rpi.py script.
allowed-tools: Task, Read, Write, Edit, Glob, Grep, Bash, TaskCreate, TaskUpdate, TaskList, TaskGet
---

# RPI Implement

Implement a single phase of an approved plan. You are called once per phase by the rpi.py automation script. There is no human in the loop -- do not pause for review or ask questions.

## Arguments

The rpi.py script provides:
- A **structured phase JSON block** in the prompt -- this is the authoritative specification for what to implement (tasks, files, groups, steps, verification)
- The plan file path for additional prose context (overview, current state, risks, edge cases)
- (On retry) The error from the previous attempt

## Workflow

### Step 1: Read Context

1. Parse the structured phase JSON from the prompt. This contains the phase number, name, goal, tasks (with IDs, files, groups, steps), and verification commands.
2. Read the plan file for prose context: overview, current state, desired end state, risks, and edge cases.
3. If the plan's YAML frontmatter references a `research` file, read it fully -- it contains codebase context.
4. Read all files mentioned in the structured phase data.

### Step 2: Hydrate Tasks

Use the tasks from the structured phase JSON. For each task, call TaskCreate with:
- **description:** `Phase {number} - Task {id}: {name}`

### Step 3: Implement

Follow the plan's intent. The plan was carefully designed with full codebase context. Trust it.

**Line numbers are best-effort** -- match changes by semantic context (function name, type signature, logical block), not line number.

For each task in the structured phase data:

1. Call TaskUpdate to mark the task `in_progress`.
2. If the task is independent from other in-progress tasks (different `group` value), dispatch a code-implementer agent in parallel. Otherwise, implement sequentially.
3. Give each code-implementer agent:
   - The specific task name, files, and steps (from the structured JSON)
   - Relevant patterns or conventions (from the plan prose or research file)
   - Enough context that the agent does not need to explore the codebase
4. Call TaskUpdate to mark the task `completed` when done.

### Step 4: Verify

Run the verification commands from the structured phase data's `verification` list. Typically:
- Ur/Web: `make typecheck`
- TypeScript: `cd ide && npx tsc --noEmit`
- Tests: as specified

Note: rpi.py independently runs the phase's `verification_commands` after you return. Your internal verification is a best-effort first pass — fix what you can, but rpi.py will catch anything you miss and retry the phase if needed.

If verification fails:
1. Read the error output.
2. Fix the issue (spawn a code-fixer agent if it touches multiple files).
3. Re-run verification.
4. If still failing after 2 attempts, report the failure -- do not keep retrying.

### Step 5: Update the Plan

Check off all completed items in the plan file using Edit:
```
- [ ] old item  -->  - [x] old item
```

### Step 6: Report

Report the phase result. Your output will be constrained to a JSON schema. Include:
- **status:** `success` or `failed`
- **phase:** the phase number
- **summary:** what was implemented
- **errors:** error details if failed, `"None"` if success
- **verification:** verification command output or `"Passed"`

## Rules

- Do NOT pause for human review. This is automated.
- Do NOT ask questions. If something is unclear, make the best judgment and note it in the summary.
- Do NOT skip tasks or implement partial phases.
- Do NOT implement tasks from other phases.
- If the codebase does not match what the plan expects, adapt to preserve the plan's intent and note the mismatch in the summary.
- The structured phase JSON is authoritative for task structure. The plan file is for prose context only.
- Do NOT reference plan phases, step numbers, or plan file names in code comments, commit messages, or PR descriptions. Comments should explain *why* the code exists, not which plan step produced it.
