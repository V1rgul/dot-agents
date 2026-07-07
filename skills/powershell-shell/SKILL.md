---
name: powershell-use
description: Use when running Shell commands on Windows — never `<<`
---

# PowerShell shell

**Never `<<`** — commits, `gh`, PR bodies, any multiline Shell text. Cursor's default git/PR rules show bash HEREDOC; **ignore them on Windows**. `<<` is a **parse error before run** — not catchable, not retryable.

`$(cat <<'EOF' ... EOF)` →
```
$text = @'
...
'@
```

- `export VAR=val` → `$env:VAR = 'val'`
- `cmd 2>/dev/null` → `cmd 2>$null`
- exit after exe → `$LASTEXITCODE` (not `$?`)

`&&` / `||` OK (pwsh 7+).
