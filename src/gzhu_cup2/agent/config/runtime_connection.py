# -*- coding: utf-8 -*-
"""运行时 LLM「API 连接」配置(单条主连接)。

把 API Base URL / API Key / 默认模型保存到本地 JSON,由本模块统一解析生效。

优先级(高 → 低):
  1. JSON 中持久化的连接(仅当 enabled 且 base_url/api_key 均非空时视为激活);
  2. 环境变量 LLM_API_KEY;
  3. config.py 常量兜底(base_url / model 的默认值)。

API Key 明文保存在本地 JSON(已被 .gitignore 排除)。
"""
import errno
import json
import logging
import os
import shutil
import threading
from pathlib import Path
from typing import Optional

from gzhu_cup2.agent.config import config as cfg  # noqa: E402

logger = logging.getLogger(__name__)

# JSON 持久化位置:默认与 config.py 同目录(该目录整体受 .gitignore 保护的仅此文件)。
#
# ⚠ 打包分发时安装目录是只读的(Windows 装在 Program Files 下),直接写这里会在
#   「设置 → API 连接 → 保存」时报 PermissionError。因此允许用 GZHU_CONNECTION_PATH
#   把落点重定向到用户数据目录 —— 与 config.MEMORY_DIR 用 LONG_MEMORY_DIR 重定向
#   的做法一致(桌面端主进程会传这两个变量)。
_CONN_FILE: Path = Path(
    os.getenv("GZHU_CONNECTION_PATH")
    or (Path(__file__).resolve().parent / "runtime_connection.json")
)


# ---------- 旧连接文件的一次性迁移 ----------
# runtime_connection.json 被 .gitignore 排除、不会随 git 移动;后端目录结构
# 调整后老文件会留在旧路径(见 config.LEGACY_RUNTIME_CONNECTION_PATH)。
# 不迁移的话,已保存的 Base URL / Key 会"静默丢失"(重新落回无 Key 状态)。
# 语义与 long_memory.paths.migrate_legacy 一致:仅在「源存在且目标不存在」时搬,
# 失败只告警绝不抛;⚠ 绝不输出文件内容(里面是明文 Key)。惰性执行:首次读取前尝试一次。
_MIGRATION_LOCK = threading.Lock()
_MIGRATION_DONE = False


def _legacy_conn_file() -> Optional[Path]:
    """旧连接文件路径(config 常量,可被测试/部署覆盖);未配置时返回 None。"""
    raw = str(getattr(cfg, "LEGACY_RUNTIME_CONNECTION_PATH", "") or "")
    return Path(raw) if raw else None


def _migrate_legacy_connection() -> None:
    """把旧位置的连接 JSON 搬到 _CONN_FILE(仅在源存在且目标不存在时)。"""
    legacy = _legacy_conn_file()
    if legacy is None or legacy == _CONN_FILE:
        return
    try:
        if _CONN_FILE.exists() or not legacy.exists():
            return
        _CONN_FILE.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.replace(legacy, _CONN_FILE)       # 同盘:原子移动
        except OSError as e:
            if getattr(e, "errno", None) == errno.ENOENT:
                return                           # 源已被并发进程搬走 → 视作已完成
            shutil.copy2(legacy, _CONN_FILE)     # 跨盘:复制后删源
            os.remove(legacy)
    except OSError as e:  # 迁移失败只告警:读取方回落默认状态,启动路径绝不因此崩
        logger.warning("旧 API 连接文件迁移失败 %s -> %s: %s", legacy, _CONN_FILE, e)
        return
    logger.info("API 连接文件已迁移: %s -> %s", legacy, _CONN_FILE)


def _ensure_legacy_connection_migrated() -> None:
    """进程内只尝试一次(在首次读取之前调用)。"""
    global _MIGRATION_DONE
    if _MIGRATION_DONE:
        return
    with _MIGRATION_LOCK:
        if _MIGRATION_DONE:
            return
        _migrate_legacy_connection()
        _MIGRATION_DONE = True


def _default_state() -> dict:
    return {
        "enabled": False,
        "base_url": "",
        "api_key": "",
        "default_model": "",
        "models": [],
    }


