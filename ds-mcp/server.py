# DirectShell MCP Server — Model Context Protocol interface for DirectShell
# Copyright (C) 2026  Martin Gehrken (IamLumae)
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
ds-mcp — DirectShell MCP Bridge
Connects any MCP-compatible LLM to any GUI application via DirectShell.

13 tools. One interface. Every application on the planet.

Usage:
    python server.py                          # uses default ds_profiles path
    python server.py --profiles /path/to/ds_profiles

MCP config (.claude.json or similar):
    {
        "mcpServers": {
            "directshell": {
                "command": "python",
                "args": ["path/to/ds-mcp/server.py", "--profiles", "path/to/ds_profiles"]
            }
        }
    }
"""

import sqlite3
import json
import os
import sys
import time
from pathlib import Path
from typing import Optional

from fastmcp import FastMCP

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

DEFAULT_PROFILES = str(
    Path(__file__).resolve().parent.parent / "target" / "release" / "ds_profiles"
)

def get_profiles_dir() -> Path:
    """Resolve the ds_profiles directory from CLI args or default."""
    for i, arg in enumerate(sys.argv):
        if arg == "--profiles" and i + 1 < len(sys.argv):
            return Path(sys.argv[i + 1])
    env = os.environ.get("DS_PROFILES")
    if env:
        return Path(env)
    return Path(DEFAULT_PROFILES)

PROFILES_DIR = get_profiles_dir()
PROFILES_JSON = PROFILES_DIR / "app_profiles.json"

# ---------------------------------------------------------------------------
# MCP Server
# ---------------------------------------------------------------------------

mcp = FastMCP(
    "DirectShell",
    instructions=(
        "Universal GUI control for any desktop application. "
        "Read any screen as structured text. Control any app via named elements. "
        "Powered by DirectShell and the Windows Accessibility Layer.\n\n"
        "## Element Type Prefixes (ds_state output)\n"
        "Every element in ds_state has a type prefix that tells you HOW to interact with it:\n"
        "- [click]    = Button, link, or clickable element. Use ds_click.\n"
        "- [keyboard] = Text input field (Edit, TextBox). Use ds_text or ds_type, then ds_click to focus first.\n"
        "- [select]   = Dropdown, combobox, or search field with suggestions. Use ds_click to focus, ds_type to enter text.\n\n"
        "## Critical: Distinguish inputs from buttons!\n"
        "A search area typically has TWO elements:\n"
        "  [select] \"Suchen\"  ← this is the INPUT FIELD (type text here)\n"
        "  [click]  \"Search\"  ← this is the SUBMIT BUTTON (click after typing)\n"
        "Always identify the input field first, type into it, THEN click the submit button.\n\n"
        "## Workflow: Read → Understand → Act\n"
        "1. ds_state() to see what's on screen (compact, numbered list)\n"
        "2. Identify the right elements by their type prefix and name\n"
        "3. For text input: ds_click on the [keyboard]/[select] field → ds_type the text → ds_key('enter') or ds_click the submit button\n"
        "4. For buttons: ds_click on the [click] element\n"
        "5. ds_state() again to verify the result\n\n"
        "## Pro Tip: Zoom Out for Content-Heavy Pages\n"
        "You read the accessibility tree, not pixels. You don't need readable font sizes.\n"
        "When a page has lots of content (chat responses, articles, docs):\n"
        "1. Zoom out: ds_key('ctrl+minus') x8 (gets to ~25%)\n"
        "2. Read everything at once with ds_screen() — ALL content in one shot\n"
        "3. Zoom back: ds_key('ctrl+0') to reset for the human\n"
        "This beats scrolling in every way: no focus issues, no multi-step loops, one read = complete content.\n\n"
        "## Live Events (Delta Perception)\n"
        "DirectShell captures live UIA events and writes them to an `events` table.\n"
        "Use ds_events() to get only what CHANGED since your last check — ~50 tokens vs ~5000 for full tree.\n"
        "Event types: automation (window/menu/content_loaded), property (Name/Value/Toggle/Enabled changes), "
        "structure (DOM mutations — child added/removed/invalidated).\n"
        "Best practice: ds_click('Save') → ds_events() → see exactly what happened, without re-reading the full tree."
    ),
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _read_active() -> dict:
    """Read is_active file. Returns {snapped: bool, app: str, a11y: str, snap: str}."""
    active_file = PROFILES_DIR / "is_active"
    if not active_file.exists():
        return {"snapped": False, "app": "none", "a11y": "", "snap": ""}
    lines = active_file.read_text(encoding="utf-8").strip().splitlines()
    app = lines[0] if lines else "none"
    if app == "none" or not lines:
        return {"snapped": False, "app": "none", "a11y": "", "snap": ""}
    return {
        "snapped": True,
        "app": app,
        "a11y": lines[1] if len(lines) > 1 else "",
        "snap": lines[2] if len(lines) > 2 else "",
    }


def _get_db_path(app: Optional[str] = None) -> Path:
    """Get path to the SQLite DB for given app or current snapped app."""
    if app:
        return PROFILES_DIR / f"{app}.db"
    status = _read_active()
    if not status["snapped"]:
        raise ValueError("DirectShell is not snapped to any application.")
    return PROFILES_DIR / f"{status['app']}.db"


def _read_file(suffix: str, app: Optional[str] = None) -> str:
    """Read an output file (.a11y, .a11y.snap, .snap) for given or current app."""
    if app:
        path = PROFILES_DIR / f"{app}{suffix}"
    else:
        status = _read_active()
        if not status["snapped"]:
            raise ValueError("DirectShell is not snapped to any application.")
        path = PROFILES_DIR / f"{status['app']}{suffix}"
    if not path.exists():
        raise FileNotFoundError(f"File not found: {path}")
    return path.read_text(encoding="utf-8")


def _db_query(sql: str, app: Optional[str] = None) -> list[dict]:
    """Execute a read-only SQL query against the element database."""
    db_path = _get_db_path(app)
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(sql).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def _inject_action(action: str, text: str = "", target: str = "", app: Optional[str] = None, wait: bool = True) -> int:
    """Insert an action into the inject table and optionally wait for completion.

    DirectShell polls the inject table, executes the action, then sets done=1.
    If wait=True, this function blocks until the action is confirmed done,
    ensuring subsequent ds_state() calls return the post-action screen.
    """
    db_path = _get_db_path(app)
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode=WAL;")
    try:
        cur = conn.execute(
            "INSERT INTO inject (action, text, target, done) VALUES (?, ?, ?, 0)",
            (action, text, target),
        )
        conn.commit()
        action_id = cur.lastrowid
    finally:
        conn.close()

    if wait:
        _wait_for_action(action_id, db_path)

    return action_id


def _wait_for_action(action_id: int, db_path: Path, timeout: float = 5.0, poll_interval: float = 0.05):
    """Poll the inject table until the action is marked done=1.

    Polls every 50ms (fast enough to feel instant, light enough to not spam).
    Times out after 5 seconds to prevent infinite hangs.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
            try:
                row = conn.execute(
                    "SELECT done FROM inject WHERE id=?", (action_id,)
                ).fetchone()
                if row and row[0] == 1:
                    # Action done — wait one more cycle for DS to write output files
                    time.sleep(0.15)
                    return
            finally:
                conn.close()
        except sqlite3.OperationalError:
            pass  # DB locked, retry
        time.sleep(poll_interval)
    # Timeout — return anyway, action might still execute


