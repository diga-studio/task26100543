"""类型定义：批次与任务的对外数据结构。"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional


class BatchStatus(str, Enum):
    PENDING = "pending"
    COMPLETED = "completed"


@dataclass(frozen=True)
class BatchInfo:
    batch_id: int
    request_id: str
    module_version: str
    status: BatchStatus
    recipient_count: int
    tasked_count: int
    created_at: str
    completed_at: Optional[str]


@dataclass(frozen=True)
class LearnerTask:
    batch_id: int
    learner_id: str
    module_version: str
    created_at: str


@dataclass(frozen=True)
class AdvanceResult:
    batch_id: int
    assigned: int
    remaining: int
    status: BatchStatus
