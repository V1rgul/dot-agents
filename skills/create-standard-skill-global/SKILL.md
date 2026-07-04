---
name: create-standard-skill-global
description: Create agent skills in the user/global skill directory (~/.agents/skills/) so they apply across projects. Use when the user asks to create a global skill, personal skill, user agent skill, or explicitly says not to create a repository-local skill. Delegates the workflow to the active skill-creation guidance, but fixes the target location to personal scope.
disable-model-invocation: true
---

# Create Standard Skill (Global)

## Instructions

When this skill is invoked, follow the active skill-creation guidance as the source of truth, with these constraints:

- Always choose personal storage: `~/.agents/skills/<skill-name>/SKILL.md`.
- Never create or modify skills under a repository-local skill directory unless the user explicitly asks.
- If the skill-creation workflow asks where to create the skill, treat the storage-location question as already answered: personal (`~/.agents/skills/`).

## Reference

- Follow the active skill-creation guidance available in the current agent environment.
