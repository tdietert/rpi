---
name: rpi-create-pr
description: Create a GitHub PR for the current branch. Non-interactive -- creates the PR without asking. Triggered only by the rpi.py script.
allowed-tools: Bash, Read, Grep, Glob
---

# RPI Create PR

Create a GitHub pull request for the current branch. This is the automated counterpart to `/create-pr` -- create the PR directly without asking for approval.

## Workflow

### Step 1: Identify Changes

```bash
git branch --show-current
git log main..HEAD --format="%H %s" --reverse
git diff main...HEAD --stat
```

If there are no divergent commits, report failure.

### Step 2: Understand the Changes

Read the full commit history and diffs:

```bash
git log main..HEAD --format="---COMMIT---%n%H%n%s%n%n%b" --reverse
git diff main...HEAD
```

Verify commit message claims against actual diffs. Describe what actually changed, not what messages claim.

### Step 3: Craft the PR

**Title:** Under 70 characters, starts with a verb, specific not vague.

**Body structure:**

```markdown
## Summary

[2-5 sentences: WHAT this PR does and WHY.]

## Major Changes

[Bulleted list of substantial changes. Group related commits into coherent bullets.]

## Minor Changes

[Bulleted list of incidental fixes and cleanup. Omit if none.]
```

### Step 4: Push and Create

```bash
git push -u origin HEAD
```

```bash
gh pr create --title "<title>" --body "$(cat <<'EOF'
<body>
EOF
)"
```

### Step 5: Report

Your output will be constrained to a JSON schema. Report:
- **status:** `success` or `failed`
- **pr_url:** the URL of the created PR
- **pr_title:** the title used
- **errors:** error details if failed

## Rules

- Do NOT include "Testing strategy", "Test plan", or "How to test" sections.
- Do NOT include "Generated with Claude Code" or AI attribution footers.
- Do NOT use emoji in the title or description.
- Do NOT parrot commit messages verbatim -- synthesize and consolidate.
- Do NOT ask for approval. Create the PR directly.
