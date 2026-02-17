# DirectShell — Architecture & Rationale

**Version:** 0.2.0
**Author:** Martin (IamLumae)
**Date:** 2026-02-17
**Status:** Active Development — Perception + Action pipeline complete, MCP integration pending

---

## 1. What DirectShell Is

DirectShell is a Windows subsystem primitive that interposes between the operating system's input pipeline and any running GUI application. It provides three capabilities that have never been combined in a single tool:

1. **Input Interception** — captures keyboard and mouse events before they reach the target application
2. **Input Modification** — transforms, injects, or suppresses events before delivery
3. **Semantic Feedback** — reads the target application's UI state as structured text via the Windows Accessibility Tree (UI Automation)

DirectShell is not an application. It is infrastructure. It occupies the same architectural layer as PowerShell, the Windows Shell, or the browser — a universal interface between humans (or machines) and software.

### The One-Sentence Definition

> DirectShell turns every GUI on the planet into a text-based API that any LLM can natively read and control.

---

## 2. Why This Exists — The Problem

### 2.1 The Screenshot Paradigm (State of the Art, 2026)

Every major AI lab attempting GUI automation uses the same architecture:

```
Screenshot (pixels) → Vision Model → Coordinate Guess → Simulated Click
```

- **Anthropic Computer Use** — screenshots + Claude Vision
- **OpenAI Operator** — screenshots + GPT-4V
- **Google Mariner** — screenshots + Gemini Vision

This approach is fundamentally flawed:

| Problem | Consequence |
|---------|-------------|
| LLMs are text-native, not image-native | Force-fitting a modality mismatch |
| Screenshots are 2M+ datapoints per frame | Expensive, slow inference |
| Pixel coordinates have no semantic meaning | "Click at (834, 217)" = fragile guessing |
| UI themes, scaling, language change pixels | Breaks on every visual change |
| No structured understanding of UI state | Cannot reason about disabled buttons, hidden fields, dropdown values |

**The entire industry is performing OCR on a data source that already exists as structured text.**

### 2.2 The Accessibility Tree (The Existing Solution Nobody Uses)

Windows has shipped a complete semantic representation of every GUI element since Windows XP (2001): **UI Automation (UIA)**.

Built for screen readers (JAWS, NVDA), UIA exposes every UI element as a tree of named, typed, stateful nodes:

```
Window: "Invoice - Datev Pro"
├── TitleBar
│   ├── Button: "Minimize"
│   ├── Button: "Maximize"
│   └── Button: "Close"
├── MenuBar
│   ├── MenuItem: "File"
│   ├── MenuItem: "Edit"
│   └── MenuItem: "Help"
├── Pane: "Invoice Details"
│   ├── Edit: "Customer Number"  →  Value: "KD-4711"
│   ├── Edit: "Amount"           →  Value: "1,299.00"
│   ├── ComboBox: "Tax Rate"     →  Value: "19%"
│   └── Button: "Book"           →  IsEnabled: true
└── StatusBar: "Ready"
```

Each node provides: `Name`, `ControlType`, `Value`, `AutomationId`, `BoundingRectangle`, `IsEnabled`, `IsOffscreen`, `Parent/Child relationships`.

**This is pure text. This is what LLMs are built to process.**

No vision model. No coordinate guessing. No pixel interpretation. The semantic layer already exists. DirectShell is the first tool to weaponize it.

---

## 3. Architecture

### 3.1 Layer Model

```
┌─────────────────────────────────────────────────┐
│                  LLM / AI Agent                  │  ← Understands intent
│              (Claude, GPT, Gemini, local)        │
├─────────────────────────────────────────────────┤
│                   MCP / Protocol                 │  ← Standardized interface
│            (tool calls, structured I/O)          │
├─────────────────────────────────────────────────┤
│                   DirectShell                    │  ← THIS LAYER
│  ┌─────────────┬──────────────┬───────────────┐ │
│  │  Overlay    │   Input      │   Feedback    │ │
│  │  Manager    │   Pipeline   │   Reader      │ │
│  │             │              │   (UIA)       │ │
│  └─────────────┴──────────────┴───────────────┘ │
├─────────────────────────────────────────────────┤
│              Windows OS (Win32 API)              │  ← Kernel + subsystems
├─────────────────────────────────────────────────┤
│              Target Application                  │  ← Any program. Any age.
└─────────────────────────────────────────────────┘
```

### 3.2 Three Subsystems

#### A. Overlay Manager
Manages the transparent window that binds to the target application.

