---
name: json-jq
description: Use jq for JSON validation and extraction
paths:
  - "**/*.json"
---

Use `jq`; do not parse JSON with grep, brace counting, or shell strings.

- Validate: `jq empty -- file.json`
- Format/query: `jq . file.json`; `jq '.key[]' file.json`
- NDJSON: `jq -s . lines.ndjson`
- Large inputs: filter early and use compact output (`-c`)
