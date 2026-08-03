# Implementation

The script reads Codex JSONL sessions from `$CODEX_HOME`, defaulting to `~/.codex`, and reads Cursor's local `state.vscdb` read-only from the platform's Cursor user-data directory.

- For Codex, read incremental `last_token_usage`; use a cumulative-delta fallback only for legacy events without that field.
- Apply the selected `--fields` projection to `total`; with `--detail`, apply the same projection to thread rows. Always include `total.thread_count`. Omit unavailable per-thread fields and empty sections.
- Exclude `models` from the default `--detail` fields; collect it only when explicitly requested with `--fields models` or `--fields all`.
- With neither `--detail` nor `--fields`, collect only activity presence and report `thread_count`. Treat omitted selectors and the explicit `codex:all cursor:all` selection as the same overview request, including the `description → total → thread_count` shape; selector order is irrelevant. JSON omits hints. Explain that the available detail-field names are passed after `--fields`.
- Pass the requested fields and time boundaries through the collector into each provider parser. Do not collect a full result and discard fields afterward.
- For Cursor, apply the time boundaries in SQLite `WHERE` predicates and select only the JSON properties required for activity, turns, or tool calls. Do not decode bubble JSON in Python.
- For Codex `provider:all` selection, use JSONL modification times only as an `after` candidate prefilter, then enforce the exact event-time window in the parser. Skip irrelevant record types before JSON decoding and stop after the first qualifying token request when only activity presence is required.
- Never suggest a CLI flag that was already supplied in successful output. For a partially specified time range, suggest only the missing boundary flag. Usage-error help is a reference and always lists the complete supported flag set.
- Reject malformed `--after` and `--before` values. Reject `--after` when it is greater than or equal to either `--before` or the current time.
- Express hints using arguments only; never include the executable name or path.
- When a thread selector does not match, suggest provider-scoped discovery with `<provider>:all --detail --fields provider,thread_id,thread_name` and preserve any explicitly supplied time boundaries. Never suggest retrying the missing selector.
- When a field selection contains fields unavailable from a selected provider, name the provider and fields in a non-JSON hint and point to `--fields --help` for the authoritative provider table. This explicit reference may repeat the already-supplied `--fields` flag.
- Identify each thread with `provider` and `thread_id`; do not emit the redundant per-thread `thread` or `thread_count` fields.
- Do not emit `bin`, `selection`, `source_file_count`, `token_event_count`, or `derived_sources`.
- Name each thread's first and last recorded token timestamps `start` and `end`. Set total `start` to the earliest available start and total `end` to the latest available end; omit either key when no corresponding timestamp is available.
- For numeric total fields, ignore per-thread `null` or absent values, aggregate available values, and return `0` when none are available.
- Count a Codex turn from each unique `turn_context`, a request from each token-bearing `token_count` event, and a tool call from each unique response item whose type ends in `_call`.
- Report distinct Codex effective model configurations from `turn_context.payload.model` and `turn_context.payload.effort` as `model@effort` entries in detailed `models` output. Apply the selected time range and carry the latest preceding configuration forward to token events in that range. Fall back to the model alone for legacy records without effort; omit the field when no model is recorded.
- Report `total.models` as the sorted union of available per-thread model configurations. Omit it when no selected thread has model data.
- Preserve thread and total `models` as arrays in JSON. Join both with commas only in the TOON projection and encode TOON arrays with the pipe delimiter so the model shape stays consistent within each format and thread rows remain tabular without quoting multi-model cells.
- For Cursor, count human bubbles as turns and unique local tool-call IDs as tool calls. Omit requests, token-event metadata, all token categories, and all token-derived categories because Cursor's local database does not record usable values for them.
- Do not report Cursor `composerData.modelConfig` as model usage: it is mutable composer configuration, while local bubble records do not retain per-request model history.
- Use the compact token schema `tokens.input.{total,cache_read,cache_write,uncached}`, `tokens.output.{total,reasoning}`, and `tokens.total`. Calculate `input.uncached` as `max(0, input.total - input.cache_read - input.cache_write)`, treating an unavailable cache-write count as zero.
- Treat cache-read input as cached input. Treat reasoning output as a subset of output, not an additional amount to add to total tokens.
- Treat a zero-thread result as a successful, definitive empty state with `thread_count: 0` and zero-valued totals. In non-JSON output, add `No recorded activity matches the selected time range; adjust --after or --before` to `help`.
