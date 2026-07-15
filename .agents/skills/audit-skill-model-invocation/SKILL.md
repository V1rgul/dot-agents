---
name: audit-skill-model-invocation
description: "Analyze whether skills should be model-invocable or non-model-invocable. Use when explicitly asked to audit skill invocation behavior or reduce automatically loaded skill context."
disable-model-invocation: true
---

# Audit Skill Invocability

1. Run the bundled `scripts/skills-audit-model-invocation.js`.
2. If it reports metadata mismatches, ask once whether the user wants to fix them all. If yes, run `scripts/skills-metadata-fix.js` and report its result.
3. For each discovered skill:
	1. Read its complete `SKILL.md`.
	2. Check whether another skill directs the agent to use it. If so, it must be model-invocable; this overrides the default bias.
	3. Otherwise, determine whether it should be model-invocable or non-model-invocable.
	4. If that differs from its current status, suggest the change and explain why.
4. Do not list correctly classified skills.

Default to non-model-invocable, especially for niche, costly, destructive, context-heavy, overlapping, or explicitly orchestrated workflows. Prefer model-invocable when users commonly request the capability without naming the skill and automatic discovery materially improves correctness or completion.

Use only **model-invocable** and **non-model-invocable** in the analysis. Do not expose metadata implementation details by default.
