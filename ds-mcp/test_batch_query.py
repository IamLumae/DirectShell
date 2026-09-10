import pathlib
import sqlite3
import tempfile
import unittest
from contextlib import closing
from unittest.mock import patch
import server


class BatchQuery(unittest.TestCase):
    def test_batch_validates_every_step_before_any_action(self):
        with patch.object(server,'_require_ds'),patch.object(server,'_is_cdp_available',return_value=True),patch.object(server,'_cdp_click') as click:
            for bad in [{}, {'action':'oops'}, {'action':'click','target':12}, {'action':'key','text':'enter','extra':True}]:
                with self.assertRaises(ValueError):server.ds_batch([{'action':'click','target':'First'},bad],'unknown')
            click.assert_not_called()

    def test_batch_stops_at_first_native_failure_and_reports_partial_progress(self):
        with patch.object(server,'_require_ds'),patch.object(server,'_is_cdp_available',return_value=False),patch.object(server,'_log_action'),patch.object(server,'_inject_action',side_effect=[1,RuntimeError('PATTERN_UNAVAILABLE'),3]) as inject:
            with self.assertRaisesRegex(RuntimeError,'1/3 confirmed.*step 2'):
                server.ds_batch([{'action':'click','target':name} for name in ['One','Two','Three']],'unknown')
            self.assertEqual(inject.call_count,2)
            self.assertTrue(all(c.kwargs['wait'] for c in inject.call_args_list))

    def test_browser_unknown_action_is_not_counted_as_success(self):
        with patch.object(server,'_require_ds'),patch.object(server,'_is_cdp_available',return_value=True):
            with self.assertRaises(ValueError):server.ds_batch([{'action':'ignored'}],'unknown')

    def test_query_is_bounded_read_only_and_elements_only(self):
        with tempfile.TemporaryDirectory() as folder:
            root=pathlib.Path(folder)
            with closing(sqlite3.connect(root/'fixture.db')) as db,db:
                db.executescript('CREATE TABLE elements(id INTEGER,name TEXT,value TEXT); CREATE TABLE inject(text TEXT);')
                db.executemany('INSERT INTO elements VALUES(?,?,?)',[(i,'Button','x') for i in range(220)])
            with patch.object(server,'PROFILES_DIR',root),patch.object(server,'_require_ds'),patch.object(server,'_is_cdp_available',return_value=False),patch.object(server,'_read_active',return_value={'snapped':True,'app':'fixture'}):
                self.assertEqual(server.ds_query('SELECT count(*) AS n FROM elements','unknown'),[{'n':220}])
                self.assertEqual(len(server.ds_query('SELECT name FROM elements LIMIT 3','unknown')),3)
                for sql in ['DELETE FROM elements','SELECT * FROM inject','PRAGMA database_list',"SELECT load_extension('evil')",'SELECT * FROM elements','SELECT randomblob(100000)','SELECT * FROM elements; SELECT 1']:
                    with self.subTest(sql=sql),self.assertRaises((ValueError,sqlite3.Error)):
                        server.ds_query(sql,'unknown')
                with self.assertRaises(ValueError):server.ds_query('SELECT 1','unknown',app='../escape')

    def test_query_rejects_browser_mode_instead_of_reading_old_native_target(self):
        with patch.object(server,'_require_ds'),patch.object(server,'_is_cdp_available',return_value=True):
            with self.assertRaisesRegex(ValueError,'native'):server.ds_query('SELECT 1','unknown')

if __name__=='__main__':unittest.main()
