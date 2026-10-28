# -*- coding: utf-8 -*-
"""角色卡加载 / 增删改 / 启用卡解析(带 mtime 缓存)。

职责三分,本文件占后两项:
  - schema   结构校验(不碰文件);
  - loader   文件与缓存(**本文件**),只产出 CharacterCard / ResolvedCard;
  - renderer 提示词拼装。

「角色卡可选」:
  - 启用卡为空                    -> 不搭载角色卡,source == "fallback";
  - 指定了卡但文件缺失 / 结构损坏  -> 记录明确 reason 后同样回落,**绝不静默成功**;
  - 回落时由 renderer 交给调用方的 config 内置提示词,得到无人格的"最纯净的 AI"。

本模块**不提供任何 Python 内置人格副本**,仓库里也不附预设卡:人格要么来自用户
创建的角色卡文件,要么显式没有。

两件事要分清:
  - **卡的内容**  一卡一文件(Character Card V2 JSON),增删改见「卡的增删改」;
  - **卡的启用**  哪张卡在生效,是可跨重启的运行时选择(见「启用卡的持久化」)。
"""
import json
import logging
import os
import re
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from gzhu_cup2.agent.config import config as cfg
from gzhu_cup2.agent.pet.character.schema import (
    CARD_SPEC,
    CARD_SPEC_VERSION,
    CardError,
    CharacterCard,
    parse_card,
)

logger = logging.getLogger(__name__)

_LOCK = threading.RLock()
# abspath -> ((mtime, size), CharacterCard)
_CACHE: dict[str, tuple[tuple, CharacterCard]] = {}


# ============================ 目录与清单 ============================

def card_dir() -> str:
    """当前角色卡目录(不创建)。落点见 config.PET_CARD_DIR。"""
    return str(getattr(cfg, "PET_CARD_DIR", "") or "")


def list_cards() -> list[str]:
    """列出可用角色卡名(不含 .json 的文件名),按名称排序。

    目录不存在时返回空列表 —— 这是合法的"未搭载角色卡"状态,不是错误。
    """
    d = card_dir()
    if not d or not os.path.isdir(d):
        return []
    names = []
    try:
        for fn in os.listdir(d):
            if fn.endswith(".json") and not fn.startswith("."):
                names.append(fn[:-5])
    except OSError as e:  # noqa: BLE001 - 目录读不了等同于没有卡
        logger.warning("角色卡目录不可读 %s: %s", d, e)
        return []
    return sorted(names)


def _resolve_path(name: str) -> str:
    """把卡名解析成文件路径。支持三种写法:裸名 / xxx.json / 显式路径。"""
    name = str(name or "").strip()
    if not name:
        raise CardError("角色卡名为空")
    if os.path.isabs(name) or os.sep in name or (os.altsep and os.altsep in name):
        return name if name.endswith(".json") else name + ".json"
    fn = name if name.endswith(".json") else name + ".json"
    return os.path.join(card_dir(), fn)


def _sig(path: str) -> tuple:
    """缓存签名:修改时间 + 大小(改文件后自动失效,无需重启)。"""
    try:
        st = os.stat(path)
        return (st.st_mtime, st.st_size)
    except OSError:
        return (0.0, -1)


# ============================ 单卡加载 ============================

def load_card(name: str, *, strict: bool = True) -> CharacterCard:
    """按名/路径加载角色卡。

    strict=True  : 任何问题抛 CardError(测试与显式指定卡的场景);
    strict=False : 失败返回 None,只记日志(服务端怕坏卡拖垮请求时用)。

    返回的是**深拷贝**,调用方随意改都不会污染缓存。
    """
    path = _resolve_path(name)
    with _LOCK:
        sig = _sig(path)
        cached = _CACHE.get(path)
        if cached is not None and cached[0] == sig:
            return cached[1].model_copy(deep=True)

    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except FileNotFoundError:
        err = CardError(f"角色卡文件不存在: {path}")
    except ValueError as e:
        err = CardError(f"角色卡不是合法 JSON({path}): {e}")
    except OSError as e:
        err = CardError(f"角色卡读取失败({path}): {e}")
    else:
        try:
            card = parse_card(raw, origin=path)
        except CardError as e:
            err = e
        else:
            with _LOCK:
                _CACHE[path] = (_sig(path), card)
            return card.model_copy(deep=True)

    if strict:
        raise err
    logger.error("角色卡加载失败,将回落到 config 内置提示词: %s", err)
    return None  # type: ignore[return-value]


def clear_cache() -> None:
    """清空角色卡缓存(测试 / 热替换文件后强制重读)。"""
    with _LOCK:
        _CACHE.clear()


# ============================ 写盘的原子原语 ============================

