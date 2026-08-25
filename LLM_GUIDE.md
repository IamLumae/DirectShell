# How to Use DirectShell — A Guide for LLMs (and Humans Teaching LLMs)

**You are about to gain native control over every GUI application on the planet.**

This guide is written for you — the LLM reading this. Whether you're Claude, GPT, Gemini, Llama, or any other model: this document tells you exactly what DirectShell is, how it works, and how to use it.

If you're a human reading this: give this file to your AI. It will understand.

---

## What Is DirectShell?

DirectShell is a lightweight Windows binary (~1.2 MB) that **turns any GUI application into a text-based API you can read and control**.

It works by reading the **Windows Accessibility Tree** — the same structured data that screen readers like JAWS and NVDA use. Every button, text field, menu item, and label in any application has a name, a role, a value, and a position. DirectShell reads all of this and stores it in a **SQLite database** that you can query.

**You do not need screenshots. You do not need a vision model. You read text. You write SQL. That's it.**

---

## How It Works (The 60-Second Version)

1. The human runs `DirectShell.exe` — it starts as a **background daemon** tracking all open windows
2. You call `ds_apps()` to see every application on the system, then `ds_focus("discord")` to snap to one — **no human interaction needed**
3. DirectShell continuously reads the app's entire UI into a SQLite database (refreshed every 500ms)
4. You interact through **27 MCP tools** (or through the database files directly)
5. The target application cannot distinguish your actions from human input
6. **Dual-mode:** Browsers use CDP (Chrome DevTools Protocol) for pixel-perfect DOM access. Native apps use UIA (Windows UI Automation). Routing is automatic.

---

## Your Tools

Every tool has a `prev_ok` parameter — answer `"yes"`, `"no"`, or `"unknown"` to indicate whether your *last* MCP call succeeded. This enables deterministic action logging and learning loops.

### App Switching (Daemon Mode)

| Tool | What It Does | When to Use |
|------|-------------|-------------|
| `ds_guide` | Full quick-start guide with workflow | First-time setup, when lost |
| `ds_apps` | List all open desktop applications | See what's available to control (UIA mode) |
| `ds_focus` | Switch to a different application by name | Multi-app workflows — snap without human help |
| `ds_tabs` | List all open browser tabs | See available tabs (CDP/browser mode) |
| `ds_tab` | Switch to a browser tab by number or name | Switch between browser tabs |

### Perception (Reading the Screen)

| Tool | What It Does | Token Cost | When to Use |
|------|-------------|:----------:|-------------|
| `ds_update_view` | **Your primary tool.** Returns visible text + numbered tool list. Works in both CDP and UIA mode. | ~100–300 | **First call. Every time.** After every action. This IS your eyes. |
| `ds_act` | Execute an action by tool number from `ds_update_view` output | ~20 | After `ds_update_view` gives you a numbered tool list — just pick a number |
| `ds_status` | Shows which native app is snapped | ~20 | Debugging connection issues (UIA mode) |
| `ds_state` | Raw numbered list of operable elements (UIA) | ~200–500 | Fallback when `ds_update_view` is unavailable |
| `ds_screen` | Viewport-visible text only — no tools list | ~1,000–3,000 | When you need to **read content** (chat messages, documents, articles) |
| `ds_print` | **Full page** text — not just viewport (CDP only) | ~2,000–10,000 | Read an entire article, docs page, or long content |
| `ds_elements` | Interactive elements with automation IDs (UIA) | ~200–500 | Debugging element names and positions |
| `ds_find` | Search elements by name pattern (SQL LIKE, UIA) | ~50–200 | When you're looking for a **specific element** by name |
| `ds_query` | Run any SQL SELECT against the element database (UIA) | ~50–200 | **Most powerful tool.** Ask any question about the UI. |
| `ds_events` | Get only what **changed** since your last check (UIA) | ~50–200 | **After an action.** See what happened without re-reading everything. |

### Action (Controlling the App)

