#!/bin/bash
# Axiom-Flow 冒烟链路测试：验证 mineru-api 健康 + 真实 PDF 端到端解析
# 用法（在 WSL 内）：wsl docker exec mineru-api bash /tmp/smoke.sh —— 需要先 cp 进容器
# 或直接调用仓库 smoke 脚本（见 operations.md）
#
# 注意：file_parse 为同步接口，首次调用含 vLLM warmup（约 1 分钟），
# multipart 字段名为 files（复数）；容器未挂载 Windows 盘，需 docker cp 传入 PDF。
set -e

BASE=http://127.0.0.1:8002
SAMPLE=/tmp/smoke.pdf

echo "== 1. 健康检查 =="
curl -sf "$BASE/health" | python3 -m json.tool | head -5

echo "== 2. 提交解析任务（同步，含 warmup）=="
RESP=$(curl -sf -X POST "$BASE/file_parse" -F "files=@$SAMPLE" -F 'do_ocr=false' --max-time 600)
echo "$RESP" | python3 -c "
import sys, json
d = json.load(sys.stdin)
print('task_id:', d.get('task_id'))
print('status:', d.get('status'))
print('backend:', d.get('backend'))
print('error:', d.get('error'))
for name, r in d.get('results', {}).items():
    print('--- result:', name)
    print('md 长度:', len(r.get('md_content', '')))
    print('图片数:', len(r.get('images', [])) if isinstance(r.get('images'), list) else r.get('images'))
    print('前 200 字:', r.get('md_content', '')[:200].replace(chr(10), ' '))
" || echo "$RESP" | head -c 500

echo "== 3. 完成 =="