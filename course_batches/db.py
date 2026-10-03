"""SQLite 连接与表结构定义。"""

from __future__ import annotations

import sqlite3

SCHEMA = """
CREATE TABLE IF NOT EXISTS teams (
    team_id TEXT NOT NULL,
    student_id TEXT NOT NULL,
    PRIMARY KEY (team_id, student_id)
);

CREATE TABLE IF NOT EXISTS batches (
    batch_id INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id TEXT NOT NULL UNIQUE,
    module_version TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS batch_members (
    batch_id INTEGER NOT NULL REFERENCES batches(batch_id),
    student_id TEXT NOT NULL,
    PRIMARY KEY (batch_id, student_id)
);

CREATE TABLE IF NOT EXISTS tasks (
    task_id INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id INTEGER NOT NULL REFERENCES batches(batch_id),
    student_id TEXT NOT NULL,
    module_version TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (batch_id, student_id)
);
"""


def connect(path: str) -> sqlite3.Connection:
    """打开（必要时创建）数据库并确保表结构存在。"""
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    return conn
