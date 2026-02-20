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
import re
import socket
import requests
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
        "**NEW TO DIRECTSHELL? Call ds_guide() first.** It explains every tool and the workflow.\n\n"
        "**Quick start:** ds_apps() → ds_focus('app') → ds_update_view() → ds_act(N)"
    ),
)

# ---------------------------------------------------------------------------
# Action Logger — deterministic learning loop
# ---------------------------------------------------------------------------

_ACTION_LOG_DIR = PROFILES_DIR / "action_log"

def _log_action(tool_name: str, params: dict, result: str, prev_ok: str):
    """Log an MCP action with success feedback from previous action. Never raises."""
    try:
        _ACTION_LOG_DIR.mkdir(exist_ok=True)
        ctx = {}
        try:
            if _is_cdp_available():
                ctx["mode"] = "cdp"
            else:
                status = _read_active()
                ctx["mode"] = "uia"
                ctx["app"] = status.get("app", "unknown")
        except Exception:
            ctx["mode"] = "unknown"
        entry = {
            "ts": time.time(),
            "tool": tool_name,
            "params": params,
            "result": result[:200] if result else "",
            "prev_ok": prev_ok,
            **ctx,
        }
        from datetime import datetime
        log_file = _ACTION_LOG_DIR / f"{datetime.now().strftime('%Y-%m-%d')}.jsonl"
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        pass  # logging must never break tool execution


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


def _get_snapped_app() -> str:
    """Return the currently snapped app name, or empty string."""
    status = _read_active()
    return status["app"] if status["snapped"] else ""


def _learning_hint() -> str:
    """Short reminder to save learnings after actions."""
    app = _get_snapped_app()
    return f" | tip: ds_learn('{app}', '<context>')" if app else ""


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


# --- Shared CSS selector for interactive elements (used by extract, click, type) ---
# Note: tabindex catches many non-interactive elements; exclude tabindex=-1.
_INTERACTIVE_SELECTORS = 'input,textarea,select,button,[role="button"],[role="textbox"],[role="menuitem"],[role="link"],[role="checkbox"],[role="radio"],[role="combobox"],[role="searchbox"],[role="tab"],[role="option"],[role="listitem"],[role="treeitem"],[role="gridcell"],[role="switch"],[role="slider"],[contenteditable="true"],[tabindex]:not([tabindex="-1"]),a[href]'

# --- Shared JS for viewport-only text extraction (used by ds_update_view + ds_screen) ---
_VIEWPORT_TEXT_JS = r'''(() => {
    const vh = window.innerHeight;
    const blockTags = new Set(['P','H1','H2','H3','H4','H5','H6','LI','TD','TH','DT','DD','PRE','BLOCKQUOTE','FIGCAPTION','ARTICLE','SECTION','HEADER','FOOTER','MAIN','ASIDE','NAV','DIV','SPAN','A','LABEL']);
    const blocks = document.querySelectorAll('p,h1,h2,h3,h4,h5,h6,li,td,th,dt,dd,pre,blockquote,figcaption,label,span,div,a,article,section');
    const parts = [], seen = new Set();
    for (const el of blocks) {
        const r = el.getBoundingClientRect();
        if (!r.height || r.bottom < 0 || r.top > vh) continue;
        if (r.height > vh * 1.5) continue;
        const s = getComputedStyle(el);
        if (s.opacity === '0' || s.visibility === 'hidden' || s.display === 'none') continue;
        let hasBlockChild = false;
        for (const child of el.children) {
            if (blockTags.has(child.tagName) && child.getBoundingClientRect().height > 0) {
                const cs = getComputedStyle(child);
                if (cs.display !== 'inline' && cs.display !== 'inline-block') { hasBlockChild = true; break; }
            }
        }
        if (hasBlockChild) continue;
        const t = el.innerText?.trim();
        if (!t || t.length < 2 || seen.has(t)) continue;
        seen.add(t);
        parts.push(t);
    }
    return parts.join('\n');
})()'''

# --- JS for full-page text extraction (used by ds_print) ---
_FULLPAGE_TEXT_JS = r'''(() => {
    const blockTags = new Set(['P','H1','H2','H3','H4','H5','H6','LI','TD','TH','DT','DD','PRE','BLOCKQUOTE','FIGCAPTION','ARTICLE','SECTION','HEADER','FOOTER','MAIN','ASIDE','NAV','DIV','SPAN','A','LABEL']);
    const blocks = document.querySelectorAll('p,h1,h2,h3,h4,h5,h6,li,td,th,dt,dd,pre,blockquote,figcaption,label,span,div,a,article,section');
    const parts = [], seen = new Set();
    for (const el of blocks) {
        const r = el.getBoundingClientRect();
        if (!r.height) continue;
        const s = getComputedStyle(el);
        if (s.opacity === '0' || s.visibility === 'hidden' || s.display === 'none') continue;
        let hasBlockChild = false;
        for (const child of el.children) {
            if (blockTags.has(child.tagName) && child.getBoundingClientRect().height > 0) {
                const cs = getComputedStyle(child);
                if (cs.display !== 'inline' && cs.display !== 'inline-block') { hasBlockChild = true; break; }
            }
        }
        if (hasBlockChild) continue;
        const t = el.innerText?.trim();
        if (!t || t.length < 2 || seen.has(t)) continue;
        seen.add(t);
        parts.push(t);
    }
    return parts.join('\n');
})()'''


_BROWSER_APPS = {"opera", "chrome", "edge", "firefox", "brave", "vivaldi", "chromium"}

def _is_cdp_available() -> bool:
    """Check if CDP should be used: snapped app must be a browser AND port 9222 must be open.
    Non-browser apps (Discord, etc.) always use UIA even if a browser is running in the background."""
    # Check if snapped app is a browser
    try:
        status = _read_active()
        if status["snapped"] and status["app"] not in _BROWSER_APPS:
            return False  # Native app snapped — never use CDP
    except Exception:
        pass
    # Check if CDP port is actually open
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(0.3)
        result = s.connect_ex(("127.0.0.1", 9222))
        s.close()
        return result == 0
    except Exception:
        return False


_cdp_active_tab_id: Optional[str] = None  # Track which tab we're focused on
_cdp_tool_map: dict[str, dict] = {}  # display label -> tool dict from last ds_update_view()

def _cdp_tabs() -> list[dict]:
    """Get all CDP browser tabs."""
    return requests.get("http://127.0.0.1:9222/json/list", timeout=2).json()

def _cdp_ws(tab_id: Optional[str] = None):
    """Get a CDP websocket connection. Uses tracked active tab, or first page tab."""
    import websocket as _ws
    tabs = _cdp_tabs()
    target_id = tab_id or _cdp_active_tab_id

    if target_id:
        tab = next((t for t in tabs if t.get("id") == target_id and "webSocketDebuggerUrl" in t), None)
        if tab:
            return _ws.create_connection(tab["webSocketDebuggerUrl"], timeout=5)

    # Fallback: first page tab
    page_tab = next((t for t in tabs if t.get("type") == "page" and "webSocketDebuggerUrl" in t), None)
    if not page_tab:
        raise RuntimeError("No CDP page tab found")
    return _ws.create_connection(page_tab["webSocketDebuggerUrl"], timeout=5)


def _cdp_dispatch_click(ws, x: float, y: float, click_count: int = 1, msg_id_base: int = 2) -> None:
    """Dispatch real mouse click at viewport coords (x, y)."""
    for eid, method_type in [(msg_id_base, "mousePressed"), (msg_id_base + 1, "mouseReleased")]:
        ws.send(json.dumps({"id": eid, "method": "Input.dispatchMouseEvent", "params": {
            "type": method_type, "x": x, "y": y, "button": "left", "clickCount": click_count
        }}))
        ws.recv()


