"""
SQLite 持久化：workflows + workflow_runs 表。
复用主 db.py 的连接方式。
"""

from __future__ import annotations
import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path

DB_PATH = Path("data/tasks.db")


@contextmanager
def _conn():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_workflow_db():
    """创建 workflows 和 workflow_runs 表（幂等）。"""
    DB_PATH.parent.mkdir(exist_ok=True)
    with _conn() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS workflows (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL DEFAULT '',
                description TEXT NOT NULL DEFAULT '',
                yaml_source TEXT NOT NULL,
                parameters TEXT NOT NULL DEFAULT '[]',
                blocks TEXT NOT NULL DEFAULT '[]',
                source_type TEXT NOT NULL DEFAULT 'api',
                source_path TEXT,
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS workflow_runs (
                id TEXT PRIMARY KEY,
                workflow_id TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                parameters TEXT NOT NULL DEFAULT '{}',
                block_results TEXT NOT NULL DEFAULT '{}',
                current_block TEXT,
                logs TEXT NOT NULL DEFAULT '[]',
                error TEXT,
                started_at TEXT,
                finished_at TEXT,
                created_at TEXT DEFAULT (datetime('now')),
                FOREIGN KEY (workflow_id) REFERENCES workflows(id)
            );
        """)


# ── Workflows CRUD ────────────────────────────────────────────────────────────

def save_workflow(w: dict):
    with _conn() as conn:
        conn.execute("""
            INSERT OR REPLACE INTO workflows
              (id, title, description, yaml_source, parameters, blocks,
               source_type, source_path, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
        """, (
            w["id"], w.get("title", ""), w.get("description", ""),
            w["yaml_source"],
            json.dumps(w.get("parameters", []), ensure_ascii=False),
            json.dumps(w.get("blocks", []), ensure_ascii=False),
            w.get("source_type", "api"),
            w.get("source_path"),
        ))


def load_all_workflows() -> dict[str, dict]:
    with _conn() as conn:
        rows = conn.execute("SELECT * FROM workflows ORDER BY created_at").fetchall()
    result = {}
    for row in rows:
        w = dict(row)
        w["parameters"] = json.loads(w["parameters"])
        w["blocks"] = json.loads(w["blocks"])
        result[w["id"]] = w
    return result


def load_workflow(wf_id: str) -> dict | None:
    with _conn() as conn:
        row = conn.execute("SELECT * FROM workflows WHERE id = ?", (wf_id,)).fetchone()
    if not row:
        return None
    w = dict(row)
    w["parameters"] = json.loads(w["parameters"])
    w["blocks"] = json.loads(w["blocks"])
    return w


def delete_workflow(wf_id: str):
    with _conn() as conn:
        conn.execute("DELETE FROM workflows WHERE id = ?", (wf_id,))


# ── Workflow Runs CRUD ────────────────────────────────────────────────────────

def save_workflow_run(r: dict):
    with _conn() as conn:
        conn.execute("""
            INSERT OR REPLACE INTO workflow_runs
              (id, workflow_id, status, parameters, block_results,
               current_block, logs, error, started_at, finished_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            r["id"], r["workflow_id"], r["status"],
            json.dumps(r.get("parameters", {}), ensure_ascii=False),
            json.dumps(r.get("block_results", {}), ensure_ascii=False),
            r.get("current_block"),
            json.dumps(r.get("logs", []), ensure_ascii=False),
            r.get("error"),
            r.get("started_at"),
            r.get("finished_at"),
        ))


def load_workflow_runs(workflow_id: str) -> list[dict]:
    with _conn() as conn:
        rows = conn.execute(
            "SELECT * FROM workflow_runs WHERE workflow_id = ? ORDER BY created_at DESC",
            (workflow_id,),
        ).fetchall()
    result = []
    for row in rows:
        r = dict(row)
        r["parameters"] = json.loads(r["parameters"])
        r["block_results"] = json.loads(r["block_results"])
        r["logs"] = json.loads(r["logs"])
        result.append(r)
    return result


def load_workflow_run(run_id: str) -> dict | None:
    with _conn() as conn:
        row = conn.execute("SELECT * FROM workflow_runs WHERE id = ?", (run_id,)).fetchone()
    if not row:
        return None
    r = dict(row)
    r["parameters"] = json.loads(r["parameters"])
    r["block_results"] = json.loads(r["block_results"])
    r["logs"] = json.loads(r["logs"])
    return r
