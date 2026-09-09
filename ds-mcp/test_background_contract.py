"""Non-interference release gate. No user UI is opened or operated."""
import pathlib
import re
import sqlite3
import tempfile
from contextlib import closing
import unittest
from unittest.mock import patch
import server

ROOT = pathlib.Path(__file__).resolve().parents[1]


class BackgroundContract(unittest.TestCase):
    def test_native_sources_do_not_inject_or_steal_focus(self):
        forbidden = r'\b(SendInput|SetCursorPos|SetForegroundWindow|SetActiveWindow|AttachThreadInput|SetWindowsHookExW|mouse_event|keybd_event)\s*\(|\.SetFocus\s*\('
        for file in (ROOT / 'src').rglob('*.rs'):
            self.assertIsNone(re.search(forbidden, file.read_text(encoding='utf-8')), str(file))
            if file.name=='native_actions.rs':
                self.assertNotRegex(file.read_text(encoding='utf-8'),r'\bWM_(LBUTTONDOWN|LBUTTONUP|MOUSEMOVE)\b', 'Even target-window mouse messages activated the real Electron QA window')

    def test_browser_does_not_bring_windows_to_front(self):
        self.assertFalse('Page.bringToFront' in (ROOT / 'ds-mcp/server.py').read_text(encoding='utf-8'))
        self.assertIn("'--headless=new'", (ROOT / 'ds-mcp/attia_entry.py').read_text(encoding='utf-8'))

    def test_all_uia_clients_use_the_no_autofocus_factory(self):
        # Prevent a later per-tool CoCreateInstance from silently restoring the default.
        for file in (ROOT / 'src').rglob('*.rs'):
            if file.name == 'automation_client.rs':
                continue
            self.assertNotIn('CUIAutomation', file.read_text(encoding='utf-8'), str(file))
        actions = (ROOT / 'src/native_actions.rs').read_text(encoding='utf-8')
        # Helper order in the file is not execution order: injected clients are
        # already configured. The entry point must create via the factory before
        # handing its client to execute_on, which owns target lookup.
        entry = actions[actions.index('pub unsafe fn execute('):actions.index('pub unsafe fn execute_on(')]
        self.assertLess(entry.index('automation_client::create()'), entry.index('execute_on('))

    def test_native_queue_requires_selected_app_and_new_backend(self):
        with tempfile.TemporaryDirectory() as folder:
            db=pathlib.Path(folder)/'fixture.db'
            with patch.object(server,'_read_active',return_value={'snapped':True,'app':'fixture'}), patch.object(server,'_get_db_path',return_value=db):
                with self.assertRaisesRegex(RuntimeError,'TARGET_NOT_SELECTED'):
                    server._inject_action('click',target='Synthetic',app='other')
                with self.assertRaisesRegex(RuntimeError,'NATIVE_BACKEND_OUTDATED'):
                    server._inject_action('click',target='Synthetic')
                with closing(sqlite3.connect(db)) as conn, conn:
                    conn.executescript("CREATE TABLE capabilities(name TEXT); INSERT INTO capabilities VALUES('semantic_native_v1'); CREATE TABLE inject(id INTEGER PRIMARY KEY, action TEXT,text TEXT,target TEXT,done INTEGER,expires_at INTEGER,outcome TEXT DEFAULT '');")
                with patch.object(server,'_wait_for_action') as wait:
                    action_id=server._inject_action('text',text='🦉',target='Synthetic')
                    wait.assert_called_once_with(action_id,db)
                with closing(sqlite3.connect(db)) as conn, conn:
                    row=conn.execute('SELECT text,target,done,expires_at FROM inject').fetchone()
                    self.assertEqual(row[:3],('🦉','Synthetic',0))
                    self.assertGreater(row[3],0)
                    conn.execute("UPDATE inject SET done=2,outcome='PATTERN_UNAVAILABLE'")
                with self.assertRaisesRegex(RuntimeError,'PATTERN_UNAVAILABLE'):
                    server._wait_for_action(action_id,db,timeout=.1)


if __name__ == '__main__':
    unittest.main()
