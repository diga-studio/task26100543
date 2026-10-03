"""派课批次：接收、限量推进与查询。"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Iterable, List

STATUS_PENDING = "pending"
STATUS_COMPLETED = "completed"


@dataclass(frozen=True)
class BatchInfo:
    batch_id: int
    request_id: str
    module_version: str
    status: str
    total: int
    done: int


@dataclass(frozen=True)
class TaskView:
    task_id: int
    batch_id: int
    student_id: str
    module_version: str


def receive_batch(
    conn: sqlite3.Connection,
    request_id: str,
    team_ids: Iterable[str],
    module_version: str,
) -> BatchInfo:
    """接收批次：冻结目标团队成员并集；同 request_id 重试返回原批次。"""
    with conn:
        cursor = conn.execute(
            "INSERT OR IGNORE INTO batches (request_id, module_version) VALUES (?, ?)",
            (request_id, module_version),
        )
        if cursor.rowcount == 1:
            batch_id = cursor.lastrowid
            ids = list(team_ids)
            if ids:
                placeholders = ",".join("?" for _ in ids)
                conn.execute(
                    f"INSERT INTO batch_members (batch_id, student_id) "
                    f"SELECT DISTINCT ?, student_id FROM teams WHERE team_id IN ({placeholders})",
                    [batch_id, *ids],
                )
    row = conn.execute(
        "SELECT batch_id FROM batches WHERE request_id = ?", (request_id,)
    ).fetchone()
    return get_batch(conn, row[0])


def advance_batch(
    conn: sqlite3.Connection, batch_id: int, limit: int
) -> List[TaskView]:
    """限量推进：至多落定 limit 份任务；名单用尽则完成批次。"""
    if limit < 0:
        raise ValueError("limit 不能为负数")
    with conn:
        row = conn.execute(
            "SELECT module_version, status FROM batches WHERE batch_id = ?",
            (batch_id,),
        ).fetchone()
        if row is None:
            raise KeyError(f"批次不存在: {batch_id}")
        module_version, status = row
        if status == STATUS_COMPLETED:
            return []
        pending = conn.execute(
            "SELECT bm.student_id FROM batch_members bm "
            "LEFT JOIN tasks t ON t.batch_id = bm.batch_id AND t.student_id = bm.student_id "
            "WHERE bm.batch_id = ? AND t.task_id IS NULL "
            "ORDER BY bm.student_id LIMIT ?",
            (batch_id, limit),
        ).fetchall()
        created = []
        for (student_id,) in pending:
            cursor = conn.execute(
                "INSERT INTO tasks (batch_id, student_id, module_version) VALUES (?, ?, ?)",
                (batch_id, student_id, module_version),
            )
            created.append(
                TaskView(
                    task_id=cursor.lastrowid,
                    batch_id=batch_id,
                    student_id=student_id,
                    module_version=module_version,
                )
            )
        remaining = conn.execute(
            "SELECT COUNT(*) FROM batch_members bm "
            "LEFT JOIN tasks t ON t.batch_id = bm.batch_id AND t.student_id = bm.student_id "
            "WHERE bm.batch_id = ? AND t.task_id IS NULL",
            (batch_id,),
        ).fetchone()[0]
        if remaining == 0:
            conn.execute(
                "UPDATE batches SET status = ? WHERE batch_id = ?",
                (STATUS_COMPLETED, batch_id),
            )
        return created


def get_batch(conn: sqlite3.Connection, batch_id: int) -> BatchInfo:
    """查询批次状态。"""
    row = conn.execute(
        "SELECT request_id, module_version, status FROM batches WHERE batch_id = ?",
        (batch_id,),
    ).fetchone()
    if row is None:
        raise KeyError(f"批次不存在: {batch_id}")
    request_id, module_version, status = row
    total = conn.execute(
        "SELECT COUNT(*) FROM batch_members WHERE batch_id = ?", (batch_id,)
    ).fetchone()[0]
    done = conn.execute(
        "SELECT COUNT(*) FROM tasks WHERE batch_id = ?", (batch_id,)
    ).fetchone()[0]
    return BatchInfo(
        batch_id=batch_id,
        request_id=request_id,
        module_version=module_version,
        status=status,
        total=total,
        done=done,
    )


def list_student_tasks(conn: sqlite3.Connection, student_id: str) -> List[TaskView]:
    """学员视角任务列表：只包含已完成批次的任务。"""
    rows = conn.execute(
        "SELECT t.task_id, t.batch_id, t.student_id, t.module_version "
        "FROM tasks t JOIN batches b ON b.batch_id = t.batch_id "
        "WHERE t.student_id = ? AND b.status = ? "
        "ORDER BY t.task_id",
        (student_id, STATUS_COMPLETED),
    ).fetchall()
    return [
        TaskView(task_id=r[0], batch_id=r[1], student_id=r[2], module_version=r[3])
        for r in rows
    ]