def _cdp_eval(ws, js: str, msg_id: int = 1):
    """Evaluate JS in the active page context and return the raw CDP response."""
    ws.send(json.dumps({"id": msg_id, "method": "Runtime.evaluate", "params": {"expression": js, "returnByValue": True}}))
    return json.loads(ws.recv())


def _cdp_find_coords_for_tool(ws, tool: dict) -> Optional[dict]:
    """Try to find the element for a tool dict and return {x,y} or None.

    Prefers tool["dsid"] (data-ds-mcp attribute), then tool["selector"].
    Falls back to tool["x"], tool["y"] when available.
    """
    dsid = tool.get("dsid") or ""
    selector = tool.get("selector") or ""

    # Find by stable DS id across document, open shadow roots, and same-origin iframes.
    if dsid:
        safe = dsid.replace("\\", "\\\\").replace("'", "\\'").replace("\n", "\\n")
        js = f"""(() => {{
            const id = '{safe}';
            const attr = 'data-ds-mcp';
            const sel = `[${{attr}}="${{CSS.escape(id)}}" ]`.replace(' ]', ']');
            const vw = window.innerWidth, vh = window.innerHeight;
            function centerFor(el, offX, offY) {{
                const r = el.getBoundingClientRect();
                if (!r.width || !r.height) return null;
                const x = offX + r.x + r.width/2;
                const y = offY + r.y + r.height/2;
                if (x < 0 || y < 0 || x > vw || y > vh) return null;
                return {{x, y}};
            }}
            function findInRoot(root, offX, offY) {{
                try {{
                    const el = root.querySelector ? root.querySelector(sel) : null;
                    if (el) return centerFor(el, offX, offY);
                }} catch (_) {{}}
                // Traverse to discover open shadow roots
                try {{
                    const tw = document.createTreeWalker(root, NodeFilter.SHOW_ELEMENT);
                    while (tw.nextNode()) {{
                        const node = tw.currentNode;
                        if (node && node.shadowRoot) {{
                            const c = findInRoot(node.shadowRoot, offX, offY);
                            if (c) return c;
                        }}
                    }}
                }} catch (_) {{}}
                return null;
            }}
            // 1) main document
            let c = findInRoot(document, 0, 0);
            if (c) return JSON.stringify(c);
            // 2) same-origin iframes (coords offset by iframe rect)
            for (const iframe of document.querySelectorAll('iframe')) {{
                try {{
                    const idoc = iframe.contentDocument;
                    if (!idoc) continue;
                    const ir = iframe.getBoundingClientRect();
                    c = findInRoot(idoc, ir.x, ir.y);
                    if (c) return JSON.stringify(c);
                }} catch (_) {{}}
            }}
            return 'not_found';
        }})()"""
        resp = _cdp_eval(ws, js, msg_id=1)
        val = resp.get("result", {}).get("result", {}).get("value", "error")
        if val and val not in ("not_found", "error"):
            try:
                return json.loads(val)
            except Exception:
                pass

    # Find by selector in the top-level document only (selectors don't pierce shadow/iframe).
    if selector:
        safe = selector.replace("\\", "\\\\").replace("'", "\\'").replace("\n", "\\n")
        js = f"""(() => {{
            const sel = '{safe}';
            const el = document.querySelector(sel);
            if (!el) return 'not_found';
            const r = el.getBoundingClientRect();
            if (!r.width || !r.height) return 'not_found';
            return JSON.stringify({{x: r.x + r.width/2, y: r.y + r.height/2}});
        }})()"""
        resp = _cdp_eval(ws, js, msg_id=2)
        val = resp.get("result", {}).get("result", {}).get("value", "error")
        if val and val not in ("not_found", "error"):
            try:
                return json.loads(val)
            except Exception:
                pass

    # Fallback: stored coords from extraction (best-effort).
    if isinstance(tool.get("x"), (int, float)) and isinstance(tool.get("y"), (int, float)):
        return {"x": float(tool["x"]), "y": float(tool["y"])}

    return None


def _cdp_click_tool(tool: dict) -> str:
    """Click a DOM element using a tool dict (stable id/selector/coords)."""
    ws = _cdp_ws()
    coords = _cdp_find_coords_for_tool(ws, tool)
    if not coords:
        ws.close()
        raise RuntimeError(f"CDP: '{tool.get('element', '?')}' not found")
    _cdp_dispatch_click(ws, coords["x"], coords["y"], msg_id_base=10)
    ws.close()
    return "clicked"


def _cdp_click(element_name: str) -> str:
    """Click a DOM element by its name.

    If element_name exists in the last CDP tool list, uses its stable target data.
    Otherwise falls back to best-effort label search.
    """
    tool = _cdp_tool_map.get(element_name)
    if tool:
        return _cdp_click_tool(tool)
    # Legacy fallback: label scan in top-level document only.
    ws = _cdp_ws()
    safe = element_name.replace("\\", "\\\\").replace("'", "\\'").replace("\n", "\\n")
    js = f"""(() => {{
        const target = '{safe}';
        const selectors = '{_INTERACTIVE_SELECTORS}';
        for (const el of document.querySelectorAll(selectors)) {{
            const label = el.getAttribute('aria-label') || el.placeholder || (el.textContent||'').trim().substring(0,120) || el.name || el.id;
            if (label === target) {{
                const r = el.getBoundingClientRect();
                return JSON.stringify({{x: r.x + r.width/2, y: r.y + r.height/2}});
            }}
        }}
        return 'not_found';
    }})()"""
    resp = _cdp_eval(ws, js, msg_id=1)
    val = resp.get("result", {}).get("result", {}).get("value", "error")
    if val == "not_found":
        ws.close()
        raise RuntimeError(f"CDP: '{element_name}' not found")
    coords = json.loads(val)
    _cdp_dispatch_click(ws, coords["x"], coords["y"], msg_id_base=2)
    ws.close()
    return "clicked"


