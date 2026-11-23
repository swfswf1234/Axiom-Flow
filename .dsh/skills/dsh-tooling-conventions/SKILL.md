---
name: dsh-tooling-conventions
description: "Use at the start of any task in the Axiom-Flow workspace on DSH — picks the right tool for file edits, shell work, and sandbox approvals so attention stays on the task instead of on environment friction. Trigger on 工具选择, 沙箱报错, 命令失败, 写入失败."
---

# DSH 工具与环境约定（Axiom-Flow）

本工作区在 DSH 上运行，环境摩擦集中在三处：**shell 写文件被拒**、**PowerShell 5.1 缺参**、
**Python 中文 stdout 乱码**。按本技能选工具，不重复试错。以下每条都在本机实测过。

## 1. 文件操作走宿主工具，不走 shell（最重要）

宿主 `read` / `write` / `edit` / `glob` / `grep` **走宿主文件后端，不经进程沙箱**，
是改仓库文件的默认路径。shell 只在「必须执行命令」时才用（见第 2 节）。

shell 的完整性级别**随会话策略变化，不是固定事实**：曾实测为 **Low**（`S-1-16-4096`，对既有
目录一律 `Access is denied`），2026-11-22 在完全权限会话实测仍为 **Low**。**先用
`whoami /groups` 现查，不按记忆假设。**

| 操作 | 用什么 | 不要用 |
|---|---|---|
| 读文件 | `read`（大文件用 `offset`/`limit` 分段） | `type`、`Get-Content`、`more` |
| 写文件 | `write`（新建/整覆盖）、`edit`（定点替换，优先） | `echo >`、`Set-Content`、`sed`、`python -c` |
| 找文件 | `glob` | `dir /s`、`Get-ChildItem -Recurse` |
| 找内容 | `grep`（ripgrep，UTF-8 安全） | `findstr`（对 UTF-8 中文**返回空**，会假报「没有」） |
| 中文计数 | 固定解释器 + `str.count()`，只打印数字 | 任何中文模式的内容检索结论 |

**判据：shell 报 Access denied 是完整性级别边界，不是权限配置错。** 换宿主工具重做即可，
不要原样重试，也不要急着提权。删除/移动类操作先用绝对路径打印并比对预期，确认后才执行。

## 2. 跑命令：优先 `cmd /c`，别一上来用 pwsh

**Windows 命令优先，且优先 `cmd.exe`。** 本机 `pwsh` 实为 **PowerShell 5.1.26100**（不是 7），
带一组确定性故障；`cmd` 的内建命令没有这些问题，中文直出也正常。

| 需求 | 首选（cmd） | pwsh 的坑 |
|---|---|---|
| 删目录 | `rmdir /s /q "绝对路径"` | `Remove-Item -Recurse` 遇到长路径/只读项易二次确认 |
| 改编码看中文 | `chcp 65001 >nul && ...` | 同效，但多一层解析 |
| 找字符串 | 宿主 `grep` | `findstr` 对 UTF-8 中文**返回空** |
| 进程/端口 | `netstat -ano`、`tasklist` | 同效 |

pwsh 仍可用的场景：管道过滤（`Select-Object -Last N`）。它的三处实测故障：

| 症状 | 实测结论 | 替代写法 |
|---|---|---|
| `-SkipHttpErrorCheck` 不认识（`ParameterBindingException`） | 属实，5.1 无此参数 | 用 `curl.exe`（`C:\windows\system32\curl.exe`） |
| `Start-Process -ArgumentList` 报 `Access is denied` | 属实 | 用 `start "" "路径"` 或直接 `&` 调用 |
| `ConvertTo-Json` 破坏中文字节 | **不属实**，实测中文 JSON 正常 | 无需规避 |

**cmd 的 stdout 直出到工具结果时中文正常**（工具按 UTF-8 解码），
所以短命令**不必**先落盘再读：`cmd /c "chcp 65001 >nul && <命令>"` 即可。
跑 Python 时 UTF-8 要两层都设：`cmd /c "chcp 65001 >nul && set PYTHONUTF8=1&& <python 命令>"`
（`chcp` 管控制台、`PYTHONUTF8` 管解释器；只设一个仍会乱码）。

只有**长输出或需要精确比对**时才落盘，且落点必须是可写目录：

```bat
chcp 65001 >nul && <命令> > "%TEMP%\qed-out.txt" 2>&1
```

用 `%TEMP%`（实测可写），**不要**用仓库内既有目录（shell 写不进去）。

