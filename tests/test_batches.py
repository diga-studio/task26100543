"""派课批次库自动化测试。"""

import pytest

from course_batches import (
    STATUS_COMPLETED,
    STATUS_PENDING,
    advance_batch,
    connect,
    get_batch,
    list_student_tasks,
    members_of,
    receive_batch,
    replace_roster,
)


@pytest.fixture()
def conn(tmp_path):
    db = connect(str(tmp_path / "batches.db"))
    yield db
    db.close()


def test_overlapping_teams_and_roster_replacement_before_first_advance(conn):
    # 团队名单重叠：s2 同时属于两个班组
    replace_roster(conn, {"team-a": ["s1", "s2"], "team-b": ["s2", "s3"]})
    # 接收时的成员并集，独立计算
    expected = members_of(conn, ["team-a", "team-b"])
    assert expected == {"s1", "s2", "s3"}

    batch = receive_batch(conn, "req-1", ["team-a", "team-b"], "mod-v1")
    assert batch.status == STATUS_PENDING
    assert batch.total == 3
    assert batch.done == 0

    # 接收后、首次推进前整体替换名单：s3 退组，s4 新入组
    replace_roster(conn, {"team-a": ["s1", "s4"], "team-b": ["s2", "s4"]})

    created = []
    while get_batch(conn, batch.batch_id).status != STATUS_COMPLETED:
        created.extend(advance_batch(conn, batch.batch_id, 2))

    # 最终任务集合必须与接收时冻结的并集完全一致
    assert {t.student_id for t in created} == expected
    assert len(created) == len(expected)  # 重叠学员只收一份
    assert all(t.module_version == "mod-v1" for t in created)

    # 学员视角同样只能看到冻结范围内、且模块版本来自该批次的任务
    for student in expected:
        visible = list_student_tasks(conn, student)
        assert [(t.batch_id, t.module_version) for t in visible] == [
            (batch.batch_id, "mod-v1")
        ]
    assert list_student_tasks(conn, "s4") == []


def test_retry_and_multiple_advances_visibility(conn):
    replace_roster(conn, {"team-a": ["s1", "s2", "s3", "s4", "s5"]})

    batch = receive_batch(conn, "req-2", ["team-a"], "mod-v9")
    # 原请求重试：返回原批次，沿用最初冻结的名单与模块版本
    replace_roster(conn, {"team-a": ["s1", "s6"]})
    retry = receive_batch(conn, "req-2", ["team-a"], "mod-v9")
    assert retry.batch_id == batch.batch_id
    assert retry.module_version == "mod-v9"
    assert retry.total == 5

    # 多次限量推进；推进期间任何未完成批次都不可被学员看到
    created = []
    for _ in range(2):
        created.extend(advance_batch(conn, batch.batch_id, 2))
        assert get_batch(conn, batch.batch_id).status == STATUS_PENDING
        for student in ("s1", "s2", "s3", "s4", "s5"):
            assert list_student_tasks(conn, student) == []

    created.extend(advance_batch(conn, batch.batch_id, 2))
    assert get_batch(conn, batch.batch_id).status == STATUS_COMPLETED

    # 最终无重复、无遗漏
    assert sorted(t.student_id for t in created) == ["s1", "s2", "s3", "s4", "s5"]
    assert len({t.task_id for t in created}) == 5

    # 完成后所有接收人同时可见
    for student in ("s1", "s2", "s3", "s4", "s5"):
        visible = list_student_tasks(conn, student)
        assert len(visible) == 1
        assert visible[0].module_version == "mod-v9"

    # 已完成批次重复接收或推进不再产生任务
    again = receive_batch(conn, "req-2", ["team-a"], "mod-v9")
    assert again.batch_id == batch.batch_id
    assert again.status == STATUS_COMPLETED
    assert advance_batch(conn, batch.batch_id, 10) == []
    assert get_batch(conn, batch.batch_id).done == 5


def test_empty_roster_batch_completes(conn):
    replace_roster(conn, {"team-empty": []})
    batch = receive_batch(conn, "req-3", ["team-empty"], "mod-v1")
    assert batch.total == 0
    assert advance_batch(conn, batch.batch_id, 5) == []
    assert get_batch(conn, batch.batch_id).status == STATUS_COMPLETED


def test_batches_are_independent(conn):
    replace_roster(conn, {"team-a": ["s1", "s2"]})
    b1 = receive_batch(conn, "req-4", ["team-a"], "mod-v1")
    b2 = receive_batch(conn, "req-5", ["team-a"], "mod-v2")
    assert b1.batch_id != b2.batch_id

    advance_batch(conn, b1.batch_id, 10)
    advance_batch(conn, b2.batch_id, 10)

    # 不同批次互不合并：同一学员在两个批次各有一份任务
    visible = list_student_tasks(conn, "s1")
    assert sorted((t.batch_id, t.module_version) for t in visible) == [
        (b1.batch_id, "mod-v1"),
        (b2.batch_id, "mod-v2"),
    ]


def test_negative_limit_rejected(conn):
    replace_roster(conn, {"team-a": ["s1"]})
    batch = receive_batch(conn, "req-6", ["team-a"], "mod-v1")
    with pytest.raises(ValueError):
        advance_batch(conn, batch.batch_id, -1)