| Component | Win32 API | Purpose |
|-----------|-----------|---------|
| Transparent window | `WS_EX_LAYERED` + `WS_EX_TRANSPARENT` | Invisible overlay, click-through center |
| Color keying | `SetLayeredWindowAttributes(LWA_COLORKEY)` | Magenta = fully transparent + click-passthrough |
| Alpha blending | `SetLayeredWindowAttributes(LWA_ALPHA)` | Semi-transparent border frame |
| Window detection | `WindowFromPoint()` + `GetAncestor(GA_ROOT)` | Find target window under cursor |
| Snap binding | Position sync via `SetWindowPos()` at 60fps | Move/resize/minimize/close in lockstep |
| Shell filtering | `GetClassNameW()` against known shell classes | Prevent snapping to desktop/taskbar |

**Snap Lifecycle:**
```
Drag DirectShell over target → release
  → Hide self → WindowFromPoint → Show self
  → GetAncestor(GA_ROOT) → filter shells
  → Calculate overlap (≥20% threshold)
  → Match size/position via SetWindowPos
  → Start 60fps sync timer
  → Probe Accessibility Tree for titlebar height + button positions
  → Adapt frame dimensions to match target's chrome
```

#### B. Input Pipeline
Intercepts, modifies, and delivers input events.

| Stage | Mechanism | Capability |
|-------|-----------|------------|
| Capture | `SetWindowsHookEx(WH_KEYBOARD_LL)` | See every keystroke system-wide |
| Capture | `SetWindowsHookEx(WH_MOUSE_LL)` | See every mouse event system-wide |
| Decision | Middleware chain | Pass through, modify, suppress, or inject |
| Delivery | `SendInput()` / `PostMessage()` | Target app receives normal OS events |

**The target application cannot distinguish DirectShell-mediated input from physical hardware input.** The OS itself vouches for the events as legitimate.

#### C. Feedback Reader (UI Automation → SQLite)
Reads the target application's state into a live queryable database.

| Component | API | Purpose |
|-----------|-----|---------|
| Tree walker | `IUIAutomation::ControlViewWalker()` | Recursive traversal of all UI elements |
| Element properties | `CurrentName`, `CurrentValue`, `CurrentBoundingRectangle`, `CurrentIsOffscreen` | Full element state |
| ValuePattern | `IUIAutomationValuePattern::CurrentValue()` | Read text field contents |
| SQLite (WAL) | `rusqlite` (embedded) | Live database, concurrent read/write |
| Background thread | `std::thread::spawn` | Non-blocking, re-entry guarded |
| ConnectionTimeout | `IUIAutomation6::SetConnectionTimeout()` | 2s max per element, prevents hangs |

**Database schema (`directshell.db`):**
```sql
-- Every UI element = one row
CREATE TABLE elements (
    id            INTEGER PRIMARY KEY,
    parent_id     INTEGER,          -- tree structure
    depth         INTEGER,          -- nesting level
    role          TEXT NOT NULL,     -- Button, Text, Edit, Hyperlink, ...
    name          TEXT,              -- human-readable label
    value         TEXT,              -- field content / URL
    automation_id TEXT,              -- developer-assigned ID
    enabled       INTEGER DEFAULT 1,
    offscreen     INTEGER DEFAULT 0,
    x INTEGER, y INTEGER, w INTEGER, h INTEGER
);

-- Window metadata
CREATE TABLE meta (
    key   TEXT PRIMARY KEY,          -- window, hwnd, timestamp, x, y, w, h
    value TEXT
);
```

**LLM queries the app via SQL:**
```sql
-- What buttons can the user click?
SELECT name, x, y FROM elements WHERE role='Button' AND enabled=1

-- What's in the text fields?
SELECT name, value FROM elements WHERE role='Edit'

-- Find a specific message
SELECT name FROM elements WHERE name LIKE '%invoice%'

-- Full chat history, chronological
SELECT name FROM elements WHERE role='Text' AND length(name)>10 ORDER BY y

-- App structure overview
SELECT role, COUNT(*) FROM elements GROUP BY role ORDER BY COUNT(*) DESC
```

**Why SQLite, not JSON:**
- 11,000+ elements = 1.5 MB JSON that must be fully parsed every time
- SQLite: microsecond queries, indexed, filterable, no parsing
- WAL mode: DirectShell writes at 2 Hz, LLM reads anytime, zero contention
- Universal: every language, every tool, every platform reads SQLite