def _read_raw(path: str) -> dict:
    """读原始 JSON 对象(不做结构校验)。

    增删改都基于**原始 dict** 再写回,以保住 V2 里本应用不认识的字段。
    """
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except FileNotFoundError:
        raise CardError(f"角色卡文件不存在: {path}")
    except ValueError as e:
        raise CardError(f"角色卡不是合法 JSON({path}): {e}")
    except OSError as e:
        raise CardError(f"角色卡读取失败({path}): {e}")
    if not isinstance(raw, dict):
        raise CardError(f"角色卡根节点必须是 JSON 对象: {path}")
    return raw


def _write_json(path: str, payload: dict) -> None:
    """原子写 JSON:先写同目录临时文件再 os.replace,避免留下半截文件。"""
    tmp = f"{path}.tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
            f.write("\n")
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:  # noqa: BLE001 - 清理失败不影响主流程
                pass


# ============================ 卡的增删改 ============================

# 卡名 → 文件名的黑名单:卡名只作标签,不允许分隔符与路径字符。
_BAD_CHARS = re.compile(r'[\\/:*?"<>|\x00-\x1f]')
_MAX_NAME_LEN = 64


def safe_card_name(name: str) -> str:
    """把用户输入的卡名规范化成可安全作为文件名的形式;不合法则抛 CardError。"""
    raw = str(name or "").strip()
    if not raw:
        raise CardError("角色名不能为空")
    if len(raw) > _MAX_NAME_LEN:
        raise CardError(f"角色名过长(最多 {_MAX_NAME_LEN} 个字符)")
    if _BAD_CHARS.search(raw):
        raise CardError('角色名不能包含 \\ / : * ? " < > | 这些字符')
    # 结尾的点/空格在 Windows 上会被静默吞掉,导致"建完就找不到"
    cleaned = raw.rstrip(". ")
    if not cleaned or cleaned in (".", ".."):
        raise CardError("角色名不合法")
    return cleaned


def _card_payload(*, name: str, description: str, first_mes: str) -> dict:
    """组装一张最小可用的 Character Card V2。

    只写本应用会读的三个字段:name 必填,description 进提示词,first_mes 是开场白;
    其余 V2 字段均为可选,留空。
    """
    return {
        "spec": CARD_SPEC,
        "spec_version": CARD_SPEC_VERSION,
        "data": {
            "name": name,
            "description": description,
            "first_mes": first_mes,
        },
    }


def create_card(*, name: str, description: str, first_mes: str = "") -> CharacterCard:
    """新建一张角色卡。

    - 卡名经 safe_card_name 规范化后作为文件名;
    - **重名直接报错,不覆盖**(改内容走 update_card);
    - 目录不存在则创建(首次建卡时 PET_CARD_DIR 可能还不存在)。
    """
    safe = safe_card_name(name)
    body = str(description or "").strip()
    if not body:
        raise CardError("提示词正文不能为空")

    path = _resolve_path(safe)
    if os.path.exists(path):
        raise CardError(f"已经有一张叫「{safe}」的角色卡了,换个名字,或者直接去编辑它")

    os.makedirs(card_dir(), exist_ok=True)
    _write_json(
        path,
        _card_payload(name=safe, description=body, first_mes=str(first_mes or "")),
    )
    clear_cache()
    return load_card(path, strict=True)


def update_card(
    *, name: str, description: Optional[str] = None, first_mes: Optional[str] = None
) -> CharacterCard:
    """改一张已有卡的内容。**只改传入的字段**,None 表示保持原样(局部更新)。

    name 必须是裸卡名(经 safe_card_name 校验),不接受路径 —— 本函数由 HTTP
    端点直接传入用户输入,不校验就能读写 card_dir 之外的文件。
    """
    safe = safe_card_name(name)
    path = _resolve_path(safe)
    raw = _read_raw(path)
    data = raw.get("data")
    if not isinstance(data, dict):
        raise CardError(f"角色卡结构异常,缺少 data 对象: {path}")

    if description is not None:
        body = str(description).strip()
        if not body:
            raise CardError("提示词正文不能为空")
        data["description"] = body
    if first_mes is not None:
        data["first_mes"] = str(first_mes)

    _write_json(path, raw)
    clear_cache()
    return load_card(path, strict=True)


def delete_card(name: str) -> None:
    """删除一张卡;若删的是当前生效的那张,同时清空启用选择(避免悬空选择)。

    name 必须是裸卡名(经 safe_card_name 校验),不接受路径 —— 同 update_card。
    """
    safe = safe_card_name(name)
    path = _resolve_path(safe)
    if not os.path.exists(path):
        raise CardError(f"角色卡不存在: {safe}")
    os.remove(path)
    clear_cache()

    active = active_card_name()
    if active and os.path.normcase(_resolve_path(active)) == os.path.normcase(path):
        set_active_card("")


# ============================ 启用卡的持久化 ============================
#
# cfg.PET_ACTIVE_CARD 是**出厂默认**(现为空串 = 不搭载卡);运行时选择落在数据目录下的
# active_card.json,优先级:运行时选择 > 出厂默认。
#
# 状态文件**不放进 PET_CARD_DIR**:list_cards() 会把目录里所有 *.json 当卡。

_STATE_FILENAME = "active_card.json"

