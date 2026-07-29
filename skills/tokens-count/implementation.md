# Implementation

The script reads Codex JSONL sessions from `$CODEX_HOME`, defaulting to `~/.codex`, and reads Cursor's local `state.vscdb` read-only from the platform's Cursor user-data directory.

- For Codex, read incremental `last_token_usage`; use a cumulative-delta fallback only for legacy events without that field.
- Report available counts and `tokens` for each thread and in `total`. Omit unavailable per-thread fields and empty sections.
- Identify each thread with `provider` and `thread_id`; do not emit the redundant per-thread `thread` or `thread_count` fields.
- Do not emit `selection`, `source_file_count`, `token_event_count`, or `derived_sources`.
- Name each thread's first and last recorded token timestamps `start` and `end`. Set total `start` to the earliest available start and total `end` to the latest available end; omit either key when no corresponding timestamp is available.
- For numeric total fields, ignore per-thread `null` or absent values, aggregate available values, and return `0` when none are available.
- Count a Codex turn from each unique `turn_context`, a request from each token-bearing `token_count` event, and a tool call from each unique response item whose type ends in `_call`.
- For Cursor, count human bubbles as turns and unique local tool-call IDs as tool calls. Omit requests, token-event metadata, all token categories, and all token-derived categories because Cursor's local database does not record usable values for them.
- Use the compact token schema `tokens.input.{total,cache_read,cache_write,uncached}`, `tokens.output.{total,reasoning}`, and `tokens.total`. Calculate `input.uncached` as `max(0, input.total - input.cache_read - input.cache_write)`, treating an unavailable cache-write count as zero.
- Treat cache-read input as cached input. Treat reasoning output as a subset of output, not an additional amount to add to total tokens.
- Treat a zero-thread result as a successful, definitive empty state with `thread_count: 0` and zero-valued totals.