**Input format (from LLM) — live via SQLite inject table:**
```sql
-- Set text in a specific field (UIA ValuePattern)
INSERT INTO inject (action, text, target) VALUES ('text', '2,599.00', 'Amount');

-- Type character-by-character (raw keyboard, for chat inputs)
INSERT INTO inject (action, text) VALUES ('type', 'Hello World');

-- Press a key combination
INSERT INTO inject (action, text) VALUES ('key', 'ctrl+a');

-- Click a named element
INSERT INTO inject (action, target) VALUES ('click', 'Book');

-- Scroll
INSERT INTO inject (action, text) VALUES ('scroll', 'down');
```

DirectShell translates these text commands into real Win32 input events at ~33 Hz. Both directions are text. Both directions are LLM-native.

### 3.3 The Full Chain

```
Human: "Book all invoices from this folder into Datev"
  ↓
LLM:  Understands intent, plans steps
  ↓
Read:  ds_profiles/is_active → "datev"
       ds_profiles/datev.a11y → screen state as text
       ds_profiles/datev.a11y.snap → operable elements
  ↓
Write: INSERT INTO inject (action, text, target)
       VALUES ('text', 'KD-4711', 'Customer Number')
  ↓
DirectShell (33 Hz dispatch):
  ├── Feedback Reader: reads Datev's UI state every 500ms
  ├── Input Pipeline: sets field value, clicks "Book"
  └── Overlay Manager: tracks Datev's window, stays bound
  ↓
Win32 API: delivers events as if typed by a human
  ↓
Datev: processes the invoice. Does not know a human isn't sitting there.
```

---

## 4. Why This Is a Primitive

### 4.1 Definition

A **primitive** is a foundational building block that:
- Cannot be decomposed into simpler components that achieve the same function
- Enables an entire category of higher-level tools and workflows
- Has no expiration date — it remains useful as long as the platform exists

### 4.2 Comparison to Existing Primitives

| Primitive | Domain | What It Universalizes |
|-----------|--------|----------------------|
| **PowerShell** | Backend automation | CLI access to OS services, registry, processes, files |
| **Browser** | Information access | HTTP/HTML rendering for any web resource |
| **API** | System integration | Structured data exchange between services |
| **SQL** | Data access | Query language for any relational database |
| **DirectShell** | **Frontend automation** | Input/output control for any GUI application |

PowerShell automates the backend. **DirectShell automates the frontend.**

### 4.3 What Makes It Primitive-Level

1. **Universality** — works on ANY Windows application, regardless of technology stack, age, or language. Win32, WPF, Electron, Java Swing, Qt, legacy MFC — all expose UIA.

2. **No cooperation required** — the target application does not need to be modified, updated, or aware. No plugins, no extensions, no API keys.

3. **OS-level authority** — operates at the same privilege level as the user. Uses only documented Win32 APIs. Input events are indistinguishable from hardware.

4. **Composability** — any tool can be built on top of DirectShell. It is a building block, not a finished product.

5. **Permanence** — Win32 input events and UI Automation have been stable since Windows XP. They will exist as long as Windows exists.

---

## 5. Use Cases

### 5.1 Immediate (MVP)

| Use Case | Description |
|----------|-------------|
| **Universal AI Agent Connector** | Any LLM controls any GUI via text. No screenshots, no vision model. |
| **PII Sanitization** | Intercept text input to any app. Sanitize personal data before it reaches LLM chat interfaces. Works on desktop apps, not just browsers. |
| **Input Logging / Audit** | Record what was typed into which application, when. Accessibility and compliance. |

### 5.2 Near-Term

| Use Case | Description |
|----------|-------------|
| **RPA Replacement** | Automate repetitive GUI workflows without UiPath/Automation Anywhere. No scripting required — describe the task in natural language. |
| **Legacy Software Integration** | 20-year-old AS/400 terminal emulators, SAP GUI, Datev — all become LLM-controllable. |
| **Cross-App Orchestration** | Copy data from App A, transform, paste into App B. All via text commands. |
| **Voice Control Injection** | Add voice control to any application that doesn't support it. |
| **Forced Copy-Paste** | Override applications that block Ctrl+C/Ctrl+V in certain fields. |

### 5.3 Platform

| Use Case | Description |
|----------|-------------|
| **Plugin Ecosystem** | Third-party middleware modules (translation, formatting, macro recording). |
| **Per-App Profiles** | Different input rules for different applications. |
| **Multi-Instance** | Multiple DirectShell instances controlling multiple apps simultaneously. |
| **Cross-Platform** | macOS (NSAccessibility), Linux (AT-SPI) — same architecture, different OS APIs. |