def _load_profiles() -> dict:
    """Load app profiles from JSON file."""
    if PROFILES_JSON.exists():
        return json.loads(PROFILES_JSON.read_text(encoding="utf-8"))
    return {}


def _save_profiles(profiles: dict):
    """Save app profiles to JSON file."""
    PROFILES_JSON.write_text(
        json.dumps(profiles, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# READ TOOLS — Perceive the screen
# ---------------------------------------------------------------------------

@mcp.tool()
def ds_status() -> dict:
    """Check if DirectShell is snapped to an application.

    Returns the current state: which app is snapped,
    and paths to all output files. Call this first to know
    what DirectShell is currently attached to.
    """
    status = _read_active()
    if status["snapped"]:
        app = status["app"]
        status["files"] = {
            "db": str(PROFILES_DIR / f"{app}.db"),
            "snap": str(PROFILES_DIR / f"{app}.snap"),
            "a11y": str(PROFILES_DIR / f"{app}.a11y"),
            "a11y_snap": str(PROFILES_DIR / f"{app}.a11y.snap"),
        }
        # List all previously snapped apps
        status["known_apps"] = sorted(set(
            p.stem for p in PROFILES_DIR.glob("*.db")
        ))
    return status


@mcp.tool()
def ds_state(app: Optional[str] = None) -> str:
    """Get the current screen state as a compact numbered list of operable elements.

    This is the primary perception tool. Returns numbered elements like:
    [1] [keyboard] "Search Box" @ 100,200 (300x30)
    [2] [click] "Save" @ 500,600 (80x25)

    Use these element names with ds_click, ds_text, ds_type.

    Args:
        app: Optional app name. If omitted, uses the currently snapped app.
    """
    # NOTE for LLMs: The type prefix tells you what the element IS:
    #   [click]    = button/link → use ds_click
    #   [keyboard] = text input  → use ds_text or ds_click + ds_type
    #   [select]   = combobox/search field → use ds_click + ds_type
    return _read_file(".a11y.snap", app)


@mcp.tool()
def ds_screen(app: Optional[str] = None) -> str:
    """Get the full screen reader view with focus, input targets, and all visible content.

    More detailed than ds_state. Includes:
    - Focus: what element currently has keyboard focus
    - Input Targets: all text fields with their current values
    - Content: all visible text, links, labels

    Use this when you need full context about what's on screen.

    Args:
        app: Optional app name. If omitted, uses the currently snapped app.
    """
    return _read_file(".a11y", app)


@mcp.tool()
def ds_elements(app: Optional[str] = None) -> str:
    """Get all interactive elements with their input type classification.

    Returns every interactive, enabled, visible element with:
    - Input type: [keyboard], [click], [select]
    - Element name
    - Position and size
    - Automation ID (if available)

    More complete than ds_state but less contextual than ds_screen.

    Type prefixes: [click] = button/link, [keyboard] = text input, [select] = combobox/search field.

    Args:
        app: Optional app name. If omitted, uses the currently snapped app.
    """
    return _read_file(".snap", app)


@mcp.tool()
def ds_query(sql: str, app: Optional[str] = None) -> list[dict]:
    """Run a SQL query against the DirectShell element database.

    The 'elements' table contains every UI element:
    - id, parent_id, depth, role, name, value, automation_id
    - enabled (1/0), offscreen (1/0)
    - x, y, w, h (position and size)

    Examples:
        "SELECT name, value FROM elements WHERE role='Edit'"
        "SELECT name FROM elements WHERE role='Button' AND enabled=1"
        "SELECT count(*) as n FROM elements WHERE name LIKE '%unread%'"
        "SELECT role, COUNT(*) as n FROM elements GROUP BY role ORDER BY n DESC"

    Args:
        sql: A SELECT query. Write queries are not allowed.
        app: Optional app name. If omitted, uses the currently snapped app.
    """
    sql_lower = sql.strip().lower()
    if not sql_lower.startswith("select"):
        raise ValueError("Only SELECT queries are allowed. Use action tools to modify state.")
    return _db_query(sql, app)


@mcp.tool()
def ds_find(name_pattern: str, app: Optional[str] = None) -> list[dict]:
    """Find elements by name pattern.

    Searches for elements whose name matches the pattern (case-insensitive).
    Uses SQL LIKE — use % as wildcard.

    Examples:
        ds_find("Save")           — exact match
        ds_find("%invoice%")      — contains "invoice"
        ds_find("Customer%")      — starts with "Customer"

    Returns matching elements with id, role, name, value, automation_id,
    enabled, offscreen, position.

    Args:
        name_pattern: Search pattern. Use % as wildcard.
        app: Optional app name. If omitted, uses the currently snapped app.
    """
    return _db_query(
        "SELECT id, role, name, value, automation_id, enabled, offscreen, "
        "x, y, w, h FROM elements "
        f"WHERE name LIKE ? AND enabled=1 AND offscreen=0 "
        "ORDER BY y, x",
        # Can't use parameterized query through our helper, so sanitize
        app,
    ) if False else _find_impl(name_pattern, app)


def _find_impl(pattern: str, app: Optional[str] = None) -> list[dict]:
    """Actual implementation of ds_find with parameterized query."""
    db_path = _get_db_path(app)
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT id, role, name, value, automation_id, enabled, offscreen, "
            "x, y, w, h FROM elements "
            "WHERE name LIKE ? AND enabled=1 AND offscreen=0 "
            "ORDER BY y, x",
            (pattern,),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def _clean(s: str, max_len: int = 60) -> str:
    """Strip invisible Unicode junk and truncate."""
    import re
    if not s:
        return ""
    # Remove zero-width chars, combining marks, invisible separators
    s = re.sub(r'[\u034f\u200b-\u200f\u2028-\u202f\u2060-\u206f\ufeff]', '', s)
    s = re.sub(r'\s{3,}', ' ', s).strip()  # collapse whitespace runs
    if len(s) > max_len:
        s = s[:max_len - 3] + "..."
    return s


def _distill_events(raw: list[dict]) -> dict:
    """Distill raw UIA events into a compact, high-value summary.

    Instead of returning 190 raw events (~12,000 tokens), produces a condensed
    digest (~50-200 tokens) with only the information that matters.
    """
    if not raw:
        return {"events": [], "summary": "No new events."}

    # --- Categorize ---
    automation = []   # window_opened, menu_opened, content_loaded
    prop_changes = [] # Name/Value/ToggleState/IsEnabled changes
    struct_counts = {}

    for ev in raw:
        et = ev.get("event_type", "")
        if et == "automation":
            automation.append(ev)
        elif et == "property":
            prop_changes.append(ev)
        elif et == "structure":
            detail = ev.get("detail", "")
            struct_counts[detail] = struct_counts.get(detail, 0) + 1

    # --- Deduplicate property changes: keep LAST value per (cleaned_name, detail) ---
    seen_props = {}
    for ev in prop_changes:
        key = (_clean(ev.get("element_name", ""), 40), ev.get("detail", ""))
        seen_props[key] = ev  # last write wins
    unique_props = list(seen_props.values())

    # --- Split properties into high-value vs noise ---
    value_changes = []   # Value, ToggleState, IsEnabled — always show
    name_changes = []    # Name — usually noise (tab titles, email subjects)

    for ev in unique_props:
        detail = ev.get("detail", "")
        if detail == "Name":
            name_changes.append(ev)
        else:
            value_changes.append(ev)

    # --- Build events list (compact) ---
    events = []

    # Automation events — deduplicated, always shown
    auto_seen = set()
    for ev in automation:
        detail = ev.get("detail", "")
        name = _clean(ev.get("element_name", ""))
        entry = f"{detail}: {name}" if name else detail
        if entry not in auto_seen:
            auto_seen.add(entry)
            events.append(entry)

    # Value/Toggle/Enabled changes — always shown (high signal)
    for ev in value_changes:
        detail = ev.get("detail", "")
        name = _clean(ev.get("element_name", ""), 40)
        new_val = _clean(ev.get("new_value", "") or "", 80)
        if detail == "Value" and new_val:
            label = f" ({name})" if name else ""
            events.append(f"Value{label}: \"{new_val}\"")
        elif detail == "ToggleState":
            state = {0: "off", 1: "on", 2: "indeterminate"}.get(
                int(new_val) if new_val.isdigit() else -1, new_val
            )
            events.append(f"Toggle {name}: {state}" if name else f"Toggle: {state}")
        elif detail == "IsEnabled":
            events.append(f"{'Enabled' if new_val == '1' else 'Disabled'}: {name}")

    # Name changes — summarized (usually noise like email subjects)
    if name_changes:
        if len(name_changes) <= 2:
            for ev in name_changes:
                events.append(f"Renamed: \"{_clean(ev.get('element_name', ''), 50)}\"")
        else:
            events.append(f"{len(name_changes)} elements renamed")

    # Structure — single line with counts
    struct_total = sum(struct_counts.values())
    if struct_total:
        parts = [f"{v} {k}" for k, v in struct_counts.items() if v > 0]
        events.append(f"DOM: {', '.join(parts)}")

    # --- Summary line ---
    summary_parts = []
    if automation:
        summary_parts.append(f"{len(auto_seen)} automation")
    if value_changes:
        summary_parts.append(f"{len(value_changes)} value changes")
    if name_changes:
        summary_parts.append(f"{len(name_changes)} renames")
    if struct_total:
        summary_parts.append(f"{struct_total} DOM mutations")
    summary = f"{len(raw)} events: {', '.join(summary_parts)}." if summary_parts else f"{len(raw)} events."

    return {
        "summary": summary,
        "events": events,
    }


@mcp.tool()
def ds_events(app: Optional[str] = None, mark_consumed: bool = True) -> dict:
    """Get new (unconsumed) UI events since last check.

    Returns live events from UIA event handlers:
    - automation: window_opened, menu_opened, content_loaded
    - property: Name/Value/ToggleState/IsEnabled changes on elements
    - structure: child_added, child_removed, children_invalidated, etc.

    Each event has: id, timestamp, event_type, element_name, element_role,
    detail, new_value, consumed.

    By default, marks returned events as consumed so they won't appear again.
    Use mark_consumed=False to peek without consuming.

    This is the delta-based perception tool — ~50 tokens vs ~5000 for full tree.
    Use ds_events() between actions to see what changed.

    Args:
        app: Optional app name. If omitted, uses the currently snapped app.
        mark_consumed: If True (default), mark events as consumed after reading.
    """
    db_path = _get_db_path(app)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT id, timestamp, event_type, element_name, element_role, "
            "detail, new_value FROM events WHERE consumed = 0 ORDER BY id"
        ).fetchall()
        raw = [dict(row) for row in rows]
        if mark_consumed and raw:
            max_id = max(r["id"] for r in raw)
            conn.execute("UPDATE events SET consumed = 1 WHERE id <= ?", (max_id,))
            conn.commit()
        return _distill_events(raw)
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# ACTION TOOLS — Control the application
# ---------------------------------------------------------------------------

@mcp.tool()
def ds_click(element_name: str, app: Optional[str] = None) -> dict:
    """Click a named UI element.

    DirectShell finds the element by name in the accessibility tree,
    calculates its center point, and sends a native mouse click.
    The click is indistinguishable from physical hardware input.

    Args:
        element_name: The exact name of the element to click (as shown in ds_state).
        app: Optional app name. If omitted, uses the currently snapped app.

    Returns:
        Confirmation with the action ID.
    """
    action_id = _inject_action("click", target=element_name, app=app)
    return {"action": "click", "target": element_name, "id": action_id, "status": "queued"}


@mcp.tool()
def ds_text(value: str, target: str, app: Optional[str] = None) -> dict:
    """Set text in a named input field instantly via UIA ValuePattern.

    This is the fast path — sets the entire string at once.
    Use this for form fields, address bars, search boxes.

    If the field doesn't support ValuePattern, DirectShell automatically
    falls back to character-by-character keyboard input.

    Args:
        value: The text to set.
        target: The name of the target input field (as shown in ds_state).
        app: Optional app name. If omitted, uses the currently snapped app.

    Returns:
        Confirmation with the action ID.
    """
    action_id = _inject_action("text", text=value, target=target, app=app)
    return {"action": "text", "value": value, "target": target, "id": action_id, "status": "queued"}


@mcp.tool()
def ds_type(text: str, app: Optional[str] = None) -> dict:
    """Type text character-by-character via raw keyboard simulation.

    Sends each character as a physical keystroke with 5ms delay.
    Types into whatever currently has keyboard focus.

    Use this for chat inputs, terminals, and fields that reject
    programmatic text setting. Supports \\t for Tab and \\n for Enter.

    Args:
        text: The text to type. Use \\t for Tab, \\n for Enter.
        app: Optional app name. If omitted, uses the currently snapped app.

    Returns:
        Confirmation with the action ID.
    """
    action_id = _inject_action("type", text=text, app=app)
    return {"action": "type", "text": text, "id": action_id, "status": "queued"}


@mcp.tool()
def ds_key(combo: str, app: Optional[str] = None) -> dict:
    """Press a keyboard shortcut or key combination.

    Supports modifiers (ctrl, alt, shift, win) combined with any key.

    Examples:
        "ctrl+s"         — Save
        "ctrl+shift+s"   — Save As
        "enter"          — Press Enter
        "tab"            — Press Tab
        "alt+f4"         — Close window
        "ctrl+a"         — Select All
        "f5"             — Refresh
        "escape"         — Cancel
        "pagedown"       — Scroll exactly one viewport down (use this for page navigation!)
        "pageup"         — Scroll exactly one viewport up
        "home"           — Jump to top of page
        "end"            — Jump to bottom of page

    Args:
        combo: Key combination string (e.g., "ctrl+shift+s").
        app: Optional app name. If omitted, uses the currently snapped app.

    Returns:
        Confirmation with the action ID.
    """
    action_id = _inject_action("key", text=combo, app=app)
    return {"action": "key", "combo": combo, "id": action_id, "status": "queued"}


@mcp.tool()
def ds_scroll(direction: str, amount: int = 1, app: Optional[str] = None) -> dict:
    """Scroll in the target application.

    For page-by-page navigation, prefer ds_key("pagedown") / ds_key("pageup")
    which moves exactly one viewport height. Use ds_scroll only for fine-grained
    mouse wheel scrolling (e.g., scrolling inside a specific panel).

    Args:
        direction: One of "up", "down", "left", "right".
        amount: Number of scroll notches (default 1). Each notch ~3 lines.
        app: Optional app name. If omitted, uses the currently snapped app.

    Returns:
        Confirmation with the action ID.
    """
    if direction not in ("up", "down", "left", "right"):
        raise ValueError(f"Invalid direction: {direction}. Use up/down/left/right.")
    # Only wait on the last scroll action
    ids = []
    for i in range(amount):
        is_last = (i == amount - 1)
        ids.append(_inject_action("scroll", text=direction, app=app, wait=is_last))
    return {"action": "scroll", "direction": direction, "amount": amount, "ids": ids, "status": "queued"}


@mcp.tool()
def ds_batch(actions: list[dict], app: Optional[str] = None) -> list[dict]:
    """Execute multiple actions in sequence.

    Each action is a dict with 'action' (required), 'text', and 'target' fields.
    Actions are inserted into the queue in order and executed at 33 Hz.

    Example:
        [
            {"action": "click", "target": "Amount"},
            {"action": "text", "text": "2599.00", "target": "Amount"},
            {"action": "key", "text": "tab"},
            {"action": "text", "text": "19%", "target": "Tax Rate"},
            {"action": "click", "target": "Save"}
        ]

    Args:
        actions: List of action dicts. Each needs at minimum an 'action' field.
        app: Optional app name. If omitted, uses the currently snapped app.

    Returns:
        List of confirmations with action IDs.
    """
    results = []
    total = len(actions)
    for i, act in enumerate(actions):
        is_last = (i == total - 1)
        action_id = _inject_action(
            action=act.get("action", "text"),
            text=act.get("text", ""),
            target=act.get("target", ""),
            app=app,
            wait=is_last,  # only wait for the last action
        )
        results.append({
            "action": act.get("action"),
            "id": action_id,
            "status": "queued",
        })
    return results


# ---------------------------------------------------------------------------
# PROFILE TOOLS — Learn and remember applications
# ---------------------------------------------------------------------------

@mcp.tool()
def ds_profile_list() -> dict:
    """List all known application profiles and previously snapped apps.

    Shows which apps have been seen before and which have
    learned profiles with semantic element mappings.
    """
    # Known apps from .db files
    known_apps = sorted(set(p.stem for p in PROFILES_DIR.glob("*.db")))

    # Loaded profiles
    profiles = _load_profiles()

    return {
        "known_apps": known_apps,
        "profiled_apps": list(profiles.keys()),
        "profiles": {
            name: {
                "description": p.get("description", ""),
                "element_count": len(p.get("elements", {})),
            }
            for name, p in profiles.items()
        },
    }


@mcp.tool()
def ds_profile_save(
    app: str,
    description: str,
    elements: dict[str, str],
) -> dict:
    """Save a semantic profile for an application.

    Maps application-specific element names to universal semantic roles.
    This lets future interactions use semantic names instead of
    application-specific labels.

    Example:
        app: "sap"
        description: "SAP GUI for Windows - Buchungsmaske"
        elements: {
            "Buchen": "save",
            "Stornieren": "cancel",
            "Kontonummer": "account_number",
            "Betrag": "amount",
            "Buchungsdatum": "date"
        }

    Args:
        app: Application name (matches the .db filename).
        description: Human-readable description of this app/screen.
        elements: Dict mapping element names to semantic roles.

    Returns:
        Confirmation.
    """
    profiles = _load_profiles()
    profiles[app] = {
        "description": description,
        "elements": elements,
        "updated": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    _save_profiles(profiles)
    return {
        "app": app,
        "status": "saved",
        "element_count": len(elements),
    }


@mcp.tool()
def ds_profile_get(app: str) -> dict:
    """Get the saved profile for an application.

    Returns the semantic element mapping if one has been saved.

    Args:
        app: Application name.
    """
    profiles = _load_profiles()
    if app not in profiles:
        return {"app": app, "status": "no_profile", "hint": "Use ds_profile_save to create one."}
    return {"app": app, **profiles[app]}


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print(f"DirectShell MCP Bridge — profiles: {PROFILES_DIR}", file=sys.stderr)
    mcp.run()
