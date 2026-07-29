---
name: tokens-count
description: Count locally recorded Codex token usage and Codex or Cursor activity per thread and across threads, optionally within a datetime range. Use when asked for tokens, cache reads or writes, uncached input, output, reasoning output, turns, model requests, tool calls, thread counts, or comparisons involving Codex or Cursor thread IDs.
---

# Count thread usage

Run the bundled script with provider-qualified selectors:

```text
python <skill-dir>/scripts/count_tokens.py codex:<thread-id>
python <skill-dir>/scripts/count_tokens.py cursor:<thread-id>
python <skill-dir>/scripts/count_tokens.py codex:<thread-id> cursor:<thread-id>
python <skill-dir>/scripts/count_tokens.py codex:all
python <skill-dir>/scripts/count_tokens.py cursor:all
```

Install the pinned TOON dependency before first use:

```text
python -m pip install -r <skill-dir>/requirements.txt
```

Pass one or more provider-qualified thread selectors positionally. Add `--after <datetime>` and/or `--before <datetime>` as needed. Treat `--after` as inclusive and `--before` as exclusive. Accept ISO 8601 datetimes; interpret a datetime without an offset in the machine's local timezone.

Use the provider-qualified `codex:all` or `cursor:all` selector only when the user requests every thread for that provider. Do not combine a provider's `all` selector with specific threads from the same provider. The script reads Codex JSONL sessions from `$CODEX_HOME`, defaulting to `~/.codex`, and reads Cursor's local `state.vscdb` read-only from the platform's Cursor user-data directory.

The default output is the aggregate `total` encoded with the Python `toon_format` package. Add `--detail` to return a flattened tabular `threads` array followed by `total`. In TOON detail output, encode unavailable thread fields as empty cells. Add `--json` to retain the nested JSON shape with unavailable keys omitted. Return or summarize the selected output without changing token semantics:

- For Codex, read incremental `last_token_usage`; use a cumulative-delta fallback only for legacy events without that field.
- Report available counts and `tokens` for each thread and in `total`. Omit unavailable per-thread fields and empty sections.
- Identify each thread with `provider` and `thread_id`; do not emit the redundant per-thread `thread` or `thread_count` fields.
- Do not emit `selection`, `source_file_count`, `token_event_count`, or `derived_sources`.
- Name each thread's first and last recorded token timestamps `start` and `end`. Set total `start` to the earliest available start and total `end` to the latest available end; omit either key when no corresponding timestamp is available.
- For numeric total fields, ignore per-thread `null` or absent values, aggregate available values, and return `0` when none are available.
- Count a Codex turn from each unique `turn_context`, a request from each token-bearing `token_count` event, and a tool call from each unique response item whose type ends in `_call`.
- For Cursor, count human bubbles as turns and unique local tool-call IDs as tool calls. Omit requests, token-event metadata, all token categories, and all token-derived categories because Cursor's local database does not record usable values for them.
- Apply `--after` and `--before` to activity-event timestamps as well as token events.
- Use the compact token schema `tokens.input.{total,cache_read,cache_write,uncached}`, `tokens.output.{total,reasoning}`, and `tokens.total`. Calculate `input.uncached` as `input.total - input.cache_read`.
- Treat cache-read input as cached input. Treat reasoning output as a subset of output, not an additional amount to add to total tokens.
- Return only the total by default. With `--detail`, always return a `threads` array and a `total` block, including when the result contains zero or one thread.