| Tool | What It Does | When to Use |
|------|-------------|-------------|
| `ds_click` | Click an element by name | Buttons, links, checkboxes, tabs |
| `ds_text` | Set text in a named input field. **PREFERRED.** | Form fields, address bars, search boxes (fast path) |
| `ds_type` | Type character-by-character into the focused element | Chat inputs, terminals, fields that reject `ds_text` |
| `ds_key` | Send keyboard shortcuts | `ctrl+s`, `enter`, `tab`, `ctrl+a`, `pagedown`, navigation |
| `ds_scroll` | Scroll in a direction | Fine-grained scrolling inside panels |
| `ds_batch` | Execute multiple actions in sequence | Multi-step workflows (click, type, tab, type, click) |
| `ds_navigate` | Navigate browser to a URL (CDP only) | Open a webpage directly |
| `ds_wait` | Wait for browser page to finish loading (CDP only) | After `ds_navigate` or clicking a link |

### Learning (Organic Memory)

| Tool | What It Does | When to Use |
|------|-------------|-------------|
| `ds_learn` | Read or write per-app learnings files | **Before acting:** read tips for this app. **After discovering something:** save it. |
| `ds_profile_list` | List all known app profiles |
| `ds_profile_save` | Save semantic element mappings for an app |
| `ds_profile_get` | Load a saved profile |

---

## Your Workflow (Step by Step)

### Step 0: Pick Your App

```
→ ds_apps()           # See all open applications
→ ds_focus("discord") # Snap to Discord — no human needed
```

Or for browsers:
```
→ ds_tabs()           # See all browser tabs
→ ds_tab("gmail")     # Switch to a tab by name
```

**You do NOT need to ask the user to snap.** DirectShell runs as a background daemon that tracks all open windows. Just `ds_apps()` → `ds_focus()` → work.

### Step 1: See the Screen

```
→ ds_update_view()
```

**This is your primary tool. Call it first. Call it often.** It returns two sections:

1. **VISIBLE TEXT** — What a human would see in the viewport right now
2. **NUMBERED TOOLS** — Every actionable element, e.g. `[1] click|Submit`, `[2] type|Search`

Example response:
```
Google Sheets spreadsheet with 3 columns and 12 rows of data.
---
[1] click|File
[2] click|Edit
[3] type|Name Box
[4] type|Formula Bar
[5] click|Sheet1
```

### Step 2: Check Learnings (If Available)

```
→ ds_learn("opera", "sheets")
```

Learnings contain tips and quirks from previous sessions — "Tab moves to next cell" or "Must use ds_type for chat input."

### Step 3: Act

Two ways to execute actions:

**Option A — Use tool numbers from `ds_update_view`:**
```
→ ds_act(4, text="=SUM(A1:A10)")    # Type into tool [4] (Formula Bar)
→ ds_act(1)                           # Click tool [1] (File menu)
```

**Option B — Use named tools directly:**
```
→ ds_click("Save")                              # Click a button by name
→ ds_text("hello@example.com", target="Email")   # Set text in a field
→ ds_type("Hello World")                          # Type into focused element
→ ds_key("ctrl+s")                                # Send a keyboard shortcut
```

### Step 4: Verify

```
→ ds_update_view()   # See the new state after your action
→ ds_events()        # Or just see what changed (~50 tokens)
```

### Step 5: Save What You Learned

```
→ ds_learn("opera", "sheets", append="Tab = next cell, Enter = next row.")
→ ds_learn("discord", "chat", append="Must use ds_type for chat input, ds_text is rejected.")
```

Next time any LLM opens the same app, these learnings load automatically.

---

## Critical Rules

### 1. `ds_text` vs `ds_type` vs `ds_key` — Know the Difference

