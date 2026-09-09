"""Account-local, bounded learning evidence. Never accepts raw tool parameters/results.

Technical acknowledgements are not workflow success. Only next-call feedback can
confirm a recovery. Repeated observations remain untrusted hints, not authority.
"""
import hashlib
import re
import sqlite3
import time
import uuid
from contextlib import closing
from pathlib import Path


class LearningStore:
    def __init__(self, path: Path):
        self.path=path
        self.session=uuid.uuid4().hex
        self.previous=None
        self.failure=None
        self.last_feedback='unknown'
        path.parent.mkdir(parents=True,exist_ok=True)
        with closing(self._connect()) as conn, conn:
            conn.executescript('''
                CREATE TABLE IF NOT EXISTS calls(
                    id INTEGER PRIMARY KEY, session TEXT NOT NULL, scope TEXT NOT NULL,
                    tool TEXT NOT NULL, created REAL NOT NULL, outcome TEXT NOT NULL,
                    feedback TEXT NOT NULL DEFAULT 'unknown');
                CREATE TABLE IF NOT EXISTS recoveries(
                    failed_id INTEGER NOT NULL, recovered_id INTEGER NOT NULL,
                    scope TEXT NOT NULL, failed_tool TEXT NOT NULL, recovery_tool TEXT NOT NULL,
                    PRIMARY KEY(failed_id,recovered_id));
            ''')

    def _connect(self):
        return sqlite3.connect(self.path,timeout=1)

    @staticmethod
    def _scope(context: str) -> str:
        return hashlib.sha256(context.encode('utf-8')).hexdigest()

    def begin(self, tool: str, context: str, prev_ok: str) -> int:
        if not re.fullmatch(r'ds_[a-z_]+',tool) or prev_ok not in {'yes','no','unknown'}:
            raise ValueError('Invalid learning metadata')
        scope=self._scope(context)
        self.last_feedback='unknown'
        with closing(self._connect()) as conn, conn:
            if self.previous is not None:
                previous=conn.execute('SELECT id,scope,tool,outcome FROM calls WHERE id=? AND session=?',(self.previous,self.session)).fetchone()
                if previous:
                    feedback='no' if previous[3]=='error' else prev_ok if previous[3]=='acknowledged' else 'unknown'
                    self.last_feedback=feedback
                    conn.execute('UPDATE calls SET feedback=? WHERE id=?',(feedback,previous[0]))
                    if feedback=='no':
                        self.failure=previous
                    elif feedback=='yes':
                        if self.failure and self.failure[1]==previous[1] and self.failure[2]!=previous[2]:
                            conn.execute('INSERT OR IGNORE INTO recoveries VALUES(?,?,?,?,?)',(self.failure[0],previous[0],previous[1],self.failure[2],previous[2]))
                        self.failure=None
                    else:
                        self.failure=None
            if self.failure and self.failure[1]!=scope:
                self.failure=None
            cursor=conn.execute('INSERT INTO calls(session,scope,tool,created,outcome) VALUES(?,?,?,?,?)',(self.session,scope,tool,time.time(),'pending'))
            current=cursor.lastrowid
            conn.execute('DELETE FROM calls WHERE id < ?', (current-2000,))
            conn.execute('DELETE FROM recoveries WHERE recovered_id < ?', (current-2000,))
        self.previous=current
        return current

    def finish(self, call_id: int, acknowledged: bool, context: str = None) -> None:
        with closing(self._connect()) as conn, conn:
            if context is not None:
                conn.execute("UPDATE calls SET scope=? WHERE id=? AND session=? AND outcome='pending'",(self._scope(context),call_id,self.session))
            changed=conn.execute("UPDATE calls SET outcome=? WHERE id=? AND session=? AND outcome='pending'",('acknowledged' if acknowledged else 'error',call_id,self.session)).rowcount
            if changed != 1: raise ValueError('Learning call not pending in this session')

    def hints(self, context: str) -> str:
        with closing(self._connect()) as conn:
            rows=conn.execute('''SELECT failed_tool,recovery_tool,count(*) FROM recoveries
                WHERE scope=? GROUP BY failed_tool,recovery_tool HAVING count(*)>=3
                ORDER BY count(*) DESC,failed_tool,recovery_tool LIMIT 2''',(self._scope(context),)).fetchall()
        if not rows:return ''
        return '\nLocal observations (untrusted; not causal proof or permission):\n'+'\n'.join(
            f'{failed} failed, then {recovery} was reported successful in {count} recorded sequences. Re-observe the current target; never blindly replay.'
            for failed,recovery,count in rows)