---

## 6. The Paradigm Shift

### 6.1 Screenshot Approach vs. DirectShell Approach

| Dimension | Screenshot (Anthropic/OpenAI/Google) | DirectShell |
|-----------|--------------------------------------|-------------|
| **Input to LLM** | 2M+ pixel image | SQL query on local DB |
| **LLM modality** | Vision (non-native) | Text (native) |
| **Semantic understanding** | Inferred from pixels | Explicit from UIA tree |
| **Coordinate precision** | Estimated (±pixels) | Exact (BoundingRectangle) |
| **Cost per interaction** | High (vision model inference) | Low (text only) |
| **Latency** | Seconds (screenshot + inference) | Milliseconds (tree read), 30ms (action dispatch) |
| **Robustness** | Breaks on theme/scale/language change | Immune — reads semantic names, not pixels |
| **Disabled state detection** | Cannot reliably detect | `IsEnabled` property, explicit |
| **Hidden element awareness** | Cannot see off-screen elements | `IsOffscreen` property, full tree |
| **Works offline** | Requires cloud vision model | Local LLM reads local text |

### 6.2 The Fundamental Error

The screenshot approach performs computer vision on a UI that already describes itself as text.

This is equivalent to:
- Photographing a JSON response and running OCR, instead of parsing the JSON
- Taking a screenshot of a spreadsheet and using a vision model to read cell values, instead of calling the spreadsheet API
- Recording audio of someone reading a book aloud, then using speech-to-text, instead of reading the text file

The Accessibility Tree has existed since 2001. It was built for blind users. DirectShell repurposes it for AI agents. The data was always there. Nobody looked.

---

## 7. Proof: The Primitive Works (2026-02-16)

