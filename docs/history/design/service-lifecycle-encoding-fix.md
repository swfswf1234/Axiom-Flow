# 生命周期脚本 `_pid_is_alive` 编码修复（V2-012）

> 归档说明：Superseded（v0.1 版本文档体系对齐，V2-015）。随 service-lifecycle.md 一并归档；
> 服务生命周期契约见 `docs/architecture/api.md` ①生命周期与健康分类。历史资料只解释背景，
> 不覆盖当前设计。

设计状态：Superseded
实现状态：Implemented
最后更新：2026-08-18
关联代码：`scripts/axiom_flow_service.py`（`_pid_is_alive`）
关联测试：`tests/unit/test_service_scripts.py`
关联 ADR：无

- 需求方：QED-Engine（根仓库 REQ-040）
- 目标项目：Axiom-Flow
- 接口面：`scripts/axiom_flow_service.py` 的 `_pid_is_alive`（生命周期脚本内部实现，不涉及端口/变量/目录/协议变更）
- 评审方：用户
- 执行方：Axiom-Flow

## 背景与故障现象

2026-08-18 用户实测：控制台停止/重启 Axiom-Flow（8902）报「停止失败（脚本退出码 1）」。

异常堆栈（与 QED-Tracker / 根仓库 web 脚本同构）：

```
File "scripts/axiom_flow_service.py", line 102, in _pid_is_alive
    return str(pid) in result.stdout
TypeError: argument of type 'NoneType' is not iterable
```

前置异常（readerthread 内）：

```
UnicodeDecodeError: 'utf-8' codec can't decode byte 0xcf in position 4: invalid continuation byte
```

## 根因

中文 Windows 下 `tasklist` 输出表头为 GBK 编码（「映像名称」的 GBK 首字节含 0xcf），而
`subprocess.run(..., text=True)` 在 Python 默认 utf-8 解码下读取失败 → `_readerthread`
中断 → `result.stdout` 为 None → `str(pid) in result.stdout` 抛 TypeError → 脚本退出码 1。

## 故障传播与修复

```mermaid
flowchart LR
    A["tasklist 输出 GBK 表头<br/>（中文 Windows）"] --> B["subprocess.run text=True<br/>默认 utf-8 解码"]
    B -->|"解码失败"| C["_readerthread 中断"]
    C --> D["result.stdout = None"]
    D --> E["str(pid) in result.stdout<br/>TypeError → 退出码 1"]
    B -.->|"修复 1：errors=replace"| F["stdout 保留替换符<br/>PID 数字为 ASCII 仍可匹配"]
    D -.->|"修复 2：(result.stdout or '')"| G["stdout 为空时兜底 False<br/>不抛异常"]
```

## 修复方案

对 `_pid_is_alive`（`scripts/axiom_flow_service.py`）两处收敛：

1. `subprocess.run` 增加 `errors="replace"`：容忍 tasklist 非 utf-8 输出，readerthread
   不再中断（PID 数字为 ASCII，替换乱码不影响匹配）。
2. 返回值改为 `return str(pid) in (result.stdout or "")`：stdout 极端为空时兜底返回 False。

修复范式已在根仓库 `scripts/qed_web_service.py`（同构脚本）验证通过：`errors="replace"` +
`(result.stdout or "")`，配套回归测试 `tests/test_qed_web_service.py::test_pid_is_alive_tolerates_non_utf8_stdout`。

## 验收标准

- `_pid_is_alive` 在 stdout=None（模拟解码失败）时不抛 TypeError，返回 False。
- `subprocess.run` 调用含 `errors="replace"`。
- 正常 tasklist 输出（ASCII）仍能正确判定进程存活。
- Axiom-Flow 全量门禁全绿（pytest tests -q + ruff check src tests scripts）+ 真实 8902
  手动启动后经脚本停止实测通过。
- 回执根仓库 REQ-040（提交号 + 测试输出）。

## 关联

- 本仓库 todo：V2-012
- 根仓库 todo：REQ-040
- 同构问题：QED-Tracker `scripts/qed_tracker_service.py` `_pid_is_alive`（QED-033）
