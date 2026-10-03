# 团队派课批次库

Python + SQLite 的本地派课批次库，由上层调用。团队名单、批次状态与学员任务全部持久化在本地 SQLite 文件中。

## 能力

- `set_team_members(team_id, learner_ids)`：整体替换团队名单，单次调用原子生效
- `receive_batch(request_id, team_ids, module_version) -> batch_id`：接收派课批次，冻结接收生效时目标团队的成员并集；同一 `request_id` 重试返回原批次
- `advance_batch(batch_id, limit) -> AdvanceResult`：限量落定学员任务并持久化；全部落定后批次完成（空名单批次推进即完成）
- `get_batch_info(batch_id) -> BatchInfo` / `get_learner_tasks(learner_id) -> [LearnerTask]`：批次状态查询；学员仅能看到整批完成后的任务

## 语义保证

- 批次范围在接收时冻结，之后的入组、退组、名单替换均不影响
- 同一批次与学员之间恰有一份任务（`tasks` 表 `(batch_id, learner_id)` 主键）
- 不同批次互不合并，任务模块版本来自所属批次
- 未完成批次对学员不可见；完成后所有接收人同时可见
- 已完成批次重复接收/推进不再产生任务

## 示例

```python
from dispatch import DispatchService

with DispatchService("dispatch.sqlite3") as svc:
    svc.set_team_members("A", ["s1", "s2"])
    svc.set_team_members("B", ["s2", "s3"])
    batch_id = svc.receive_batch("req-1", ["A", "B"], "mod-v1")
    while True:
        result = svc.advance_batch(batch_id, limit=100)
        if result.status == "completed":
            break
    tasks = svc.get_learner_tasks("s1")
```

## 测试

```sh
python3 -m unittest discover -s tests -v
```