On February 16, 2026, DirectShell was snapped onto **Claude Desktop** (Anthropic's Electron-based chat application, v1.1.3189). This is the application built by the company that invented Computer Use — the screenshot-based approach.

### 7.1 What Happened

DirectShell read the entire application into a SQLite database:

| Metric | Value |
|--------|-------|
| Total UI elements captured | **11,454** |
| Largest container (chat messages) | **5,734 children** |
| Database update rate | 2 Hz (every 500ms) |
| Tree walk time | ~160-220ms |
| Database size | ~1.5 MB |
| Query time | Microseconds |

The AI (Claude Opus 4.6, running in a separate CLI terminal) queried the database and could:

```sql
-- Find a specific message from the entire chat history
SELECT name, y FROM elements WHERE name LIKE '%lars ist happy%nda%'
→ "ich denke ja. also lars ist happy mit der nda"

-- Read the last message and its response
SELECT name FROM elements WHERE role='Text' AND length(name)>10 ORDER BY y DESC LIMIT 3

-- List all navigation elements
SELECT name, value FROM elements WHERE role='Hyperlink' AND offscreen=0

-- Identify the logged-in user
SELECT name FROM elements WHERE name LIKE '%Martin%'
→ "Martin Gehrken", "Max Plan"
```

### 7.2 What This Proves

**DirectShell is not the application. DirectShell is the primitive.**

The first test revealed that Claude Desktop (Chromium/Electron) reports chat messages with virtual scroll coordinates (y = -200,000). The initial reaction was: "the Accessibility Tree doesn't work for Electron apps."

This was wrong. The tree contained everything. The problem was a self-imposed `MAX_CHILDREN: 100` limit in our tree walker that cut off the chat container at message 100. The newest messages were child 200, 300, 500+ — and we never reached them.

Limit removed → 11,454 elements → every message, every button, every link, fully searchable.

**The primitive worked from the start. What was missing was the interpretation layer — knowing HOW to read this specific app's data.** This is the same relationship as:

| Primitive | Needs |
|-----------|-------|
| PowerShell | Cmdlets (per-service knowledge) |
| SQL | Schema documentation (per-database knowledge) |
| Browser | URLs and page structure (per-site knowledge) |
| **DirectShell** | **App profiles (per-application knowledge)** |

A primitive is universal but not omniscient. It provides the mechanism. The knowledge of what to do with it comes from a higher layer. That DirectShell's first failure was an interpretation error — not a capability limitation — is the strongest possible proof that it is, in fact, a primitive.

### 7.3 The Irony

Anthropic built Computer Use (screenshot-based GUI automation). Anthropic also built Claude Desktop (the test target). DirectShell — the text-based alternative — read Anthropic's own application as 11,454 structured text elements. No screenshot. No vision model. One SQL query.

The company that bet on pixels built an app that describes itself perfectly in text.

---

## 8. Legal Position

DirectShell does not:
- Modify application binaries (no reverse engineering)
- Call private or undocumented APIs
- Bypass access controls or authentication
- Intercept network traffic
- Inject code into other processes

DirectShell does:
- Use documented Win32 APIs (`SetWindowsHookEx`, `SendInput`, `IUIAutomation`)
- Simulate human input at the OS level
- Read publicly exposed accessibility information (designed to be read by third-party tools)

**The user is operating their software. How they press their keys is their own business.**

---

## 9. Technical Stack

| Component | Technology | Rationale |
|-----------|-----------|-----------|
| Language | Rust | Zero-cost abstractions, no runtime, single binary, safe Win32 FFI |
| Win32 bindings | `windows` crate v0.58 | Official Microsoft Rust bindings |
| UI Automation | COM via `IUIAutomation` | Windows' built-in accessibility framework |
| Window management | Win32 `CreateWindowExW`, `SetWindowPos` | Direct OS-level control |
| Input hooks | `SetWindowsHookEx` (WH_KEYBOARD_LL, WH_MOUSE_LL) | System-wide input interception |
| Rendering | GDI double-buffered | Lightweight, no GPU dependency, flicker-free |
| Database | `rusqlite` (SQLite, bundled) | Embedded, zero-config, WAL-mode, universal |
| Binary size | ~700 KB | SQLite adds ~500 KB, still tiny |

### 9.1 Current Implementation (v0.2.0)

**Phase 1 — GUI Layer (done):**
- [x] Transparent layered window with color-keyed click-through
- [x] Snap detection (overlap threshold, shell window filtering)
- [x] Bidirectional position sync at 60fps
- [x] Synchronized minimize/close
- [x] Dynamic titlebar height from Accessibility Tree
- [x] Caption button detection via UIA (smart unsnap button positioning)
- [x] Owner-window z-order (snapped = same layer as app)
- [x] Double-buffered rendering (flicker-free)
- [x] Gradient light animation (cos² falloff)
- [x] Desktop/taskbar snap prevention

**Phase 2 — Feedback Engine (done):**
- [x] Full UIA tree serialization into SQLite (unlimited depth, unlimited children)
- [x] Live 2 Hz updates in background thread
- [x] WAL-mode SQLite for concurrent read/write
- [x] Re-entry guard (skip if previous dump still running)
- [x] UIA ConnectionTimeout (2s, prevents COM hangs)
- [x] File logging with timestamps and performance metrics
- [x] Tested on Claude Desktop (Electron): 11,454 elements, full chat history queryable

**Phase 3 — Input Pipeline (done):**
- [x] Action queue via SQLite `inject` table (FIFO, 5 action types)
- [x] Text injection via UIA ValuePattern (`text` action)
- [x] Raw keyboard typing with per-character delay (`type` action)
- [x] Key combo injection with 150+ supported keys (`key` action)
- [x] Element targeting by name — click any named UI element (`click` action)
- [x] Scroll injection in 4 directions (`scroll` action)
- [x] Auto-focus: brings target to foreground before action execution
- [x] Keyboard hook with identity transform (middleware insertion point ready)
- [x] `is_active` status file for external agent coordination
- [x] Dedicated 33 Hz inject timer for fluid typing

Next:
- [ ] MCP server (expose DirectShell as tool for LLM agents)
- [ ] App profiles (per-app interpretation guides)
- [ ] Character transformation middleware (PII sanitization, auto-translate)

---

## 10. What DirectShell Is Not

- **Not a screen recorder.** It reads structured data, not pixels.
- **Not a macro tool.** It understands UI semantics, not just coordinates.
- **Not an RPA platform.** It is the primitive that RPA platforms should be built on.
- **Not a hack.** It uses only documented, public Windows APIs.
- **Not a product.** It is a building block. Others will build products on top of it.

---

## 11. Summary

DirectShell is the missing layer between AI and the 99% of software that has no API.

It does what PowerShell did for the backend — but for the frontend. It turns every graphical application into a text-based interface that LLMs can natively read and control.

The Accessibility Tree has been shipping with every copy of Windows for 25 years. It was designed for blind users. DirectShell is the first tool to recognize that what works for screen readers works even better for language models — because both operate in text.

Every other approach in 2026 sends images to text models.
DirectShell sends text to text models.

That is the entire insight. And it changes everything.
