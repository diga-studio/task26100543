"""派课服务：名单维护、接收批次、限量推进、查询。"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable, List, Sequence

from .models import AdvanceResult, BatchInfo, BatchStatus, LearnerTask
from .store import Store


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class DispatchService:
    def __init__(self, db_path: str) -> None:
        self._store = Store(db_path)

    def close(self) -> None:
        self._store.close()

    def __enter__(self) -> "DispatchService":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # ---- 名单维护 ----
    def set_team_members(self, team_id: str, learner_ids: Iterable[str]) -> None:
        """整体替换一个团队的成员名单，单次调用原子生效。"""
        self._store.replace_team_members(team_id, list(learner_ids))

    # ---- 接收批次 ----
    def receive_batch(
        self,
        request_id: str,
        team_ids: Sequence[str],
        module_version: str,
    ) -> int:
        """接收派课批次，返回批次号。

        冻结接收生效时目标团队的成员并集；同一 request_id 重试
        直接返回原批次，沿用最初冻结的名单与模块版本。
        """
        existing = self._store.find_batch_by_request(request_id)
        if existing is not None:
            return int(existing["batch_id"])

        with self._store.transaction() as conn:
            # 事务内重查，避免并发重复建批
            row = conn.execute(
                "SELECT batch_id FROM batches WHERE request_id = ?", (request_id,)
            ).fetchone()
            if row is not None:
                return int(row["batch_id"])

            batch_id = self._store.insert_batch(
                conn, request_id, module_version, _now()
            )
            self._store.insert_batch_teams(conn, batch_id, team_ids)
            # 在同一事务内读取名单并冻结，保证“接收生效时”的快照语义
            if team_ids:
                placeholders = ",".join("?" for _ in team_ids)
                recipients = [
                    r[0]
                    for r in conn.execute(
                        f"SELECT DISTINCT learner_id FROM team_members"
                        f" WHERE team_id IN ({placeholders})",
                        list(team_ids),
                    ).fetchall()
                ]
            else:
                recipients = []
            self._store.insert_batch_recipients(conn, batch_id, recipients)
        return batch_id

    # ---- 限量推进 ----
    def advance_batch(self, batch_id: int, limit: int) -> AdvanceResult:
        """为批次限量落定学员任务并持久化；全部落定后批次完成。

        已完成批次重复推进不再产生任务。空名单批次推进即完成。
        """
        if limit < 0:
            raise ValueError("limit must be >= 0")

        with self._store.transaction() as conn:
            batch = conn.execute(
                "SELECT * FROM batches WHERE batch_id = ?", (batch_id,)
            ).fetchone()
            if batch is None:
                raise KeyError(f"unknown batch_id: {batch_id}")

            total = self._store.recipient_count(batch_id)

            if batch["status"] == "completed":
                return AdvanceResult(batch_id, 0, 0, BatchStatus.COMPLETED)

            pending = self._store.pending_recipients(conn, batch_id, limit)
            if pending:
                self._store.insert_tasks(
                    conn, batch_id, pending, batch["module_version"], _now()
                )

            tasked = self._store.tasked_count(conn, batch_id)
            remaining = total - tasked
            if remaining == 0:
                self._store.complete_batch(conn, batch_id, _now())
                status = BatchStatus.COMPLETED
            else:
                status = BatchStatus.PENDING
            return AdvanceResult(batch_id, len(pending), remaining, status)

    # ---- 查询 ----
    def get_batch_info(self, batch_id: int) -> BatchInfo:
        batch = self._store.get_batch(batch_id)
        if batch is None:
            raise KeyError(f"unknown batch_id: {batch_id}")
        with self._store.transaction() as conn:
            tasked = self._store.tasked_count(conn, batch_id)
        return BatchInfo(
            batch_id=int(batch["batch_id"]),
            request_id=batch["request_id"],
            module_version=batch["module_version"],
            status=BatchStatus(batch["status"]),
            recipient_count=self._store.recipient_count(batch_id),
            tasked_count=tasked,
            created_at=batch["created_at"],
            completed_at=batch["completed_at"],
        )

    def get_learner_tasks(self, learner_id: str) -> List[LearnerTask]:
        """学员视角：仅整批完成后的任务可见，模块版本来自所属批次。"""
        return [
            LearnerTask(
                batch_id=int(r["batch_id"]),
                learner_id=r["learner_id"],
                module_version=r["module_version"],
                created_at=r["created_at"],
            )
            for r in self._store.tasks_for_learner(learner_id)
        ]
