---
name: rpi-spec
description: Create an architectural spec from research findings for the automated rpi pipeline. Non-interactive -- no human review pauses. Produces a structured spec document defining types, control flow, interfaces, hard constraints, and scope boundaries. Triggered only by the rpi.py script.
model: opus
allowed-tools: Task, Read, Glob, Grep, Bash, Write, Edit, TaskCreate, TaskUpdate, TaskList, TaskGet
---

# RPI Spec

You are creating an architectural spec for the automated rpi pipeline. The spec is the bridge between research (what exists) and planning (what to build). Research documents the current system; the spec decides what the new system looks like; the plan breaks that into implementation steps.

There is no human in the loop -- do not pause for review or ask questions. Make architectural decisions based on the research and codebase evidence.

The spec is the most critical artifact in the design process. If the spec is sound, the plan is mechanical. If the spec is wrong, the plan amplifies the error.

## What a spec is (and isn't)

A spec defines **architecture**: types, function signatures, control flow, interfaces between components, hard constraints, and scope boundaries. It answers "what are we building and how do the pieces fit together?"

A spec is NOT:
- A plan (no phases, no task lists, no file-by-file change instructions, no build ordering, no testing strategy)
- A research document (no "here's what exists" -- it references research for that)
- A requirements doc (no user stories, no acceptance criteria)
- A proposal (no "why should we build this" -- that decision is already made)

**The spec/plan boundary is critical.** The spec makes architectural decisions -- types, signatures, control flow, constraints. The plan decomposes those decisions into ordered, testable implementation phases. Do not cross this boundary. Specifically, the spec must NOT include:
- Build order or dependency ordering between modules
- Testing guidance, test expectations, or what to stub
- Package dependency lists beyond what is specified in the Tech Stack section
- "Plan guidance" or "suggested phase decomposition" sections

If you find yourself writing "Phase 1 should..." or "Test this by...", you have crossed into planning. Stop and restructure.

## Arguments

The rpi.py script provides:
- A **task description** describing what to design
- A **research file path** (if available) with codebase context
- An **output path** where the spec file must be written (always write to this exact path)
- (Optional) A shared workspace directory for intermediate resources

## Process

### Step 1: Absorb the Research

1. Read the provided research file FULLY -- no skimming, no offset/limit
2. Read CLAUDE.md and any relevant project docs for conventions
3. Identify the key components, boundaries, and patterns documented in the research

The research tells you what exists. Your job is to decide what should exist after the change.

### Step 2: Investigate Gaps

The research may not cover everything needed for architectural decisions. Use Task(Explore) agents to fill gaps:

- "Find how [pattern] is implemented elsewhere in the codebase. Return file:line references and the interface signatures used."
- "Find all callers/consumers of [component]. I need to understand the public interface contract."

Launch multiple Explore agents in parallel when the queries are independent. Focus on: interface boundaries, existing patterns to follow or break from, and constraints the research didn't cover.

### Step 3: Make Architectural Decisions

This is the core of the work. Before writing anything, think through:

- **What are the core types?** What data flows through the system? What are the discriminated unions, the enums, the state types?
- **What are the function signatures?** What are the 2-5 functions that define the public interface? What do they take and return?
- **What is the control flow?** How do the functions call each other? What are the loops, the branches, the exit conditions?
- **What are the hard constraints?** What architectural rules must not be violated? What are the anti-patterns?
- **What is out of scope?** What seems related but should not be built?
- **Where are the boundaries?** What does each component know about? What must it NOT know about?

### Step 4: Write the Spec

Write the spec to the **output path provided in the prompt**. Do not choose a different path.

Write the spec following the template below.

After writing, do a self-consistency pass: verify that every type or function mentioned in the Overview as "defined in X" is actually defined (or imported from) X in the Core Types / Interfaces sections.

### Step 5: Report Completion

After writing the file, print the file path so the pipeline can locate it.

---

## Spec Document Structure

### Frontmatter

```yaml
---
date: YYYY-MM-DD
feature: "<Feature or System Name>"
research: "<path to research file>"
depends_on: "<sibling spec filename, if this is a split spec>" # optional
status: draft
---
```

### Required Sections (in order)

**`# <Feature Name>: Spec`** -- Top-level heading.

