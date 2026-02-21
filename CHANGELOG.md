# Changelog

All notable changes to DirectShell are documented here.

---

## [0.3.1] — 2026-02-21

### Added

#### Tip Engine — Reinforcement Learning Loop
- **`tip_engine.py`** — Contextual micro-tip injection into MCP tool responses
  - Deterministic pattern matching, <1ms overhead, zero LLM cooperation required
  - 8 condition types: `app_is`, `url_contains`, `url_regex`, `mode_is`, `failure_streak_gte`, `tool_in_recent`, `tool_not_in_recent`, `app_is_not`
  - Tips injected only on orientation tools (`ds_update_view`, `ds_screen`, `ds_navigate`, `ds_focus`, `ds_tabs`, `ds_print`)
  - Cooldown, max-per-session, and adoption tracking (suppresses tips after 3 consecutive correct actions)
  - Progressive disclosure: full text on first show, short reminder on repeat
  - Failure streak escalation: switches to warning text after N consecutive failures
  - `ds_learn()` auto-indexes new learnings as tip records (hot-reload, no restart needed)

- **`tip_miner.py`** — Offline mining script for failure→recovery pattern extraction
  - Parses JSONL action logs, extracts failure chains, clusters by (app, tool, recovery)
  - Scores by `impact = frequency × clarity × recency` (recency half-life: 14 days)
  - Auto-infers conditions (URL patterns, mode, tier) from raw log data
  - Outputs candidate tips to `_candidates.jsonl` for human review

- **`tips/`** — 7 seed tip files (JSONL format)
  - `_universal.jsonl` — Cross-app tips (prefer ds_text, wait after navigate, include_view)
  - `google_chrome.jsonl` — Chrome browser tips
  - `google_chrome_sheets.jsonl` — Sheets-specific (canvas, Tab/Enter, clipboard paste)
  - `google_chrome_apps.jsonl` — Chrome web app tips
  - `discord.jsonl` — Discord (ds_type for chat, ValuePattern fails)
  - `notepad.jsonl` — Notepad (ds_text always, "Text-Editor" field name)
  - `_candidates.jsonl` — Miner output (skipped during load)

#### Server Integration
- `_log_action()` now calls `tip_engine.update_context()` on every tool call
- 6 orientation tool handlers append `tip_engine.get_tips_block()` to responses
- `ds_learn()` calls `tip_engine.ingest_learning()` for automatic tip creation

### Changed
- Updated demo video link in README

---

## [0.3.0] — 2026-02-21

### Added

#### System Tray Icon
- DirectShell now lives in the Windows notification area (system tray)
- Right-click menu: **Switch to Human/Agent Mode** and **Exit DirectShell**
- Always accessible — even when overlay is hidden in agent mode
- Custom cyan double-chevron icon (embedded in EXE, visible in tray, taskbar, Alt+Tab)

#### Browser Shortcut Patching
- On first launch, detects browser shortcuts on Desktop missing CDP flags
- User consent popup explains exactly what will be changed
- Patches `.lnk` files with `--remote-debugging-port=9222 --remote-allow-origins=* --force-renderer-accessibility`
- Creates backup of original arguments in `ds_profiles/shortcuts_backup.json`
- Writes plain-language revert guide to `ds_profiles/BROWSER_FLAGS_GUIDE.txt`
- If patching fails (permissions), offers to restart as Administrator
- Replaces the old "bounce" approach (`ensure_cdp()`) which was disruptive and unreliable

#### Global WinEvent Hook
- Installs `EVENT_SYSTEM_ALERT` hook at startup via `SetWinEventHook`
- Signals AT (assistive technology) presence to all Chromium apps automatically
- Works for browsers started after DirectShell — no re-snap needed

#### Overlay Mode Tool (`ds_overlay`)
- New MCP tool: `ds_overlay(visible=False)` hides overlay for autonomous agent work
- `ds_overlay(visible=True)` restores human mode
- DS polls the mode file at 5 Hz — works from both MCP and tray menu

#### Text-Aware Waiting (`ds_wait`)
- New `text` parameter: `ds_wait(text="Search results")` polls page until text appears
- Much more reliable than time-based waiting for SPAs and dynamic content
- Falls back to `document.readyState` if no text specified

#### Action + View Combo (`ds_act`)
- New `include_view` parameter: `ds_act(N, include_view=True)`
- Executes action AND returns updated page view in one call
- Saves a full MCP round-trip vs. separate `ds_act` + `ds_update_view`