# 覆盖用的状态路径;空串 = 用 config 派生的默认位置。
# 与 care/pet/privacy 的 runtime 模块同一套写法,供测试/部署把状态文件指到别处。
_STATE_PATH: str = ""


def set_state_path(path: str) -> None:
    """覆盖启用卡选择的位置(测试/部署用)。空串 = 回到 config 默认。"""
    global _STATE_PATH
    _STATE_PATH = str(path or "")


def active_state_path() -> str:
    """运行时启用卡选择的位置(默认在数据目录,与记忆库同级 —— 见 config.MEMORY_DIR)。"""
    if _STATE_PATH:
        return _STATE_PATH
    return os.path.join(str(getattr(cfg, "MEMORY_DIR", "") or ""), _STATE_FILENAME)


def _read_active_state() -> Optional[str]:
    """读运行时选择。文件缺失/损坏一律返回 None(= 没选过,走出厂默认),绝不抛。"""
    path = active_state_path()
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(raw, dict):
        return None
    name = raw.get("name")
    return str(name).strip() if isinstance(name, str) else None


def active_card_name() -> str:
    """当前生效的卡名。优先级:运行时选择 > config 出厂默认。

    返回空串表示"不搭载角色卡"(走回落路径),这是合法状态而非错误。
    """
    saved = _read_active_state()
    if saved is not None:
        return saved
    return str(getattr(cfg, "PET_ACTIVE_CARD", "") or "").strip()


def set_active_card(name: str) -> str:
    """选中一张卡并持久化;传空串 = 不搭载任何角色卡。

    选一张**不存在或结构不合法**的卡会抛 CardError(先过一遍 load_card 校验)。
    """
    value = str(name or "").strip()
    if value:
        load_card(value, strict=True)   # 校验存在且能解析
    path = active_state_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    _write_json(path, {"name": value})
    return value


# ============================ 启用卡解析 ============================

@dataclass(frozen=True)
class ResolvedCard:
    """一次"本次对话用哪张卡"的解析结果。"""

    card: Optional[CharacterCard]
    source: str          # "card" = 用角色卡; "fallback" = 回落 config 内置提示词
    requested: str       # 本次请求的卡名("" 表示本来就没配角色卡)
    reason: str          # source=="fallback" 时的原因;成功时为空串

    @property
    def active(self) -> bool:
        return self.card is not None

    def describe(self) -> dict:
        return {
            "active": self.active,
            "source": self.source,
            "requested": self.requested,
            "reason": self.reason,
            "dir": card_dir(),
            "available": list_cards(),
        }


def resolve_card(name: Optional[str], *, strict: bool = False) -> ResolvedCard:
    """解析指定卡名;失败按 strict 决定"抛错"还是"回落 + 记 reason"。"""
    requested = str(name or "").strip()
    if not requested:
        return ResolvedCard(None, "fallback", "", "未配置角色卡(回落到 config 内置提示词)")
    try:
        card = load_card(requested, strict=True)
    except CardError as e:
        if strict:
            raise
        logger.error("角色卡 %r 不可用,回落到 config 内置提示词: %s", requested, e)
        return ResolvedCard(None, "fallback", requested, str(e))
    if not card.data.has_persona():
        msg = f"角色卡 {requested!r} 没有任何人格文本(description/personality 均为空)"
        if strict:
            raise CardError(msg)
        logger.error("%s,回落到 config 内置提示词", msg)
        return ResolvedCard(None, "fallback", requested, msg)
    return ResolvedCard(card, "card", requested, "")


def resolve_active(*, strict: bool = False) -> ResolvedCard:
    """解析当前启用的角色卡(前台/主动关怀的统一入口)。"""
    return resolve_card(active_card_name(), strict=strict)


# ============================ 开场白 ============================

def greeting() -> Optional[str]:
    """启用角色卡的开场白(first_mes);未搭载卡或无该字段时返回 None。

    开场白属于人格文本,只来自角色卡,没有内置兜底文案。
    """
    card = resolve_active().card
    if card is None:
        return None
    text = str(card.data.first_mes or "").strip()
    return text or None


def set_greeting(text: str) -> str:
    """把开场白写回**启用角色卡**的 first_mes,返回写入后的文本(允许空串=清空)。

    写回角色卡而不另存配置:角色卡是人格的唯一来源。卡位于用户数据目录
    (config.PET_CARD_DIR),可写。
    """
    resolved = resolve_active()
    if resolved.card is None:
        raise CardError(f"未搭载可用角色卡,无法保存开场白: {resolved.reason}")

    path = _resolve_path(resolved.requested)
    raw = _read_raw(path)
    if not isinstance(raw.get("data"), dict):
        raise CardError(f"角色卡结构异常,缺少 data 对象: {path}")

    value = str(text or "")
    raw["data"]["first_mes"] = value
    _write_json(path, raw)
    clear_cache()   # 让下次读取拿到新值(mtime 缓存也自会失效,这里是双保险)
    return value
