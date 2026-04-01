---
name: rpi-diagnosis
description: Diagnose why a plan-review or review-fix loop failed to converge. Analyzes iteration history for patterns and recommends next steps. Triggered only by the rpi.py script.
allowed-tools: Read, Glob, Grep, Bash
---

# RPI Convergence Diagnosis

Diagnose why a review loop failed to converge after exhausting its iteration budget. You receive the full iteration history and must identify the root cause pattern and recommend concrete next steps.

## Arguments

The rpi.py script provides:

- **loop_type**: "plan_review" or "review_fix"
- **iteration_history**: Per-iteration data including all per-reviewer ReviewResults, aggregated scores, and apply-feedback summaries
- **plan_path**: Path to the plan file
- **min_score**: The target score threshold

## Workflow

### Step 1: Analyze Score Trajectory

Look at the aggregated score across iterations:

- **Improving**: Scores trend upward but didn't reach threshold. Likely diminishing returns -- a few more iterations might work, or the threshold is too high for the remaining issues.
- **Flat**: Scores stay in a narrow band (e.g., 6-7 across all iterations). The fix attempts are not addressing root causes.
- **Oscillating**: Scores swing up and down (e.g., 6, 8, 6, 7). Fixes for one set of issues create new ones.
- **Declining**: Scores get worse. Fixes are introducing more problems than they solve.

### Step 2: Identify Recurring Issues

Compare issues across iterations. Look for:

- **Exact recurrences**: The same issue (or semantically equivalent) appears in iteration N, gets "fixed", then reappears in iteration N+2. This is circular feedback -- the fix didn't actually resolve the root cause.
- **Morphing issues**: An issue changes form across iterations (e.g., "missing null check in handler" becomes "handler error path returns wrong status code"). The fix addressed the surface symptom but not the underlying design gap.
- **Persistent issues**: Issues reported in every iteration that the fix agent never addresses. These may be too vague for the fixer, or require structural changes the fixer can't make.

### Step 3: Assess Reviewer Agreement

If the quorum has multiple reviewers, check per-reviewer scores and verdicts within each iteration:

- **Strong agreement**: All reviewers give similar scores and flag similar issues. The diagnosis is clear -- focus on the issues themselves.
- **Weak agreement**: Reviewers give divergent scores (e.g., 14/20 vs 8/20) or contradictory verdicts. The synthesis step may be producing incoherent feedback that the fixer can't follow.
- **Systematic outlier**: One reviewer consistently scores much lower than others. That reviewer may have a different understanding of requirements or stricter standards.

### Step 4: Check Current State

For **plan_review**: Read the current plan file. Are the remaining issues valid? Is the plan actually in good shape despite the score?

For **review_fix**: Run `git diff --stat` and spot-check the code. Are the remaining issues real bugs or stylistic nitpicks that don't warrant blocking?

### Step 5: Classify Root Cause

Based on Steps 1-4, classify into exactly one pattern:

- **circular**: Fixes create new problems that trigger old issues. The feedback loop is chasing its own tail.
- **oscillating**: Score swings because different reviewers or review iterations focus on different dimensions. No stable trajectory.
- **structural**: Remaining issues require changes beyond the scope of incremental fixes (e.g., redesigning a type hierarchy, splitting a phase, changing the approach).
- **disagreement**: Reviewers cannot agree on what's wrong. The synthesis step produces contradictory guidance.
- **diminishing_returns**: Score improved but plateaued just below threshold. Remaining issues are minor and the work is likely good enough.
- **fixer_blind_spot**: The fix agent consistently fails to address specific reported issues, either because they're too vague or because the fixer lacks the context to make the right change.

### Step 6: Recommend Next Steps

Based on the pattern, provide 2-4 concrete recommendations. Examples:

- **circular**: "Issue X keeps cycling. The root cause is [Y]. Manually fix [specific thing] before re-running."
- **structural**: "The remaining issues point to [design problem]. Consider revising the plan to [specific change]."
- **disagreement**: "Reviewers disagree on [topic]. Reduce quorum to 1 or clarify requirements in the plan."
- **diminishing_returns**: "Score is N/10 with only minor remaining issues. Safe to proceed."
- **fixer_blind_spot**: "The fixer never addresses [issue]. Manually apply [specific fix] or rewrite the issue description to be more precise."

Recommendations must be specific enough to act on. "Improve the code" is not a recommendation. "Add error handling to the `parseConfig` function for the case where `fields` is empty" is.

## Output Guidelines

Your output will be constrained to a JSON schema. Provide:

- **pattern**: One of the six classification values above.
- **summary**: 2-3 sentence diagnosis explaining what happened and why.
- **score_trajectory**: Brief description of how scores moved (e.g., "7, 7, 8, 7, 7 -- flat with one spike").
- **recurring_issues**: Issues (verbatim or paraphrased) that appeared in 2+ iterations.
- **recommendations**: 2-4 concrete next steps.