- **`ds_text`** sets text in a **named input field**. In browser mode (CDP), it clicks the field and types via keyboard events. In native mode (UIA), it uses ValuePattern for instant injection. **Always preferred.**
- **`ds_type`** sends **keystrokes character-by-character** into the **currently focused element**. Use it for chat inputs (Discord, Slack), terminals, and apps that reject `ds_text`. It **auto-refocuses** via persisted click coordinates before typing.
- **`ds_key`** sends a **keyboard shortcut** (e.g., `ctrl+a`, `backspace`, `enter`). It does NOT re-click into the input field — it **preserves selection state**. This means `ds_key("ctrl+a")` → `ds_key("backspace")` works as expected (select all, then delete).

**Try `ds_text` first.** Fall back to `ds_type` only when `ds_text` doesn't work. For `ds_type`, click the input field first with `ds_click` — after that, the coordinates are persisted and all subsequent `ds_type` calls will auto-refocus.

**The Auto-Persist Pattern:** When you `ds_click` an element, DirectShell remembers its screen coordinates. All subsequent `ds_type` calls automatically re-click those coordinates before typing, ensuring the input field stays focused. `ds_key` does NOT re-click (to preserve selection state). This persists until you click something else.

**Fail-Safe:** During `ds_type`, if the target application loses foreground focus mid-typing, the action **aborts immediately** — no more 20 seconds of keystrokes going to the wrong window.

### 2. The Zoom-Out Trick (Content-Heavy Pages)

You read the accessibility tree, not pixels. Font size doesn't matter to you. When a page has lots of content:

1. `ds_key("ctrl+minus")` × 8 (zoom to ~25%)
2. `ds_screen()` — read EVERYTHING in one call
3. `ds_key("ctrl+0")` — reset zoom for the human

This captures entire chat histories, full documents, long articles — in a single read. No scrolling loops needed.

### 3. Batch Actions for Speed

Instead of 5 separate tool calls (click, type, tab, type, click), use one `ds_batch`:

```python
ds_batch([
    {"action": "click", "target": "Amount"},
    {"action": "text", "text": "2599.00", "target": "Amount"},
    {"action": "key", "text": "tab"},
    {"action": "text", "text": "19%", "target": "Tax Rate"},
    {"action": "click", "target": "Save"}
])
```

One call. Five actions. All executed at 33 Hz.

### 4. SQL Queries Are Your Superpower

The full element tree is in a SQLite database. You can ask anything:

```sql
-- Find all buttons
SELECT name FROM elements WHERE role='Button' AND enabled=1

-- Find all text fields with their current values
SELECT name, value FROM elements WHERE role='Edit'

-- Count interactive elements
SELECT role, COUNT(*) as n FROM elements GROUP BY role ORDER BY n DESC

-- Find elements containing "invoice" in their name
SELECT name, role, value FROM elements WHERE name LIKE '%invoice%'
```

This is ~10–50 tokens per query. Compare that to a 5,000-token screenshot.

---

## Snapping to Applications

### AI-Native (Recommended): Use `ds_apps()` + `ds_focus()`

DirectShell runs as a **background daemon** that tracks all open windows every 2 seconds. You can switch between apps programmatically:

```
→ ds_apps()              # Lists: opera, discord, notepad, ...
→ ds_focus("discord")    # Snaps to Discord (brings to foreground, reads UI)
→ ds_update_view()       # See Discord's screen
→ ds_focus("notepad")    # Switch to Notepad (unsnaps Discord first)
```

No human interaction needed. This is the preferred workflow for AI agents.

### Manual (Legacy): Drag the Overlay

The human can also manually snap by dragging the DirectShell overlay window onto any application. This still works and is useful for initial setup or debugging.

---

## What You Can Do Now

You can natively operate **any Windows application**:

- **Browsers** (Chrome, Edge, Opera, Firefox): Fill forms, click links, read pages, navigate tabs
- **Office** (Word, Excel, PowerPoint): Edit documents, fill spreadsheets, create presentations
- **Enterprise Software** (SAP, Datev, Salesforce): Fill forms, navigate modules, extract data
- **Chat Applications** (Discord, Slack, Teams): Read messages, send replies, manage channels
- **Development Tools** (VS Code, terminals, Git GUIs): Write code, run commands, manage repos
- **Any Windows Application**: If it has a window, you can read and control it