def _cdp_send_keys(ws, text: str, msg_id_start: int = 20) -> None:
    # Simulated keyboard — keyDown → keyUp for all keys.
    # Regular chars + Enter: keyDown with "text" property. Tab: keyDown without text.
    # NO "char" events (causes double input).
    special_keys = {
        "\t": ("Tab", "Tab", 9, None),      # no text
        "\n": ("Enter", "Enter", 13, "\r"),  # text=\r (Puppeteer convention)
    }
    msg_id = msg_id_start
    for ch in text:
        if ch in special_keys:
            key_val, code, vk, txt = special_keys[ch]
            params = {"type": "keyDown", "key": key_val, "code": code,
                      "windowsVirtualKeyCode": vk, "nativeVirtualKeyCode": vk}
            if txt:
                params["text"] = txt
            ws.send(json.dumps({"id": msg_id, "method": "Input.dispatchKeyEvent", "params": params}))
            ws.recv(); msg_id += 1
            ws.send(json.dumps({"id": msg_id, "method": "Input.dispatchKeyEvent", "params": {
                "type": "keyUp", "key": key_val, "code": code,
                "windowsVirtualKeyCode": vk, "nativeVirtualKeyCode": vk
            }}))
            ws.recv(); msg_id += 1
        else:
            # IMPORTANT: Don't use ord(ch) as Windows VK for punctuation.
            # Example: ord('(')=40 which is VK_DOWN. This makes Chromium drop/mis-handle '('.
            # Minimal fix: properly map only the collision-prone ASCII punctuation set (US layout).
            punct_map = {
                "!": ("Digit1", 49, 8), "@": ("Digit2", 50, 8), "#": ("Digit3", 51, 8),
                "$": ("Digit4", 52, 8), "%": ("Digit5", 53, 8), "^": ("Digit6", 54, 8),
                "&": ("Digit7", 55, 8), "*": ("Digit8", 56, 8), "(": ("Digit9", 57, 8),
                "*": ("Digit8", 56, 8), "(": ("Digit9", 57, 8), ")": ("Digit0", 48, 8),
                "=": ("Equal", 187, 0), "+": ("Equal", 187, 8), ";": ("Semicolon", 186, 0),
                ":": ("Semicolon", 186, 8), ",": ("Comma", 188, 0), "<": ("Comma", 188, 8),
                "-": ("Minus", 189, 0), "_": ("Minus", 189, 8), ".": ("Period", 190, 0),
                ">": ("Period", 190, 8), "/": ("Slash", 191, 0), "?": ("Slash", 191, 8),
                "`": ("Backquote", 192, 0), "~": ("Backquote", 192, 8), "[": ("BracketLeft", 219, 0),
                "{": ("BracketLeft", 219, 8), "]": ("BracketRight", 221, 0), "}": ("BracketRight", 221, 8),
                "\\": ("Backslash", 220, 0), "|": ("Backslash", 220, 8), "'": ("Quote", 222, 0),
                "\"": ("Quote", 222, 8),
            }

            if ch in punct_map:
                code, vk, modifiers = punct_map[ch]
                ws.send(json.dumps({"id": msg_id, "method": "Input.dispatchKeyEvent", "params": {
                    "type": "keyDown",
                    "key": ch,
                    "code": code,
                    "windowsVirtualKeyCode": vk,
                    "nativeVirtualKeyCode": vk,
                    "modifiers": modifiers,
                    "text": ch,
                    "unmodifiedText": ch,
                }}))
                ws.recv(); msg_id += 1
                ws.send(json.dumps({"id": msg_id, "method": "Input.dispatchKeyEvent", "params": {
                    "type": "keyUp",
                    "key": ch,
                    "code": code,
                    "windowsVirtualKeyCode": vk,
                    "nativeVirtualKeyCode": vk,
                    "modifiers": modifiers,
                }}))
                ws.recv(); msg_id += 1
            elif ch.isalpha():
                kc = ord(ch.upper())
                cd = f"Key{ch.upper()}"
                ws.send(json.dumps({"id": msg_id, "method": "Input.dispatchKeyEvent", "params": {
                    "type": "keyDown", "key": ch, "text": ch,
                    "code": cd, "windowsVirtualKeyCode": kc, "nativeVirtualKeyCode": kc
                }}))
                ws.recv(); msg_id += 1
                ws.send(json.dumps({"id": msg_id, "method": "Input.dispatchKeyEvent", "params": {
                    "type": "keyUp", "key": ch,
                    "code": cd, "windowsVirtualKeyCode": kc, "nativeVirtualKeyCode": kc
                }}))
                ws.recv(); msg_id += 1
            elif ch.isdigit():
                kc = ord(ch)
                cd = f"Digit{ch}"
                ws.send(json.dumps({"id": msg_id, "method": "Input.dispatchKeyEvent", "params": {
                    "type": "keyDown", "key": ch, "text": ch,
                    "code": cd, "windowsVirtualKeyCode": kc, "nativeVirtualKeyCode": kc
                }}))
                ws.recv(); msg_id += 1
                ws.send(json.dumps({"id": msg_id, "method": "Input.dispatchKeyEvent", "params": {
                    "type": "keyUp", "key": ch,
                    "code": cd, "windowsVirtualKeyCode": kc, "nativeVirtualKeyCode": kc
                }}))
                ws.recv(); msg_id += 1
            else:
                # Unknown char: best-effort insertText (avoids bad VK mapping)
                ws.send(json.dumps({"id": msg_id, "method": "Input.insertText", "params": {"text": ch}}))
                ws.recv(); msg_id += 1
    return None


def _cdp_type_to_tool(text: str, tool: dict) -> str:
    """Focus a tool target (if available) and type text via CDP."""
    ws = _cdp_ws()
    coords = _cdp_find_coords_for_tool(ws, tool)
    if coords:
        _cdp_dispatch_click(ws, coords["x"], coords["y"], msg_id_base=10)
    _cdp_send_keys(ws, text, msg_id_start=20)
    ws.close()
    return "typed"


def _cdp_type(text: str, target: str = "") -> str:
    """Type text via CDP — simulated keyboard events for every character. Works everywhere.

    If target matches a tool from the last ds_update_view, focuses it via stable target data.
    Otherwise falls back to best-effort label search + click.
    """
    tool = _cdp_tool_map.get(target) if target else None
    if tool:
        return _cdp_type_to_tool(text, tool)

    ws = _cdp_ws()
    if target:
        safe = target.replace("\\", "\\\\").replace("'", "\\'").replace("\n", "\\n")
        js = f"""(() => {{
            const t = '{safe}';
            const selectors = '{_INTERACTIVE_SELECTORS}';
            for (const el of document.querySelectorAll(selectors)) {{
                const label = el.getAttribute('aria-label') || el.placeholder || (el.textContent||'').trim().substring(0,120) || el.name || el.id;
                if (label === t) {{
                    const r = el.getBoundingClientRect();
                    return JSON.stringify({{x: r.x + r.width/2, y: r.y + r.height/2}});
                }}
            }}
            return 'not_found';
        }})()"""
        resp = _cdp_eval(ws, js, msg_id=1)
        val = resp.get("result", {}).get("result", {}).get("value", "error")
        if val == "not_found":
            ws.close()
            raise RuntimeError(f"CDP: '{target}' not found")
        coords = json.loads(val)
        _cdp_dispatch_click(ws, coords["x"], coords["y"], msg_id_base=10)
    _cdp_send_keys(ws, text, msg_id_start=20)
    ws.close()
    return "typed"


def _cdp_key(combo: str) -> str:
    """Press a key combo via CDP Input.dispatchKeyEvent."""
    # Parse combo: "ctrl+shift+a" → modifiers + key
    parts = combo.lower().split("+")
    key = parts[-1]
    modifiers = 0
    if "alt" in parts[:-1]: modifiers |= 1
    if "ctrl" in parts[:-1]: modifiers |= 2
    if "shift" in parts[:-1]: modifiers |= 8

    # Map common key names to CDP key codes
    key_map = {
        "enter": ("Enter", "Enter", 13), "tab": ("Tab", "Tab", 9),
        "escape": ("Escape", "Escape", 27), "backspace": ("Backspace", "Backspace", 8),
        "delete": ("Delete", "Delete", 46), "space": (" ", "Space", 32),
        "pagedown": ("PageDown", "PageDown", 34), "pageup": ("PageUp", "PageUp", 33),
        "home": ("Home", "Home", 36), "end": ("End", "End", 35),
        "arrowup": ("ArrowUp", "ArrowUp", 38), "arrowdown": ("ArrowDown", "ArrowDown", 40),
        "arrowleft": ("ArrowLeft", "ArrowLeft", 37), "arrowright": ("ArrowRight", "ArrowRight", 39),
        "f5": ("F5", "F5", 116),
    }

    if key in key_map:
        key_val, code, vk = key_map[key]
    elif len(key) == 1:
        key_val, code, vk = key, f"Key{key.upper()}", ord(key.upper())
    else:
        key_val, code, vk = key, key, 0

    ws = _cdp_ws()
    for evt in ["keyDown", "keyUp"]:
        ws.send(json.dumps({"id": 1, "method": "Input.dispatchKeyEvent", "params": {
            "type": evt, "key": key_val, "code": code,
            "windowsVirtualKeyCode": vk, "nativeVirtualKeyCode": vk,
            "modifiers": modifiers
        }}))
        ws.recv()
    ws.close()
    return "ok"


