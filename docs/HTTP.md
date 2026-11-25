# 接口

- `GET /`：工作台。
- `GET /api/catalog`：阶段、字段和样例。
- `POST /api/stages/{stage}`：按阶段返回处理结果。
- `POST /api/runs`：执行完整批次并保存草稿。
- `GET /api/runs/{id}`：读取批次。
- `POST /api/runs/{id}/review`：签发或暂缓，提交 `decision`、`comment`、`expected_revision`。
- `GET /api/runs/{id}/evidence`：下载已签发证据。
- `GET /api/examples/{profile}`：下载样例。
- `GET /healthz`：健康检查。

## 下发资格与补正

- `GET /dispatch`：下发资格工作台页面。
- `GET /api/dispatch/board`（别名 `/api/dispatch`、`/api/groups`）：查看理赔组、`can_dispatch`、待核补正与留档凭证。
- `POST /api/dispatch/demo`：载入三个可下发示例组。
- `POST /api/source-updates`：登记票据源数据更新；在 `affected_group_ids`、`group_id` 或 `changes` 中给出受影响组。
- `POST /api/corrections`：提交/重试补正。补正改变 `member_id` 或 `service_date` 时，原组与新组均进入重新核对。
- `POST /api/corrections/{correction_id}/withdraw`：撤回补正，恢复原签发内容；同号重放返回 `WITHDRAWN`，不会再次停发。
- `GET /api/runs/{id}/dispatch`：查看已创建批次对应的下发组，首次访问自动按 `member_id|service_date` 同步。