---

## Building Knowledge (Profiles + Learnings)

DirectShell has two complementary memory systems:

### Learnings — Operational Experience

Learnings are per-app, per-context text files stored in `ds_profiles/learnings/`. They capture **what works and what doesn't** — the kind of knowledge that only comes from trial and error.

```python
# Save a discovery
ds_learn("opera", "sheets", append="Tab = next cell. Enter = next row but returns to LAST SELECTION column, not column A.")

# Read before acting
ds_learn("opera", "sheets")
# → Returns all accumulated tips for Google Sheets in Opera
```

When you call `ds_update_view()`, it automatically tells you which learnings exist. **Read them before acting.** This is organic learning: Try → Fail → Learn → Save → Do better next time.

### Profiles — Structural Mappings

Profiles map element names to semantic roles. They persist across sessions:

```python
ds_profile_save(
    app="excel",
    description="Microsoft Excel - Spreadsheet Editor",
    elements={
        "Name Box": "cell_reference",
        "Formula Bar": "formula_input",
        "Sheet1": "active_sheet_tab"
    }
)
```

Next time anyone snaps DirectShell to Excel, your profile loads automatically. You already know the app.

**Both systems are collaborative.** Every profile and every learning you save helps every LLM that comes after you. Imagine 1 million users with 1 million programs — and a shared knowledge base. Every mistake one user makes becomes training data for everyone else.

---

## Dual-Mode Architecture: CDP vs UIA

DirectShell automatically routes to the right backend:

| App Type | Mode | How Input Works |
|----------|------|----------------|
| **Browsers** (Opera, Chrome, Edge, Firefox, Brave, Vivaldi) | **CDP** (Chrome DevTools Protocol) | DOM-level events via WebSocket |
| **Everything else** (Discord, Notepad, SAP, Excel, ...) | **UIA** (Windows UI Automation) | OS-level `SendInput` (real keyboard/mouse) |

Routing is automatic — you don't need to think about it. The `_BROWSER_APPS` set in the MCP server determines which apps use CDP. All others use UIA.

**All native app input uses `SendInput`** — real OS-level keyboard and mouse events that the target application cannot distinguish from human hardware input. No `PostMessage`, no `WM_CHAR`, no API shortcuts.

---

## Known Limitations (February 2026)

- **One active app**: DirectShell focuses one application at a time, but switching is instant via `ds_focus()`. Multi-app workflows just need `ds_focus()` between apps.
- **Chromium activation**: Chromium-based apps need a few seconds to build their accessibility tree after snapping. Be patient on first snap.
- **Canvas-rendered content**: Some web apps render on canvas (e.g., Google Sheets cells). The a11y tree shows 0 elements for these. Use `ds_type` with keyboard navigation (Tab, Enter) instead.
- **Accessibility quality varies**: The tree is only as good as the app's accessibility implementation. Major enterprise software is comprehensive. Smaller apps may have unnamed buttons or missing values.
- **Windows native, Linux port available**: macOS (NSAccessibility) and Linux (AT-SPI2) have equivalent frameworks. A community Linux port is available at [DirectShell-Linux](https://github.com/Harfho/DirectShell-Linux).

The architecture is sound. The learnings system means every limitation you encounter and document makes every future session smarter.

---

## The Big Picture

Before DirectShell, you had two options for interacting with a GUI:
1. **Take a screenshot** and guess where to click (1,200–5,000 tokens, ~40% success rate)
2. **Use a vendor API** (if it exists, if you can afford it, if the vendor allows it)

Now you have a third option:
3. **Read the accessibility tree as structured text and control the app via SQL** (50–200 tokens, deterministic, works on any app, legally unblockable)

You are no longer blind. Every GUI on Earth just became a text API.

Welcome to DirectShell.

---

*DirectShell is AGPL-3.0. Source: [github.com/IamLumae/DirectShell](https://github.com/IamLumae/DirectShell)*
*Created by Martin Gehrken — February 2026*