def _cdp_scroll(direction: str, amount: int = 1) -> str:
    """Scroll via CDP JavaScript window.scrollBy — more reliable than mouseWheel events."""
    # Each notch ~100px
    px = 100 * amount
    if direction == "down": js = f"window.scrollBy(0, {px})"
    elif direction == "up": js = f"window.scrollBy(0, -{px})"
    elif direction == "right": js = f"window.scrollBy({px}, 0)"
    elif direction == "left": js = f"window.scrollBy(-{px}, 0)"
    else: js = "null"

    ws = _cdp_ws()
    ws.send(json.dumps({"id": 1, "method": "Runtime.evaluate", "params": {"expression": js, "returnByValue": True}}))
    ws.recv()
    ws.close()
    return "ok"


def _cdp_navigate(url: str) -> str:
    """Navigate the browser to a URL via CDP."""
    ws = _cdp_ws()
    ws.send(json.dumps({"id": 1, "method": "Page.navigate", "params": {"url": url}}))
    ws.recv()
    ws.close()
    return "ok"


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


def _with_chars(text: str) -> str:
    """Append char count to output for tracking."""
    return f"{text}\n({len(text)} chars)"


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

GUIDE_PATH = Path(__file__).resolve().parent / "GUIDE.md"

@mcp.tool()
def ds_guide(prev_ok: str) -> str:
    """Read this FIRST before using any other DirectShell tool.

    Returns the complete quick-start guide: which tool to call first,
    what each tool does, and the standard workflow.

    DirectShell has two modes:
    - Browser mode (CDP): When a Chromium browser runs with --remote-debugging-port=9222.
      Uses Chrome DevTools Protocol for all interactions. Tools: ds_tabs, ds_tab, ds_navigate, ds_update_view, ds_screen, ds_click, ds_text, ds_type, ds_key, ds_scroll, ds_batch, ds_act.
    - Native app mode (UIA): For all other desktop applications (Discord, Notepad, SAP, etc.).
      Uses Windows UI Automation. Tools: ds_apps, ds_focus, ds_state, ds_elements, ds_query, ds_find, ds_events, plus all action tools.

    ds_update_view() works in BOTH modes and is always the best starting point.

    Args:
        prev_ok: Was your LAST MCP call successful? MUST answer: "yes", "no", or "unknown".
    """
    if GUIDE_PATH.exists():
        return GUIDE_PATH.read_text(encoding="utf-8")
    return "GUIDE.md not found next to server.py."


@mcp.tool()
def ds_status(prev_ok: str) -> dict:
    """Check if DirectShell is snapped to a native application (UIA mode).

    Returns {snapped: true/false, app: "name"}.
    Not needed for browser mode — use ds_tabs() instead.

    Args:
        prev_ok: Was your LAST MCP call successful? MUST answer: "yes", "no", or "unknown".
    """
    status = _read_active()
    return {"snapped": status["snapped"], "app": status["app"]}


@mcp.tool()
def ds_apps(prev_ok: str) -> str:
    """List all open desktop applications (UIA mode, for native apps).

    Returns which app is currently focused and all available apps.
    Use ds_focus(app) to switch to one. For browser tabs, use ds_tabs() instead.

    Args:
        prev_ok: Was your LAST MCP call successful? MUST answer: "yes", "no", or "unknown".
    """
    windows_file = PROFILES_DIR / "windows.json"
    if not windows_file.exists():
        return "DirectShell daemon not running. Wait 2 seconds and retry."
    data = json.loads(windows_file.read_text(encoding="utf-8"))
    status = _read_active()
    focused = status["app"] if status["snapped"] else "none"
    apps = sorted(set(w["app"] for w in data.get("windows", [])))
    result = f"Focused: {focused}\nApps: {', '.join(apps)}"
    return _with_chars(result)


@mcp.tool()
def ds_focus(app: str, prev_ok: str) -> dict:
    """Switch to a native desktop application (UIA mode).

    Snaps DirectShell to the named app so it can read its UI and receive actions.
    The app must appear in ds_apps() output. For browser tabs, use ds_tab() instead.

    Args:
        app: Application name as shown in ds_apps() (e.g., "discord", "notepad")
        prev_ok: Was your LAST MCP call successful? MUST answer: "yes", "no", or "unknown".
    """
    # Clean up any old result
    result_file = PROFILES_DIR / "snap_result"
    if result_file.exists():
        result_file.unlink()

    # Write snap request
    req_file = PROFILES_DIR / "snap_request"
    req_file.write_text(app.strip(), encoding="utf-8")

    # Wait for result (DS polls every 200ms, snap takes ~500ms)
    for _ in range(30):  # 3 seconds max
        time.sleep(0.1)
        if result_file.exists():
            try:
                result = json.loads(result_file.read_text(encoding="utf-8"))
                result_file.unlink()
                return result
            except (json.JSONDecodeError, OSError):
                continue

    # Timeout — check if is_active changed
    status = _read_active()
    if status["snapped"] and status["app"] == app.strip().lower():
        return {"status": "ok", "app": app}
    return {"status": "timeout", "reason": f"DirectShell did not snap to '{app}' within 3 seconds."}


@mcp.tool()
def ds_tabs(prev_ok: str) -> str:
    """List all open browser tabs (CDP/browser mode).

    Returns numbered list: [1] Page Title | https://url.com
    Active tab marked with *. Use ds_tab(number_or_name) to switch.
    Requires browser running with --remote-debugging-port=9222.

    Args:
        prev_ok: Was your LAST MCP call successful? MUST answer: "yes", "no", or "unknown".
    """
    if not _is_cdp_available():
        return "CDP not available. Browser not running with --remote-debugging-port=9222."
    tabs = _cdp_tabs()
    page_tabs = [t for t in tabs if t.get("type") == "page"]
    lines = []
    for i, t in enumerate(page_tabs, 1):
        active = " *" if t.get("id") == _cdp_active_tab_id else ""
        lines.append(f"[{i}]{active} {t.get('title', '?')} | {t.get('url', '?')}")
    result = "\n".join(lines) if lines else "No tabs found."
    return _with_chars(result)


@mcp.tool()
def ds_tab(identifier: str, prev_ok: str) -> str:
    """Switch to a browser tab by number or name (CDP/browser mode).

    After switching, call ds_update_view() to read the new tab's content.

    Args:
        identifier: Tab number from ds_tabs() (e.g., "3") or part of tab title (e.g., "gmail").
        prev_ok: Was your LAST MCP call successful? MUST answer: "yes", "no", or "unknown".
    """
    global _cdp_active_tab_id
    if not _is_cdp_available():
        return "CDP not available."
    tabs = _cdp_tabs()
    page_tabs = [t for t in tabs if t.get("type") == "page"]
    if not page_tabs:
        return "No tabs found."

    target = None
    # Try as number first
    try:
        idx = int(identifier) - 1
        if 0 <= idx < len(page_tabs):
            target = page_tabs[idx]
    except ValueError:
        pass

    # Try as name match
    if not target:
        search = identifier.lower()
        target = next((t for t in page_tabs if search in t.get("title", "").lower()), None)
        if not target:
            target = next((t for t in page_tabs if search in t.get("url", "").lower()), None)

    if not target:
        return f"No tab matching '{identifier}'. Use ds_tabs() to see available tabs."

    # Activate via CDP
    import websocket as _ws
    ws = _ws.create_connection(target["webSocketDebuggerUrl"], timeout=5)
    ws.send(json.dumps({"id": 1, "method": "Page.bringToFront", "params": {}}))
    ws.recv()
    ws.close()
    _cdp_active_tab_id = target["id"]
    r = f"Switched to: {target.get('title', '?')}"
    _log_action("ds_tab", {"identifier": identifier}, r, prev_ok)
    return r


