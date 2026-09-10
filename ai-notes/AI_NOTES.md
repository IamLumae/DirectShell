# DirectShell AI Notes
Shared lessons from AI sessions driving DirectShell — newest first.
Read with get_notes, append with append_note. Check here first when
something misbehaves: another AI may have already solved it.

## [2026-08-24 07:20] save-as dialogs / pre-filled fields
- Symptom: type_text with an element APPENDS to existing field content (it reads current text and sets current+new). Using it on a Save As filename field pre-filled with 'Unsaved Document 2' produced 'Unsaved Document 2directshell-test.txt'.
- Instead: Use paste_text {element} when you need to REPLACE a field's content — it clicks the field, ctrl+a select-all, then pastes. type_text is for appending or empty fields.

## [2026-08-24 14:30] firefox
- Symptom: Typing a URL with perform(action='type') without a target fired keystrokes at the page content; the '/' in 'https://' opened Firefox Quick Find and the text was swallowed by the find bar.
- Instead: Pass target='Search with Google or enter address' (or use paste_text with element). The daemon now also guards this: URL-shaped text typed without an element is verified against the focused widget and delivered via clipboard paste if nothing editable has focus.