## 3. Python：必须用固定解释器并开 UTF-8

```
D:/software/anaconda3/envs/QED_env/python.exe
```

- 裸 `python` 是 **3.10.9**，`import tomllib` 直接 `ModuleNotFoundError`——会伪装成项目故障。
- QED_env 是 **3.12.0**，`pytest 9.0.3`、`ruff 0.16.1` 已装好。
- 不加编码变量时，中文 stdout 会按 **gbk** 编码（实测 `中文` → 字节 `3F 3F 3F 3F`），显示为乱码。
  一律前置 `set PYTHONUTF8=1&&`。

```bat
set PYTHONUTF8=1&& cd /d D:\coding\QED-Engine\Axiom-Flow&& D:\software\anaconda3\envs\QED_env\python.exe -m pytest tests -q
```

**缓存目录坑（本仓库必踩）**：`.pytest_cache/` 与 `.ruff_cache/` 对 Low 会话不可写——
`ruff` 会以 `Failed to create temporary file` 报错、pytest 报 `PytestCacheWarning`。
对策：`set RUFF_CACHE_DIR=%TEMP%\ruff-cache-af&&`（或 `ruff --no-cache`）、pytest 加
`-p no:cacheprovider`。**这不是代码问题，别去改代码。**

`conda run`：单行 `python -c` 正常；**多行必败**（rc=1，夹带 `KeyError('pkgs_dirs')` 噪音，
读起来像环境坏了）。需要多行时先写临时文件再执行。

## 4. 沙箱：不主动试探，被拒才单次提权

沙箱等级是**会话策略**，由用户设定，工具调用无法自行提升。

- 被拒绝时如实报原文，**不要换写法反复试**——每次探测都是一次无谓中断。
- 确有需要时用 `sandbox_permissions` 做**单次**重试，并在 `justification` 里说明理由，由用户批准。
- 不做「先试一次看看能不能过」的探测。
- `dataset/**`（`QED_DATA_ROOT`）禁止读写（项目规则与测试隔离铁律双重拦截）。

## 5. 宿主工具 vs shell 的可写范围（本机实测）

下表是 **Low 完整性会话**的实测边界；完全权限会话下整列都可写。
改仓库文件仍优先宿主工具——与完整性级别无关，图的是 `edit` 的字面量定位与 diff 可审。

| 位置 | 宿主 `write`/`edit` | shell（cmd/pwsh，Low 会话） |
|---|---|---|
| 工作区根下的新目录（如 `.dsh/`） | 可写 | 可写 |
| `.dsh/skills/`、`.qoder/**`、`tmp/**`、`docs/**`、`src/**`、`tests/**`、`scripts/**` | 可写 | **拒绝**（既有目录为 Medium 级别） |
| `%TEMP%` | 可写 | 可写 |

所以：**改仓库文件永远用宿主工具**；需要 shell 落盘时用 `%TEMP%`。

## 6. DSH 技能发现（本机专用事实）

- DSH 扫描**项目根 `.dsh/skills/<name>/SKILL.md`**（rank 100），其次 `<项目根>/.agents/skills`
  （200）、自定义目录（300）、`~/.dsh/skills`（400）、`~/.agents/skills`（500）、打包内置（600）。
- **项目根取最近的含 `.git` 祖先**。Axiom-Flow 是独立 git 仓库 ⇒ 项目根就是它自己，
  **上游根仓库 `D:\coding\QED-Engine\.dsh\skills\` 不在本会话扫描范围内**（只读参照用）。
- `.qoder/skills/` 服务 Qoder 端，**不是** DSH 扫描根。
- **`.dsh/skills/` 是 DSH 端唯一正本**，随仓库版本控制入库（`.gitignore` 用 `/.dsh/*` +
  `!.dsh/skills/`）。改技能直接改 `.dsh/skills/`，改完即对下一次模型请求生效（目录被 watch）。
- frontmatter 的 `description` 含 `: `（冒号+空格）时**必须加双引号**，否则 YAML 解析失败，
  该技能被**静默丢弃**——不会报错，只是目录里没有它。改完技能务必确认它重新出现在目录中。
- 校验脚本在上游根仓库 `scripts/check_dsh_skills.py`（本仓库无此脚本，只读调用）。

## 7. 反复失败时

同一操作失败两次就停下，把失败原文报给用户，不要继续换姿势重试。
环境摩擦是工具层问题，与业务无关，交回用户处置比继续试错更省时间。
