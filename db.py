"""
SQLite persistence layer for tasks, explore tasks, memories, and recordings.
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
                created_at TEXT DEFAULT (datetime('now')),
                started_at TEXT,
                finished_at TEXT
            );

        """)
        # Migration: add timing columns if missing
        cols = {r[1] for r in conn.execute("PRAGMA table_info(tasks)").fetchall()}
        if "started_at" not in cols:
            conn.execute("ALTER TABLE tasks ADD COLUMN started_at TEXT")
        if "finished_at" not in cols:
            conn.execute("ALTER TABLE tasks ADD COLUMN finished_at TEXT")
        conn.executescript("""
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
              (id, task, status, logs, screenshots, curation, generated, started_at, finished_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            t["id"], t["task"], t["status"],
            json.dumps(t.get("logs", []), ensure_ascii=False),
            json.dumps(t.get("screenshots", []), ensure_ascii=False),
            json.dumps(t["curation"], ensure_ascii=False) if t.get("curation") else None,
            json.dumps(t["generated"], ensure_ascii=False) if t.get("generated") else None,
            t.get("started_at"),
            t.get("finished_at"),
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


# ── Memories ─────────────────────────────────────────────────────────────────

def init_memory_db():
    DB_PATH.parent.mkdir(exist_ok=True)
    with _conn() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS memories (
                id TEXT PRIMARY KEY,
                memory_type TEXT NOT NULL,
                domain TEXT NOT NULL DEFAULT '',
                title TEXT NOT NULL,
                content TEXT NOT NULL,
                source_task_id TEXT,
                hit_count INTEGER DEFAULT 0,
                last_used_at TEXT,
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT DEFAULT (datetime('now'))
            );
            CREATE INDEX IF NOT EXISTS idx_memories_domain ON memories(domain);
            CREATE INDEX IF NOT EXISTS idx_memories_type ON memories(memory_type);
        """)


def save_memory(m: dict):
    with _conn() as conn:
        conn.execute("""
            INSERT OR REPLACE INTO memories
              (id, memory_type, domain, title, content, source_task_id,
               hit_count, last_used_at, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
        """, (
            m["id"], m["memory_type"], m.get("domain", ""),
            m["title"], json.dumps(m["content"], ensure_ascii=False) if isinstance(m["content"], (dict, list)) else m["content"],
            m.get("source_task_id"),
            m.get("hit_count", 0), m.get("last_used_at"),
            m.get("created_at"),
        ))


def load_memories(domain: str = None, memory_type: str = None) -> list[dict]:
    query = "SELECT * FROM memories WHERE 1=1"
    params = []
    if domain:
        query += " AND domain = ?"
        params.append(domain)
    if memory_type:
        query += " AND memory_type = ?"
        params.append(memory_type)
    query += " ORDER BY updated_at DESC"
    with _conn() as conn:
        rows = conn.execute(query, params).fetchall()
    result = []
    for row in rows:
        m = dict(row)
        try:
            m["content"] = json.loads(m["content"])
        except (json.JSONDecodeError, TypeError):
            pass
        result.append(m)
    return result


def get_memory(memory_id: str) -> dict | None:
    with _conn() as conn:
        row = conn.execute("SELECT * FROM memories WHERE id = ?", (memory_id,)).fetchone()
    if not row:
        return None
    m = dict(row)
    try:
        m["content"] = json.loads(m["content"])
    except (json.JSONDecodeError, TypeError):
        pass
    return m


def delete_memory(memory_id: str) -> bool:
    with _conn() as conn:
        cur = conn.execute("DELETE FROM memories WHERE id = ?", (memory_id,))
        return cur.rowcount > 0


def delete_memories_batch(ids: list[str]) -> int:
    if not ids:
        return 0
    placeholders = ",".join("?" for _ in ids)
    with _conn() as conn:
        cur = conn.execute(f"DELETE FROM memories WHERE id IN ({placeholders})", ids)
        return cur.rowcount


def update_memory_hit(memory_id: str):
    with _conn() as conn:
        conn.execute(
            "UPDATE memories SET hit_count = hit_count + 1, last_used_at = datetime('now') WHERE id = ?",
            (memory_id,),
        )


def get_memory_stats() -> dict:
    with _conn() as conn:
        total = conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
        by_type = {
            row[0]: row[1]
            for row in conn.execute("SELECT memory_type, COUNT(*) FROM memories GROUP BY memory_type").fetchall()
        }
        top_domains = [
            {"domain": row[0], "count": row[1]}
            for row in conn.execute(
                "SELECT domain, COUNT(*) as cnt FROM memories WHERE domain != '' GROUP BY domain ORDER BY cnt DESC LIMIT 10"
            ).fetchall()
        ]
    return {"total": total, "by_type": by_type, "top_domains": top_domains}


# ── Recordings ───────────────────────────────────────────────────────────────

def init_recording_db():
    DB_PATH.parent.mkdir(exist_ok=True)
    with _conn() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS recordings (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL DEFAULT '',
                start_url TEXT NOT NULL DEFAULT '',
                actions TEXT NOT NULL DEFAULT '[]',
                parameters TEXT NOT NULL DEFAULT '[]',
                workflow_id TEXT,
                status TEXT NOT NULL DEFAULT 'recording',
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT DEFAULT (datetime('now'))
            );
        """)


def save_recording(r: dict):
    with _conn() as conn:
        conn.execute("""
            INSERT OR REPLACE INTO recordings
              (id, title, start_url, actions, parameters, workflow_id, status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
        """, (
            r["id"], r.get("title", ""), r.get("start_url", ""),
            json.dumps(r.get("actions", []), ensure_ascii=False),
            json.dumps(r.get("parameters", []), ensure_ascii=False),
            r.get("workflow_id"),
            r.get("status", "recording"),
            r.get("created_at"),
        ))


def load_all_recordings() -> list[dict]:
    with _conn() as conn:
        rows = conn.execute("SELECT * FROM recordings ORDER BY created_at DESC").fetchall()
    result = []
    for row in rows:
        r = dict(row)
        r["actions"] = json.loads(r["actions"])
        r["parameters"] = json.loads(r["parameters"])
        result.append(r)
    return result


def get_recording(recording_id: str) -> dict | None:
    with _conn() as conn:
        row = conn.execute("SELECT * FROM recordings WHERE id = ?", (recording_id,)).fetchone()
    if not row:
        return None
    r = dict(row)
    r["actions"] = json.loads(r["actions"])
    r["parameters"] = json.loads(r["parameters"])
    return r


def delete_recording(recording_id: str) -> bool:
    with _conn() as conn:
        cur = conn.execute("DELETE FROM recordings WHERE id = ?", (recording_id,))
        return cur.rowcount > 0
