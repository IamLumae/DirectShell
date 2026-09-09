# DS: non-interference contract

User decision, 2026-09-09: DS must not move/intercept/inject the user's mouse or
keyboard, switch foreground focus, or require the user to stop working.
This supersedes the earlier foreground-checked native injection pilot.

Martin's clarification in the same task: this is NOT a mandate to replace native
application control with a VM, separate app copies or permanent read-only tools.
The objective remains operating his actual running applications through a universal
semantic interface, without screenshots. Missing control support is development
work, not justification for abandoning that objective.

## Historical development checkpoint — 2026-09-09

- Rust global input injection, keyboard interception and explicit focus setters
  removed, including the standalone implementation. Old/expired injection queue
  entries are rejected rather than replayed or retried.
- A first semantic native backend now exists in `src/native_actions.rs`, wired
  through the existing queue: exact unique element selection, ValuePattern writes,
  Invoke/Toggle actions, agent-owned text target/selection and explicit failures.
  This replaces the earlier blanket BACKGROUND_UNSUPPORTED stopgap. Its coverage
  and real provider behavior are still under development, not production-approved.
  No guessed foreground fallback, clipboard transport or focus restoration.
- Owned Edge starts headless. Selecting a browser tab changes only the CDP
  target, without bringing a Windows window forward. CDP input is scoped to that
  private browser target. Browser-only work does not start the native helper.
- Native helper window starts hidden with `WS_EX_NOACTIVATE`. Position tracking
  no longer moves the user's target window to follow the DS overlay.
- Regression gate: `ds-mcp/test_background_contract.py`; Rust release build.
  This static gate is necessary, not a proof that every application has no
  indirect focus side effects.

## Remaining implementation / release work

### Confirmed client defect: implicit UIA autofocus

The first semantic prototype still left `IUIAutomation2.AutoSetFocus` at its
default TRUE. Microsoft documents that UIA itself focuses controls before
`Invoke`/`SetValue`. Removing explicit focus calls from DS therefore did not
remove this activation path. Martin reported renewed interference; the old
fixture was observed in the foreground, but no event trace identifies the exact
call responsible for that incident. Its text write also failed readback.

`src/automation_client.rs` now owns all five UIA creation sites. It disables
AutoSetFocus and verifies FALSE before any element/pattern lookup. Failure aborts
creation instead of returning an unsafe client. A real-COM configuration test
failed with the original default and passed after the fix, without looking up or
operating a window. A source gate prevents bypassing this factory.

This closes a confirmed client-side cause, not proof that all app-defined action
handlers are non-interfering. The Main-PC helper was stopped after the failed
test; no further Main-PC UI actions were used for this fix.

Practical acceptance must use an independent synthetic foreground work window
and the synthetic DS target in the background. Record foreground/focus events
continuously from before helper startup through target selection, mutation,
readback and shutdown, plus pointer samples and the work-window content/selection.
A transient activation followed by restoration FAILS; missing/dropped monitoring
data is INCONCLUSIVE, never PASS. Verify the intended target state separately.
Record helper hash, provider description, app version and action correlation.
The former single-window button test is not this acceptance. Until this test is
implemented and green in QA, native background operation remains unaccepted.

Source: https://learn.microsoft.com/en-us/windows/win32/api/uiautomationclient/nf-uiautomationclient-iuiautomation2-get_autosetfocus

Native background control is NOT complete. UIA Value/Invoke/Scroll patterns can
avoid global device input, but application-defined handlers can open dialogs or
activate windows. Do not claim universal non-interference based on a pattern
call alone. Investigate and improve the actual provider/control path. A separate
execution environment is NOT a user-approved prerequisite or product direction.
The preceding blanket claim that it was required was Luna's overreach.

Microsoft distinguishes desktops from an independent input device/session:
only one desktop of an interactive window station receives user input at a time.
An additional desktop/monitor alone is not proof of independent mouse/keyboard.

Sources:
- https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-sendinput
- https://learn.microsoft.com/en-us/windows/win32/winstation/desktops
- https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-setthreaddesktop

Installed ATTIA 0.7 and the frozen vendor runtime still contain the previous
implementation until an explicitly verified new installer is delivered. No
production update or user-app restart was performed for this contract change.

## ATTIA 0.9 acceptance update — 2026-09-10

The preceding installed-version statements describe the earlier checkpoint,
not the current release. ATTIA0.8 subsequently shipped;0.9 packages this follow-up.
Native actions now include ExpandCollapse/legacy default actions and bounded
ScrollPattern/ScrollItem operations. Virtualized lists prefer the adjacent
offscreen item's ScrollIntoView with a visible-state readback. Acknowledgement
without the expected observation remains an error, never an input replay.
There is no automatic restore/minimize or foreground-window manipulation.

The0.9 frozen MCP passed six native Electron scroll actions with independent DOM
readback (four background-visible, two minimized),0 activations and0 foreground
events in160 passive samples. Main WinForms actions, explicit native routing with
an open managed browser, stable action numbers after learning, and the base-window
read/write/invoke bridge also passed.11 Rust,24 Python and52 desktop contract tests
were green. Actual Discord Inbox open/close and repeated semantic list scrolling
were observed during development; a later run was inconclusive because another
actor changed the foreground/channel. These bounded tests do not establish
universal provider behavior or a guarantee for arbitrary application handlers.
No customer/agent conversation was generated for these tests.
