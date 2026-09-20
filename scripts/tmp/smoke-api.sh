#!/bin/bash
# Axiom-Flow 冒烟：MinerU（5002）健康 + 真实 PDF 端到端解析（提交 → 轮询 → 产物）。
# 用法（WSL 内，容器未挂载 Windows 盘时先 docker cp 传入 PDF）：
#   wsl docker exec mineru-api bash /tmp/smoke.sh
# 契约（MinerU 3.4.4，PLAN-003 W0 冻结表）：
#   POST /tasks（multipart 字段 files + 产物形态开关）→ 202 {task_id}
#   GET  /tasks/{task_id}         → 顶层 status: pending|processing|completed|failed
#   GET  /tasks/{task_id}/result  → 202 未就绪 / 200 {results:{<file_stem>:{md_content,content_list}}}
# 地址：QED_OCR_MODEL_URL（默认 http://127.0.0.1:5002，生命周期归 8900）。
set -e

BASE="${QED_OCR_MODEL_URL:-http://127.0.0.1:5002}"
SAMPLE="${1:-/tmp/smoke.pdf}"

echo "== 1. 健康检查 =="
curl -sf "$BASE/health" | python3 -m json.tool | head -5

echo "== 2. 提交解析任务 =="
TASK=$(curl -sf -X POST "$BASE/tasks" \
  -F "files=@$SAMPLE" \
  -F "return_md=true" -F "return_content_list=true" -F "return_middle_json=true" \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['task_id'])")
echo "task_id: $TASK"

echo "== 3. 轮询状态（首次含 vLLM warmup，约 1 分钟）=="
for _ in $(seq 1 120); do
  STATE=$(curl -sf "$BASE/tasks/$TASK" | python3 -c "import sys,json; print(json.load(sys.stdin).get('status',''))")
  echo "status: $STATE"
  case "$STATE" in
    completed|done|finished|success) break ;;
    failed|error) echo "解析失败"; curl -s "$BASE/tasks/$TASK" | python3 -m json.tool; exit 1 ;;
  esac
  sleep 5
done

echo "== 4. 结构化产物摘要 =="
curl -sf "$BASE/tasks/$TASK/result" | python3 -c "
import sys, json
results = json.load(sys.stdin).get('results') or {}
for stem, payload in results.items():
    content = payload.get('content_list') or []
    if isinstance(content, str):
        content = json.loads(content)
    print(stem, 'content_list 条目:', len(content))
    print(stem, 'md_content 长度:', len(payload.get('md_content') or payload.get('markdown') or ''))
"
