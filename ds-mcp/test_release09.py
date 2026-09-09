import json
import unittest
import tempfile
import pathlib
import sqlite3
import os
from contextlib import closing
from unittest.mock import patch
import server
from test_browser_regressions import Socket

class Release09(unittest.TestCase):
    def test_fresh_file_does_not_make_old_capture_fresh(self):
        with tempfile.TemporaryDirectory() as folder:
            root=pathlib.Path(folder)
            snap=root/'fixture.a11y.snap';snap.write_text('view')
            os.utime(snap,(200,200))
            with closing(sqlite3.connect(root/'fixture.db')) as db, db:
                db.execute('CREATE TABLE meta(key TEXT,value TEXT)')
                db.execute("INSERT INTO meta VALUES('timestamp','90000')")
            with patch.object(server,'PROFILES_DIR',root),patch.object(server,'_read_active',return_value={'snapped':True,'app':'fixture'}):
                self.assertEqual(server._await_native_snapshot('fixture',100,timeout=.01)['status'],'pending')
                with closing(sqlite3.connect(root/'fixture.db')) as db, db:db.execute("UPDATE meta SET value='110000'")
                self.assertEqual(server._await_native_snapshot('fixture',100,timeout=.1)['status'],'ok')

    def test_numbered_action_cannot_cross_target_or_mode(self):
        with patch.object(server,'_require_ds'),patch.object(server,'_is_cdp_available',return_value=False),patch.object(server,'_get_snapped_app',return_value='other'),patch.object(server,'_inject_action') as inject:
            server._active_view={'tools':[{'action':'click','element':'Apply'}],'owner':('uia','original',None)}
            with self.assertRaisesRegex(ValueError,'another target'):server.ds_act(1,'unknown')
            inject.assert_not_called()

    def test_default_native_views_do_not_dump_full_chats(self):
        with patch.object(server,'_require_ds'),patch.object(server,'_is_cdp_available',return_value=False),patch.object(server,'_read_file',return_value='x'*40000),patch.object(server._tip_engine,'get_tips_block',return_value=''):
            for tool in [server.ds_state,server.ds_elements,server.ds_screen]:
                self.assertLess(len(tool('unknown')),8300)

    def test_window_titles_allow_punctuation_but_profile_paths_remain_closed(self):
        from attia_entry import validate_call
        validate_call('ds_focus',{'app':'#team | Example - Discord','prev_ok':'unknown'})
        with self.assertRaises(Exception):validate_call('ds_learn',{'app':'../escape','context':'general','prev_ok':'unknown'})

    def test_native_type_keeps_literal_windows_path(self):
        text=r'C:\new\text'
        with patch.object(server,'_require_ds'),patch.object(server,'_is_cdp_available',return_value=False),patch.object(server,'_log_action'),patch.object(server,'_learning_hint',return_value=''),patch.object(server,'_inject_action',return_value=1) as inject:
            server.ds_type(text,prev_ok='unknown')
            self.assertEqual(inject.call_args.kwargs['text'],text)

    def test_navigation_checks_correlated_browser_failure(self):
        ws=Socket();ws.recv=lambda:json.dumps({'id':1,'result':{'errorText':'net::ERR_ABORTED'}})
        with patch.object(server,'_require_ds'),patch.object(server,'_is_cdp_available',return_value=True),patch.object(server,'_cdp_ws',return_value=ws):
            with self.assertRaisesRegex(RuntimeError,'ERR_ABORTED'):
                server.ds_navigate('http://fixture/',prev_ok='unknown')

    def test_scroll_waits_for_each_native_action_and_bounds_amount(self):
        with patch.object(server,'_require_ds'),patch.object(server,'_is_cdp_available',return_value=False),patch.object(server,'_log_action'),patch.object(server,'_inject_action',return_value=1) as inject:
            server.ds_scroll('down','unknown',amount=3,target='Messages')
            self.assertEqual(inject.call_count,3)
            self.assertTrue(all(c.kwargs['wait'] for c in inject.call_args_list))
            self.assertEqual(inject.call_args.kwargs['target'],'Messages')
            with self.assertRaises(ValueError):server.ds_scroll('down','unknown',amount=0)

    def test_native_view_bounded_and_numbered_tools_match_returned_subset(self):
        snap='\n'.join(f'[{i}] [mouse] "Button {i}"' for i in range(300))
        with patch.object(server,'_require_ds'),patch.object(server,'_is_cdp_available',return_value=False),patch.object(server,'_log_action'),patch.object(server,'_get_snapped_app',return_value='fixture'),patch.object(server._tip_engine,'get_tips_block',return_value=''),patch.object(server,'_read_file',side_effect=['## Content\n'+'x'*40000,snap]):
            view=server.ds_update_view('unknown')
            self.assertLess(len(view),12000)
            self.assertIn('200 tools omitted',view)
            self.assertEqual(len(server._active_view['tools']),100)

if __name__=='__main__':unittest.main()
