---
name: commit-all
description: Commit all changes since the last commit. Use when the user says to "commit everything" or "commit all changes".
disable-model-invocation: true
---

# commit-all

## Instructions

- Create exactly one git commit that includes **all intended changes** in the repo since `HEAD` (tracked + untracked)
- Don't include gitignored files
- If secrets or **new** artifacts would be included, alert the user before proceeding
