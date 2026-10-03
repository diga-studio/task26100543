"""SQLite 存储层：schema 与底层读写。

所有多步写操作都在单个事务内完成（BEGIN IMMEDIATE），
保证名单替换、批次冻结、限量推进的原子性。
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from typing import Iterator, List, Optional, Sequence, Tuple

SCHEMA = """
CREATE TABLE IF NOT EXISTS team_members (
    team_id    TEXT NOT NULL,
    learner_id TEXT NOT NULL,
    PRIMARY KEY (team_id, learner_id)
);

CREATE TABLE IF NOT EXISTS batches (
    batch_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id     TEXT NOT NULL UNIQUE,
    module_version TEXT NOT NULL,
    status         TEXT NOT NULL DEFAULT 'pending',
    created_at     TEXT NOT NULL,
    completed_at   TEXT
);

CREATE TABLE IF NOT EXISTS batch_teams (
    batch_id INTEGER NOT NULL REFERENCES batches(batch_id),
    team_id  TEXT NOT NULL,
    PRIMARY KEY (batch_id, team_id)
);

CREATE TABLE IF NOT EXISTS batch_recipients (
    batch_id   INTEGER NOT NULL REFERENCES batches(batch_id),
    learner_id TEXT NOT NULL,
    PRIMARY KEY (batch_id, learner_id)
);

CREATE TABLE IF NOT EXISTS tasks (
    batch_id       INTEGER NOT NULL REFERENCES batches(batch_id),
    learner_id     TEXT NOT NULL,
    module_version TEXT NOT NULL,
    created_at     TEXT NOT NULL,
    PRIMARY KEY (batch_id, learner_id)
);
"""


class Store:
    def __init__(self, db_path: str) -> None:
        self._conn = sqlite3.connect(db_path)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.executescript(SCHEMA)

    def close(self) -> None:
        self._conn.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """单事务执行多步写；BEGIN IMMEDIATE 保证写期间名单不被并发改动。"""
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            yield self._conn
        except Exception:
            self._conn.rollback()
            raise
        else:
            self._conn.commit()

    # ---- 名单 ----
    def replace_team_members(self, team_id: str, learner_ids: Sequence[str]) -> None:
        """整体替换一个团队的成员名单。"""
        with self.transaction() as conn:
            conn.execute("DELETE FROM team_members WHERE team_id = ?", (team_id,))
            conn.executemany(
                "INSERT OR IGNORE INTO team_members (team_id, learner_id) VALUES (?, ?)",
                [(team_id, lid) for lid in dict.fromkeys(learner_ids)],
            )

    def union_members(self, team_ids: Sequence[str]) -> List[str]:
        """计算多个团队当前成员并集（去重）。"""
        if not team_ids:
            return []
        placeholders = ",".join("?" for _ in team_ids)
        rows = self._conn.execute(
            f"SELECT DISTINCT learner_id FROM team_members WHERE team_id IN ({placeholders})",
            list(team_ids),
        ).fetchall()
        return [r["learner_id"] for r in rows]

    # ---- 批次 ----
    def find_batch_by_request(self, request_id: str) -> Optional[sqlite3.Row]:
        return self._conn.execute(
            "SELECT * FROM batches WHERE request_id = ?", (request_id,)
        ).fetchone()

    def get_batch(self, batch_id: int) -> Optional[sqlite3.Row]:
        return self._conn.execute(
            "SELECT * FROM batches WHERE batch_id = ?", (batch_id,)
        ).fetchone()

    def insert_batch(
        self,
        conn: sqlite3.Connection,
        request_id: str,
        module_version: str,
        created_at: str,
    ) -> int:
        cur = conn.execute(
            "INSERT INTO batches (request_id, module_version, status, created_at)"
            " VALUES (?, ?, 'pending', ?)",
            (request_id, module_version, created_at),
        )
        return int(cur.lastrowid)

    def insert_batch_teams(
        self, conn: sqlite3.Connection, batch_id: int, team_ids: Sequence[str]
    ) -> None:
        conn.executemany(
            "INSERT OR IGNORE INTO batch_teams (batch_id, team_id) VALUES (?, ?)",
            [(batch_id, tid) for tid in dict.fromkeys(team_ids)],
        )

    def insert_batch_recipients(
        self, conn: sqlite3.Connection, batch_id: int, learner_ids: Sequence[str]
    ) -> None:
        conn.executemany(
            "INSERT OR IGNORE INTO batch_recipients (batch_id, learner_id) VALUES (?, ?)",
            [(batch_id, lid) for lid in dict.fromkeys(learner_ids)],
        )

    def complete_batch(
        self, conn: sqlite3.Connection, batch_id: int, completed_at: str
    ) -> None:
        conn.execute(
            "UPDATE batches SET status = 'completed', completed_at = ?"
            " WHERE batch_id = ? AND status = 'pending'",
            (completed_at, batch_id),
        )

    # ---- 接收人 / 任务 ----
    def recipient_count(self, batch_id: int) -> int:
        row = self._conn.execute(
            "SELECT COUNT(*) AS c FROM batch_recipients WHERE batch_id = ?",
            (batch_id,),
        ).fetchone()
        return int(row["c"])

    def pending_recipients(
        self, conn: sqlite3.Connection, batch_id: int, limit: int
    ) -> List[str]:
        """该批次中尚未落定任务的接收人，按稳定顺序取前 limit 个。"""
        rows = conn.execute(
            """
            SELECT r.learner_id
            FROM batch_recipients r
            WHERE r.batch_id = ?
              AND NOT EXISTS (
                  SELECT 1 FROM tasks t
                  WHERE t.batch_id = r.batch_id AND t.learner_id = r.learner_id
              )
            ORDER BY r.learner_id
            LIMIT ?
            """,
            (batch_id, limit),
        ).fetchall()
        return [r["learner_id"] for r in rows]

    def insert_tasks(
        self,
        conn: sqlite3.Connection,
        batch_id: int,
        learner_ids: Sequence[str],
        module_version: str,
        created_at: str,
    ) -> None:
        conn.executemany(
            "INSERT OR IGNORE INTO tasks (batch_id, learner_id, module_version, created_at)"
            " VALUES (?, ?, ?, ?)",
            [(batch_id, lid, module_version, created_at) for lid in learner_ids],
        )

    def tasked_count(self, conn: sqlite3.Connection, batch_id: int) -> int:
        row = conn.execute(
            "SELECT COUNT(*) AS c FROM tasks WHERE batch_id = ?", (batch_id,)
        ).fetchone()
        return int(row["c"])

    def tasks_for_learner(self, learner_id: str) -> List[sqlite3.Row]:
        """仅返回已完成批次中的任务（学员视角）。"""
        return self._conn.execute(
            """
            SELECT t.batch_id, t.learner_id, t.module_version, t.created_at
            FROM tasks t
            JOIN batches b ON b.batch_id = t.batch_id
            WHERE t.learner_id = ? AND b.status = 'completed'
            ORDER BY t.batch_id
            """,
            (learner_id,),
        ).fetchall()

    def all_tasks_for_batch(self, batch_id: int) -> List[sqlite3.Row]:
        return self._conn.execute(
            "SELECT * FROM tasks WHERE batch_id = ? ORDER BY learner_id",
            (batch_id,),
        ).fetchall()
