# ADR 0001：v2 探索方向——推倒重来与后端解析定位

状态：Accepted
最后更新：2026-08-14
关联代码：无
关联测试：无

## 背景

2026-08-14 用户裁决：Axiom-Flow 转入探索型重构，与 QED-Engine 合作展示。12 本数学分析书籍已
下载（`dataset/qed-tracker/raw/books/math-qe/01_math_analysis`），第一版聚焦「PDF 完整解析 +
统一格式 + 高还原展示」的**后端解析能力**，前端展示由 QED-Engine 负责。

v1 路线（qwen-vl-ocr 逐页解析、MySQL `af_` 持久化、知识/工作簿/发布、评测治理）已完成其历史
使命，与 v2 方向不再匹配。历史 v0.1 基线曾使用 MinerU + 检索栈，本次回归该路线但升级至
MinerU vlm/hybrid 后端。

## 决定

1. **推倒重来**：旧代码与测试不沿用（git 历史与 `docs/history/v1-20260814/` 保留作经验参考），
   v1 决策 ADR 全量归档，新 ADR 编号从 0001 重新开始。
2. **定位**：本仓库是 QED-Engine 的**后端解析组件**，前端由 QED-Engine 实现，本仓库只保证
   API 契约与产物格式稳定。
3. **技术路线**：MinerU（vlm/hybrid 后端，本地 vLLM 推理）为主解析引擎，质量不达标时按页
   调用百炼 qwen-vl-plus 兜底；产物为文件系统 + 轻量 SQLite，Milvus 后续接入。
4. **部署**：分层混合——WSL Docker Compose 容器化推理服务（mineru-api + vLLM），Windows 本地
   运行编排层与 API。
5. **第一版范围**：解析 + 渲染数据供给；Milvus 检索、知识图谱、数学解析器列为后续探索项，
   仅预留字段。

## 后果

- 正面：技术栈贴合 2026 年文档解析主流，公式/版面质量显著优于 v1 逐页 OCR；容器化隔离模型
  环境；与 QED-Engine 分工清晰。
- 代价：旧测试与契约全部失效需重建；本地依赖（WSL + Docker + 模型下载）首次配置成本高。
- 缓解：归档镜像保证可回滚；本地门禁（pytest + ruff）继续作为唯一门禁，不引入远端 CI。

## 取代

本 ADR 取代 v1 全部决策（见 `docs/history/v1-20260814/adr/` 归档），为本纪元唯一事实来源。