@mcp.tool()
def ds_navigate(url: str, prev_ok: str) -> str:
    """Navigate the active browser tab to a URL (CDP/browser mode).

    Loads the URL in the current tab. Call ds_wait() after to ensure the page loaded,
    then ds_update_view() to read the new content.

    Args:
        url: Full URL including https:// (e.g., "https://example.com").
        prev_ok: Was your LAST MCP call successful? MUST answer: "yes", "no", or "unknown".
    """
    if not _is_cdp_available():
        return "CDP not available."
    ws = _cdp_ws()
    ws.send(json.dumps({"id": 1, "method": "Page.navigate", "params": {"url": url}}))
    result = json.loads(ws.recv())
    ws.close()
    frame_id = result.get("result", {}).get("frameId", "")
    r = f"Navigated to {url}" if frame_id else f"Navigation sent to {url}"
    _log_action("ds_navigate", {"url": url}, r, prev_ok)
    return r


@mcp.tool()
def ds_wait(prev_ok: str, seconds: float = 2.0) -> str:
    """Wait for a browser page to finish loading (CDP/browser mode).

    Call this after ds_navigate() or ds_click() on a link before reading content.
    Waits for the DOM to reach 'complete' state, or falls back to a timed wait.

    Args:
        prev_ok: Was your LAST MCP call successful? MUST answer: "yes", "no", or "unknown".
        seconds: Maximum seconds to wait (default 2.0, max 10.0).
    """
    seconds = min(seconds, 10.0)
    if not _is_cdp_available():
        time.sleep(seconds)
        return "waited (no CDP)"
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            ws = _cdp_ws()
            ws.send(json.dumps({"id": 1, "method": "Runtime.evaluate", "params": {
                "expression": "document.readyState", "returnByValue": True
            }}))
            state = json.loads(ws.recv()).get("result", {}).get("result", {}).get("value", "")
            ws.close()
            if state == "complete":
                return f"ready ({state})"
        except Exception:
            pass
        time.sleep(0.2)
    return f"timeout after {seconds}s"


@mcp.tool()
def ds_state(prev_ok: str, app: Optional[str] = None) -> str:
    """Get operable UI elements from the accessibility tree (UIA/native app mode only).

    Returns numbered elements with type prefixes:
    [1] [keyboard] "Search Box" @ 100,200 (300x30)  → text input, use ds_text
    [2] [click] "Save" @ 500,600 (80x25)            → button, use ds_click

    NOTE: This reads UIA data. For browsers, use ds_update_view() instead (uses CDP).

    Args:
        prev_ok: Was your LAST MCP call successful? MUST answer: "yes", "no", or "unknown".
        app: Optional app name. If omitted, uses the currently snapped app.
    """
    return _with_chars(_read_file(".a11y.snap", app))


@mcp.tool()
def ds_screen(prev_ok: str, app: Optional[str] = None) -> str:
    """Get all text currently visible in the viewport — nothing hidden or off-screen.

    In browser mode (CDP): extracts only viewport-rendered text from the DOM.
    In native mode (UIA): reads the accessibility screen reader view.

    Use this when you need just the text without the tool list. For text + tools, use ds_update_view().

    Args:
        prev_ok: Was your LAST MCP call successful? MUST answer: "yes", "no", or "unknown".
        app: Optional app name. If omitted, uses the currently snapped app.
    """
    if _is_cdp_available():
        try:
            ws = _cdp_ws()
            # Viewport-only text — leaf blocks only (no duplication)
            js = _VIEWPORT_TEXT_JS
            ws.send(json.dumps({"id": 1, "method": "Runtime.evaluate", "params": {
                "expression": js, "returnByValue": True
            }}))
            text = json.loads(ws.recv()).get("result", {}).get("result", {}).get("value", "")
            ws.close()
            return _with_chars(text.strip()) if text else "(empty page)"
        except Exception:
            pass
    return _with_chars(_read_file(".a11y", app))


@mcp.tool()
def ds_print(prev_ok: str) -> str:
    """Read the ENTIRE page content, not just the viewport (CDP/browser mode only).

    Like a print preview — returns all text on the page from top to bottom.
    Use this when you need to read a full article, documentation page, or any
    content that extends beyond the current viewport.

    For viewport-only text, use ds_screen() instead.

    Args:
        prev_ok: Was your LAST MCP call successful? MUST answer: "yes", "no", or "unknown".
    """
    if not _is_cdp_available():
        return "ds_print requires a browser with CDP (port 9222)."
    try:
        ws = _cdp_ws()
        ws.send(json.dumps({"id": 1, "method": "Runtime.evaluate", "params": {
            "expression": _FULLPAGE_TEXT_JS, "returnByValue": True
        }}))
        text = json.loads(ws.recv()).get("result", {}).get("result", {}).get("value", "")
        ws.close()
        return _with_chars(text.strip()) if text else "(empty page)"
    except Exception as e:
        return f"CDP error: {e}"


@mcp.tool()
def ds_elements(prev_ok: str, app: Optional[str] = None) -> str:
    """Get all interactive elements with type, name, position and automation ID (UIA/native app mode only).

    Type prefixes: [click] = button/link, [keyboard] = text input, [select] = dropdown.
    NOTE: This reads UIA data. For browsers, use ds_update_view() instead (uses CDP).

    Args:
        prev_ok: Was your LAST MCP call successful? MUST answer: "yes", "no", or "unknown".
        app: Optional app name. If omitted, uses the currently snapped app.
    """
    return _with_chars(_read_file(".snap", app))


@mcp.tool()
def ds_query(sql: str, prev_ok: str, app: Optional[str] = None) -> list[dict]:
    """Run a SQL query against the DirectShell element database (UIA/native app mode only).

    The 'elements' table has columns: id, parent_id, depth, role, name, value,
    automation_id, enabled (1/0), offscreen (1/0), x, y, w, h.

    Examples:
        "SELECT name, value FROM elements WHERE role='Edit'"
        "SELECT name FROM elements WHERE role='Button' AND enabled=1"
        "SELECT count(*) as n FROM elements WHERE name LIKE '%unread%'"

    NOTE: Only works for native apps (UIA). Not available in browser/CDP mode.

    Args:
        sql: A SELECT query. Write queries are not allowed.
        prev_ok: Was your LAST MCP call successful? MUST answer: "yes", "no", or "unknown".
        app: Optional app name. If omitted, uses the currently snapped app.
    """
    sql_lower = sql.strip().lower()
    if not sql_lower.startswith("select"):
        raise ValueError("Only SELECT queries are allowed. Use action tools to modify state.")
    return _db_query(sql, app)


