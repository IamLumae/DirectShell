import pathlib
import sqlite3
import tempfile
import unittest
from contextlib import closing
from learning_store import LearningStore


class DurableLearning(unittest.TestCase):
    def test_routed_call_is_saved_under_actual_destination(self):
        with tempfile.TemporaryDirectory() as folder:
            filename=pathlib.Path(folder)/'learning.db'
            store=LearningStore(filename)
            call=store.begin('ds_focus','browser:old','unknown')
            store.finish(call,True,'native:fixture')
            with closing(sqlite3.connect(filename)) as conn:
                scope=conn.execute('SELECT scope FROM calls WHERE id=?',(call,)).fetchone()[0]
            self.assertEqual(scope,store._scope('native:fixture'))

    def test_feedback_is_bound_to_previous_call_and_survives_restart(self):
        with tempfile.TemporaryDirectory() as folder:
            store=LearningStore(pathlib.Path(folder)/'learning.db')
            for _ in range(3):
                first=store.begin('ds_click','fixture','unknown');store.finish(first,False)
                second=store.begin('ds_text','fixture','no');store.finish(second,True)
                last=store.begin('ds_update_view','fixture','yes');store.finish(last,True)
            hints=store.hints('fixture')
            self.assertIn('ds_click',hints);self.assertIn('ds_text',hints)
            self.assertNotIn('ds_update_view',hints)
            restarted=LearningStore(pathlib.Path(folder)/'learning.db')
            self.assertEqual(restarted.hints('fixture'),hints)
            self.assertEqual(restarted.hints('other'), '')

    def test_no_raw_context_or_cross_scope_recovery_or_success_guess(self):
        with tempfile.TemporaryDirectory() as folder:
            filename=pathlib.Path(folder)/'learning.db'
            store=LearningStore(filename)
            first=store.begin('ds_click','private document title','unknown');store.finish(first,False)
            second=store.begin('ds_text','other','no');store.finish(second,True)
            store.begin('ds_update_view','other','yes')
            self.assertEqual(store.hints('other'), '')
            self.assertNotIn(b'private document title',filename.read_bytes())
            with self.assertRaises(ValueError):store.begin('../unsafe','x','yes')
            with closing(sqlite3.connect(filename)) as conn:
                self.assertEqual(conn.execute('SELECT count(*) FROM recoveries').fetchone()[0],0)


if __name__=='__main__':unittest.main()
