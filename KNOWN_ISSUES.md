# Known Issues & FAQ

This is a living document. DirectShell is Day 1 software — expect rough edges.

---

## Known Issues

### PowerShell disables PSReadLine after running DirectShell

**Symptom:** PowerShell shows:
```
Warning: PowerShell detected that you might be using a screen reader
and has disabled PSReadLine for compatibility purposes.
```

**Cause:** DirectShell's Chromium activation sets the `SPI_SETSCREENREADER` system flag and persists it to the registry. This is required — without it, Chromium-based apps (Chrome, Edge, Discord, VS Code, Slack) will not expose their accessibility tree.

**Fix:** Run `Import-Module PSReadLine` in your PowerShell session. Or to undo the registry change permanently:
```
reg add "HKLM\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Accessibility\ATs\directshell" /v StartExe /t REG_SZ /d "" /f
```
Alternatively, set the screen reader flag back to 0:
```powershell
# PowerShell
Add-Type -TypeDefinition 'using System.Runtime.InteropServices; public class SR { [DllImport("user32.dll")] public static extern bool SystemParametersInfo(uint a, uint b, ref int c, uint d); }';
$v = 0; [SR]::SystemParametersInfo(0x0047, 0, [ref]$v, 0x0002)
```

**Why we don't auto-revert:** The flag must stay active while DirectShell is running and while any Chromium app needs its tree read. Auto-reverting on exit would break the tree for any still-running Chromium app.

---

### MCP server says "not snapped" but the overlay is visible

**Symptom:** `ds_status()` returns `{"snapped": false}` even though the DirectShell overlay is visibly attached to an application.

**Cause:** The MCP server's `--profiles` path doesn't match where `directshell.exe` writes its database files. DirectShell creates `ds_profiles/` relative to its working directory (where you launched the EXE).

**Fix:** Check where your `ds_profiles/` folder actually is:
```bash
# Find where DirectShell created its files
dir ds_profiles\
```
Then update your MCP config `--profiles` to point to that exact path.

---

### Database is empty / SQL queries return nothing

**Symptom:** `sqlite3 ds_profiles/app.db "SELECT * FROM elements"` returns no rows.

**Cause:** Same as above — you're querying a different database than the one DirectShell is writing to. Or DirectShell hasn't completed its first tree dump yet (takes up to 500ms after snapping).

**Fix:** Make sure you're querying the correct `.db` file inside the `ds_profiles/` folder where DirectShell.exe is actually running.

---

### Terminals (PowerShell, cmd, Windows Terminal) show limited tree

**Symptom:** When snapped to a terminal, `ds_state()` shows window chrome (minimize, maximize, close, tabs) but no terminal content.

**Cause:** Terminal emulators expose minimal accessibility information for their text content. The `TermControl` used by Windows Terminal does not fully populate the accessibility tree with shell output.

**Status:** This is a limitation of the terminal's accessibility implementation, not a DirectShell bug. Most GUI applications (browsers, Office, enterprise software) expose rich trees.

---

### MCP server crashes with `ModuleNotFoundError: No module named 'fastmcp'`

**Symptom:** MCP client shows "Server disconnected" and the log contains:
```
ModuleNotFoundError: No module named 'fastmcp'
```

**Fix:**
```bash
pip install fastmcp
```
Make sure you install it for the same Python version your MCP client uses. Some systems have multiple Python installations (3.10, 3.12, 3.14) — check which one the MCP client invokes.

---

### MCP server burns CPU on Windows (anyio stdin busy-polling)

**Symptom:** The `server.py` MCP process consumes ~5% system CPU (or 95% of one core) even when idle. Visible in Task Manager as a Python process with high CPU time.

**Cause:** The FastMCP framework uses `anyio.wrap_file(sys.stdin)` internally for JSON-RPC communication. On Windows, this spawns a background thread that busy-polls stdin because Windows doesn't support async file I/O on pipes the way Unix does. When the parent process disconnects (e.g., MCP client restarts), the polling thread spins on a broken pipe.

**Impact:** Wastes CPU permanently. On a system with multiple MCP servers running, this adds up.

**Status:** Open. An attempted fix (wrapping stdin with a custom `GuardedStdin` class) broke MCP communication entirely and was reverted. The root cause is in `anyio`'s Windows implementation, not in DirectShell.

**Contributor hint:** This likely needs a fix upstream in `anyio` or a workaround at the FastMCP level. Possible approaches:
- Patch `anyio`'s `wrap_file` to use a non-busy wait on Windows pipes
- Use a custom stdio transport that avoids `anyio.wrap_file` entirely
- Add a stdin health-check thread that exits the process when the pipe breaks

---

### Unicode emoji/surrogate pairs silently truncated in keyboard input

**Symptom:** Characters above U+FFFF (emoji like `U+1F600`, rare CJK extension characters) are silently dropped or corrupted when typed via `ds_type()` or the keyboard hook.

**Cause:** `inject_char()` in `main.rs` casts `char` to `u16` directly (`ch as u16`), which truncates any codepoint that requires a UTF-16 surrogate pair (two `u16` values). The `KEYBDINPUT` struct expects UTF-16 code units, not Unicode codepoints.

**Fix needed:** Encode the character as UTF-16 (`char::encode_utf16`) and send two `KEYBDINPUT` events (high surrogate + low surrogate) for characters above U+FFFF.

**Impact:** Low — most UI automation involves ASCII/Latin text. Affects emoji input and some CJK characters.

---

### `RemoveAllEventHandlers()` background thread has no timeout

**Symptom:** On rare occasions, unsnapping from a hung or crashed application causes a background thread to hang indefinitely.

**Cause:** When unsnapping, `unregister_event_handlers()` spawns a thread that calls `IUIAutomation::RemoveAllEventHandlers()`. This COM call can block indefinitely if the target application's process is in a broken state (deadlocked, zombie).

**Impact:** The leaked thread consumes minimal resources but is never cleaned up. In normal operation (healthy target apps) the call completes in <1 second.

**Contributor hint:** Add a watchdog — if the thread doesn't complete within 5 seconds, log a warning and move on. The leaked UIA instance will be cleaned up on process exit.

---

### Chromium apps need a few seconds after snapping

**Symptom:** Snapping to Chrome, Edge, Discord, VS Code, or other Chromium-based apps shows an incomplete or empty tree initially.

**Cause:** Chromium lazily builds its accessibility tree. DirectShell triggers activation via `SPI_SETSCREENREADER` + UIA event handlers + MSAA probes, but Chromium needs 1-3 seconds to respond.

**Fix:** Wait 2-3 seconds after snapping, then query again. This is expected behavior on first snap.

---

## FAQ

### Does DirectShell modify the target application?

No. DirectShell reads the accessibility tree (which Windows provides for every application) and injects input via `SendInput` (standard Win32 API). It does not inject code, hook DLLs, or modify the target process in any way.

### Can the target application detect DirectShell?

The overlay window is transparent and click-through — the target app doesn't know it's there. Input injected via `SendInput` is indistinguishable from physical hardware input at the OS level.

### Does it work with every application?

Every application that has a window exposes an accessibility tree. The quality varies — major enterprise software and frameworks (WPF, WinForms, Qt, Chromium, Java Swing) have comprehensive trees. Smaller or custom-rendered applications may have unnamed elements.

### Why AGPL and not MIT?

AGPL ensures that if someone builds a commercial service on DirectShell, they must share their modifications. The primitive itself stays open. You can use it freely for personal and internal use.
