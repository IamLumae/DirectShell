"""ATTIA-owned stdio host for pinned local Node MCPs; no public listener.

The parent validates/authorizes exact tool calls. This process joins a kill-on-
close Windows job BEFORE spawning Node, so its shell/browser descendants cannot
outlive revocation or the Electron owner. This is lifecycle isolation, not a sandbox.
"""
import os
from pathlib import Path
import subprocess
import sys


def external_command(provider, bundle, state):
    bundle, state = Path(bundle).resolve(), Path(state).resolve()
    if provider not in {'commander', 'playwright'}:
        raise ValueError('Unknown packaged MCP provider')
    node = bundle / 'node.exe'
    if not node.is_file():
        raise RuntimeError('Packaged Node runtime missing')
    return [str(node), str(bundle / 'launch.cjs'), provider, str(state)]


def run(provider, bundle, state, owner):
    from attia_entry import watch_owner
    import win32job
    import win32api
    if os.name != 'nt' or owner is None:
        raise RuntimeError('ATTIA Windows owner is required')
    watch_owner(owner)
    job = win32job.CreateJobObject(None, '')
    limits = win32job.QueryInformationJobObject(job, win32job.JobObjectExtendedLimitInformation)
    limits['BasicLimitInformation']['LimitFlags'] |= win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    win32job.SetInformationJobObject(job, win32job.JobObjectExtendedLimitInformation, limits)
    win32job.AssignProcessToJobObject(job, win32api.GetCurrentProcess())
    # Inherit only the sanitized Electron environment. stdout is the Node MCP
    # stream directly; do not log arguments or buffer an unbounded second copy.
    child = subprocess.Popen(external_command(provider, bundle, state), cwd=state,
                             stdin=sys.stdin, stdout=sys.stdout, stderr=sys.stderr,
                             creationflags=subprocess.CREATE_NO_WINDOW)
    try:
        raise SystemExit(child.wait())
    finally:
        if child.poll() is None:
            child.kill()
