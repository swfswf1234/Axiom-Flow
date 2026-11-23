---
name: cjk-safe-edit-and-verify
description: "Use when CJK work on Windows misbehaves in this repo: a search misses a Chinese pattern, the edit tool finds 0 occurrences for text that read shows, the shell prints mojibake, or a phrase must occur exactly N times."
---

# CJK 安全编辑与验证（DSH 版）

## 概述

在中文密集仓库里，两件事会反复发生：

1. **搜索工具对中文模式漏报**——`grep` 工具返回 “no matches”，但文字明明在文件里；
2. **编辑工具对中文报 “0 occurrences”**——`edit` 找不到 `read` 明明显示过的文本。

**核心原则：搜索失败不等于不存在；编辑失败不要改写措辞重试。** 正确的做法是先通过纯 ASCII
通道取出真实字节，再从这些字节做编辑，最后用计数验证。

流水线：**探针 → 取真实字节 → 编辑 → 计数验证 → 清理临时件**。

## 为什么在 DSH 上更容易踩

DSH 的文件工具在 Windows + 中文路径/内容上有两个已知敏感点：

- **长行**：一条超长中文行（如 `api.md` 的端点表行）在 `edit` 里做字面量匹配时，
  只要有一个全角/半角标点差异就整体失配，报 0 occurrences；
- **CRLF/LF**：Windows 检出常把 LF 变成 CRLF，`old_string` 若只按屏幕显示拷贝就会失配。

两者都**不是文件损坏**，只是字节与显示不一致。

## 环境常量

- 解释器（固定，裸 `python` 是 3.10 而 `tomllib` 假失败）：
  `D:/software/anaconda3/envs/QED_env/python.exe`
- 运行时给 `PYTHONUTF8=1`，可同时消掉 gbk stdout 崩溃与 stdin 解码错误；
  `PYTHONIOENCODING=utf-8` 是较窄的替代。经 `cmd` 调用时**两层都要设**：
  `cmd /c "chcp 65001 >nul && set PYTHONUTF8=1&& <python 命令>"`——只设 `PYTHONUTF8`
  时控制台仍按旧代码页显示，中文照样乱码。
- 临时输出落点：**`%TEMP%`**；仓库内的探针文件用宿主 `write` 工具创建。
  **禁止读写 `dataset/**`**（`QED_DATA_ROOT`）——权限层与测试隔离铁律双重拦截。

## 第 1 步 — 探针（优先用 ASCII 锚）

先搜 ASCII 锚，它们匹配可靠：端点路径（`/kp-jobs`）、任务 ID（`REQ-139`）、`scripts/`、
`def test_xxx(`、`| KA-22 |`、`docs/design/`。

对中文模式，**“No matches found” 意味着「未验证」，而不是「不存在」**。不要据此报「没有这段」
或去「修」一句话。

## 第 2 步 — 取真实字节（ASCII JSON dump）

把命中写进 `ensure_ascii=True` 的 JSON，**只打印计数与 `file:line`**：

```python
import io, json, os, tempfile, time

PATTERNS = ["任务台账", "收口节拍"]                        # 精确中文
PATHS    = ["docs/trackers/todo.md", "docs/plans"]        # 文件，或目录（walk .md）


def files():
    for p in PATHS:
        if os.path.isdir(p):
            for root, dirs, names in os.walk(p):
                dirs[:] = [d for d in dirs if d not in {".git", "__pycache__", "node_modules"}]
                for n in names:
                    if n.endswith(".md"):
                        yield os.path.join(root, n)
        else:
            yield p


hits = []
for path in files():
    try:
        with io.open(path, encoding="utf-8") as f:
            for no, line in enumerate(f, 1):
                if any(pat in line for pat in PATTERNS):
                    hits.append([path.replace(os.sep, "/"), no, line.rstrip("\n")])
    except (UnicodeDecodeError, OSError):
        pass

out = os.path.join(tempfile.gettempdir(), "cjk-probe-%s.json" % time.strftime("%Y%m%dT%H%M%S"))
with io.open(out, "w", encoding="utf-8") as f:
    json.dump(hits, f, ensure_ascii=True, indent=1)
print("hits=%d file=%s" % (len(hits), out))
for path, no, _ in hits[:60]:
    print("%s:%d" % (path, no))
```

