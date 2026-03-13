"""
SQLite persistence layer for tasks and explore tasks.
Stores task metadata + JSON blobs for curation/generated results.
Screenshots stay on disk as files.
"""

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path

DB_PATH = Path("data/tasks.db")


def init_db():
    DB_PATH.parent.mkdir(exist_ok=True)
    with _conn() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS tasks (
                id TEXT PRIMARY KEY,
                task TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                logs TEXT NOT NULL DEFAULT '[]',
                screenshots TEXT NOT NULL DEFAULT '[]',
                curation TEXT,
                generated TEXT,
                created_at TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS explore_tasks (
                id TEXT PRIMARY KEY,
                url TEXT NOT NULL,
                product_context TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'pending',
                logs TEXT NOT NULL DEFAULT '[]',
                screenshots TEXT NOT NULL DEFAULT '[]',
                result TEXT,
                curation TEXT,
                generated TEXT,
                created_at TEXT DEFAULT (datetime('now'))
            );
        """)


@contextmanager
def _conn():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


# ── Tasks ─────────────────────────────────────────────────────────────────────

def save_task(t: dict):
    with _conn() as conn:
        conn.execute("""
            INSERT OR REPLACE INTO tasks
              (id, task, status, logs, screenshots, curation, generated)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            t["id"], t["task"], t["status"],
            json.dumps(t.get("logs", []), ensure_ascii=False),
            json.dumps(t.get("screenshots", []), ensure_ascii=False),
            json.dumps(t["curation"], ensure_ascii=False) if t.get("curation") else None,
            json.dumps(t["generated"], ensure_ascii=False) if t.get("generated") else None,
        ))


def load_all_tasks() -> dict[str, dict]:
    with _conn() as conn:
        rows = conn.execute("SELECT * FROM tasks ORDER BY created_at").fetchall()
    result = {}
    for row in rows:
        t = dict(row)
        t["logs"] = json.loads(t["logs"])
        t["screenshots"] = json.loads(t["screenshots"])
        t["curation"] = json.loads(t["curation"]) if t["curation"] else None
        t["generated"] = json.loads(t["generated"]) if t["generated"] else None
        result[t["id"]] = t
    return result


# ── Explore tasks ─────────────────────────────────────────────────────────────

def save_explore_task(t: dict):
    with _conn() as conn:
        conn.execute("""
            INSERT OR REPLACE INTO explore_tasks
              (id, url, product_context, status, logs, screenshots, result, curation, generated)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            t["id"], t["url"], t.get("product_context", ""), t["status"],
            json.dumps(t.get("logs", []), ensure_ascii=False),
            json.dumps(t.get("screenshots", []), ensure_ascii=False),
            json.dumps(t.get("result"), ensure_ascii=False) if t.get("result") else None,
            json.dumps(t["curation"], ensure_ascii=False) if t.get("curation") else None,
            json.dumps(t["generated"], ensure_ascii=False) if t.get("generated") else None,
        ))


def load_all_explore_tasks() -> dict[str, dict]:
    with _conn() as conn:
        rows = conn.execute("SELECT * FROM explore_tasks ORDER BY created_at").fetchall()
    result = {}
    for row in rows:
        t = dict(row)
        t["logs"] = json.loads(t["logs"])
        t["screenshots"] = json.loads(t["screenshots"])
        t["result"] = json.loads(t["result"]) if t["result"] else None
        t["curation"] = json.loads(t["curation"]) if t["curation"] else None
        t["generated"] = json.loads(t["generated"]) if t["generated"] else None
        result[t["id"]] = t
    return result
