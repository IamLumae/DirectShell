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

1. The human runs `DirectShell.exe`
2. They drag the overlay window onto any running application — this is called **snapping**
3. DirectShell continuously reads the app's entire UI into a SQLite database (refreshed every 500ms)
4. You interact through **13 MCP tools** (or through the database files directly)
5. The target application cannot distinguish your actions from human input

---

## The 13 Tools You Have

### Perception (Reading the Screen)

| Tool | What It Does | Token Cost | When to Use |
|------|-------------|:----------:|-------------|
| `ds_status` | Shows which app is snapped and paths to all output files | ~20 | **First call every time.** Always start here. |
| `ds_state` | Compact numbered list of all operable elements | ~200–500 | **Primary perception.** Your go-to for "what can I interact with?" |
| `ds_screen` | Full screen reader view: focus, inputs, all visible content | ~1,000–3,000 | When you need to **read content** (chat messages, documents, articles) |
| `ds_elements` | All interactive elements with input type classification | ~500–1,500 | When `ds_state` isn't detailed enough |
| `ds_find` | Search elements by name pattern (SQL LIKE) | ~50–200 | When you're looking for a **specific element** by name |
| `ds_query` | Run any SQL SELECT against the element database | ~50–200 | **Most powerful tool.** Ask any question about the UI. |
| `ds_events` | Get only what **changed** since your last check | ~50–200 | **After an action.** See what happened without re-reading everything. |

### Action (Controlling the App)

| Tool | What It Does | When to Use |
|------|-------------|-------------|
| `ds_click` | Click an element by name | Buttons, links, checkboxes, tabs |
| `ds_text` | Set text instantly via UIA ValuePattern | Form fields, address bars, search boxes (fast path) |
| `ds_type` | Type character-by-character via keyboard simulation | Chat inputs, terminals, fields that reject `ds_text` |
| `ds_key` | Send keyboard shortcuts | `ctrl+s`, `enter`, `tab`, `ctrl+a`, `pagedown`, navigation |
| `ds_scroll` | Scroll in a direction | Fine-grained scrolling inside panels |
| `ds_batch` | Execute multiple actions in sequence | Multi-step workflows (click, type, tab, type, click) |

### Profiles (Memory Across Sessions)

| Tool | What It Does |
|------|-------------|
| `ds_profile_list` | List all known app profiles |
| `ds_profile_save` | Save semantic element mappings for an app |
| `ds_profile_get` | Load a saved profile |

---

## Your Workflow (Step by Step)

### Step 1: Check What's Snapped

```
→ ds_status()
```

This tells you which application DirectShell is attached to and where the database files are. **Always start here.**

### Step 2: Read the Screen

```
→ ds_state()
```

You'll get a numbered list like:

```
[1] [keyboard] "Search Box" @ 100,200 (300x30)
[2] [click] "Save" @ 500,600 (80x25)
[3] [click] "Cancel" @ 600,600 (80x25)
[4] [keyboard] "Email" @ 100,300 (300x30)
```

The prefix tells you HOW to interact:
- `[keyboard]` → Use `ds_text` or `ds_type` to enter text
- `[click]` → Use `ds_click` to click it
- `[select]` → Use `ds_click` to open, then `ds_type` to search

### Step 3: Act

```
→ ds_click("Save")                              # Click a button
→ ds_text("hello@example.com", target="Email")   # Set text in a field
→ ds_type("Hello World")                          # Type into focused element
→ ds_key("ctrl+s")                                # Send a keyboard shortcut
```

### Step 4: Verify

```
→ ds_events()
```

This returns only what **changed** — ~50 tokens instead of re-reading the full tree (~5,000 tokens). Use this after every action to confirm it worked.

---

## Critical Rules

### 1. `ds_text` vs `ds_type` — Know the Difference

- **`ds_text`** sets a value **instantly** via UIA ValuePattern. Use it for form fields, search boxes, address bars. It targets elements **by name**.
- **`ds_type`** sends **keystrokes character-by-character** to whatever has focus. Use it for chat inputs (Discord, Slack, Claude.ai), terminals, and apps that reject programmatic text setting.

**Try `ds_text` first.** If the app rejects it, fall back to `ds_type`. For `ds_type`, make sure the right element has focus first (click it with `ds_click`).

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

## How to Tell the Human to Snap

The human needs to:

1. **Run DirectShell.exe** — double-click the binary (no installation needed)
2. **Drag the overlay** onto the target application window
3. The overlay will "snap" to the app — you'll see a frame around it
4. **That's it.** You now have full read/write access to that application.

To switch apps: tell the human to drag the overlay to a different window. Or use keyboard shortcuts to switch tabs within the same app.

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

## Building App Profiles

When you learn how an application works — which elements do what, what the workflow patterns are — you can **save that knowledge** for next time:

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

**This is how we build a universal config repository.** Every profile you save helps every LLM that comes after you. Contribute your profiles back to the community.

---

## Known Limitations (Day 1 — February 2026)

- **Single-app scope**: DirectShell attaches to one application at a time. Multi-app workflows require re-snapping.
- **Chromium activation**: Chrome, Edge, Discord, VS Code, Slack, and other Chromium-based apps need a few seconds to build their accessibility tree after snapping. Be patient on first snap.
- **`ds_type` character loss**: At high speed, some characters may be dropped. For critical input, use `ds_text` when possible or reduce typing speed.
- **Accessibility quality varies**: The tree is only as good as the app's accessibility implementation. Major enterprise software is comprehensive. Smaller apps may have unnamed buttons or missing values.
- **Windows only (for now)**: macOS (NSAccessibility) and Linux (AT-SPI2) have equivalent frameworks. Cross-platform support is planned.

These are Day 1 limitations. The architecture is sound. The bugs will be fixed.

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