然后 **`read` 那份 JSON**——它持有每个命中行的真实字节。用那里的文本作为 `old_string` 的来源，
**永远不要**用搜索输出或管道文本。

只问计数时打印数字而非文本：

```python
import io
t = io.open("docs/trackers/todo.md", encoding="utf-8").read()
print(t.count("任务台账"), len(t.splitlines()))
```

**绝不**把整条 pytest/门禁输出通过 `grep` 过滤——中文字节会让它报 `Binary file matches`。

## 第 3 步 — 编辑

1. **优先 `edit` 工具**，`old_string` 取自 dump 出的那一行，**连同其前导空白与 3 行上下文
   也取自 dump**。
2. 若 `edit` 仍报 “0 occurrences”，**不要改写措辞重试**。对照 JSON 查三个常见原因：
   全角 vs 半角标点、行尾空白、CRLF vs LF。
3. 只有在 `edit` 确实无法匹配时，才退到带守卫的脚本替换：

```python
src = text.count(old)              # 必须唯一
assert src == 1, "anchor not unique (%d)" % src
text = text.replace(old, new, 1)
```

脚本编辑的护栏（每条都曾踩过）：

- `old` 与 `new` 都要带尾随 `\n`，否则多行替换会把下一段静默折进引用块。
- 插入章节：先断言标题恰好出现一次，再拼接；拼接后**重新计数**标题——否则锚定的替换会吞掉
  结束锚。
- **大文件先分段 `read` 定位再编辑**：整段长中文（尤其整节、整表）不要直接做字面量匹配。
  先用 `read` 的 `offset`/`limit` 逐段读出目标区域，锁定**最短唯一片段**（通常 1 行内、
  含 ASCII 锚如 `| KA-22 |`、`S1`、`/api/v1/`），用这个短片段作 `old_string`。
  让 `old_string` 短而唯一，而不是长而完整。
- **超长单行**（>2000 字符，如契约表行）：不要尝试整行匹配，改用**唯一子串替换**
  ——在替换脚本里 `assert text.count(old) == 1` 后 `replace(old, new, 1)`，只动那一段。
- 任何脚本编辑之后，**重跑第 2 步探针**在新闻本上，再声称成功。
- 脚本编辑会让文件状态过期：在对其用 `write` 前**重新完整 `read`**。

## 第 4 步 — 验证（数字 + ASCII 锚）

- 探针的前后计数（`hits=N`、`t.count(...)`），**作为实际输出粘贴**。
- 只用 ASCII 锚复述改动：端点名、任务 ID、`def test_xxx(`、`scripts/` 文件名、通过的门禁行。
- 跑受影响表面的项目门禁（`tests/contract`、`pytest tests -q`、`ruff check src tests`）
  并展示尾部输出。**一次干净的编辑不是契约套件通过的证据。**
- DSH 会话内跑门禁记得回避不可写缓存：`RUFF_CACHE_DIR=%TEMP%\…`、`-p no:cacheprovider`。

## 第 5 步 — 清理

```bat
del "%TEMP%\cjk-probe-*.json"
```

只删本会话创建的探针文件。

## 停止条件

- 中文模式在 walk 过的路径里 0 命中：报「未确认，探针在这些文件里没找到」，列出走过的路径，
  **询问后**再下结论。
- **控制台输出**乱码是打印 bug（修命令编码）；**文件内部**乱码是真损坏——先展示 dump 出的那行
  再动手。
- 主机容量问题（WSL、容器、模型服务）：归因后移交对应线，不要围绕它调业务参数。
