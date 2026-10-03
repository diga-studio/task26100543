# task26100543

团队派课批次库（Python + SQLite，纯本地文件持久化，由上层调用）。

## 能力

- `replace_roster(conn, teams)`：整体替换团队名单（`team_id -> [学员稳定标识]`）。
- `receive_batch(conn, request_id, team_ids, module_version)`：接收批次，冻结目标团队成员并集；
  同一 `request_id` 重试返回原批次。只建待处理批次，不产生学员任务。
- `advance_batch(conn, batch_id, limit)`：限量推进，至多落定 `limit` 份任务；冻结名单用尽后批次完成。
- `get_batch(conn, batch_id)` / `list_student_tasks(conn, student_id)`：查询批次状态与学员可见任务
  （仅整批完成后可见）。

## 示例

```python
from course_batches import connect, replace_roster, receive_batch, advance_batch, get_batch

conn = connect("batches.db")
replace_roster(conn, {"team-a": ["s1", "s2"], "team-b": ["s2", "s3"]})
batch = receive_batch(conn, "req-1", ["team-a", "team-b"], "mod-v1")
while get_batch(conn, batch.batch_id).status != "completed":
    advance_batch(conn, batch.batch_id, 100)
```

## 测试

```bash
python3 -m pytest tests -q
```
