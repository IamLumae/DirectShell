# DirectShell Tip Engine — Contextual tip injection for MCP tool responses
# Copyright (C) 2026  Martin Gehrken (IamLumae)
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Automatically injects 0-3 contextual micro-tips into MCP tool responses.
Zero LLM cooperation required. Deterministic pattern matching. <1ms overhead.

Usage from server.py:
    from tip_engine import tip_engine
    tip_engine.update_context("ds_click", {"element_name": "Submit"}, "ok cdp", "yes")
    tips_block = tip_engine.get_tips_block("ds_update_view")
    if tips_block:
        result += tips_block
"""

import json
import re
import time
import hashlib
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


# ---------------------------------------------------------------------------
# Data Model
# ---------------------------------------------------------------------------

@dataclass
class Condition:
    """A single match condition for a tip."""
    type: str       # app_is, app_is_not, url_contains, url_regex, mode_is,
                    # failure_streak_gte, tool_in_recent, tool_not_in_recent
    value: str      # the value to match against

    @staticmethod
    def from_dict(d: dict) -> "Condition":
        return Condition(type=d["type"], value=d["value"])


@dataclass
class Tip:
    """A single injectable tip record."""
    id: str
    text: str                               # full tip text (one line, imperative)
    text_short: str = ""                    # compressed reminder (after first show)
    app: str = "*"                          # app filter ("*" = universal)
    conditions: list[Condition] = field(default_factory=list)
    inject_on: list[str] = field(default_factory=lambda: ["ds_update_view"])
    priority: int = 50                      # lower = higher priority
    tier: int = 2                           # 1=error prevention, 2=context, 3=general
    cooldown_s: int = 300                   # seconds before re-showing
    max_per_session: int = 5                # 0 = unlimited
    warn_text: str = ""                     # escalated text on failure streaks
    warn_after_streak: int = 0              # failure streak threshold for warn_text (0=disabled)

    @staticmethod
    def from_dict(d: dict) -> "Tip":
        conditions = [Condition.from_dict(c) for c in d.get("conditions", [])]
        return Tip(
            id=d["id"],
            text=d["text"],
            text_short=d.get("text_short", ""),
            app=d.get("app", "*"),
            conditions=conditions,
            inject_on=d.get("inject_on", ["ds_update_view"]),
            priority=d.get("priority", 50),
            tier=d.get("tier", 2),
            cooldown_s=d.get("cooldown_s", 300),
            max_per_session=d.get("max_per_session", 5),
            warn_text=d.get("warn_text", ""),
            warn_after_streak=d.get("warn_after_streak", 0),
        )


# ---------------------------------------------------------------------------
# Session Context
# ---------------------------------------------------------------------------

@dataclass
class SessionContext:
    """In-memory state tracked across tool calls within one session."""
    app: Optional[str] = None               # current snapped app
    url: Optional[str] = None               # current URL (browser mode)
    mode: Optional[str] = None              # "browser" or "native"
    last_tools: deque = field(default_factory=lambda: deque(maxlen=10))
    last_params: deque = field(default_factory=lambda: deque(maxlen=10))
    consecutive_failures: int = 0           # prev_ok="no" streak
    tips_shown_ts: dict = field(default_factory=dict)     # tip_id -> last shown timestamp
    tips_shown_count: dict = field(default_factory=dict)  # tip_id -> session show count
    adoption_tracker: dict = field(default_factory=dict)  # tip_id -> consecutive correct actions


# ---------------------------------------------------------------------------
# Tip Engine
# ---------------------------------------------------------------------------

# Tools that receive tip injection (orientation tools only)
ORIENTATION_TOOLS = {
    "ds_update_view", "ds_screen", "ds_print",
    "ds_tabs", "ds_navigate", "ds_focus",
}

MAX_TIPS_PER_RESPONSE = 3


class TipEngine:
    """Contextual tip injection engine for DirectShell MCP server."""

    def __init__(self, tips_dir: Optional[Path] = None):
        self._tips: list[Tip] = []
        self._tips_dir = tips_dir
        self._ctx = SessionContext()
        self._compiled_regex: dict[str, re.Pattern] = {}  # cache for url_regex patterns
        self._initialized = False

    def init(self, tips_dir: Path):
        """Initialize with tips directory. Called once at server startup."""
        self._tips_dir = tips_dir
        self._load_tips()
        self._initialized = True

    def _load_tips(self):
        """Load all tip JSONL files from the tips directory."""
        self._tips = []
        if not self._tips_dir or not self._tips_dir.exists():
            return
        for f in sorted(self._tips_dir.glob("*.jsonl")):
            if f.name.startswith("_candidates"):
                continue  # skip candidate files
            try:
                for line in f.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        d = json.loads(line)
                        self._tips.append(Tip.from_dict(d))
                    except (json.JSONDecodeError, KeyError):
                        continue
            except Exception:
                continue

    def reload(self):
        """Hot-reload tips from disk."""
        self._load_tips()

    # ------------------------------------------------------------------
    # Context Update (called on EVERY tool call)
    # ------------------------------------------------------------------

    def update_context(self, tool_name: str, params: dict, result: str, prev_ok: str):
        """Update session context from the latest tool call. Must be fast."""
        if not self._initialized:
            return

        ctx = self._ctx

        # Track tools
        ctx.last_tools.append(tool_name)
        ctx.last_params.append(params)

        # Track failure streak
        if prev_ok == "no":
            ctx.consecutive_failures += 1
        elif prev_ok == "yes":
            ctx.consecutive_failures = 0
        # "unknown" doesn't change the streak

        # Extract app from ds_focus
        if tool_name == "ds_focus" and "app" in params:
            app_val = params["app"]
            if "unsnap" in str(app_val).lower():
                ctx.app = None
                ctx.url = None
                ctx.mode = None
            else:
                ctx.app = str(app_val).lower()
                ctx.mode = "native"
                ctx.url = None

        # Extract URL from ds_navigate or URL sync
        if tool_name in ("ds_navigate", "_url_sync") and "url" in params:
            ctx.url = params["url"]
            ctx.mode = "browser"

        # Detect browser mode from ds_tabs / ds_update_view context
        if tool_name in ("ds_tabs", "ds_tab"):
            ctx.mode = "browser"

        # Update adoption tracker: if a tip was shown and the LLM is now
        # doing the right thing, increment adoption counter
        self._update_adoption(tool_name, params, prev_ok)

    def _update_adoption(self, tool_name: str, params: dict, prev_ok: str):
        """Track whether the LLM follows tip advice (adoption) or regresses."""
        # For each tip that was recently shown, check if LLM follows advice
        # This is heuristic: we check if the current action aligns with tip content
        # For now, adoption is based on simple prev_ok success after tip display
        if prev_ok == "yes":
            for tip_id in list(self._ctx.adoption_tracker.keys()):
                self._ctx.adoption_tracker[tip_id] = \
                    self._ctx.adoption_tracker.get(tip_id, 0) + 1
        elif prev_ok == "no":
            # Regression — reset adoption for recently shown tips
            for tip_id in list(self._ctx.adoption_tracker.keys()):
                self._ctx.adoption_tracker[tip_id] = 0

    # ------------------------------------------------------------------
    # Condition Matching
    # ------------------------------------------------------------------

    def _matches_condition(self, cond: Condition) -> bool:
        """Evaluate a single condition against current context."""
        ctx = self._ctx
        t = cond.type
        v = cond.value

        if t == "app_is":
            return ctx.app is not None and v.lower() in ctx.app.lower()

        if t == "app_is_not":
            return ctx.app is None or v.lower() not in ctx.app.lower()

        if t == "url_contains":
            return ctx.url is not None and v.lower() in ctx.url.lower()

        if t == "url_regex":
            if v not in self._compiled_regex:
                try:
                    self._compiled_regex[v] = re.compile(v, re.IGNORECASE)
                except re.error:
                    return False
            return ctx.url is not None and bool(self._compiled_regex[v].search(ctx.url))

        if t == "mode_is":
            return ctx.mode == v

        if t == "failure_streak_gte":
            try:
                return ctx.consecutive_failures >= int(v)
            except ValueError:
                return False

        if t == "tool_in_recent":
            return v in ctx.last_tools

        if t == "tool_not_in_recent":
            return v not in ctx.last_tools

        return False  # unknown condition type

    def _tip_matches(self, tip: Tip, current_tool: str) -> bool:
        """Check if a tip should fire for the current tool call."""
        # Must be an injection point for this tip
        if current_tool not in tip.inject_on:
            return False

        # App filter
        if tip.app != "*":
            if self._ctx.app is None:
                return False
            if tip.app.lower() not in self._ctx.app.lower():
                return False

        # All conditions must match (AND logic)
        for cond in tip.conditions:
            if not self._matches_condition(cond):
                return False

        return True

    # ------------------------------------------------------------------
    # Selection & Injection
    # ------------------------------------------------------------------

    def get_tips(self, current_tool: str) -> list[Tip]:
        """Get the top tips to inject for the current tool response."""
        if not self._initialized or not self._tips:
            return []

        now = time.time()
        candidates = []

        for tip in self._tips:
            # Skip if not matching
            if not self._tip_matches(tip, current_tool):
                continue

            # Skip if in cooldown
            last_shown = self._ctx.tips_shown_ts.get(tip.id, 0)
            if now - last_shown < tip.cooldown_s:
                continue

            # Skip if max_per_session exceeded
            if tip.max_per_session > 0:
                shown_count = self._ctx.tips_shown_count.get(tip.id, 0)
                if shown_count >= tip.max_per_session:
                    continue

            # Skip if adopted (3+ consecutive correct actions after tip shown)
            adoption = self._ctx.adoption_tracker.get(tip.id, 0)
            if adoption >= 3 and self._ctx.consecutive_failures == 0:
                continue

            candidates.append(tip)

        # Sort by tier first (ascending), then priority (ascending)
        candidates.sort(key=lambda t: (t.tier, t.priority))

        # Take top N
        selected = candidates[:MAX_TIPS_PER_RESPONSE]

        # Record shown state
        for tip in selected:
            self._ctx.tips_shown_ts[tip.id] = now
            self._ctx.tips_shown_count[tip.id] = \
                self._ctx.tips_shown_count.get(tip.id, 0) + 1
            # Start adoption tracking for this tip
            if tip.id not in self._ctx.adoption_tracker:
                self._ctx.adoption_tracker[tip.id] = 0

        return selected

    def _format_tip_text(self, tip: Tip) -> str:
        """Get the appropriate text for a tip based on context."""
        ctx = self._ctx

        # Escalated warning on failure streak
        if (tip.warn_text and tip.warn_after_streak > 0
                and ctx.consecutive_failures >= tip.warn_after_streak):
            return tip.warn_text

        # Progressive disclosure: use short text after first show
        shown_count = ctx.tips_shown_count.get(tip.id, 0)
        if shown_count > 1 and tip.text_short:
            return tip.text_short

        return tip.text

    def get_tips_block(self, current_tool: str) -> str:
        """Get the formatted tips block to append to a tool response.
        Returns empty string if no tips match (zero token overhead)."""
        tips = self.get_tips(current_tool)
        if not tips:
            return ""

        lines = []
        for tip in tips:
            lines.append(self._format_tip_text(tip))

        return "\n~~~tips\n" + "\n".join(lines) + "\n~~~"

    # ------------------------------------------------------------------
    # ds_learn Integration
    # ------------------------------------------------------------------

    def ingest_learning(self, app: str, context: str, text: str):
        """Auto-index a ds_learn() call into a tip record."""
        if not self._initialized or not self._tips_dir:
            return

        # Generate deterministic ID from content
        h = hashlib.md5(f"{app}:{context}:{text}".encode()).hexdigest()[:8]
        tip_id = f"learned_{app.lower()}_{context.lower()}_{h}"

        # Check for duplicate
        for existing in self._tips:
            if existing.id == tip_id:
                return  # already exists

        # Build tip record
        tip_dict = {
            "id": tip_id,
            "text": text.strip(),
            "app": app.lower(),
            "conditions": [],
            "inject_on": ["ds_update_view", "ds_screen"],
            "priority": 40,
            "tier": 2,
            "cooldown_s": 300,
            "max_per_session": 3,
        }

        # Write to JSONL file
        self._tips_dir.mkdir(parents=True, exist_ok=True)
        tip_file = self._tips_dir / f"{app.lower()}.jsonl"
        try:
            with open(tip_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(tip_dict, ensure_ascii=False) + "\n")
        except Exception:
            return

        # Hot-reload into memory
        self._tips.append(Tip.from_dict(tip_dict))


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

tip_engine = TipEngine()