@mcp.tool()
def ds_find(name_pattern: str, prev_ok: str, app: Optional[str] = None) -> list[dict]:
    """Find UI elements by name pattern (UIA/native app mode only).

    Uses SQL LIKE matching (% = wildcard). Examples:
        ds_find("Save")       → exact match
        ds_find("%invoice%")  → contains "invoice"

    NOTE: Only works for native apps (UIA). Not available in browser/CDP mode.

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
def ds_events(prev_ok: str, app: Optional[str] = None, mark_consumed: bool = True) -> dict:
    """Get UI events since last check — what changed on screen (UIA/native app mode only).

    Returns condensed digest: window opens, value changes, toggle states, DOM mutations.
    Much lighter than re-reading the full screen (~50 tokens vs ~5000).

    NOTE: Only works for native apps (UIA). Not available in browser/CDP mode.

    Args:
        app: Optional app name. If omitted, uses the currently snapped app.
        mark_consumed: If True (default), events won't appear again on next call.
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
def ds_click(element_name: str, prev_ok: str, app: Optional[str] = None) -> str:
    """Click a UI element by its exact name. Works in both browser (CDP) and native (UIA) mode.

    The element name must match exactly as shown in ds_update_view() or ds_state().

    Args:
        element_name: The exact element name (e.g., "Submit", "Neuer Chat").
        app: Optional app name (UIA mode only).
        prev_ok: Was your LAST MCP call successful? MUST answer: "yes", "no", or "unknown".
    """
    if _is_cdp_available():
        _cdp_click(element_name)
        r = f"ok cdp{_learning_hint()}"
    else:
        action_id = _inject_action("click", target=element_name, app=app)
        r = f"ok #{action_id}{_learning_hint()}"
    _log_action("ds_click", {"element_name": element_name}, r, prev_ok)
    return r


@mcp.tool()
def ds_text(value: str, target: str, prev_ok: str, app: Optional[str] = None) -> str:
    """Set text in a named input field. PREFERRED over ds_type — instant and reliable.

    First focuses the target field, then types the text via simulated keyboard events.
    In browser mode: uses CDP Input.dispatchKeyEvent (real keyboard simulation).
    In native mode: uses UIA ValuePattern (instant set, no character-by-character typing).

    Args:
        value: The text to insert (e.g., "Hello world").
        target: Exact name of the input field from ds_update_view() (e.g., "Search", "Message").
        app: Optional app name (UIA mode only).
        prev_ok: Was your LAST MCP call successful? MUST answer: "yes", "no", or "unknown".
    """
    if _is_cdp_available():
        _cdp_type(value, target)
        r = f"ok cdp{_learning_hint()}"
    else:
        action_id = _inject_action("text", text=value, target=target, app=app)
        r = f"ok #{action_id}{_learning_hint()}"
    _log_action("ds_text", {"value": value, "target": target}, r, prev_ok)
    return r


@mcp.tool()
def ds_type(text: str, prev_ok: str, app: Optional[str] = None) -> str:
    """Type text into the currently focused element. Use ds_text() instead when possible.

    Only use ds_type when ds_text doesn't work (Discord chat, terminals, canvas apps).
    Types into whatever currently has keyboard focus — no target parameter.
    Use \\t for Tab, \\n for Enter within the text.

    Args:
        text: The text to type (e.g., "hello\\n" to type hello and press Enter).
        app: Optional app name (UIA mode only).
        prev_ok: Was your LAST MCP call successful? MUST answer: "yes", "no", or "unknown".
    """
    _log_action("ds_type", {"text": text}, "", prev_ok)
    text = text.replace("\\t", "\t").replace("\\n", "\n").replace("\\r", "\r")
    if _is_cdp_available():
        _cdp_type(text)
        return f"ok cdp{_learning_hint()}"
    action_id = _inject_action("type", text=text, app=app)
    return f"ok #{action_id}{_learning_hint()}"


@mcp.tool()
def ds_key(combo: str, prev_ok: str, app: Optional[str] = None) -> str:
    """Press a keyboard shortcut. Works in both browser (CDP) and native (UIA) mode.

    Common combos: "enter", "tab", "escape", "pagedown", "pageup",
    "ctrl+a", "ctrl+c", "ctrl+v", "ctrl+s", "alt+arrowleft" (back), "f5" (refresh).

    Args:
        combo: Key combination (e.g., "ctrl+shift+s"). Modifiers: ctrl, alt, shift.
        app: Optional app name (UIA mode only).
        prev_ok: Was your LAST MCP call successful? MUST answer: "yes", "no", or "unknown".
    """
    if _is_cdp_available():
        _cdp_key(combo)
        r = f"ok cdp{_learning_hint()}"
    else:
        action_id = _inject_action("key", text=combo, app=app)
        r = f"ok #{action_id}{_learning_hint()}"
    _log_action("ds_key", {"combo": combo}, r, prev_ok)
    return r


@mcp.tool()
def ds_scroll(direction: str, prev_ok: str, amount: int = 1, app: Optional[str] = None) -> str:
    """Scroll the page. Works in both browser (CDP) and native (UIA) mode.

    Each notch scrolls ~3 lines. Use ds_update_view() after scrolling to see new content.

    Args:
        direction: "up", "down", "left", or "right".
        amount: Number of scroll notches (default 1, use 3-5 for a full page).
        app: Optional app name (UIA mode only).
        prev_ok: Was your LAST MCP call successful? MUST answer: "yes", "no", or "unknown".
    """
    if direction not in ("up", "down", "left", "right"):
        raise ValueError(f"Invalid direction: {direction}. Use up/down/left/right.")
    if _is_cdp_available():
        _cdp_scroll(direction, amount)
        r = f"ok cdp{_learning_hint()}"
    else:
        ids = []
        for i in range(amount):
            is_last = (i == amount - 1)
            ids.append(_inject_action("scroll", text=direction, app=app, wait=is_last))
        r = f"ok #{ids[-1]}"
    _log_action("ds_scroll", {"direction": direction, "amount": amount}, r, prev_ok)
    return r


@mcp.tool()
def ds_batch(actions: list[dict], prev_ok: str, app: Optional[str] = None) -> str:
    """Execute multiple actions in sequence. Works in both browser (CDP) and native (UIA) mode.

    Use this to chain clicks, text input, and key presses without round-trips.

    Each action dict needs: "action" (click/text/key), plus "target" and/or "text".
    Example: [{"action": "click", "target": "Email"}, {"action": "text", "text": "hi@test.com", "target": "Email"}, {"action": "key", "text": "tab"}]

    Args:
        actions: List of action dicts. Each needs "action" (click/text/key) at minimum.
        app: Optional app name (UIA mode only).
        prev_ok: Was your LAST MCP call successful? MUST answer: "yes", "no", or "unknown".
    """
    if _is_cdp_available():
        for act in actions:
            a = act.get("action", "click")
            if a == "click":
                _cdp_click(act.get("target", ""))
            elif a in ("type", "text"):
                _cdp_type(act.get("text", ""), act.get("target", ""))
            elif a == "key":
                _cdp_key(act.get("text", ""))
        r = f"ok {len(actions)} actions cdp{_learning_hint()}"
    else:
        total = len(actions)
        last_id = 0
        for i, act in enumerate(actions):
            is_last = (i == total - 1)
            last_id = _inject_action(
                action=act.get("action", "text"),
                text=act.get("text", ""),
                target=act.get("target", ""),
                app=app,
                wait=is_last,
            )
        r = f"ok {total} actions, last #{last_id}{_learning_hint()}"
    _log_action("ds_batch", {"actions": actions}, r, prev_ok)
    return r


# ---------------------------------------------------------------------------
# PROFILE TOOLS — Learn and remember applications
# ---------------------------------------------------------------------------

