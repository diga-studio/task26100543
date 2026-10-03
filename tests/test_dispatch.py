import os
import tempfile
import unittest

from dispatch import BatchStatus, DispatchService


class DispatchTestCase(unittest.TestCase):
    def setUp(self) -> None:
        fd, self.db_path = tempfile.mkstemp(suffix=".sqlite3")
        os.close(fd)
        os.unlink(self.db_path)
        self.svc = DispatchService(self.db_path)

    def tearDown(self) -> None:
        self.svc.close()
        if os.path.exists(self.db_path):
            os.unlink(self.db_path)

    def learner_task_map(self, learner_id: str):
        return {t.batch_id: t for t in self.svc.get_learner_tasks(learner_id)}


class TestFrozenRoster(DispatchTestCase):
    def test_overlapping_teams_and_roster_replacement_before_advance(self):
        # 团队重叠：s2 同时属于 A、B
        self.svc.set_team_members("A", ["s1", "s2", "s3"])
        self.svc.set_team_members("B", ["s2", "s4"])
        expected_union = {"s1", "s2", "s3", "s4"}

        batch_id = self.svc.receive_batch("req-1", ["A", "B"], "mod-v1")

        # 接收后、首次推进前整体替换名单：不应影响已冻结批次
        self.svc.set_team_members("A", ["s9", "s10"])
        self.svc.set_team_members("B", [])

        info = self.svc.get_batch_info(batch_id)
        self.assertEqual(info.recipient_count, 4)
        self.assertEqual(info.status, BatchStatus.PENDING)

        # 分多次限量推进
        r1 = self.svc.advance_batch(batch_id, 1)
        self.assertEqual((r1.assigned, r1.remaining), (1, 3))
        r2 = self.svc.advance_batch(batch_id, 2)
        self.assertEqual((r2.assigned, r2.remaining), (2, 1))
        r3 = self.svc.advance_batch(batch_id, 10)
        self.assertEqual((r3.assigned, r3.remaining), (1, 0))
        self.assertEqual(r3.status, BatchStatus.COMPLETED)

        # 最终任务集合与接收时独立计算的并集完全一致
        tasked = set()
        for lid in expected_union | {"s9", "s10"}:
            for t in self.svc.get_learner_tasks(lid):
                self.assertNotIn(t.learner_id, tasked)
                tasked.add(t.learner_id)
                self.assertEqual(t.module_version, "mod-v1")
        self.assertEqual(tasked, expected_union)

        # 新名单成员不在旧批次中
        self.assertEqual(self.svc.get_learner_tasks("s9"), [])
        self.assertEqual(self.svc.get_learner_tasks("s10"), [])


class TestRetryAndVisibility(DispatchTestCase):
    def test_retry_and_multiple_advances(self):
        self.svc.set_team_members("A", ["s1", "s2"])
        self.svc.set_team_members("B", ["s2", "s3"])

        b1 = self.svc.receive_batch("req-1", ["A", "B"], "mod-v1")
        # 原请求重试：返回原批次，沿用最初冻结的名单与模块版本
        b2 = self.svc.receive_batch("req-1", ["A", "B"], "mod-v1")
        self.assertEqual(b1, b2)
        info = self.svc.get_batch_info(b1)
        self.assertEqual(info.module_version, "mod-v1")
        self.assertEqual(info.recipient_count, 3)

        # 第一次推进后：批次未完成，学员不可见任何任务
        self.svc.advance_batch(b1, 2)
        for lid in ("s1", "s2", "s3"):
            self.assertEqual(self.svc.get_learner_tasks(lid), [])

        # 中途改名单不影响批次
        self.svc.set_team_members("A", ["s7"])
        self.svc.advance_batch(b1, 1)
        self.assertEqual(self.svc.get_batch_info(b1).status, BatchStatus.COMPLETED)

        # 完成后：所有接收人同时可见，模块版本来自批次
        for lid in ("s1", "s2", "s3"):
            tasks = self.svc.get_learner_tasks(lid)
            self.assertEqual(len(tasks), 1)
            self.assertEqual(tasks[0].batch_id, b1)
            self.assertEqual(tasks[0].module_version, "mod-v1")
        self.assertEqual(self.svc.get_learner_tasks("s7"), [])

        # 已完成批次重复接收/推进不再产生任务
        b3 = self.svc.receive_batch("req-1", ["A", "B"], "mod-v1")
        self.assertEqual(b3, b1)
        r = self.svc.advance_batch(b1, 100)
        self.assertEqual((r.assigned, r.remaining), (0, 0))
        for lid in ("s1", "s2", "s3"):
            self.assertEqual(len(self.svc.get_learner_tasks(lid)), 1)

    def test_multiple_batches_do_not_merge(self):
        self.svc.set_team_members("A", ["s1", "s2"])
        b1 = self.svc.receive_batch("req-1", ["A"], "mod-v1")
        b2 = self.svc.receive_batch("req-2", ["A"], "mod-v2")
        self.assertNotEqual(b1, b2)

        self.svc.advance_batch(b1, 100)
        # b1 完成可见，b2 未完成不可见
        tasks = self.learner_task_map("s1")
        self.assertEqual(set(tasks), {b1})
        self.assertEqual(tasks[b1].module_version, "mod-v1")

        self.svc.advance_batch(b2, 100)
        tasks = self.learner_task_map("s1")
        self.assertEqual(set(tasks), {b1, b2})
        self.assertEqual(tasks[b2].module_version, "mod-v2")

    def test_empty_roster_batch_completes(self):
        self.svc.set_team_members("A", [])
        b = self.svc.receive_batch("req-empty", ["A", "MISSING"], "mod-v1")
        self.assertEqual(self.svc.get_batch_info(b).recipient_count, 0)
        r = self.svc.advance_batch(b, 5)
        self.assertEqual(r.status, BatchStatus.COMPLETED)
        self.assertEqual(r.assigned, 0)
        self.assertEqual(self.svc.get_batch_info(b).status, BatchStatus.COMPLETED)

    def test_zero_limit_advance_is_noop(self):
        self.svc.set_team_members("A", ["s1"])
        b = self.svc.receive_batch("req-1", ["A"], "mod-v1")
        r = self.svc.advance_batch(b, 0)
        self.assertEqual((r.assigned, r.remaining), (0, 1))
        self.assertEqual(self.svc.get_batch_info(b).status, BatchStatus.PENDING)
        self.assertEqual(self.svc.get_learner_tasks("s1"), [])

    def test_unknown_batch_raises(self):
        with self.assertRaises(KeyError):
            self.svc.advance_batch(999, 1)
        with self.assertRaises(KeyError):
            self.svc.get_batch_info(999)

    def test_persistence_across_reopen(self):
        self.svc.set_team_members("A", ["s1", "s2"])
        b = self.svc.receive_batch("req-1", ["A"], "mod-v1")
        self.svc.advance_batch(b, 1)
        self.svc.close()
        self.svc = DispatchService(self.db_path)
        # 重开后重试仍返回原批次，推进继续从未完成任务开始
        self.assertEqual(self.svc.receive_batch("req-1", ["A"], "mod-v1"), b)
        r = self.svc.advance_batch(b, 10)
        self.assertEqual((r.assigned, r.remaining), (1, 0))
        self.assertEqual(len(self.svc.get_learner_tasks("s1")), 1)
        self.assertEqual(len(self.svc.get_learner_tasks("s2")), 1)


if __name__ == "__main__":
    unittest.main()
