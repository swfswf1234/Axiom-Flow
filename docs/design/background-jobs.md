# 持久化后台任务

设计状态：Accepted
实现状态：Implemented
最后更新：2026-07-27
关联代码：`backend/application/jobs.py`、`backend/worker/runner.py`、`backend/worker/__main__.py`
关联测试：`tests/test_v03_jobs.py`、`tests/test_code_document_mapping.py`
关联 ADR：`docs/adr/0006-persistent-jobs-and-api-v1.md`

任务类型首期为 `parse_document` 和 `extract_knowledge`，状态固定为 `queued`、`running`、
`succeeded`、`failed`、`cancel_requested`、`cancelled`。

API 在同一事务内按 `kind + aggregate_id + input_version` 创建幂等任务。独立 Worker 使用
MySQL 8 的 `SELECT ... FOR UPDATE SKIP LOCKED` 领取任务，写入 `lease_owner`、
`lease_expires_at` 和心跳；过期租约在未超过 `max_attempts` 时重新排队，否则失败。

解析按页更新 `progress_current/progress_total`，每页间检查取消请求。模型调用预算、调用量、
回退次数和错误均归属于单个任务。限流、超时和 5xx 属于可重试错误；损坏 PDF、非法模型输出
和领域校验失败属于永久错误。任务错误只保存脱敏摘要。
