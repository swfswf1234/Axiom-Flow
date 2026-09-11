# ADR 治理

状态：Current
确认状态：已确认
最后更新：2026-09-11
治理对象：ADR 编号、准入、结构、状态流转、元数据与归档路径
依据：QED-Tracker `docs/standards/adr-governance.md` 治理模式（[ADR 0003](../adr/0003-standards-governance-alignment.md)）
关联测试：`tests/contract/test_adr_governance.py`

## 目的与边界

用轻量 ADR 记录影响长期约束的决定（推倒重来、技术选型、部署边界等），保证新纪元决策可追溯。
v2 纪元编号从 0001 重新开始；v1 决策归档于 `docs/history/v1-20260814/adr/`，不参与当前序列。

## 规则

- **编号**：递增整数，四位补零（0001）。不重用已归档编号。
- **注册时机**：作出影响长期约束的决定时注册；A/B 类日常操作不需要 ADR。
- **结构**：状态（Draft/Accepted/Superseded/Historical）、最后更新、背景、决定、后果（正面/
  代价/缓解）、取代关系。
- **评审**：探索项目由用户直接确认，不设委员会。
- **状态变更**：被新决定取代时标记 Superseded 并在正文注明取代者；v1 决策整体归档为
  Historical。

## 元数据

ADR 声明：状态、最后更新、关联代码、关联测试（无则写"无"）。

## 执行与门禁

- 新增或改动 ADR 后运行 `tests/contract/test_adr_governance.py`（编号、元数据、索引与章节
  守护）与全量门禁（见[开发指南](../guides/development.md)）。
- ADR 索引 `docs/adr/index.md` 是登记唯一入口，编号与状态必须与正文一致，下一个可用编号同步
  递增。

## 变更与取代

改变编号、准入、结构或状态流转规则时必须先新增 ADR。措辞、链接和不改变语义的整理可直接
修改。
