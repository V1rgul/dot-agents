---
name: commit
description: One coherent commit for the current feature or fix, including clearly related working-tree changes (even from other chats). Not chat-scoped; use commit-local for that.
disable-model-invocation: true
---

# commit

## Instructions

Make **one** commit for the logical change-set behind the current task (feature, bugfix, or refactor described in the chat)—not the whole repo.

Stage what clearly belongs: same area of code, same behavior change, obvious knock-on fixes. Use `git add -p` when a file mixes related and unrelated edits. If a hunk’s link to this task is unclear, leave it unstaged.

Do not merge two unrelated features; commit only what matches this conversation and leave the rest unstaged (or ask before splitting into two commits).

If secrets or **new** artifacts would be included, alert the user before proceeding