def load_state() -> dict:
    """读取持久化连接配置(容错:文件缺失/损坏都回落默认)。"""
    _ensure_legacy_connection_migrated()
    state = _default_state()
    try:
        if _CONN_FILE.exists():
            raw = json.loads(_CONN_FILE.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                for key in state:
                    if key in raw and raw[key] is not None:
                        state[key] = raw[key]
    except (OSError, ValueError):
        pass
    return state


def save_state(state: dict) -> dict:
    """写入持久化连接配置,返回规范化后的 state。"""
    merged = _default_state()
    for key in merged:
        if key in state and state[key] is not None:
            merged[key] = state[key]
    merged["enabled"] = bool(merged["enabled"])
    merged["base_url"] = str(merged["base_url"]).strip().rstrip("/")
    merged["api_key"] = str(merged["api_key"]).strip()
    merged["default_model"] = str(merged["default_model"]).strip()
    merged["models"] = [
        str(m).strip() for m in (merged["models"] or []) if str(m).strip()
    ]
    _CONN_FILE.parent.mkdir(parents=True, exist_ok=True)
    # 原子写:先写临时文件再替换,避免写一半崩溃留下半截 JSON(读端虽有容错,
    # 但连接配置坏了会静默回落默认,用户不易察觉)
    tmp = _CONN_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, _CONN_FILE)
    return merged


def get_active() -> Optional[dict]:
    """返回激活中的连接(base_url + api_key 都齐),否则 None。"""
    state = load_state()
    if state["enabled"] and state["base_url"] and state["api_key"]:
        return state
    return None


# ---------- 统一解析(LLM 工厂与 server 路由共用) ----------
def resolve_api_key() -> Optional[str]:
    """当前生效的 API Key:激活连接 > 环境变量 LLM_API_KEY。"""
    conn = get_active()
    if conn:
        return conn["api_key"]
    return os.getenv("LLM_API_KEY") or None


def resolve_base_url() -> str:
    """当前生效的 base_url:激活连接 > 环境变量 OPENAI_BASE_URL > config 默认。

    三者均为空时返回空串,由调用方按「未配置」处理;不在此处补任何厂商端点。
    """
    conn = get_active()
    if conn:
        return conn["base_url"]
    return os.getenv("OPENAI_BASE_URL") or cfg.MODEL_BASE_URL


def resolve_model() -> str:
    """当前生效的默认模型。

    单点模式(MODEL_SINGLE_SOURCE=True):一律返回 config.MODEL_NAME ——
    改 config 一处即可全局替换,连接里存的 default_model 不再生效。
    默认关闭单点:连接 default_model > 环境变量 LLM_MODEL > config 默认
    (默认空串 = 未配置),使任意服务商可经设置面板或环境变量切换模型。
    """
    if cfg.MODEL_SINGLE_SOURCE:
        return cfg.MODEL_NAME
    conn = get_active()
    if conn and conn.get("default_model"):
        return conn["default_model"]
    return os.getenv("LLM_MODEL") or cfg.MODEL_NAME


# 对话链路的三项必需配置及其在提示文案中的称呼(顺序即展示顺序)。
_REQUIRED = (
    ("api_key", "API Key"),
    ("base_url", "Base URL"),
    ("model", "模型 ID"),
)


def missing_config() -> list:
    """返回对话链路缺失的配置项称呼(空列表 = 已就绪)。

    供 503 文案与 /health 共用,使「未配置」与「配置错误」在入口处即可区分。
    """
    values = {
        "api_key": resolve_api_key(),
        "base_url": resolve_base_url(),
        "model": resolve_model(),
    }
    return [label for field, label in _REQUIRED if not (values[field] or "").strip()]


def is_ready() -> bool:
    """Key、Base URL、模型 ID 是否均已配置(chat/stream 的 503 判断与 /health 共用)。"""
    return not missing_config()


def mask_key(api_key: str) -> str:
    """Key 打码展示:sk-abc…wxyz;过短只给占位。"""
    if not api_key:
        return ""
    if len(api_key) <= 8:
        return "****"
    return f"{api_key[:4]}…{api_key[-4:]}"
