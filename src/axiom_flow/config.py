"""统一配置（三段式：服务 / 数据与数据库 / 解析引擎）。

读取优先级：真实环境变量 > 自身 `.env` > 父目录 `.env`（向上走查）> 内置最小默认值。
本服务只做「模型 API 调用 + 预处理/后处理」：模型生命周期与调参归 QED-Engine（8900），
配置只保留必要参数与本地模型地址；服务启动不依赖 8900 与模型在线。

本仓库 `.env` 支持独立部署（含 `QED_DB_NAME=qed` 与引擎配置）；由 QED-Engine 启动时经
向上走查跟随根 `.env`，两边配置允许不一致（2026-09-17 用户裁决，PLAN-002）。

密钥（`API_KEY`、`QED_DB_PASSWORD`）只经环境读取，绝不进入 `Settings` 的 repr；
`AXIOM_API_KEY` 已退役，无回退。缺密钥时相关能力降级，不阻塞启动。

**例外：管理面 token**（PLAN-010）——`QED_ADMIN_TOKEN` 与 `QED_DB_PASSWORD` 同级（不入 repr/日志），
但它**不是「缺了就降级」的密钥**：`QED_ADMIN_ENABLED=1` 而它为空时 `require_admin_configured()` 抛
`AdminConfigError`，服务拒绝启动。开关缺省为关（此时路由不挂载，`QED_ADMIN_TOKEN` 无需配置）。

已删除且不保留别名：`QED_API_SELECT`、`QED_LLM_GATEWAY_URL`、`QED_LLM_TIMEOUT`、
`QED_PROXY`、`QED_API_PROVIDER`、`QED_MODEL`、`QED_EMBEDDING_MODEL`、`AXIOM_FLOW_DATA_DIR`、
`AXIOM_PADDLEOCR_URL`；MinerU 直连地址读 `QED_OCR_MODEL_URL`（5002，`QED_MINERU_URL` 已删除）。
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field, replace
from functools import partial
from pathlib import Path
from typing import Any

# `.env` 值的行内注释：`#` 前必须有空白才算注释（DEFECT-008）。
_INLINE_COMMENT = re.compile(r"\s#.*$")


def _strip_env_comment(value: str) -> str:
    """取 `.env` 值本体：引号内原样保留，未加引号时剥掉尾部 ` # 说明`。

    根仓库 `.env` 用 `KEY=value # 说明` 写法；不剥离会让注释混进 URL/路径成为坏配置。
    """
    if value[:1] in ('"', "'"):
        closing = value.find(value[0], 1)
        if closing != -1:
            return value[1:closing]
    return _INLINE_COMMENT.sub("", value).strip()


def _default_data_dir() -> Path:
    """仓库内默认数据目录（无 `QED_DATA_ROOT` 时的兜底）。"""
    return Path(__file__).resolve().parents[2] / "data"


class AdminConfigError(RuntimeError):
    """管理面启用却无 token：拒绝启动（ADR 0004 无静默兜底）。异常文本不含 token 值。"""


def _to_bool(value: Any, default: bool = False) -> bool:
    """bool 转换：`1/true/yes/on` 为真；坏值一律按 `default`（关）。

    开关的容错方向必须是「关」——把 `QED_ADMIN_ENABLED=ture` 这类拼错判成「启用」等于凭空多出
    一个管理面。因此这里不抛错、只回落 False。
    """
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off", ""}:
        return False
    return default


@dataclass(frozen=True, slots=True)
class Settings:
    data_dir: Path = _default_data_dir()
    vision_model: str = "qwen-vl-plus"  # qwen-vl 云端档位身份（预留引擎，注释态配置）
    ocr_engine: str = "mineru"  # 解析引擎：mineru / paddleocr-vl / qwen-vl（后两者预留）
    ocr_model_url: str = "http://127.0.0.1:5002"  # 图像槽位本地端点（MinerU 容器，/health）
    ocr_timeout: int = 600  # 单个解析块一次调用的超时秒数（见 `parse_window_size`）
    parse_window_size: int = 56  # 全本解析的每块页数（真机 W0 冻结，PLAN-007）
    db_host: str = "127.0.0.1"
    db_port: int = 3306
    db_name: str = "qed"
    db_user: str = "root"
    db_password: str = field(default="", repr=False)
    port: int = 8902
    admin_enabled: bool = False  # 非公开管理面总开关（缺省关：路由根本不挂载，PLAN-010）
    admin_token: str = field(default="", repr=False)  # 管理面 token；启用时必填，绝不进 repr/日志

    @property
    def db_configured(self) -> bool:
        return bool(self.db_password)

    def require_admin_configured(self) -> None:
        """fail-closed：`admin_enabled` 为真但 token 为空 ⇒ 启动失败，而不是降级成无凭据管理面。"""
        if self.admin_enabled and not self.admin_token:
            raise AdminConfigError(
                "QED_ADMIN_ENABLED=1 必须同时配置 QED_ADMIN_TOKEN；已拒绝启动（不静默放行）。"
            )


def _to_int(value: Any, default: int) -> int:
    """int 转换容错：坏值（ValueError/TypeError）回退内置默认，不阻断启动。"""
    try:
        return int(value)
    except (ValueError, TypeError):
        return default


_ENV_MAP = {
    "AXIOM_VISION_MODEL": ("vision_model", str),
    "AXIOM_OCR_ENGINE": ("ocr_engine", str),
    "QED_OCR_MODEL_URL": ("ocr_model_url", str),
    "AXIOM_OCR_TIMEOUT": ("ocr_timeout", partial(_to_int, default=600)),
    "AXIOM_PARSE_WINDOW_SIZE": ("parse_window_size", partial(_to_int, default=56)),
    "QED_DB_HOST": ("db_host", str),
    "QED_DB_PORT": ("db_port", partial(_to_int, default=3306)),
    "QED_DB_NAME": ("db_name", str),
    "QED_DB_USER": ("db_user", str),
    "QED_DB_PASSWORD": ("db_password", str),
    "AXIOM_PORT": ("port", partial(_to_int, default=8902)),
    "QED_ADMIN_ENABLED": ("admin_enabled", _to_bool),
    "QED_ADMIN_TOKEN": ("admin_token", str),
}

# 数据根键：根 .env 统一数据根（本仓库不再有私有覆盖键）。
_DATA_DIR_KEYS = ("QED_DATA_ROOT",)


def _env_file_values(start: Path | None = None) -> dict[str, str]:
    """从 start 向上走查全部 `.env`，合并为视图（不修改 os.environ，避免测试环境污染）。

    先加载者（自身 .env）优先，父目录 `.env` 兜底；空值（如 `KEY=`）跳过留给兜底来源。
    键限 `QED_*`/`AXIOM_*` 与唯一密钥 `API_KEY`；已有环境变量优先于文件值（见 `_env_value`）。
    """
    values: dict[str, str] = {}
    start = start or Path.cwd()
    for candidate in (candidate / ".env" for candidate in (start, *start.parents)):
        if not candidate.is_file():
            continue
        for raw in candidate.read_text(encoding="utf-8-sig").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = _strip_env_comment(value.strip())
            if not value:
                continue
            if key.startswith(("QED_", "AXIOM_")) or key == "API_KEY":
                values.setdefault(key, value)
    return values


def _env_value(key: str) -> str:
    """单键取值：真实环境变量 > 自身 .env > 父目录 .env。"""
    return os.environ.get(key) or _env_file_values().get(key) or ""


def load_settings(**overrides: Any) -> Settings:
    file_values = _env_file_values()
    values: dict[str, Any] = {}
    for env_name, (field_name, converter) in _ENV_MAP.items():
        raw = os.environ.get(env_name) or file_values.get(env_name)
        if raw is not None:
            values[field_name] = converter(raw)
    for env_name in _DATA_DIR_KEYS:
        raw = os.environ.get(env_name) or file_values.get(env_name)
        if raw:
            values["data_dir"] = raw
            break
    values.update({key: value for key, value in overrides.items() if value is not None})
    if "data_dir" in values:
        values["data_dir"] = Path(values["data_dir"]).expanduser()
    settings = replace(Settings(), **values)
    data_dir = settings.data_dir
    if not data_dir.is_absolute():
        data_dir = (Path.cwd() / data_dir).resolve()
    return replace(
        settings,
        data_dir=data_dir,
        ocr_model_url=settings.ocr_model_url.rstrip("/"),
    )


def llm_api_key() -> str:
    """唯一供应商密钥（对齐 QED-Tracker ARCH-017）：只读 `API_KEY`，`AXIOM_API_KEY` 无回退。

    仅 qwen-vl 云端档位引擎（预留）使用；本地 MinerU 解析路径不需要密钥。
    """
    return _env_value("API_KEY")
