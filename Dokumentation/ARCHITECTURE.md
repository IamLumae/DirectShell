# DirectShell Architecture Reference

**Version:** 0.2.0
**Language:** Rust (Edition 2021)
**Platform:** Windows 10/11 (x86_64)
**Build System:** Cargo
**Binary Type:** Win32 GUI Subsystem (`#![windows_subsystem = "windows"]`)

---

## Table of Contents

1. [System Overview](#1-system-overview)
2. [Dependencies](#2-dependencies)
3. [Constants and Configuration](#3-constants-and-configuration)
4. [Global State](#4-global-state)
5. [Window Management](#5-window-management)
   - 5.1 [Window Creation](#51-window-creation)
   - 5.2 [Layered Window Rendering](#52-layered-window-rendering)
   - 5.3 [Window Procedure](#53-window-procedure)
   - 5.4 [Double-Buffered Painting](#54-double-buffered-painting)
   - 5.5 [Light Animation](#55-light-animation)
6. [Snap Engine](#6-snap-engine)
   - 6.1 [Snap Detection](#61-snap-detection)
   - 6.2 [Snap Execution](#62-snap-execution)
   - 6.3 [Unsnap](#63-unsnap)
   - 6.4 [Position Synchronization](#64-position-synchronization)
   - 6.5 [Caption Probe](#65-caption-probe)
7. [Accessibility Tree Engine](#7-accessibility-tree-engine)
   - 7.1 [Tree Walking](#71-tree-walking)
   - 7.2 [Streaming Pipeline](#72-streaming-pipeline)
   - 7.3 [Chromium Accessibility Activation](#73-chromium-accessibility-activation)
8. [Output File Generation](#8-output-file-generation)
   - 8.1 [.db — SQLite Element Database](#81-db--sqlite-element-database)
   - 8.2 [.snap — Interactive Element Snapshot](#82-snap--interactive-element-snapshot)
   - 8.3 [.a11y — Screen Reader View](#83-a11y--screen-reader-view)
   - 8.4 [.a11y.snap — Operable Element Index](#84-a11ysnap--operable-element-index)
9. [Action Queue (Input Pipeline)](#9-action-queue-input-pipeline)
   - 9.1 [Queue Schema](#91-queue-schema)
   - 9.2 [Action Types](#92-action-types)
   - 9.3 [Text Injection](#93-text-injection)
   - 9.4 [Key Injection](#94-key-injection)
   - 9.5 [Click Injection](#95-click-injection)
   - 9.6 [Scroll Injection](#96-scroll-injection)
   - 9.7 [Dispatch Loop](#97-dispatch-loop)
10. [Keyboard Hook](#10-keyboard-hook)
11. [Timer Architecture](#11-timer-architecture)
12. [Persistence Model](#12-persistence-model)
13. [Data Flow Diagrams](#13-data-flow-diagrams)

---

## 1. System Overview

DirectShell is a Win32 overlay application that attaches to any target window and provides programmatic access to its UI through the Windows UI Automation (UIA) framework. It operates as a transparent frame overlay that snaps onto a target application's title bar and continuously dumps the target's accessibility tree into a SQLite database. External processes interact with the target application by reading the generated output files and writing actions into the SQLite action queue.

**Execution Model:**

```
DirectShell.exe (Win32 GUI)
├── Main Thread: Message loop, window procedure, painting, timer dispatch
├── Tree Thread (spawned per dump): UIA tree walk, SQLite write, file generation
└── Keyboard Hook: Global low-level keyboard interception (WH_KEYBOARD_LL)
```

**External Interface:**

```
External Process (e.g., CLI agent)
├── READ:  ds_profiles/is_active        ← Check snap state + discover file paths
├── READ:  ds_profiles/{app}.a11y       ← Understand screen content
├── READ:  ds_profiles/{app}.a11y.snap  ← Identify operable elements
├── READ:  ds_profiles/{app}.snap       ← All interactive elements
├── READ:  ds_profiles/{app}.db         ← Full element tree (SQLite)
└── WRITE: ds_profiles/{app}.db         ← INSERT INTO inject table
```

---

## 2. Dependencies

| Crate | Version | Features | Purpose |
|-------|---------|----------|---------|
| `rusqlite` | 0.31 | `bundled` | SQLite database (bundled, no system dependency) |
| `windows` | 0.58 | See below | Win32 API bindings |

**Windows crate features:**

| Feature | Usage |
|---------|-------|
| `Win32_Foundation` | HWND, RECT, BOOL, LRESULT, WPARAM, LPARAM |
| `Win32_UI_WindowsAndMessaging` | Window creation, messages, timers, hooks |
| `Win32_Graphics_Gdi` | GDI painting, brushes, pens, double buffering |
| `Win32_System_LibraryLoader` | GetModuleHandleW |
| `Win32_UI_Accessibility` | IUIAutomation, tree walking, element properties |
| `Win32_System_Com` | CoInitializeEx, CoCreateInstance |
| `Win32_System_Ole` | VARIANT support |
| `Win32_System_Variant` | VARIANT type definitions |
| `Win32_UI_Input_KeyboardAndMouse` | SendInput, virtual key codes |
| `Win32_System_Threading` | GetCurrentThreadId, AttachThreadInput |

---

## 3. Constants and Configuration

### Visual Constants

| Constant | Value | Description |
|----------|-------|-------------|
| `INVIS` | `0x00FF00FF` | Magenta color key (transparent) |
| `TOP_CLR` | `0x00827873` | Title bar background (anthracite) |
| `SIDE_CLR` | `0x00736964` | Side border background |
| `BOT_CLR` | `0x005F5550` | Bottom border background |
| `HL_CLR` | `0x00D7CDC8` | Highlight/light color |
| `SH_CLR` | `0x00413732` | Shadow color |
| `ICON_CLR` | `0x00D0D0D0` | Button icon color |
| `ALPHA` | `180` | Window opacity (0-255) |
| `CORNER_R` | `8` | Corner radius (pixels) |

All color values are in `COLORREF` format (`0x00BBGGRR`).

### Dimension Constants

| Constant | Value | Description |
|----------|-------|-------------|
| `DEFAULT_TOP_H` | `20` | Default title bar height (unsnapped) |
| `SIDE_W` | `4` | Side border width |
| `GRIP` | `12` | Hit-test grip area for dragging |
| `FALLBACK_BTN_X` | `140` | Fallback caption button offset |
| `INIT_W` | `500` | Initial window width |
| `INIT_H` | `350` | Initial window height |
| `SNAP_THRESH` | `0.20` | Minimum overlap ratio to trigger snap (20%) |

### Timer Constants

| Constant | ID | Interval | Description |
|----------|----|----------|-------------|
| `SYNC_TIMER` | `1` | `16 ms` | Position synchronization (~60 Hz) |
| `ANIM_TIMER` | `2` | `33 ms` | Light animation (~30 Hz) |
| `TREE_TIMER` | `3` | `500 ms` | Accessibility tree dump (2 Hz) |
| `INJECT_TIMER` | `4` | `30 ms` | Action queue processing (~33 Hz) |

### Tree Walk Constants

| Constant | Value | Description |
|----------|-------|-------------|
| `MAX_DEPTH` | `i32::MAX` | No depth limit on tree traversal |
| `MAX_CHILDREN` | `i32::MAX` | No child count limit per node |
| `STREAM_BATCH` | `200` | Commit interval during streaming insert |
| `TREE_TIMEOUT_MS` | `2000` | UIA connection timeout per dump |

### File System Constants

| Constant | Value | Description |
|----------|-------|-------------|
| `DB_DIR` | `"ds_profiles"` | Directory for persistent per-app databases |
| `ACTIVE_FILE` | `"ds_profiles/is_active"` | Status file for AI agents (current snap state) |
| `LOG_FILE` | `"directshell.log"` | Runtime log file |

---

## 4. Global State

All global state uses `std::sync::atomic` types for lock-free thread safety between the main thread and the tree dump thread.

| Variable | Type | Description |
|----------|------|-------------|
| `TARGET_HW` | `AtomicIsize` | HWND of the snapped target window (0 = none) |
| `IS_SNAPPED` | `AtomicBool` | Whether DirectShell is currently snapped to a target |
| `TREE_BUSY` | `AtomicBool` | Mutex-like flag preventing concurrent tree dumps |
| `CURRENT_DB` | `Mutex<String>` | Path to the current app's SQLite database |
| `KB_HOOK` | `AtomicIsize` | Handle to the installed keyboard hook |
| `LAST_X/Y/W/H` | `AtomicI32` | Last known position and size of the overlay |
| `BTN_OFF_X` | `AtomicI32` | Target app's caption button offset from right edge |
| `DYN_TOP_H` | `AtomicI32` | Dynamic title bar height (matched from target) |
| `START_TIME` | `OnceLock<Instant>` | Application start time (for animation) |

All atomic operations use `SeqCst` ordering.

**Helper functions:**

| Function | Signature | Description |
|----------|-----------|-------------|
| `tgt()` | `→ HWND` | Returns current target window handle |
| `snapped()` | `→ bool` | Returns snap state |
| `top_h()` | `→ i32` | Returns current title bar height |
| `save(x,y,w,h)` | `→ ()` | Stores position/size atomically |
| `saved()` | `→ (i32,i32,i32,i32)` | Retrieves stored position/size |

---

## 5. Window Management

### 5.1 Window Creation

DirectShell creates a single `WS_POPUP` window with the following extended styles:

- `WS_EX_LAYERED` — Enables per-pixel transparency via color keying
- `WS_EX_TOPMOST` — Stays above all other windows (removed when snapped)

```
Window Class: "DirectShell"
Style: WS_POPUP | WS_VISIBLE
Extended Style: WS_EX_LAYERED | WS_EX_TOPMOST
Background: CreateSolidBrush(INVIS)  [magenta, keyed out]
Initial Position: 200, 200
Initial Size: 500 x 350
```

The layered window attributes are configured via `SetLayeredWindowAttributes`:
- Color key: `INVIS` (magenta pixels become transparent)
- Alpha: `180` (global opacity)
- Flags: `LWA_COLORKEY | LWA_ALPHA`

**Startup sequence:**

1. Clear log file
2. `CoInitializeEx(COINIT_MULTITHREADED)` — Initialize COM for UIA
3. `SystemParametersInfoW(SPI_SETSCREENREADER, 1)` — Set global screen reader flag
4. Register window class
5. Create popup window
6. Set layered window attributes
7. Start animation timer (`ANIM_TIMER`)
8. Install global keyboard hook (`WH_KEYBOARD_LL`)
9. Enter message loop (`GetMessageW` / `TranslateMessage` / `DispatchMessageW`)

### 5.2 Layered Window Rendering

The window is rendered as a transparent overlay using Win32's layered window mechanism. The background is filled with magenta (`0x00FF00FF`), which is keyed out as transparent. Only the frame elements (title bar, side borders, bottom border) are painted in opaque colors, creating a visible border that floats over the target application.

When unsnapped, the window appears as a small floating frame with:
- Anthracite title bar with rounded top corners
- 3D beveled edges (highlight line on top, shadow line on bottom)
- Close button (red background, X icon)
- Animated light reflex traveling around the border

When snapped, the frame:
- Matches the target window's title bar height
- Displays an unsnap button (circled cross icon) positioned adjacent to the target's caption buttons
- Omits the close button and light animation

### 5.3 Window Procedure

The window procedure (`wndproc`) handles the following messages:

| Message | Behavior |
|---------|----------|
| `WM_PAINT` | Calls `paint()` for double-buffered rendering |
| `WM_NCHITTEST` | Custom hit testing: title bar area returns `HTCAPTION` (enables dragging), button areas return `HTCLIENT`, body returns `HTTRANSPARENT` (click-through) |
| `WM_LBUTTONDOWN` | Button click handling: unsnap button (when snapped) or close button (when unsnapped) |
| `WM_EXITSIZEMOVE` | After user finishes moving the window, attempts to snap to underlying window |
| `WM_MOVING` | When snapped: moves the target window in sync with the overlay |
| `WM_TIMER` | Dispatches to `do_sync` (position sync), `InvalidateRect` (animation), or `dump_tree + process_injections` (tree dump + action queue) |
| `WM_CLOSE` | Closes target window if snapped, unsnaps, destroys self |
| `WM_DESTROY` | Removes keyboard hook, posts quit message |

### 5.4 Double-Buffered Painting

The `paint()` function renders to an off-screen bitmap, then copies to screen via `BitBlt`:

1. Create compatible DC and bitmap
2. Fill with magenta (transparent background)
3. Create rounded clip region (top corners only)
4. Paint frame components:
   - Title bar (anthracite, full width)
   - Side borders (slightly darker, between title bar and bottom)
   - Bottom border (darkest)
5. Paint 3D lines (highlight on top edge, shadow on bottom edge)
6. If unsnapped: paint light reflex animation + close button
7. If snapped: paint unsnap button
8. `BitBlt` from memory DC to screen DC

### 5.5 Light Animation

A diffuse light travels continuously around the border perimeter. The animation uses:

- **Period:** 3000 ms (one full revolution)
- **Light length:** 120 pixels
- **Gradient resolution:** 24 steps
- **Falloff function:** `cos^2(distance * pi/2)` — smooth quadratic cosine falloff
- **Color interpolation:** Linear RGB interpolation between edge background color and highlight color

The perimeter is calculated as `2 * width + 2 * (height - title_bar_height)`, divided into four edges (top, right, bottom, left). The light position wraps around using modulo arithmetic with three ghost positions (center, center+perimeter, center-perimeter) to handle the wrap-around discontinuity.

The `lerp_clr` function performs per-channel linear interpolation in BGR color space:
```
result.channel = a.channel + (b.channel - a.channel) * t
```

---

## 6. Snap Engine

### 6.1 Snap Detection

`find_snap(me: HWND) → Option<HWND>`

Triggered by `WM_EXITSIZEMOVE` (after the user releases the window from a drag).

**Algorithm:**

1. Get DirectShell's bounding rectangle
2. Calculate center point
3. Temporarily hide DirectShell (`SW_HIDE`)
4. Call `WindowFromPoint` at the center — returns whatever window is beneath
5. Show DirectShell again (`SW_SHOWNA`)
6. Resolve the hit window to its top-level ancestor (`GetAncestor(GA_ROOT)`)
7. Reject if: null, self, not visible, or a shell window
8. Calculate overlap ratio between DirectShell and the candidate
9. If overlap >= `SNAP_THRESH` (20%): return the candidate

**Shell window detection** (`is_shell`): Filters out Desktop, Taskbar, and related system windows by class name matching against: `Progman`, `WorkerW`, `Shell_TrayWnd`, `Shell_SecondaryTrayWnd`, `SHELLDLL_DefView`.

### 6.2 Snap Execution

`do_snap(me: HWND, target: HWND)`

**Sequence:**

1. Read target window's bounding rectangle
2. Set DirectShell as owned window of target (`SetWindowLongPtrW` with `GWL_HWNDPARENT`)
   - This ensures Windows keeps the overlay above its owner automatically (Z-order inheritance)
3. Remove `WS_EX_TOPMOST` and reposition to match target (`SetWindowPos(HWND_NOTOPMOST)`)
4. Store target HWND and set `IS_SNAPPED = true`
5. Probe target's caption bar via UIA to determine:
   - Title bar height
   - Caption button offset (distance from right edge to leftmost caption button)
6. Derive database filename from window title (`db_name_from_title`)
7. Create `ds_profiles/` directory if needed
8. Call `activate_accessibility(target)` — Chromium-specific accessibility activation
9. Stop animation timer, start sync timer (16ms) and tree timer (500ms)
10. Trigger first tree dump immediately

**Database naming** (`db_name_from_title`):

1. Extract the last segment after ` – ` (em-dash) or ` - ` (hyphen)
   - Example: `"Google Gemini – Opera"` → `"Opera"`
2. Sanitize: lowercase, replace non-alphanumeric with underscore, trim underscores
3. Format: `ds_profiles/{sanitized}.db`
   - Example: `"ds_profiles/opera.db"`

### 6.3 Unsnap

`do_unsnap(me: HWND)`

1. Kill sync and tree timers
2. Clear database path (stops tree dumps)
3. Clear snap state and target HWND
4. Reset title bar height to default
5. Remove owner relationship (`SetWindowLongPtrW` with 0)
6. Restore `WS_EX_TOPMOST` and resize to initial dimensions
7. Restart animation timer

Database files are **not** deleted on unsnap. They persist in `ds_profiles/` for reuse on re-snap.

### 6.4 Position Synchronization

`do_sync(me: HWND)` — Called at ~60 Hz via `SYNC_TIMER`.

**Logic:**

1. If target is minimized (`IsIconic`): hide DirectShell, return
2. If DirectShell is hidden but target is visible: show DirectShell
3. Read current positions of both windows
4. Compare against last saved position:
   - If **target moved** (target position differs from saved): move DirectShell to match target
   - If **DirectShell moved** (overlay position differs from saved): move target to match DirectShell
5. Save the new synchronized position

This bidirectional sync allows the user to drag either window and have the other follow. Z-order synchronization is handled automatically by the Win32 owner/owned relationship.

If the target window is destroyed (`IsWindow` returns false), DirectShell automatically unsnaps.

### 6.5 Caption Probe

`probe_caption(target: HWND) → CaptionInfo`

Uses UIA to analyze the target window's title bar structure:

1. Create `IUIAutomation` instance
2. Get root element from target window handle
3. Find the TitleBar element (`ControlType = 50037`)
4. Read its `BoundingRectangle` → compute bar height
   - Height = `max(TitleBar.bottom - Window.top, TitleBar height, DEFAULT_TOP_H)`, clamped to 60
5. Find all Button elements (`ControlType = 50000`) within the TitleBar
6. Determine the leftmost button's X position
7. Compute offset: `window.right - leftmost_button.left`
   - Clamped to range (0, 400), fallback = 140

This information is used to position the unsnap button adjacent to the target's native caption buttons.

---

## 7. Accessibility Tree Engine

### 7.1 Tree Walking

The tree walk uses `IUIAutomation.RawViewWalker()` for an unfiltered traversal of the target window's UI Automation element tree. The walk is depth-first, processing children from first to last sibling.

**Per-element properties extracted:**

| Property | UIA Method | SQLite Column |
|----------|-----------|---------------|
| Control Type | `CurrentControlType()` | `role` (mapped via `role_name()`) |
| Name | `CurrentName()` | `name` |
| Value | `GetCurrentPattern(ValuePatternId)` | `value` |
| Automation ID | `CurrentAutomationId()` | `automation_id` |
| Enabled | `CurrentIsEnabled()` | `enabled` |
| Off-screen | `CurrentIsOffscreen()` | `offscreen` |
| Bounding Rectangle | `CurrentBoundingRectangle()` | `x`, `y`, `w`, `h` |

**Role name mapping** (`role_name`): Maps UIA ControlType IDs (50000–50038) to human-readable names:

| ID | Name | ID | Name |
|----|------|----|------|
| 50000 | Button | 50019 | TabItem |
| 50001 | Calendar | 50020 | Text |
| 50002 | CheckBox | 50021 | ToolBar |
| 50003 | ComboBox | 50022 | ToolTip |
| 50004 | Edit | 50023 | Tree |
| 50005 | Hyperlink | 50024 | TreeItem |
| 50006 | Image | 50025 | Custom |
| 50007 | ListItem | 50026 | Group |
| 50008 | List | 50027 | Thumb |
| 50009 | Menu | 50028 | DataGrid |
| 50010 | MenuBar | 50029 | DataItem |
| 50011 | MenuItem | 50030 | Document |
| 50012 | ProgressBar | 50031 | SplitButton |
| 50013 | RadioButton | 50032 | Window |
| 50014 | ScrollBar | 50033 | Pane |
| 50015 | Slider | 50034 | Header |
| 50016 | Spinner | 50035 | HeaderItem |
| 50017 | StatusBar | 50036 | Table |
| 50018 | Tab | 50037 | TitleBar |
| — | — | 50038 | Separator |

### 7.2 Streaming Pipeline

`dump_tree()` — Called at 2 Hz via `TREE_TIMER`.

**Thread model:** Each dump spawns a new `std::thread` that:

1. Acquires `TREE_BUSY` flag via atomic `compare_exchange` (prevents concurrent dumps)
2. Initializes COM on the new thread (`CoInitializeEx(COINIT_MULTITHREADED)`)
3. Validates target window still exists
4. Creates `IUIAutomation` instance, sets connection timeout to 2000ms
5. Gets root element from target HWND
6. Opens SQLite database
7. **Drops and recreates** `elements` and `meta` tables (avoids freelist bloat from DELETE)
8. Inserts metadata row (window title, HWND, timestamp, position)
9. Begins transaction
10. Walks tree via `stream_elements()`:
    - Inserts each element with sequential ID
    - Commits every 200 elements (`STREAM_BATCH`) for progressive data availability
11. Final commit
12. Generates output files: `.snap`, `.a11y`, `.a11y.snap`
13. Releases COM and `TREE_BUSY` flag

The `inject` table is **not** dropped during re-dumps. It persists across tree refreshes.

**Note on indices:** The `init_db` function creates indices (`idx_role`, `idx_offscreen`, `idx_visible`) on the `elements` table. However, since `dump_tree` drops and recreates the `elements` table on every cycle, these indices do not persist beyond the first dump. This is intentional — indices slow down INSERT operations, and since the entire table is rebuilt every 500ms, query performance on the elements table relies on SQLite's efficient sequential scan for the small-to-medium result sets typical of output file generation.

**SQLite configuration:**

| PRAGMA | Value | Purpose |
|--------|-------|---------|
| `auto_vacuum` | `FULL` | Automatic space reclamation |
| `journal_mode` | `WAL` | Write-Ahead Logging for concurrent reads |
| `synchronous` | `NORMAL` | Balanced durability/performance |

### 7.3 Chromium Accessibility Activation

`activate_accessibility(target: HWND)`

Chromium-based applications (Chrome, Edge, Opera, Electron apps) do not construct their full accessibility tree by default. They check three conditions to decide whether to build it:

1. `SPI_GETSCREENREADER` — system-wide "screen reader active" flag
2. `UiaClientsAreListening()` — whether any UIA event handlers are registered
3. `WM_GETOBJECT` on `Chrome_RenderWidgetHostHWND` — per-renderer activation

DirectShell triggers all three. The activation sequence has four phases:

**Phase 1: System-Level Signal**

- `SystemParametersInfoW(SPI_SETSCREENREADER, 1, SPIF_UPDATEINIFILE | SPIF_SENDCHANGE)` — sets and persists the global flag
- `SendMessageW(target, WM_SETTINGCHANGE, SPI_SETSCREENREADER, 0)` — sends the settings change notification directly to the target (does not wait for system broadcast)

**Phase 2: UIA Event Handler Registration (Key Innovation)**

- Creates `IUIAutomation` instance from `CUIAutomation8`
- Registers a `FocusChangedEventHandler` on the root element via `AddFocusChangedEventHandler`
- The handler is a no-op (`UiaFocusHandler` struct that implements `IUIAutomationFocusChangedEventHandler` with an empty `HandleFocusChangedEvent`)
- The handler is intentionally `Box::leak`ed — it stays alive for the lifetime of the process
- **This is the critical step:** with a UIA event handler registered, `UiaClientsAreListening()` returns `true`, causing Chromium to activate accessibility for all renderers

**Phase 3: MSAA + WM_GETOBJECT Probes (after 300ms wait)**

- `AccessibleObjectFromWindow(target, OBJID_CLIENT, IID_IAccessible)` — probes the main window
- `EnumChildWindows` with callback that:
  - Calls `AccessibleObjectFromWindow` on each child
  - Sends `WM_GETOBJECT(OBJID_CLIENT)` directly to each child
  - This specifically targets `Chrome_RenderWidgetHostHWND`, the renderer's HWND

**Phase 4: Wait + Retry (500ms)**

- Waits 500ms for Chromium to process the signals
- Repeats the `EnumChildWindows` probe a second time for reliability

The global `SPI_SETSCREENREADER` flag is also set once at application startup (in `main()`, before any snap occurs), so applications launched after DirectShell already see the flag.

**Implementation detail:** The `UiaFocusHandler` struct uses the `#[windows::core::implement(IUIAutomationFocusChangedEventHandler)]` macro to generate the COM vtable at compile time. The handler is never deregistered — this is intentional, as the COM leak keeps `UiaClientsAreListening()` permanently true.

---

## 8. Output File Generation

All output files are generated in `ds_profiles/` with filenames derived from the target application's window title. For a target window titled "Google Gemini – Opera", the files are:

| File | Path | Content |
|------|------|---------|
| Database | `ds_profiles/opera.db` | Full SQLite element tree + inject queue |
| Snapshot | `ds_profiles/opera.snap` | All interactive elements |
| Screen Reader View | `ds_profiles/opera.a11y` | Focus, input targets, content |
| Operable Snapshot | `ds_profiles/opera.a11y.snap` | Indexed operable elements |

### 8.1 .db — SQLite Element Database

**Schema:**

```sql
CREATE TABLE meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE elements (
    id            INTEGER PRIMARY KEY,
    parent_id     INTEGER,
    depth         INTEGER,
    role          TEXT NOT NULL,
    name          TEXT,
    value         TEXT,
    automation_id TEXT,
    enabled       INTEGER DEFAULT 1,
    offscreen     INTEGER DEFAULT 0,
    x             INTEGER,
    y             INTEGER,
    w             INTEGER,
    h             INTEGER
);

CREATE INDEX idx_role      ON elements(role);
CREATE INDEX idx_offscreen ON elements(offscreen);
CREATE INDEX idx_visible   ON elements(offscreen, role) WHERE offscreen=0;

CREATE TABLE inject (
    id     INTEGER PRIMARY KEY AUTOINCREMENT,
    action TEXT DEFAULT 'text',
    text   TEXT NOT NULL,
    target TEXT DEFAULT '',
    done   INTEGER DEFAULT 0
);
```

**Meta keys:**

| Key | Value |
|-----|-------|
| `window` | Window title string |
| `hwnd` | Handle in hex (e.g., `0x1A0B2C`) |
| `timestamp` | Unix timestamp in milliseconds |
| `x`, `y`, `w`, `h` | Window position and dimensions |

**Element IDs** are sequential integers assigned during the depth-first walk. `parent_id` references the parent element's ID (0 for root children).

### 8.2 .snap — Interactive Element Snapshot

`generate_snap(db_path)`

Lists all interactive, enabled, visible, named elements with their input tool classification.

**Source query:**
```sql
SELECT role, name, automation_id, x, y, w, h FROM elements
WHERE enabled=1 AND offscreen=0 AND name IS NOT NULL AND name != ''
ORDER BY y, x
```

**Input tool classification** (`input_tool`):

| Role | Tool |
|------|------|
| Edit, Document | `keyboard` |
| Button, Hyperlink, MenuItem, TabItem, ListItem, TreeItem, DataItem, SplitButton | `click` |
| CheckBox, RadioButton | `toggle` |
| ComboBox | `select` |
| Slider | `slide` |
| Spinner | `spin` |

**Output format:**
```
# opera.snap — Generated by DirectShell
# Window: Google Gemini – Opera

[keyboard] "Adressfeld" @ 168,41 (2049x29) id=addressEditor
[click] "Neuer Chat" @ 45,107 (2515x1285)
```

### 8.3 .a11y — Screen Reader View

`generate_a11y(db_path, target)`

Produces a structured text file with three sections:

**Section 1: Focus** (live UIA call)

Calls `uia.GetFocusedElement()` at generation time. Reports the currently focused element's name, role, position, and value.

```
## Focus
[keyboard] "Adressfeld" @ 168,41 (2049x29)
  value: "https://example.com"
```

**Section 2: Input Targets** (from database)

```sql
SELECT role, name, value, x, y, w, h FROM elements
WHERE enabled=1 AND offscreen=0
AND name IS NOT NULL AND name != ''
AND w > 10 AND h > 10
AND role IN ('Edit', 'Document', 'ComboBox')
ORDER BY y, x
```

Lists all text input fields and combo boxes. Includes current value preview (truncated to 100 characters).

```
## Input Targets
[keyboard] "Einen Prompt für Gemini eingeben" @ 999,1177 (1069x37)
  value: "previous input text"
```

**Section 3: Content** (from database)

```sql
SELECT name, value FROM elements
WHERE offscreen=0
AND name IS NOT NULL AND name != ''
AND w > 20 AND h > 10
AND role IN ('Text', 'Document', 'Hyperlink', 'Image', 'ListItem',
             'TreeItem', 'DataItem', 'Group')
ORDER BY y, x
```

Lists all visible text content, links, and labeled elements. If an element has both a name and a distinct value, both are shown.

```
## Content
Google Gemini
Neuer Chat (https://gemini.google.com/app)
Token-Revolution: Ein Screenshot kostet tausende Tokens.
```

### 8.4 is_active — Snap Status File

`write_active_status(db_path)`

A plain text file at `ds_profiles/is_active` that tells external agents whether DirectShell is currently snapped and to which application.

**When snapped:**
```
opera
ds_profiles/opera.a11y
ds_profiles/opera.snap
```

**When unsnapped:**
```
none
```

External agents check this file first to determine:
1. Whether DirectShell is attached to an application (line 1: app name or "none")
2. Where to find the screen reader view (line 2: `.a11y` path)
3. Where to find the interactive element snapshot (line 3: `.snap` path)

Updated on every tree dump cycle and on unsnap.

### 8.5 .a11y.snap — Operable Element Index

`generate_a11y_snap(db_path)`

Numbered index of all elements that can be operated (clicked, typed into, toggled, etc.).

**Source query:**
```sql
SELECT role, name, x, y, w, h FROM elements
WHERE enabled=1 AND offscreen=0
AND name IS NOT NULL AND name != ''
AND w > 10 AND h > 10
ORDER BY y, x
```

**Output format:**
```
# opera.a11y.snap — Operable Elements (DirectShell)
# Window: Google Gemini – Opera
# Use 'target' column in inject table to aim at an element by name

[1] [keyboard] "Adressfeld" @ 168,41 (2049x29)
[2] [click] "Neuer Chat" @ 45,107 (200x30)
[3] [keyboard] "Einen Prompt für Gemini eingeben" @ 999,1177 (1069x37)

# 3 operable elements in viewport
```

---

## 9. Action Queue (Input Pipeline)

### 9.1 Queue Schema

```sql
CREATE TABLE inject (
    id     INTEGER PRIMARY KEY AUTOINCREMENT,
    action TEXT DEFAULT 'text',
    text   TEXT NOT NULL,
    target TEXT DEFAULT '',
    done   INTEGER DEFAULT 0
);
```

| Column | Type | Description |
|--------|------|-------------|
| `id` | INTEGER | Auto-incrementing primary key, determines execution order (FIFO) |
| `action` | TEXT | Action type: `text`, `key`, `click`, `scroll` |
| `text` | TEXT | Payload (text content, key combo string, scroll direction) |
| `target` | TEXT | Target element name (for `text` and `click` actions) |
| `done` | INTEGER | 0 = pending, 1 = completed |

**Migration:** For databases created before the action column existed, two `ALTER TABLE` statements add the `target` and `action` columns with defaults. These are silently ignored if the columns already exist.

### 9.2 Action Types

| Action | `text` column | `target` column | Description |
|--------|---------------|-----------------|-------------|
| `text` | Content to set | Element name (optional) | Sets text via UIA ValuePattern (preferred) or SendInput fallback |
| `type` | Characters to type | (unused) | Raw keyboard input, character-by-character with 5ms delay |
| `key` | Key combo string | (unused) | Sends keyboard input (e.g., `enter`, `ctrl+a`) |
| `click` | (unused) | Element name | Clicks center of named element |
| `scroll` | Direction: `up`/`down`/`left`/`right` | (unused) | Sends mouse wheel event |

**`text` vs. `type` — when to use which:**

| | `text` | `type` |
|---|--------|--------|
| Mechanism | UIA ValuePattern `SetValue()` | `SendInput` per character (KEYEVENTF_UNICODE) |
| Speed | Instant (entire string at once) | ~200 chars/sec (5ms per character) |
| Tab/Enter | Not supported (literal text) | Supported (`\t` → VK_TAB, `\n` → VK_RETURN) |
| Works with | Elements that expose ValuePattern | Any focused input (including chat fields that reject SetValue) |
| Use case | Form fields, address bars, search boxes | Chat inputs (Discord, Slack), terminal emulators, games |

### 9.3 Text Injection

`inject_text(target: HWND, text: &str, target_name: &str) → bool`

**Element targeting:**

1. Build UIA condition: `IsKeyboardFocusable = true AND IsValuePatternAvailable = true`
2. If `target_name` is non-empty: add `Name = target_name` to the condition
3. Call `FindFirst(TreeScope_Descendants)` on the target window's root element

**Injection strategies (tried in order):**

1. **ValuePattern (preferred):**
   - Cast element pattern to `IUIAutomationValuePattern`
   - Read current value, append new text
   - Call `SetValue` with the combined string
   - This is the UIA-native way to set text field values

2. **SendInput fallback:**
   - Attach to target thread's input queue (`AttachThreadInput`)
   - Iterate over each character in the text
   - Call `inject_char` for each: sends `KEYEVENTF_UNICODE` with the character's UTF-16 code point
   - Detach from input queue

Before injection, the target element receives focus via `SetFocus()`.

### 9.3b Type Injection (Raw Keyboard)

Dispatched when `action = "type"`. Iterates over each character in the `text` column and sends it as a raw keyboard event.

**Special character handling:**

| Character | Action |
|-----------|--------|
| `\t` (tab) | `SendInput` with `VK_TAB` |
| `\n` or `\r` (newline) | `SendInput` with `VK_RETURN` |
| All others | `inject_char` — `KEYEVENTF_UNICODE` with the character's UTF-16 code point |

**Timing:** 5ms sleep between each character (`std::thread::sleep(Duration::from_millis(5))`). This produces a typing speed of ~200 characters per second — fast enough to be efficient, slow enough for applications to process each keystroke.

**No element targeting:** Unlike `text`, the `type` action does not search for a specific element. It sends keystrokes to whatever element currently has keyboard focus. The external agent is responsible for ensuring the correct element is focused (e.g., by using a `click` action first).

### 9.4 Key Injection

`send_key_combo(combo: &str)`

Parses a `+`-delimited key combo string and sends it via `SendInput`.

**Parsing:**

1. Split on `+`, trim whitespace
2. Map each part to a `VIRTUAL_KEY` via `key_to_vk()`
3. Classify as modifier (`Ctrl`, `Alt`, `Shift`, `Win`) or main key

**Execution:**

1. Press all modifiers down (in order)
2. Press and release the main key
3. Release all modifiers (in reverse order)

**Supported keys (150+):**

| Category | Keys |
|----------|------|
| Letters | `a`–`z` (VK 0x41–0x5A) |
| Numbers | `0`–`9` (VK 0x30–0x39) |
| Function | `f1`–`f12` |
| Modifiers | `ctrl`/`control`, `alt`/`menu`, `shift`, `win`/`lwin`, `rwin` |
| Navigation | `enter`/`return`, `tab`, `escape`/`esc`, `space`, `backspace`/`bs`, `delete`/`del`, `insert`/`ins`, `home`, `end`, `pageup`/`pgup`, `pagedown`/`pgdn` |
| Arrows | `up`, `down`, `left`, `right` |
| Locks | `capslock`/`caps`, `numlock`, `scrolllock` |
| System | `printscreen`/`prtsc`, `pause`/`break` |
| Punctuation | `;`, `=`, `,`, `-`, `.`, `/`, `` ` ``, `[`, `\`, `]`, `'` (with named aliases) |
| Numpad | `num0`–`num9`, `multiply`/`num*`, `add`/`num+`, `subtract`/`num-`, `decimal`/`num.`, `divide`/`num/` |
| Media | `volumeup`, `volumedown`, `volumemute`, `nexttrack`, `prevtrack`, `playpause`, `stop` |

**Extended key handling:**

Certain keys require the `KEYEVENTF_EXTENDEDKEY` flag in the `SendInput` call. These are identified by `is_extended_key()`:

Arrow keys, Insert, Delete, Home, End, Page Up, Page Down, Num Lock, Print Screen, Right Win, Divide (numpad).

### 9.5 Click Injection

`click_element(target_hwnd: HWND, element_name: &str) → bool`

1. Create `IUIAutomation` instance
2. Get root element from target HWND
3. Create property condition: `Name = element_name`
4. `FindFirst(TreeScope_Descendants)` — locate the element
5. Read `BoundingRectangle` → calculate center point (`cx`, `cy`)
6. Convert to absolute screen coordinates (0–65535 range):
   ```
   abs_x = cx * 65535 / screen_width
   abs_y = cy * 65535 / screen_height
   ```
7. Send two `INPUT` events via `SendInput`:
   - `MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_MOVE | MOUSEEVENTF_LEFTDOWN`
   - `MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_MOVE | MOUSEEVENTF_LEFTUP`

### 9.6 Scroll Injection

`scroll_window(target_hwnd: HWND, direction: &str)`

1. Map direction to delta values:
   | Direction | dx | dy |
   |-----------|----|----|
   | `up` | 0 | 120 |
   | `down` | 0 | -120 |
   | `left` | -120 | 0 |
   | `right` | 120 | 0 |

   One unit = `WHEEL_DELTA` (120) = one notch of scroll.

2. Get target window center, convert to absolute coordinates
3. Send `MOUSEEVENTF_WHEEL` (vertical) or `MOUSEEVENTF_HWHEEL` (horizontal) via `SendInput`
4. The `mouseData` field carries the scroll delta (cast to `u32`)

### 9.7 Dispatch Loop

`process_injections()` — Called at ~33 Hz via `INJECT_TIMER` (30ms interval, independent of tree dump timer).

**Auto-focus:** Before executing any action, the dispatch loop checks if the target application has foreground focus. If not, it brings the target to the foreground:

1. Check `GetForegroundWindow()` against the snap target
2. If different: send a momentary Alt key press (`VK_MENU` down + up) — this is a standard Windows trick to satisfy the `SetForegroundWindow` permission check
3. Call `SetForegroundWindow(target)`
4. Wait 50ms for the focus change to take effect

**Execution model:**

1. Open database (read-only path check)
2. Query: `SELECT id, COALESCE(action,'text'), text, COALESCE(target,'') FROM inject WHERE done=0 ORDER BY id LIMIT 1`
3. If no pending action: return
4. **Mark as done before execution** (`UPDATE inject SET done=1 WHERE id=?`)
   - Prevents double-fire if processing takes longer than the timer interval
5. Dispatch based on `action`:
   - `"text"` → `inject_text(target, &text, &target_name)`
   - `"type"` → character-by-character keyboard injection (5ms per char)
   - `"key"` → `send_key_combo(&text)` (no target window needed)
   - `"click"` → `click_element(target, &target_name)`
   - `"scroll"` → `scroll_window(target, &text)`
6. If execution **fails**: reset `done=0` for retry on next tick
7. Actions with no target window fail immediately (except `key`, which operates globally)

**Queue semantics:**
- FIFO ordering by `id`
- One action per tick (~30ms)
- Failed actions are retried indefinitely
- Completed actions remain in the table with `done=1`

---

## 10. Keyboard Hook

DirectShell installs a global low-level keyboard hook (`WH_KEYBOARD_LL`) via `SetWindowsHookExW`. The hook callback `kb_hook_proc` runs on the main thread as part of the message pump.

**Hook activation conditions** (all must be true):

1. DirectShell is snapped (`IS_SNAPPED = true`)
2. The keystroke is not injected by DirectShell itself (`LLKHF_INJECTED` flag not set)
3. The target application has foreground focus (`GetForegroundWindow` matches target or its ancestor)
4. No modifier keys are held (`Ctrl` and `Alt` are not pressed — preserves shortcuts like Ctrl+C)

**Processing:**

1. Build keyboard state array (256 bytes) reflecting Shift and CapsLock
2. Call `ToUnicode` to convert the virtual key + scan code to a Unicode character
3. If conversion fails (dead key, non-printable): pass through to next hook
4. If conversion succeeds (printable character):
   - On `WM_KEYDOWN`: transform the character via `transform_char()` and inject it
   - Block both `WM_KEYDOWN` and `WM_KEYUP` (return `LRESULT(1)`)

**Current transform function:** `transform_char` is an identity function (returns the character unchanged). The initial proof-of-concept uppercase transform has been removed. The function serves as the interception point for arbitrary character transformation middleware (e.g., PII sanitization, auto-translation, input filtering).

---

## 11. Timer Architecture

Four timers drive DirectShell's runtime behavior:

```
                    ┌─────────────────────┐
                    │   WM_TIMER          │
                    │   (Window Proc)     │
                    └─────────┬───────────┘
                              │
          ┌───────────────┬───┴───┬───────────────┐
          ▼               ▼       ▼               ▼
 ┌────────────┐  ┌────────────┐  ┌──────────┐  ┌──────────────┐
 │ SYNC_TIMER │  │ ANIM_TIMER │  │TREE_TIMER│  │ INJECT_TIMER │
 │   ID: 1    │  │   ID: 2    │  │  ID: 3   │  │    ID: 4     │
 │   16 ms    │  │   33 ms    │  │  500 ms  │  │    30 ms     │
 │  ~60 Hz    │  │  ~30 Hz    │  │   2 Hz   │  │   ~33 Hz     │
 └─────┬──────┘  └─────┬──────┘  └────┬─────┘  └──────┬───────┘
       │               │              │                │
       ▼               ▼              ▼                ▼
  do_sync()      InvalidateRect  dump_tree()    process_injections()
 (position)       (repaint)     (a11y tree)     (action queue)
```

**Timer lifecycle:**

| State | SYNC_TIMER | ANIM_TIMER | TREE_TIMER | INJECT_TIMER |
|-------|:----------:|:----------:|:----------:|:------------:|
| Unsnapped (idle) | Off | **On** | Off | Off |
| Snapped | **On** | Off | **On** | **On** |

The animation timer and the snap timers (sync, tree, inject) are mutually exclusive. Snap activates sync+tree+inject and kills animation. Unsnap does the reverse.

**Why INJECT_TIMER is separate from TREE_TIMER:** The tree dump runs at 2 Hz (heavy operation: full UIA tree walk + SQLite rebuild). Action dispatch needs to run much faster for fluid typing — at 33 Hz, a 200-character message takes ~1 second to type rather than ~100 seconds at 2 Hz.

---

## 12. Persistence Model

**Persistent across sessions:**
- `ds_profiles/{app}.db` — Element databases survive application restarts
- `ds_profiles/{app}.snap` — Regenerated on each dump, but file persists
- `ds_profiles/{app}.a11y` — Regenerated on each dump
- `ds_profiles/{app}.a11y.snap` — Regenerated on each dump
- `inject` table rows — Not cleared between dumps; accumulate until manually cleaned

**Updated on each dump cycle:**
- `ds_profiles/is_active` — Current snap state (app name + file paths, or "none")

**Cleared on restart:**
- `directshell.log` — Truncated at application start
- `ds_profiles/is_active` — Written to "none" on unsnap, updated on snap

**Not cleared on unsnap:**
- Database files remain in `ds_profiles/`
- On re-snap to the same application, the same database is reused

**SQLite concurrent access:**
- WAL mode enables concurrent reads from external processes while DirectShell writes
- The `inject` table can be written to by external processes while DirectShell reads it
- External readers should use `PRAGMA journal_mode=WAL` when opening the database

---

## 13. Data Flow Diagrams

### Perception Pipeline (Reading)

```
Target Application
      │
      │ UIA RawViewWalker (every 500ms)
      ▼
┌──────────────┐     ┌──────────┐     ┌──────────────────┐
│  elements    │────▶│ .snap    │     │ External Process  │
│  (SQLite)    │     │ (file)   │────▶│                   │
│              │     └──────────┘     │ 1. Read is_active │
│              │     ┌──────────┐     │ 2. Read .a11y     │
│              │────▶│ .a11y    │────▶│ 3. Read .a11y.snap│
│              │     │ (file)   │     │ 4. Query .db      │
│              │     └──────────┘     │                   │
│              │     ┌──────────┐     │                   │
│              │────▶│.a11y.snap│────▶│                   │
│              │     │ (file)   │     │                   │
│              │     └──────────┘     │                   │
│              │     ┌──────────┐     │                   │
│              │────▶│is_active │────▶│                   │
│              │     │ (file)   │     └──────────────────┘
└──────────────┘     └──────────┘
```

### Action Pipeline (Writing)

```
┌──────────────────┐
│ External Process  │
│                   │
│ INSERT INTO       │
│ inject(action,    │
│   text, target)   │
│ VALUES(...)       │
└────────┬─────────┘
         │ SQLite write
         ▼
┌──────────────┐     process_injections()
│  inject      │     (every 500ms)
│  (SQLite)    │────────────────────────┐
└──────────────┘                        │
                                        ▼
                              ┌──────────────────┐
                              │  Action Dispatch  │
                              │                   │
                              │  text  → ValuePattern / SendInput
                              │  key   → SendInput (VK codes)
                              │  click → UIA FindFirst + SendInput (mouse)
                              │  scroll→ SendInput (MOUSEEVENTF_WHEEL)
                              └─────────┬────────┘
                                        │
                                        ▼
                              Target Application
```

### Complete System Overview

```
┌──────────────────────────────────────────────────────────┐
│                    DirectShell.exe                        │
│                                                          │
│  ┌────────────────────────────────────────────────────┐  │
│  │ Main Thread                                        │  │
│  │                                                    │  │
│  │  Message Loop ──▶ Window Proc                      │  │
│  │                      │                             │  │
│  │              ┌───────┼───────────┐                 │  │
│  │              ▼       ▼       ▼           ▼         │  │
│  │          do_sync  paint  dump_tree  process_inj    │  │
│  │          (16ms)   (33ms)  (500ms)    (30ms)        │  │
│  │                                                    │  │
│  │  Keyboard Hook (WH_KEYBOARD_LL)                    │  │
│  │    ▶ Intercept keystrokes                          │  │
│  │    ▶ Transform characters                          │  │
│  │    ▶ Re-inject via SendInput                       │  │
│  └────────────────────────────────────────────────────┘  │
│                                                          │
│  ┌────────────────────────────────────────────────────┐  │
│  │ Tree Thread (spawned per dump)                     │  │
│  │                                                    │  │
│  │  CoInitializeEx ──▶ IUIAutomation                  │  │
│  │    ▶ RawViewWalker depth-first traversal           │  │
│  │    ▶ Stream to SQLite (200-row batches)            │  │
│  │    ▶ Generate .snap, .a11y, .a11y.snap             │  │
│  │  CoUninitialize                                    │  │
│  └────────────────────────────────────────────────────┘  │
│                                                          │
│  ┌──────────────────┐                                    │
│  │  ds_profiles/    │  ◀──── Persistent storage          │
│  │    opera.db      │                                    │
│  │    opera.snap    │                                    │
│  │    opera.a11y    │                                    │
│  │    opera.a11y.snap                                    │
│  └──────────────────┘                                    │
└──────────────────────────────────────────────────────────┘
                    ▲                        │
                    │ inject table           │ .a11y / .snap files
                    │ (SQLite INSERT)        │ (file read)
                    │                        ▼
            ┌──────────────────────────────────────┐
            │         External Process             │
            │  (e.g., Claude Code CLI Agent)        │
            │                                      │
            │  1. Read .a11y     → understand      │
            │  2. Read .a11y.snap → identify        │
            │  3. INSERT inject  → act              │
            │  4. Wait 500ms     → re-read          │
            └──────────────────────────────────────┘
```

---

*DirectShell v0.2.0 — Architecture Reference*
*Generated: 2026-02-17*
