#!/bin/bash
# Axiom-Flow 冒烟：MinerU（5002）健康 + 真实 PDF 端到端解析（提交 → 轮询 → 产物）。
# 用法（WSL 内，容器未挂载 Windows 盘时先 docker cp 传入 PDF）：
#   wsl docker exec mineru-api bash /tmp/smoke.sh
# 契约：POST /file_parse（multipart 字段 files）→ task_id；GET /get_task_results/{task_id}
# 地址：QED_OCR_MODEL_URL（默认 http://127.0.0.1:5002，生命周期归 8900）。
set -e

BASE="${QED_OCR_MODEL_URL:-http://127.0.0.1:5002}"
SAMPLE="${1:-/tmp/smoke.pdf}"

echo "== 1. 健康检查 =="
curl -sf "$BASE/health" | python3 -m json.tool | head -5

echo "== 2. 提交解析任务 =="
TASK=$(curl -sf -X POST "$BASE/file_parse" -F "files=@$SAMPLE" | python3 -c "import sys,json; print(json.load(sys.stdin)['task_id'])")
echo "task_id: $TASK"

echo "== 3. 轮询结果（首次含 vLLM warmup，约 1 分钟）=="
for _ in $(seq 1 120); do
  STATE=$(curl -sf "$BASE/get_task_results/$TASK" | python3 -c "import sys,json; print(json.load(sys.stdin).get('data',{}).get('state',''))")
  echo "state: $STATE"
  case "$STATE" in
    done) break ;;
    fail|error) echo "解析失败"; exit 1 ;;
  esac
  sleep 5
done

echo "== 4. 结构化产物摘要 =="
curl -sf "$BASE/get_task_results/$TASK" | python3 -c "
import sys, json
data = json.load(sys.stdin).get('data', {})
content = data.get('content_list') or []
if isinstance(content, str):
    content = json.loads(content)
print('content_list 条目:', len(content))
print('markdown 长度:', len(data.get('markdown') or ''))
"
