---
name: tokens-count
description: Count locally recorded Codex token usage and activity per thread and across threads, optionally within a datetime range. Use when asked for tokens, cache reads or writes, uncached input, output, reasoning output, turns, model requests, tool calls, thread counts, or comparisons involving Codex thread IDs.
---

# Count thread usage

Run the bundled script with provider-qualified selectors:

```text
python <skill-dir>/scripts/count_tokens.py --thread codex:<thread-id>
python <skill-dir>/scripts/count_tokens.py --thread codex:<thread-id> --thread codex:<thread-id>
python <skill-dir>/scripts/count_tokens.py --thread-all
```

Add `--after <datetime>` and/or `--before <datetime>` as needed. Treat `--after` as inclusive and `--before` as exclusive. Accept ISO 8601 datetimes; interpret a datetime without an offset in the machine's local timezone.

Use `--thread` repeatedly to select multiple Codex threads. Use `--thread-all` only when the user requests all threads. The script reads Codex JSONL sessions from `$CODEX_HOME`, defaulting to `~/.codex`.

Return or summarize the JSON without changing token semantics:

- For Codex, read incremental `last_token_usage`; use a cumulative-delta fallback only for legacy events without that field.
- Always report `thread_count`, `turn_count`, `request_count`, and `tool_call_count` for each thread and in `sum`.
- Count a Codex turn from each unique `turn_context`, a request from each token-bearing `token_count` event, and a tool call from each unique response item whose type ends in `_call`.
- Apply `--after` and `--before` to activity-event timestamps as well as token events.
- Keep `token_event_count` for compatibility; for Codex it equals `request_count`.
- Treat cache-read input as cached input. Treat reasoning output as a subset of output, not an additional amount to add to total tokens.
- Preserve `null` for unrecorded token categories.
- Include `sum` only when the result contains more than one thread.
