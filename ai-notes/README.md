# The AI_NOTES Convention

A shared, append-friendly knowledge base that any AI session driving
DirectShell reads **first** and writes back to when it learns something.

## Why

Every AI session controlling DirectShell through accessibility trees and
input injection rediscovers the same traps: keystrokes landing in browser
Quick Find bars, text appended instead of replacing dialog field contents,
focus drifting mid-action. Each session pays the same debugging cost.

The convention turns those costs into compounding assets: when an AI hits a
gotcha, it logs it; every future session — of any AI, on any machine that
syncs this file — checks the log before acting and skips the pain.

## The rules

1. **Before troubleshooting anything weird**, read the notes file. Another
   AI may have already solved it.
2. **When you discover a gotcha or workaround**, append an entry
   immediately, newest-first. One screen, no essays.
3. **Never delete or rewrite other entries.** Append-only history.

## Entry format

```markdown
## [YYYY-MM-DD HH:MM] <app / context>
- Symptom: <what went wrong / the trap>
- Instead: <what to do>
```

Newest entries go at the top, right after the header block.

## File locations

| Path | Role |
|---|---|
| `<config>/directshell/AI_NOTES.md` | Live shared file (Linux: `~/.config/directshell/`; Windows: `%APPDATA%\DirectShell\`) |
| `ai-notes/AI_NOTES.md` (this folder) | Seed shipped with the build |

On first run, if the live file does not exist yet, the build seeds it from
the bundled copy — so a fresh install already knows everything previous
sessions learned.

## MCP tool integration (reference implementation)

Harfho's Linux port (`DirectShell-Linux`, `ds-mcp/server.py`) exposes the
convention as two stdlib-only MCP tools any client can call:

- `get_notes()` — returns the live notes file content plus its resolved path
- `append_note({app?, situation, do})` — validates fields, timestamps, and
  prepends a formatted entry

Path resolution: primary config location, auto-seeded from the bundled
copy on first run; falls back to the version-local profiles directory if
the config directory cannot be created or written.

## Current knowledge

See [AI_NOTES.md](./AI_NOTES.md).
