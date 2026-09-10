import json
import sqlite3
import threading
import time
from pathlib import Path
from archforge_domain.model import canonical, digest, DomainError


class Store:
    def __init__(self, root):
        self.root=Path(root).resolve();self.root.mkdir(parents=True,exist_ok=True)
        self.db=sqlite3.connect(self.root/'projects.sqlite3',check_same_thread=False)
        self.db.row_factory=sqlite3.Row
        self.lock=threading.RLock()
        self.db.executescript('''
        PRAGMA journal_mode=WAL;
        PRAGMA synchronous=FULL;
        CREATE TABLE IF NOT EXISTS revisions(project TEXT, revision INTEGER, model TEXT, hash TEXT, created REAL, PRIMARY KEY(project,revision));
        CREATE TABLE IF NOT EXISTS plans(id TEXT PRIMARY KEY, project TEXT, base INTEGER, payload TEXT);
        CREATE TABLE IF NOT EXISTS operations(id TEXT PRIMARY KEY, project TEXT, hash TEXT, state TEXT, payload TEXT, updated REAL);
        ''');self.db.commit()

    def create(self, model):
        with self.lock,self.db:
            if self.db.execute('SELECT 1 FROM revisions WHERE project=?',(model['project_id'],)).fetchone():
                raise DomainError('PROJECT_EXISTS','Project ID already exists')
            self._insert(model)

    def _insert(self,model):
        self.db.execute('INSERT INTO revisions VALUES(?,?,?,?,?)',(model['project_id'],model['revision'],canonical(model),digest(model),time.time()))

    def get(self, project, revision=None):
        with self.lock:
            row=self.db.execute('SELECT model FROM revisions WHERE project=? '+('AND revision=?' if revision is not None else 'ORDER BY revision DESC LIMIT 1'),(project,revision) if revision is not None else (project,)).fetchone()
            if not row:raise DomainError('UNKNOWN_PROJECT','Project or revision not found')
            return json.loads(row[0])

    def list_projects(self):
        with self.lock:
            rows=self.db.execute('SELECT model FROM revisions r WHERE revision=(SELECT MAX(revision) FROM revisions WHERE project=r.project)').fetchall()
            return [{'project_id':m['project_id'],'name':m.get('name','House'),'revision':m['revision']} for m in (json.loads(r[0]) for r in rows)]

    def revisions(self,project):
        with self.lock:return [dict(r) for r in self.db.execute('SELECT revision,hash,created FROM revisions WHERE project=? ORDER BY revision DESC',(project,))]

    def save_plan(self,plan):
        with self.lock,self.db:self.db.execute('INSERT INTO plans VALUES(?,?,?,?)',(plan['plan_id'],plan['project_id'],plan['base_revision'],canonical(plan)))

    def plan(self,pid):
        with self.lock:
            row=self.db.execute('SELECT payload FROM plans WHERE id=?',(pid,)).fetchone()
            if not row:raise DomainError('UNKNOWN_PLAN','Plan not found')
            return json.loads(row[0])

    def operation(self,oid):
        with self.lock:
            row=self.db.execute('SELECT payload FROM operations WHERE id=?',(oid,)).fetchone()
            return json.loads(row[0]) if row else None

    def save_operation(self,op):
        with self.lock,self.db:self._op(op)

    def _op(self,op):
        self.db.execute('INSERT OR REPLACE INTO operations VALUES(?,?,?,?,?,?)',(op['operation_id'],op['project_id'],op['payload_hash'],op['status'],canonical(op),time.time()))

    def commit(self,model,op,expected):
        with self.lock,self.db:
            current=self.get(model['project_id'])
            if current['revision']!=expected:raise DomainError('REVISION_CONFLICT','Project changed; make a new proposal')
            model['revision']=expected+1
            self._insert(model)
            op['committed_revision']=model['revision'];self._op(op)

    def pending(self):
        with self.lock:return [json.loads(r[0]) for r in self.db.execute("SELECT payload FROM operations WHERE state IN ('staging','projecting','recovery_required')")]

    def close(self):self.db.close()