@mcp.tool()
def ds_profile_list(prev_ok: str) -> dict:
    """List all known application profiles and previously snapped apps.

    Shows which apps have been seen before and which have
    learned profiles with semantic element mappings.

    Args:
        prev_ok: Was your LAST MCP call successful? MUST answer: "yes", "no", or "unknown".
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
    prev_ok: str,
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
def ds_profile_get(app: str, prev_ok: str) -> dict:
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
# SCREEN EXTRACTION — Deterministic, no external LLM
# ---------------------------------------------------------------------------

# In-memory store for active tools
_active_view = {"screen": "", "tools": [], "data": "", "raw_tools": []}
_cdp_labels: set = set()  # CDP element labels from last update_view


# NOTE: Gemini translator path removed — ds_update_view is now fully deterministic (no LLM).


def _cdp_extract() -> dict:
    """Deterministic CDP extraction — viewport-visible tools + page text. No LLM."""
    ws = _cdp_ws()

    js = r'''(() => {
        const vw = window.innerWidth, vh = window.innerHeight;
        const SELECTORS = '__SELECTORS__';
        const tools = [];
        const labelCounts = new Map();

        function nextId(prefix = '') {
            try {
                const w = window;
                w.__ds_mcp_seq = (w.__ds_mcp_seq || 0) + 1;
                return `${prefix}ds${w.__ds_mcp_seq}`;
            } catch (_) {
                return `${prefix}ds${Math.floor(Math.random()*1e9)}`;
            }
        }

        function labelFor(el) {
            const raw =
                el.getAttribute?.('aria-label') ||
                el.placeholder ||
                (el.textContent || '').trim() ||
                el.name ||
                el.id ||
                '';
            return (raw || '').trim().replace(/\s+/g, ' ').slice(0, 120);
        }

        function isVisible(el, r, offX, offY) {
            if (!r.width || !r.height) return false;
            const x = offX + r.x + r.width/2;
            const y = offY + r.y + r.height/2;
            if (x < 0 || y < 0 || x > vw || y > vh) return false;
            try {
                const s = getComputedStyle(el);
                if (s.opacity === '0' || s.visibility === 'hidden' || s.display === 'none') return false;
                if (s.pointerEvents === 'none') return false;
            } catch (_) {}
            if (el.disabled) return false;
            return true;
        }

        function actionFor(el) {
            const tag = (el.tagName || '').toLowerCase();
            const role = (el.getAttribute && el.getAttribute('role')) || '';
            if (tag === 'select') return 'click'; // treat as click (select handling is app-specific)
            if (['input', 'textarea'].includes(tag)) return 'type';
            if (['textbox', 'searchbox', 'combobox'].includes(role)) return 'type';
            if (el.isContentEditable) return 'type';
            return 'click';
        }

        function cssPath(el) {
            // Best-effort selector for top-level DOM only (doesn't pierce iframes/shadow roots).
            if (!el || !el.tagName) return '';
            if (el.id) return `#${CSS.escape(el.id)}`;
            const parts = [];
            let cur = el;
            while (cur && cur.nodeType === 1 && parts.length < 10) {
                const tag = cur.tagName.toLowerCase();
                if (tag === 'html') break;
                let nth = 1;
                let sib = cur;
                while ((sib = sib.previousElementSibling)) {
                    if (sib.tagName === cur.tagName) nth++;
                }
                parts.unshift(`${tag}:nth-of-type(${nth})`);
                cur = cur.parentElement;
                if (cur && cur.tagName && cur.tagName.toLowerCase() === 'body') {
                    parts.unshift('body');
                    break;
                }
            }
            return parts.join(' > ');
        }

        function pushTool(el, offX, offY, prefix, allowSelector) {
            const base = labelFor(el);
            if (!base) return;
            const r = el.getBoundingClientRect();
            if (!isVisible(el, r, offX, offY)) return;

            const count = (labelCounts.get(base) || 0) + 1;
            labelCounts.set(base, count);
            const display = count === 1 ? base : `${base} (${count})`;

            let dsid = '';
            try {
                dsid = el.getAttribute('data-ds-mcp') || '';
                if (!dsid) {
                    dsid = nextId(prefix);
                    el.setAttribute('data-ds-mcp', dsid);
                }
            } catch (_) {}

            const x = offX + r.x + r.width/2;
            const y = offY + r.y + r.height/2;

            tools.push({
                action: actionFor(el),
                element: display,
                dsid: dsid,
                selector: (allowSelector ? cssPath(el) : ''),
                x: x,
                y: y
            });
        }

        function collectInDocument(doc, offX, offY, prefix, allowSelector) {
            try {
                doc.querySelectorAll(SELECTORS).forEach(el => pushTool(el, offX, offY, prefix, allowSelector));
            } catch (_) {}

            // Traverse open shadow roots and collect interactives inside them.
            try {
                const tw = doc.createTreeWalker(doc, NodeFilter.SHOW_ELEMENT);
                while (tw.nextNode()) {
                    const node = tw.currentNode;
                    if (node && node.shadowRoot) {
                        try {
                            node.shadowRoot.querySelectorAll(SELECTORS).forEach(el => pushTool(el, offX, offY, prefix, false));
                        } catch (_) {}
                    }
                }
            } catch (_) {}
        }

        // 1) Main document
        collectInDocument(document, 0, 0, '', true);

        // 2) Same-origin iframes (best effort): extract + offset coords by iframe rect
        const iframes = Array.from(document.querySelectorAll('iframe'));
        for (let i = 0; i < iframes.length; i++) {
            const iframe = iframes[i];
            try {
                const idoc = iframe.contentDocument;
                if (!idoc) continue;
                const ir = iframe.getBoundingClientRect();
                collectInDocument(idoc, ir.x, ir.y, `f${i}_`, false);
            } catch (_) {}
        }

        // Viewport-only text: only LEAF block elements (no block children = no duplication)
        const blockTags = new Set(['P','H1','H2','H3','H4','H5','H6','LI','TD','TH','DT','DD','PRE','BLOCKQUOTE','FIGCAPTION','ARTICLE','SECTION','HEADER','FOOTER','MAIN','ASIDE','NAV','DIV','SPAN','A','LABEL']);
        const blocks = document.querySelectorAll('p,h1,h2,h3,h4,h5,h6,li,td,th,dt,dd,pre,blockquote,figcaption,label,span,div,a,article,section');
        const textParts = [], textSeen = new Set();
        for (const el of blocks) {
            const r = el.getBoundingClientRect();
            if (!r.height || r.bottom < 0 || r.top > vh) continue;
            if (r.height > vh * 1.5) continue;
            const s = getComputedStyle(el);
            if (s.opacity === '0' || s.visibility === 'hidden' || s.display === 'none') continue;
            // Skip if has block-level children in viewport (let children handle text)
            let hasBlockChild = false;
            for (const child of el.children) {
                if (blockTags.has(child.tagName) && child.getBoundingClientRect().height > 0) {
                    const cs = getComputedStyle(child);
                    if (cs.display !== 'inline' && cs.display !== 'inline-block') { hasBlockChild = true; break; }
                }
            }
            if (hasBlockChild) continue;
            const t = el.innerText?.trim();
            if (!t || t.length < 2 || textSeen.has(t)) continue;
            textSeen.add(t);
            textParts.push(t);
        }
        const text = textParts.join('\n');
        return JSON.stringify({tools: tools, text: text});
    })()'''.replace('__SELECTORS__', _INTERACTIVE_SELECTORS)

    ws.send(json.dumps({"id": 1, "method": "Runtime.evaluate", "params": {"expression": js, "returnByValue": True}}))
    data = json.loads(json.loads(ws.recv())["result"]["result"]["value"])
    ws.close()

    tools = []
    for t in data.get("tools", []):
        if not isinstance(t, dict):
            continue
        action = t.get("action") or "click"
        element = t.get("element") or ""
        if not element:
            continue
        tools.append({
            "action": action,
            "element": element,
            "description": "",
            "dsid": t.get("dsid") or "",
            "selector": t.get("selector") or "",
            "x": t.get("x"),
            "y": t.get("y"),
        })

    return {"tools": tools, "text": (data.get("text") or "").strip(), "title": ""}


@mcp.tool()
def ds_update_view(prev_ok: str, app: Optional[str] = None) -> str:
    """PRIMARY TOOL — Read what's on screen and get available actions.

    Returns TWO sections separated by '---':
    1. VISIBLE TEXT — only what a human can see in the viewport right now
    2. NUMBERED TOOLS — clickable/typeable elements, e.g. [1] click|Submit, [2] type|Search

    Use ds_act(N) to execute tool N from the list. For type tools, pass text: ds_act(2, text="query").

    Works in both browser (CDP) and native (UIA) mode automatically.
    Call this FIRST in any workflow, and AFTER every action to see the result.

    Args:
        app: Optional app name (UIA mode only).
        prev_ok: Was your LAST MCP call successful? MUST answer: "yes", "no", or "unknown".
    """
    _log_action("ds_update_view", {}, "", prev_ok)
    global _active_view, _cdp_labels, _cdp_tool_map

    # --- Deterministic CDP path (only for browser apps) ---
    cdp = None
    if _is_cdp_available():
        try:
            cdp = _cdp_extract()
        except Exception:
            cdp = None

    if cdp and cdp["tools"]:
        _active_view = {"screen": cdp["text"], "tools": cdp["tools"], "data": ""}
        _cdp_labels = {t["element"] for t in cdp["tools"]}
        _cdp_tool_map = {t["element"]: t for t in cdp["tools"] if isinstance(t, dict) and t.get("element")}

        tool_lines = []
        for i, t in enumerate(cdp["tools"], 1):
            tool_lines.append(f"[{i}] {t['action']}|{t['element']}")

        result = cdp["text"] + "\n---\n" + "\n".join(tool_lines)
        return _with_chars(result)

    # --- Fallback: UIA a11y (native apps without CDP) ---
    a11y = _read_file(".a11y", app)
    snap = _read_file(".a11y.snap", app)

    # Parse snap elements directly (deterministic, no LLM)
    tools = []
    for line in snap.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if '"' in line:
            name = line.split('"')[1]
            action = "type" if line.startswith("[keyboard]") else "click"
            tools.append({"action": action, "element": name, "description": ""})

    _active_view = {"screen": a11y, "tools": tools, "data": ""}
    _cdp_labels = set()
    _cdp_tool_map = {}

    tool_lines = []
    for i, t in enumerate(tools, 1):
        tool_lines.append(f"[{i}] {t['action']}|{t['element']}")

    # Extract text content from a11y
    content = ""
    in_content = False
    for line in a11y.splitlines():
        if line.startswith("## Content"):
            in_content = True
            continue
        if in_content and line.startswith("##"):
            break
        if in_content and line.strip():
            content += line.strip() + "\n"

    result = (content.strip() or "(no text)") + "\n---\n" + "\n".join(tool_lines)
    return _with_chars(result)


    # --- OLD: Gemini translator path (kept for reference) ---
    # raw_response = _call_translator(a11y, snap)
    # parsed = _parse_translator_response(raw_response)
    # _active_view = {"screen": parsed["screen"], "tools": parsed["tools"], "data": parsed["data"]}
    # tool_lines = []
    # for i, t in enumerate(parsed["tools"], 1):
    #     tool_lines.append(f"  [{i}] {t['action']}|{t['element']}| {t['description']}")
    # app_name = app or _get_snapped_app()
    # learnings_dir = PROFILES_DIR / "learnings"
    # available_learnings = []
    # if learnings_dir.is_dir() and app_name:
    #     prefix = app_name.lower() + "_"
    #     for f in sorted(learnings_dir.glob(f"{prefix}*.md")):
    #         context = f.stem[len(prefix):]
    #         available_learnings.append(context)
    # result = {
    #     "screen": parsed["screen"],
    #     "tools": "\n".join(tool_lines),
    #     "tool_count": len(parsed["tools"]),
    #     "data": parsed["data"],
    # }
    # if available_learnings:
    #     result["learnings"] = available_learnings
    #     result["learnings_hint"] = f"Read learnings BEFORE acting: ds_learn('{app_name}', '<context>')"
    # else:
    #     result["learnings"] = []
    #     result["learnings_hint"] = (
    #         f"No learnings yet for '{app_name}'. After completing actions, "
    #         f"save what you learned: ds_learn('{app_name}', '<context>', append='your insight here')"
    #     )
    # return result


@mcp.tool()
def ds_learn(app: str, context: str, prev_ok: str, append: Optional[str] = None) -> dict:
    """Read or save tips/quirks for an app. Persists across sessions.

    Save what you learn about an app (e.g., "Discord: must use ds_type for chat, ds_text doesn't work").
    Read before interacting with an app to recall past learnings.

    Args:
        app: Application name (e.g., "opera", "discord", "chatgpt").
        context: Topic (e.g., "general", "input", "navigation").
        append: Text to save. Omit to read existing learnings.
    """
    learnings_dir = PROFILES_DIR / "learnings"
    learnings_dir.mkdir(parents=True, exist_ok=True)
    filepath = learnings_dir / f"{app.lower()}_{context.lower()}.md"

    if append:
        with open(filepath, "a", encoding="utf-8") as f:
            f.write(f"\n{append}\n")

    if filepath.exists():
        content = filepath.read_text(encoding="utf-8")
    else:
        content = f"No learnings yet for {app}/{context}"

    return {"app": app, "context": context, "file": str(filepath), "content": content}


@mcp.tool()
def ds_act(tool_number: int, prev_ok: str, text: Optional[str] = None, app: Optional[str] = None) -> str:
    """Execute a numbered tool from ds_update_view() output.

    ds_update_view() returns tools like: [1] click|Submit  [2] type|Search
    Call ds_act(1) to click Submit, or ds_act(2, text="query") to type in Search.

    IMPORTANT: Always call ds_update_view() first — ds_act only works with the latest tool list.

    Args:
        tool_number: The tool number (1-based) from ds_update_view output.
        text: Text to type (required when the tool is a 'type' action, ignored for 'click').
        app: Optional app name (UIA mode only).
        prev_ok: Was your LAST MCP call successful? MUST answer: "yes", "no", or "unknown".
    """
    if not _active_view["tools"]:
        raise ValueError("No active tools. Call ds_update_view() first.")

    idx = tool_number - 1
    if idx < 0 or idx >= len(_active_view["tools"]):
        raise ValueError(f"Tool {tool_number} not found. Available: 1-{len(_active_view['tools'])}")

    tool = _active_view["tools"][idx]
    element = tool["element"]
    action_type = tool["action"]

    if action_type == "type":
        if not text:
            raise ValueError(f"Tool {tool_number} is a type action — provide text parameter.")
        if _is_cdp_available():
            _cdp_type_to_tool(text, tool)
            r = f"ok cdp{_learning_hint()}"
        else:
            action_id = _inject_action("type", text=text, target=element, app=app)
            r = f"ok #{action_id}{_learning_hint()}"
    else:
        if _is_cdp_available():
            _cdp_click_tool(tool)
            r = f"ok cdp{_learning_hint()}"
        else:
            action_id = _inject_action("click", target=element, app=app)
            r = f"ok #{action_id}{_learning_hint()}"
    _log_action("ds_act", {"tool_number": tool_number, "element": element, "action": action_type, "text": text}, r, prev_ok)
    return r


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print(f"DirectShell MCP Bridge — profiles: {PROFILES_DIR}", file=sys.stderr)
    mcp.run()
