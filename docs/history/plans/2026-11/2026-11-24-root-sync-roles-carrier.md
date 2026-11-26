# 根侧书目同步 roles 到货承载（root-sync-roles-carrier）

状态：Completed
任务类型：B
最后更新：2026-11-25
关联 ADR：[ADR 0019](../../../adr/0019-curation-outcome-and-book-kind-columns.md)（`doc_kind` 由根侧 `roles` 派生、`''`＝未声明；受理门对未声明书 422 兜底）
关联设计：[database-design.md](../../../architecture/database-design.md)（`af_books.doc_kind` 列清单正本）、[api.md](../../../architecture/api.md)（受理门种类闸与 `POST /books/sync` payload）
关联 Tracker：docs/trackers/completed.md（PLAN-045／REQ-155 关闭行；跨项目长线请求 REQ-155 以本壳为承载，见「承载 REQ-155」节）
归档判定：Retain（根侧到货代码、测试与真机回读属不可替代外部证据；本壳尚无有效提交锚点，不删除）

## 目标与成功标准

**目标（已达成）**：承接跨项目长线请求 REQ-155，确认根侧 8900 `POST /books/sync` 已把
`roles` 数组透传给 8902，并完成一次本仓 `doc_kind` 派生回读。本仓侧派生与受理 422 兜底由
PLAN-028 底座（ARCH-036）交付；本壳没有新增本仓代码，也没有写入根仓库。

1. **S1 到货可判**：根侧同步行含 `roles` 数组，`af_books.doc_kind` 由同步腿派生为非空的
   `textbook`／`exercise`。
2. **S2 兜底保持**：`roles` 未到货或不在闭集时，未声明书仍被受理门 422 拒、零写入；到货不改变这条兜底。
3. **S3 零越界**：本仓对根仓库零写入；`roles` 的转发改造由 QED-Engine 根仓库自身执行。

成功标准条目化判据的正本在 `docs/trackers/completed.md` 的 PLAN-045 关闭行；本壳承接承载、到货与回读证据。

## 范围与非目标

**范围**：登记并关闭 REQ-155 跨项目长线；记录根侧 `roles` 到货；在本仓回读 `doc_kind` 派生结果。

**非目标**：不改本仓 `src`／`tests`／迁移；不改受理门 422 兜底；不写根仓库任何文件；不新增端点。

## 前置条件

1. PLAN-028 底座（ARCH-036）已交付 `derive_doc_kind`、两真列与受理 422 兜底，W14 真库加列窗口已完成。
2. 根侧 QED-Engine 8900 `books/sync` 的 `roles` 透传已到货；到货前的逐键白名单缺位是历史事实，不再构成阻塞。
3. 本仓对根仓库只读，跨项目改造与回执由根仓库自身执行。

## 工作项

| 编号 | 归属 | 内容 | 落点 | 前置 | 状态 |
| --- | --- | --- | --- | --- | --- |
| W1 | 承载 | 登记 REQ-155 跨项目长线，承载由 PLAN-028 迁入本壳，判据一字不改 | 本壳「承载 REQ-155」节、todo PLAN-045 行 | — | 已完成（2026-11-24，随 PLAN-028 收口迁入） |
| W2 | 根侧到货 | 根侧在 8900 `books/sync` 载荷补 `roles`，原样转发字符串数组 | QED-Engine 根仓库（本仓只读、零写入） | W1 | 已完成（2026-11-07 修复到货；代码、测试与真机读数见「过程与证据」） |
| W3 | 到货回读 | 回读同步结果，确认 `doc_kind` 派生非空并留读数 | 本壳「过程与证据」 | W2 | 已完成（b05／b12／b13＝`textbook`，b07／b11＝`exercise`；8903 梳理屏列出五本） |

## 承载 REQ-155

**请求发出（目标：QED-Engine）**：8900 `POST /books/sync` 转发给 8902 的 payload 补 `roles`
（`qt_books.roles` 在 8901 书库腿已出全列），使 8902 能派生种类。本仓不写根仓库文件。

- **目的**：使本仓 `doc_kind` 由同步输入自动派生，消除未声明书的 422 兜底状态。
- **成功标准**：8902 收到的每条同步行含 `roles` 数组，种类列据此可派生非空（`textbook`／`exercise`）。
- **工作项**：见上表 W1~W3。
- **状态**：Achieved（2026-11-25 关闭；根侧到货与本仓回读均有证据）。

**载体沿革**：本节原载于 PLAN-028 壳，2026-11-24 PLAN-028 收口后改由本壳承载；REQ-155 自身判据一字未改。

## 验证与验收

- **到货判据**：根侧同步行含 `roles` 后 `af_books.doc_kind` ∈ {`textbook`,`exercise`} 非空；缺位仍 422 零写入。
- **兜底判据**：本仓 `derive_doc_kind` 与受理门逻辑未改动，既有单测和契约守护继续覆盖空数组、未知值与混合角色优先级。
- **零越界**：本仓 `src`／`tests`／迁移／根仓库文件在本壳范围零改动。

### S1~S3 关闭读数

| 判据 | 读数 | 判定 |
| --- | --- | --- |
| S1 | 根侧载荷含 `roles`；b05／b12／b13 回读 `textbook`，b07／b11 回读 `exercise`；8903 梳理屏列出五本 | Achieved |
| S2 | 本仓派生与 422 兜底零改动；空值仍派生 `''` 并由受理门拒绝 | Achieved |
| S3 | 本壳只写本仓计划与台账；根侧代码、测试和修复记录均为只读引用 | Achieved |

## 回滚

本壳零代码改动，无回滚面；若根侧 `roles` 到货后出现派生异常，按本仓受理门 422 兜底维持现状，由根侧修正 payload 后重放同步（幂等）。

## 关闭与归档

- 关闭判据＝W1~W3 全部有判定，S1~S3 逐条有读数，已于 2026-11-25 达成。
- 归档判定 **Retain**：根侧到货代码、测试与 2026-11-07 真机回读属外部不可替代证据，且本壳未提交，Git 锚点不成立。
- 收口按 [任务生命周期](../../../standards/task-lifecycle.md)「计划生命周期」第 5 步六步顺序执行。

## 过程与证据

- 2026-11-24｜PLAN-028 收口，REQ-155 承载迁入本壳；本仓侧 `derive_doc_kind`、两真列与受理 422 兜底
  已由 ARCH-036 交付，W14 真库加列窗口已完成。
- 2026-11-25｜只读核对根侧到货证据：
  - `QED-Engine/backend/qed_engine/api/axiom.py:154-155` 在同步载荷中写入
    `"roles": [str(role) for role in (b.get("roles") or [])]`；`:201-214` 把该列表原样交给
    `sync_books`。`backend/qed_engine/clients/axiom_client.py:109-111` 以 `json=books` 转发给 8902。
  - `QED-Engine/tests/test_api.py:1842-1909` 的 `test_axiom_sync_books_via_gateway` 断言
    `body[0]["roles"] == ["textbook"]`，覆盖载荷透传。
  - `QED-Engine/docs/plans/design-bugfix-log.md:859-871`（BUGFIX-049）记录 2026-11-07 修复与真机回读：
    重启 8900 后重新同步，b05／b12／b13＝`textbook`，b07／b11＝`exercise`；8903 梳理屏书籍框
    列出五本，b13 以「教材」可选且「解析目录」入口可用。
- 本仓回读结论：S1~S3 全部 Achieved，REQ-155 关闭；本仓无需代码、测试、迁移或根仓库写入。
