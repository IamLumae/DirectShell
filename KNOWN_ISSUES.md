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
