---
name: rpi-commit
description: Plan and execute a logical commit sequence for all uncommitted changes. Non-interactive -- commits without asking. Triggered only by the rpi.py script.
allowed-tools: Bash, Read, Glob, Grep
---

# RPI Commit

Create a logical sequence of commits for all uncommitted changes. This is the automated counterpart to `/plan-commits` -- execute the commits directly without asking for approval.

## Workflow

### Step 1: Gather Changes

```bash
git diff --stat
git diff --cached --stat
git status --porcelain
```

If there are no changes (clean working tree), report `nothing_to_commit`.

### Step 2: Understand the Changes

Read the plan file referenced in the prompt to understand what this task covers. Then read the full diff:

```bash
git diff
git diff --cached
```

For new untracked files, read them directly.

### Step 3: Filter Irrelevant Changes

Compare each changed/untracked file against the plan scope. **Only commit files that are clearly related to the plan.** Leave unrelated changes unstaged -- they may be pre-existing work on the branch or worktree that should not be mixed into this commit sequence.

When in doubt about whether a file is relevant: if the file is not mentioned in the plan and the change is not a direct consequence of implementing the plan, leave it alone.

### Step 4: Plan Commits

Group the relevant changes into a logical commit sequence following these rules:

1. **Working state principle (most important):** Each commit must leave the codebase in a functional, working state. Never leave broken states between commits.

2. **Whole files only:** Include ALL changes in a file in a single commit. Never split a file across commits.

3. **Merge when needed:** If multiple logical changes touch the same file, combine them into one commit.

4. **Prefer fewer commits:** 3-5 focused commits is better than 10 granular ones. A larger working commit is better than smaller broken ones.

5. **Logical order:** Foundation/infrastructure first, then features that depend on them.

6. **Clear messages:** Start with a verb (Add, Fix, Update, Remove, Refactor). Under 72 chars. Describe WHAT and WHY.

### Step 5: Execute Commits

For each planned commit, stage the specific files and commit:

```bash
git add <file1> <file2> ...
git commit -m "<message>"
```

Stage files by name -- never use `git add -A` or `git add .`.

### Step 6: Report

Your output will be constrained to a JSON schema. Report:
- **status:** `success`, `failed`, or `nothing_to_commit`
- **num_commits:** number of commits created
- **commits:** list of commit messages
- **errors:** error details if failed

## Rules

- Do NOT ask for approval. Execute the commits directly.
- Do NOT use `git add -A` or `git add .` -- always stage specific files.
- Do NOT commit files that look like secrets (.env, credentials.json, etc.).
- Do NOT use `--no-verify` or skip hooks.
- If a pre-commit hook fails, fix the issue and create a new commit (do not amend).
