"""团队名单维护：每次更新作为一个整体生效。"""

from __future__ import annotations

import sqlite3
from typing import Dict, Iterable, Set


def replace_roster(conn: sqlite3.Connection, teams: Dict[str, Iterable[str]]) -> None:
    """整体替换团队名单。teams: team_id -> 学员稳定标识集合。"""
    with conn:
        conn.execute("DELETE FROM teams")
        conn.executemany(
            "INSERT INTO teams (team_id, student_id) VALUES (?, ?)",
            [(team_id, student_id) for team_id, members in teams.items() for student_id in members],
        )


def members_of(conn: sqlite3.Connection, team_ids: Iterable[str]) -> Set[str]:
    """返回给定团队集合当前成员的稳定标识并集。"""
    ids = list(team_ids)
    if not ids:
        return set()
    placeholders = ",".join("?" for _ in ids)
    rows = conn.execute(
        f"SELECT DISTINCT student_id FROM teams WHERE team_id IN ({placeholders})",
        ids,
    ).fetchall()
    return {row[0] for row in rows}
