#!/usr/bin/env python3
# DirectShell Tip Miner — Extract tip candidates from action logs
# Copyright (C) 2026  Martin Gehrken (IamLumae)
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Offline script that analyzes JSONL action logs and generates tip candidates
from failure→recovery patterns.

Usage:
    python tip_miner.py                           # uses default paths
    python tip_miner.py --logs /path/to/action_log --out /path/to/tips/_candidates.jsonl

The script:
1. Parses all JSONL action log files
2. Extracts failure chains (consecutive prev_ok=no runs)
3. Finds recovery actions (first prev_ok=yes after chain)
4. Clusters patterns by (app, failed_tool, recovery_tool)
5. Scores by impact (frequency × clarity × recency)
6. Outputs candidate tip records to _candidates.jsonl
"""

import json
import sys
import math
import hashlib
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Optional


def _discover_paths():
    """Find default paths for action logs and tips output."""
    # Check for --logs and --out CLI args
    logs_dir = None
    out_file = None
    for i, arg in enumerate(sys.argv):
        if arg == "--logs" and i + 1 < len(sys.argv):
            logs_dir = Path(sys.argv[i + 1])
        if arg == "--out" and i + 1 < len(sys.argv):
            out_file = Path(sys.argv[i + 1])

    if not logs_dir:
        # Default: look for ds_profiles via breadcrumb or dev fallback
        import os
        local_app = os.environ.get("LOCALAPPDATA", "")
        if local_app:
            breadcrumb = Path(local_app) / "DirectShell" / "profiles_path.txt"
            if breadcrumb.exists():
                try:
                    path = breadcrumb.read_text(encoding="utf-8").strip()
                    if path:
                        logs_dir = Path(path) / "action_log"
                except Exception:
                    pass
        if not logs_dir:
            logs_dir = Path(__file__).resolve().parent.parent / "target" / "release" / "ds_profiles" / "action_log"

    if not out_file:
        out_file = Path(__file__).resolve().parent / "tips" / "_candidates.jsonl"

    return logs_dir, out_file


def parse_logs(logs_dir: Path) -> list[dict]:
    """Parse all JSONL log files into a flat list of action entries."""
    entries = []
    if not logs_dir.exists():
        print(f"Log directory not found: {logs_dir}")
        return entries

    for f in sorted(logs_dir.glob("*.jsonl")):
        try:
            for line in f.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                    entry["_file"] = f.name
                    entries.append(entry)
                except json.JSONDecodeError:
                    continue
        except Exception:
            continue

    print(f"Parsed {len(entries)} actions from {len(list(logs_dir.glob('*.jsonl')))} log files")
    return entries


def extract_failure_chains(entries: list[dict]) -> list[dict]:
    """Extract failure→recovery pairs from the action stream.

    A failure chain is 1+ consecutive prev_ok=no entries followed by
    prev_ok=yes (the recovery).

    Each chain also carries the last_url — the most recent URL from a
    ds_navigate call before the failure started. This is critical for
    condition inference later.
    """
    chains = []
    last_url = None  # track last navigated URL across the stream
    i = 0

    while i < len(entries):
        # Track URLs as we scan forward
        entry = entries[i]
        if entry.get("tool") == "ds_navigate":
            url = entry.get("params", {}).get("url", "")
            if url:
                last_url = url
        # Also track URLs from ds_tab results (tab title contains URL info)
        if entry.get("tool") == "ds_tab":
            result = entry.get("result", "")
            # ds_tab result is like "Switched to: Title | URL"
            # but doesn't always contain URL — still useful for context

        # Look for start of failure chain
        if entry.get("prev_ok") != "no":
            i += 1
            continue

        # Found a failure. Collect the chain.
        chain_start = i
        url_at_failure = last_url  # snapshot the URL context

        while i < len(entries) and entries[i].get("prev_ok") == "no":
            i += 1

        chain_length = i - chain_start
        if chain_length < 1:
            continue

        # Look for recovery within next 3 actions
        recovery = None
        chain_app = entries[chain_start].get("app", entries[chain_start].get("mode", "unknown"))
        for j in range(i, min(i + 3, len(entries))):
            if entries[j].get("prev_ok") == "yes":
                recovery = entries[j]
                break

        if recovery:
            chains.append({
                "failures": [entries[k] for k in range(chain_start, i)],
                "recovery": recovery,
                "chain_length": chain_length,
                "app": chain_app,
                "failed_tool": entries[chain_start].get("tool", "unknown"),
                "recovery_tool": recovery.get("tool", "unknown"),
                "timestamp": entries[chain_start].get("ts", 0),
                "last_url": url_at_failure,  # URL context for condition inference
            })

    print(f"Extracted {len(chains)} failure chains")
    return chains


def cluster_chains(chains: list[dict]) -> dict:
    """Group failure chains by (app, failed_tool, recovery_tool) pattern."""
    clusters = defaultdict(list)
    for chain in chains:
        key = (chain["app"], chain["failed_tool"], chain["recovery_tool"])
        clusters[key].append(chain)

    print(f"Clustered into {len(clusters)} unique patterns")
    return dict(clusters)


def score_clusters(clusters: dict) -> list[dict]:
    """Score each cluster by impact = frequency × clarity × recency."""
    now = datetime.now().timestamp()
    scored = []

    for (app, failed_tool, recovery_tool), chains in clusters.items():
        frequency = len(chains)

        # Clarity: how consistent is the recovery tool?
        recovery_tools = [c["recovery_tool"] for c in chains]
        most_common = max(set(recovery_tools), key=recovery_tools.count)
        clarity = recovery_tools.count(most_common) / len(recovery_tools)

        # Recency: exponential decay based on most recent occurrence
        most_recent_ts = max(c["timestamp"] for c in chains)
        days_ago = (now - most_recent_ts) / 86400
        recency = math.exp(-0.05 * days_ago)  # half-life ~14 days

        impact = frequency * clarity * recency

        # Extract params pattern from failures
        failure_params = []
        for chain in chains:
            for fail in chain["failures"]:
                params = fail.get("params", {})
                failure_params.append(params)

        # Generate tip text
        tip_text = _generate_tip_text(app, failed_tool, recovery_tool, failure_params, chains)

        scored.append({
            "app": app,
            "failed_tool": failed_tool,
            "recovery_tool": recovery_tool,
            "frequency": frequency,
            "clarity": round(clarity, 2),
            "recency": round(recency, 2),
            "impact": round(impact, 2),
            "tip_text": tip_text,
            "chains": chains,
        })

    # Sort by impact descending
    scored.sort(key=lambda x: x["impact"], reverse=True)
    return scored


def _generate_tip_text(app: str, failed_tool: str, recovery_tool: str,
                       failure_params: list[dict], chains: list[dict]) -> str:
    """Generate a micro-tip from the failure→recovery pattern."""
    # Special case: tool switch pattern (ds_type → ds_text or vice versa)
    if failed_tool in ("ds_type", "ds_text") and recovery_tool in ("ds_type", "ds_text") and failed_tool != recovery_tool:
        return f"Use {recovery_tool} instead of {failed_tool} for {app} — {failed_tool} fails here"

    # Special case: click fails, different action recovers
    if failed_tool in ("ds_click", "ds_act") and recovery_tool != failed_tool:
        return f"{failed_tool} fails on {app} — use {recovery_tool} instead"

    # Special case: retry with ds_update_view
    if recovery_tool == "ds_update_view":
        return f"After {failed_tool} failure on {app}: re-read with ds_update_view before retrying"

    # Generic pattern
    return f"On {app}: {failed_tool} may fail — recover with {recovery_tool}"


def _infer_conditions(cluster: dict) -> list[dict]:
    """Infer tip conditions from the raw data inside a failure cluster.

    Examines all failure and recovery entries to extract:
    - URL patterns (from ds_navigate params in nearby entries)
    - Mode (cdp vs uia)
    - Failed element patterns (from ds_click/ds_act params)
    """
    conditions = []
    chains = cluster["chains"]
    app = cluster["app"]

    # --- 1. Extract URLs from the failure context ---
    # Primary source: last_url tracked across the entry stream (set before failure started)
    # Secondary: URLs in params of failure/recovery entries themselves
    urls = []
    for chain in chains:
        # The last_url is the most recent ds_navigate URL before this chain
        if chain.get("last_url"):
            urls.append(chain["last_url"])
        # Also check failure entry params (e.g. ds_navigate failures)
        for entry in chain["failures"]:
            url = entry.get("params", {}).get("url", "")
            if url:
                urls.append(url)
        # Also check recovery entry
        rec_url = chain["recovery"].get("params", {}).get("url", "")
        if rec_url:
            urls.append(rec_url)

    if urls:
        # Find the most common URL domain/path pattern
        url_pattern = _extract_common_url_pattern(urls)
        if url_pattern:
            conditions.append({"type": "url_contains", "value": url_pattern})

    # --- 2. Infer mode condition if consistent ---
    modes = set()
    for chain in chains:
        for entry in chain["failures"]:
            m = entry.get("mode")
            if m:
                modes.add(m)
    if len(modes) == 1:
        mode = modes.pop()
        conditions.append({"type": "mode_is", "value": mode})

    # --- 3. If app is unknown but mode is known, don't add app condition ---
    # (app filter is set separately in the tip record's "app" field)

    return conditions


def _extract_common_url_pattern(urls: list[str]) -> Optional[str]:
    """Find the most specific common substring across URLs.

    Extracts domain fragments or path segments that appear in >50% of URLs.
    Returns a short, reusable pattern for url_contains matching.
    """
    if not urls:
        return None

    # Extract domains and paths
    from urllib.parse import urlparse
    parts = []
    for u in urls:
        try:
            parsed = urlparse(u)
            # Keep host + first 2 path segments
            host = parsed.hostname or ""
            path_parts = [p for p in parsed.path.split("/") if p][:2]
            parts.append((host, "/".join(path_parts)))
        except Exception:
            continue

    if not parts:
        return None

    # Find common host pattern
    hosts = [p[0] for p in parts if p[0]]
    if hosts:
        # Most common host
        most_common_host = max(set(hosts), key=hosts.count)
        host_ratio = hosts.count(most_common_host) / len(hosts)
        if host_ratio > 0.5:
            # Try to find a meaningful subdomain/path combo
            paths = [p[1] for p in parts if p[0] == most_common_host and p[1]]
            if paths:
                most_common_path = max(set(paths), key=paths.count)
                path_ratio = paths.count(most_common_path) / len(paths)
                if path_ratio > 0.5:
                    return most_common_path  # e.g. "spreadsheets/d"
            # Fallback to domain fragment (strip www. and common TLDs)
            clean_host = most_common_host.replace("www.", "")
            # Use the most identifying part (e.g. "google.com/travel" vs just "google.com")
            return clean_host

    return None


def _infer_inject_on(cluster: dict) -> list[str]:
    """Decide which tools should trigger this tip based on the failure pattern."""
    failed_tool = cluster["failed_tool"]
    recovery_tool = cluster["recovery_tool"]

    # Base: always inject on orientation tools
    inject_on = ["ds_update_view", "ds_screen"]

    # If the failure happens right after navigation, also inject on ds_navigate
    if failed_tool in ("ds_click", "ds_act", "ds_type", "ds_text"):
        inject_on.append("ds_navigate")

    # If failures happen on app focus, inject on ds_focus too
    if failed_tool == "ds_focus" or recovery_tool == "ds_focus":
        inject_on.append("ds_focus")

    return inject_on


def _infer_tier(cluster: dict) -> int:
    """Assign tier based on failure severity and pattern type."""
    failed_tool = cluster["failed_tool"]
    recovery_tool = cluster["recovery_tool"]
    impact = cluster["impact"]

    # Tier 1: Tool-switch patterns (ds_text↔ds_type) — these ALWAYS fail, error prevention
    if failed_tool in ("ds_type", "ds_text") and recovery_tool in ("ds_type", "ds_text"):
        return 1

    # Tier 1: Consistent high-frequency failure (>5 occurrences, >80% clarity)
    if cluster["frequency"] >= 5 and cluster["clarity"] >= 0.8:
        return 1

    # Tier 1: Click on canvas-like apps (clicks always fail, different tool recovers)
    if failed_tool in ("ds_click", "ds_act") and recovery_tool in ("ds_type", "ds_key"):
        return 1

    # Tier 2: Everything else with meaningful impact
    if impact >= 1.0:
        return 2

    # Tier 3: Low-impact general patterns
    return 3


def generate_candidates(scored: list[dict], min_impact: float = 0.5) -> list[dict]:
    """Generate tip candidate records from scored clusters.

    This is where raw failure data gets transformed into structured tip records.
    The key step is _infer_conditions() which extracts contextual triggers
    (URL patterns, mode, app) from the actual log entries in each cluster.
    """
    candidates = []

    for cluster in scored:
        if cluster["impact"] < min_impact:
            continue

        h = hashlib.md5(
            f"{cluster['app']}:{cluster['failed_tool']}:{cluster['recovery_tool']}".encode()
        ).hexdigest()[:8]

        # INFER structured parameters from raw data
        conditions = _infer_conditions(cluster)
        inject_on = _infer_inject_on(cluster)
        tier = _infer_tier(cluster)

        tip_record = {
            "id": f"mined_{cluster['app']}_{h}",
            "text": cluster["tip_text"],
            "app": cluster["app"] if cluster["app"] != "unknown" else "*",
            "conditions": conditions,
            "inject_on": inject_on,
            "priority": max(5, 50 - int(cluster["impact"] * 10)),
            "tier": tier,
            "cooldown_s": 300,
            "max_per_session": 3,
            "_meta": {
                "frequency": cluster["frequency"],
                "clarity": cluster["clarity"],
                "recency": cluster["recency"],
                "impact": cluster["impact"],
                "inferred_conditions": len(conditions),
                "mined_at": datetime.now().isoformat(),
            }
        }

        candidates.append(tip_record)

    return candidates


def main():
    logs_dir, out_file = _discover_paths()

    print(f"=== DirectShell Tip Miner ===")
    print(f"Logs:   {logs_dir}")
    print(f"Output: {out_file}")
    print()

    # 1. Parse logs
    entries = parse_logs(logs_dir)
    if not entries:
        print("No log entries found. Nothing to mine.")
        return

    # 2. Extract failure chains
    chains = extract_failure_chains(entries)
    if not chains:
        print("No failure chains found. The LLM is doing great!")
        return

    # 3. Cluster
    clusters = cluster_chains(chains)

    # 4. Score
    scored = score_clusters(clusters)

    # 5. Generate candidates
    candidates = generate_candidates(scored)

    # 6. Write output
    out_file.parent.mkdir(parents=True, exist_ok=True)
    with open(out_file, "w", encoding="utf-8") as f:
        for c in candidates:
            f.write(json.dumps(c, ensure_ascii=False) + "\n")

    print(f"\n=== Results ===")
    print(f"Failure chains:  {len(chains)}")
    print(f"Unique patterns: {len(clusters)}")
    print(f"Candidates:      {len(candidates)}")
    print(f"Written to:      {out_file}")

    # Print top candidates
    if candidates:
        print(f"\nTop candidates:")
        for c in candidates[:10]:
            meta = c.get("_meta", {})
            print(f"  [{meta.get('impact', '?'):.1f}] {c['text']} (freq={meta.get('frequency', '?')}, clarity={meta.get('clarity', '?')})")


if __name__ == "__main__":
    main()