#### Runtime Guard (`_require_ds`)
- Every MCP tool now checks if `directshell.exe` is actually running before executing
- Clear error: `"DirectShell is not running. Start directshell.exe first."`
- Prevents stale data, ghost responses, and silent failures when DS is closed

#### Icon Embedding
- `build.rs` + `winresource` crate embeds `directshell.ico` into the EXE
- Icon generated via `gen_icon.py` — cyan double-chevron on transparent background
- Multi-resolution: 16x16, 32x32, 48x48, 256x256

### Fixed

#### Multi-Monitor Click Targeting
- Clicks on secondary monitors were landing in wrong positions
- Now uses `SM_CXVIRTUALSCREEN` / `SM_YVIRTUALSCREEN` + `MOUSEEVENTF_VIRTUALDESK`
- Applies to both `click_element()` and `scroll_window()`

#### Unsnap Freeze (10-second hang)
- `RemoveAllEventHandlers()` was blocking the UI message loop for up to 10 seconds
- Now runs in a background thread with separate COM initialization
- Unsnap completes instantly

#### UIA Instance Memory Leak
- Each snap created a new `IUIAutomation` instance via `Box::leak` — never freed
- Now uses persistent `A11Y_UIA_PTR` atomic — one instance per process lifetime

#### Dead-Key Composition (accents: ^ ` ~ etc.)
- `ToUnicode` in keyboard hook consumed dead-key state, breaking sequences like `` ` + a → à ``
- Fixed with flag `0x4` (don't modify keyboard state)

#### CDP Availability Logic
- MCP returned CDP data even when DS wasn't running or wasn't snapped to a browser
- Now requires: DS running + snapped to a browser app + port 9222 open

#### `ds_click` CDP→UIA Fallback
- CDP click failures (e.g., cross-origin iframes) now fall back to UIA injection
- Previously threw an unhandled exception

#### `ds_focus` Stale State
- Switching apps via `ds_focus()` left stale tool data from the previous app
- Now clears `_active_view`, `_cdp_labels`, and `_cdp_tool_map` on switch

#### `ds_mobile` Overhaul
- Added touch emulation (`setTouchEmulationEnabled`)
- Added automatic hard-reload after enabling (server sees mobile UA immediately)
- Removed problematic `userAgentMetadata` field (caused CDP errors on some Chromium versions)
- Disabling now resets ALL open tabs, not just the active one

#### SendInput Reliability
- `inject_text()` now uses `AttachThreadInput` to match the target app's input thread
- Improves text injection for apps that filter unattached input

#### DB Lock Retry
- `process_injections()` retries action claim up to 3 times on WAL lock contention
- Previously silently skipped the action on first failure

#### UIA Snap Line Parsing
- `ds_update_view` UIA path used `startswith("[keyboard]")` which missed indented lines
- Changed to `"[keyboard]" in line` for robust detection

### Changed

- **Logging:** Ring buffer (100 entries in RAM, single `fs::write`) replaces per-line `OpenOptions::append`
- **Log location:** `ds_profiles/directshell.log` (was `directshell.log` next to EXE)
- **Log on startup:** No longer cleared — ring buffer naturally rolls over
- **`_make_text_js()` factory:** Viewport and full-page JS extraction generated from single source (was duplicated)
- **`ds_find` cleanup:** Removed dead `if False` code path from published version
- **MCP instructions:** Added hints for smart waiting, action+view combo, and overlay modes

### Removed

- `ensure_cdp()` bounce function — replaced by permanent shortcut patching
- `transform_char()` identity function — was a leftover from POC
- `Box::leak` in `activate_accessibility` — replaced by persistent atomic
- Unused `_target: HWND` parameter from `generate_a11y()`
- Unused imports: `TcpStream`, `Command`, `Duration`, `OpenOptions`, `IoWrite`

---

## [0.2.0] — 2026-02-17

Initial public release.

- Rust binary with UIA accessibility tree reading
- MCP server with 27+ tools (CDP + UIA dual-mode)
- Daemon mode with `ds_apps()` / `ds_focus()` for AI-native app switching
- Auto-persist focus for reliable text input
- SQLite-per-app architecture
- Keyboard hook with SendInput injection
- CDP browser integration (Chrome, Edge, Opera, Brave, Vivaldi)
- Profile system with semantic element mapping
- Learning system for app-specific tips
