"""解析引擎适配器：直连本地模型服务，统一输入/输出契约（模型无感知）。

设计事实源：`docs/design/model-integration.md`。
- `base`：适配器协议与页级结果结构（`parse_document` / `parse_page`）；
- `factory`：引擎名 → 适配器的显式分派（未实现/未知即报错，不自动兜底）；解析与文本两类槽位
  分册登记，后者当前为空（`KA-22`：梳理未接入前判 503）。
- `mineru`：MinerU 容器（`QED_OCR_MODEL_URL`，5002）接入；PaddleOCR-VL / qwen-vl 预留。

适配器不暴露模型调参（归 QED-Engine）；输出由 `normalize/` 归一为统一 blocks。
"""