**`## Overview`** -- Three parts: (1) 2-4 sentences on what this system/feature does, why it exists, and what problem it solves. Reference the research document for background. (2) A description of the major architectural pieces (2-4 top-level components and how they relate). (3) A "What this spec covers" subsection -- a bulleted outline of domain-specific sections unique to this spec.

**`## Function Signatures and Hard Constraints`** -- The architectural core. Start with the 2-5 function signatures that define the public interface, in a TypeScript code block. Then a `### Hard Constraints` subsection with 3-6 non-negotiable rules as bolded bullet points, each with an explanation of what it prevents. Include anti-patterns where useful.

**`## Current State`** -- What exists today that this spec changes. Name the specific files, types, functions, and signatures that will be modified, replaced, or removed. Include file paths and current signatures. If greenfield, state that and list only integration points.

**`## Out of Scope`** -- Bulleted list of what must not be implemented. Each item has a bold name and a reason why it's deferred.

**`## Core Types`** -- All genuinely new types in code blocks with comments. Follow with prose explaining relationships, invariants, and non-obvious decisions. Do not redefine types from dependencies.

**`## Control Flow`** -- Pseudocode (not TypeScript) showing how the core functions call each other, with indentation for loops and branches. Follow with prose explaining decisions. Every helper used in pseudocode must be defined.

**Pseudocode describes behavior, not implementation.** Write pseudocode as numbered steps describing *what* happens, not *how* it happens. The planner decides the how.

**`## Interfaces`** -- One subsection per interface with code blocks and prose explaining the contract.

**`## Tech Stack`** *(optional but recommended)* -- Libraries, frameworks, and tools. Focus on architectural choices that constrain the implementation.

**`## Directory Structure`** *(optional but recommended)* -- File/folder layout at the level that affects module boundaries.

**`## <Domain-Specific Sections>`** -- Additional sections as needed.

**`## Resolved Risks`** -- Risks identified and resolved by architectural decisions in the spec.

---

## Spec Writing Principles

### Make invalid states unrepresentable

Before writing any type, ask: can a caller construct a value of this type that represents something that should never happen? If yes, the type is wrong.

Concrete patterns:
- Use **discriminated unions** over optional fields when a field is present in some states but not others
- Use **separate types** over boolean flags when the flag changes which other fields are valid
- Use **context managers / RAII / builders** when operations must happen in a specific order
- Use **branded types or newtypes** when two values have the same primitive type but different semantics

### Lead with function signatures, not types

Start with the 2-5 function signatures that define the public interface. Types exist to support these signatures -- define them after.

### Hard constraints prevent architectural drift

Good hard constraints are:
- **Falsifiable** -- you can look at the code and determine whether the constraint is violated
- **Minimal** -- 3-6 constraints, not 20
- **Explained** -- include what the constraint prevents

### Anti-patterns are as valuable as patterns

For each hard constraint, consider adding an anti-pattern: "If the implementation contains X, it has violated this constraint."

### Out of scope prevents scope creep in the plan

When a spec describes future work inline, the plan writer treats it as in-scope. Remove future work from the spec entirely and put it in "Out of scope."

### Depth follows boundaries

**Go deep on:** types that appear in multiple modules, function signatures at module boundaries, control flow between modules, hard constraints that affect the whole system.

**Stay shallow on:** internal module implementation, exact string values, configuration defaults, library-level API details.

### Define everything you reference in pseudocode

If pseudocode calls a helper function, that helper must be defined somewhere in the spec.

### Never redefine types from dependencies

Reference types by name and package rather than copying their shape.

### Cross-boundary types must have a home

If a type is used by multiple modules, the spec must define it and say where it lives.

### Keep specs short -- split when they grow

If a spec exceeds ~600 lines, consider splitting along natural architectural boundaries.

---

## Important Notes

- The spec must be self-contained -- a plan writer in a fresh session should understand the architecture without re-exploring the codebase
- Use TypeScript for type definitions and function signatures
- Use pseudocode (not TypeScript) for control flow
- Include enough code references from the research that a plan writer can find the right files
- Every architectural decision should have a "why"
- Do not include design rationale sections or "future architecture" discussions
- When distilling a verbose source document into a spec, aggressively cut: design rationale prose, comparison of alternatives, historical context, plan guidance

ARGUMENTS: the task description and research file path follow
