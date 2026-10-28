# -*- coding: utf-8 -*-
"""会话记录存储(进程内 + SQLite 写穿;零 FastAPI 依赖)。

**职责边界**
  - 只管"会话记录的存取 + 并发安全",不碰 FastAPI、不碰 LLM、不碰记忆。

**与长期记忆的分工(重要)**
  本模块保存**逐字对话记录**(前端白板视图),现已**持久化**:
  每次变更写穿到 ``SESSIONS_DB_PATH``(默认 ``memory_data/sessions.sqlite3``),
  后端/桌宠重启后自动把全部会话与轮次从磁盘载回 —— 不再是"重启即失"。
  设环境变量 ``LONG_MEMORY_BACKEND=memory``(大小写不敏感)可关闭落盘、退回
  纯内存(测试套件用它做隔离)。测试/部署也可用 :func:`set_state_path` 重定向。
  桌宠对用户的"记忆"由 ``long_memory`` 负责:事实/片段/日桶/风险分面持久化在
  ``memory_data/memory.sqlite3``,每轮由 ``retrieve_memory`` 节点读出来注入提示词。

**内存为主、磁盘为影子**
  ``_threads`` 始终是活对象(可原地改)的来源;SQLite 只是写穿副本。
  任何落盘异常都只告警并吞掉,会话继续活在内存里 —— 磁盘故障永不打断聊天。

**并发**
  两个线程会写它 —— 请求线程(用户发言)与关怀调度线程(主动消息投递),
  因此所有读写都持同一把可重入锁。
"""
import json
import logging
import os
import re
import sqlite3
import threading
import time
import uuid
from datetime import datetime

from gzhu_cup2.agent.config import config as cfg

logger = logging.getLogger(__name__)

_lock = threading.RLock()
_threads: dict = {}  # thread_id -> ThreadRecord

# ---- 磁盘影子副本(写穿持久化) ----
_conn: "sqlite3.Connection | None" = None  # 惰性单连接;仅在 _lock 内读写
_state_path: "str | None" = None           # None = 未重定向,按 env/config 决定
_loaded = False                            # 首次访问是否已把磁盘行载入 _threads


def now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def memory_files() -> list:
    """数据目录下的实际文件(名字 + 大小);只读展示,**不含内容**。

    「知道数据在哪」本身就是隐私能力 —— 所以连目录与清单一起给用户。
    """
    out = []
    try:
        for name in sorted(os.listdir(cfg.MEMORY_DIR)):
            path = os.path.join(cfg.MEMORY_DIR, name)
            if os.path.isfile(path):
                out.append({"name": name, "bytes": os.path.getsize(path)})
    except OSError:
        pass  # 目录还没建(尚无任何数据)不算错误
    return out


def default_user() -> str:
    """当前用户键(未指定时用 env/config 的默认用户,含合法化回落)。

    内部走 :func:`resolve_user`:admin/settings/pet_mode 等端点用它,
    与对话链路的用户解析必须同轨,否则带 ``X-User-Id`` 时对话落到
    分区用户、而清库/设置静默落到默认用户。
    """
    return resolve_user()


# ---- 用户键:唯一出口 + 合法化 ----
# 用户键会被拼进长期记忆的 SQLite 命名空间 ("memory", <user>, <kind>),
# 并出现在 care/pet 的 JSON 状态键里,因此**不能接受任意字符串**。
# 允许:ASCII 字母/数字/下划线/点/连字符,长度 1~64。
# 不允许空格、斜杠、全角字符 —— 它们既可能是路径穿越,也会让命名空间
# 在排障视图里难以辨认。非法值一律**回落**到默认用户并告警(不抛异常:
# 一个坏请求头不应该让整条对话链路 500)。
_USER_KEY_MAX_LEN = 64
_USER_KEY_OK = r"^[A-Za-z0-9_.\-]{1,%d}$" % _USER_KEY_MAX_LEN
_USER_KEY_RE = re.compile(_USER_KEY_OK)
_USER_KEY_WARNED: set = set()


def is_valid_user_key(value) -> bool:
    """用户键是否合法(纯函数,可单测)。"""
    if not isinstance(value, str):
        return False
    return bool(_USER_KEY_RE.match(value))


def resolve_user(raw: "str | None" = None) -> str:
    """把"请求声明的用户"解析成**合法**的长期记忆用户键。

    优先级:显式传入(raw,通常来自 ``X-User-Id`` 请求头)> env/config 默认值。
    任一来源非法都回落到 ``cfg.MEMORY_USER_KEY_DEFAULT``,并只告警一次
    (避免每个请求刷一行日志)。"谁是用户"只在本函数决定。
    """
    fallback = str(getattr(cfg, "MEMORY_USER_KEY_DEFAULT", "local_user") or "local_user")
    for candidate in (raw, getattr(cfg, "MEMORY_USER_KEY", None)):
        if candidate is None:
            continue
        if is_valid_user_key(candidate):
            return str(candidate)
        key = str(candidate)[:64]
        if key not in _USER_KEY_WARNED:
            _USER_KEY_WARNED.add(key)
            logger.warning("非法用户键已回落为 %r: %r(见 web/sessions.resolve_user)", fallback, key)
    return fallback


class ThreadRecord:
    """一条会话:标题 + 若干轮次。

    ``turns`` 里每轮是 dict,靠 ``kind`` 区分::

        chat      -> {ts, user, ai, analysis, kind="chat"}
        proactive -> {ts, user="", ai, kind="proactive", care_kind, care_msg_id,
                      inline?(True = 用户正在聊天时顺口接的一句,前端用轻量标注)}

    ``kind`` 只表达"这一轮谁发起",**不因是否在聊天中而改写**:写成 chat 却
    没有 ``user``,前端就会渲染出一个空的用户气泡。
    """

    def __init__(self, thread_id: str, title: str):
        self.id = thread_id
        self.title = title
        self.created_at = now()
        self.updated_at = self.created_at
        self.turns: list = []


# ============================ 磁盘后端(写穿影子副本) ============================
# 全部函数只在持有 _lock 时调用(以 _locked 后缀标注),因此单连接即可保证线程安全。

def _disk_path() -> "str | None":
    """当前生效的落盘路径;None = 纯内存(不做任何磁盘 IO)。

    优先级:set_state_path 显式重定向 > 环境变量 LONG_MEMORY_BACKEND(大小写
    不敏感;= "memory" 即禁用落盘,测试隔离用)> config 默认 SESSIONS_DB_PATH。
    """
    if _state_path is not None:
        return _state_path
    if os.getenv("LONG_MEMORY_BACKEND", "sqlite").strip().lower() == "memory":
        return None
    return str(cfg.SESSIONS_DB_PATH)


def _conn_for_locked(path: str) -> "sqlite3.Connection | None":
    """惰性建立单连接并确保表存在;任何失败告警后返回 None(退回纯内存)。"""
    global _conn
    if _conn is not None:
        return _conn
    try:
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        conn = sqlite3.connect(path, check_same_thread=False)
        conn.execute(
            "CREATE TABLE IF NOT EXISTS sessions ("
            "id TEXT PRIMARY KEY, title TEXT, created_at TEXT, updated_at TEXT, turns TEXT)"
        )
        conn.commit()
    except Exception:  # noqa: BLE001 - 落盘失败不得打断聊天
        logger.warning("会话持久化不可用(%s),退回纯内存", path, exc_info=True)
        return None
    _conn = conn
    return conn


def _repair_legacy_inline_proactive_locked(rec: "ThreadRecord") -> bool:
    """把历史上被误标成 ``chat`` 的主动消息改回 ``proactive``。

    老写法把"用户正在聊天时投递的主动消息"落成
    ``kind="chat", user=""`` —— 前端按 chat 轮渲染用户那一侧,于是多出一个
    空的用户气泡,而且"是它先开口"的标记整个消失。

    判定必须**同时**满足三条:``kind == "chat"``、``user`` 为空、有 ``care_kind``。
    真实聊天轮次永远有用户输入,不会被误伤;老记录没有 ``care_kind`` 也不动。
    """
    changed = False
    for t in rec.turns:
        if not isinstance(t, dict):
            continue
        if (str(t.get("kind") or "chat") != "chat"
                or str(t.get("user") or "").strip()
                or not t.get("care_kind")):
            continue
        t["kind"] = "proactive"
        t["inline"] = True  # 当时本就"在对话里接着说",不该补成独立卡片
        changed = True
    return changed


def _ensure_loaded_locked() -> None:
    """首次访问时把磁盘上的全部会话载入 _threads(幂等;失败静默告警)。"""
    global _loaded
    if _loaded:
        return
    _loaded = True
    path = _disk_path()
    if path is None:
        return
    conn = _conn_for_locked(path)
    if conn is None:
        return
    try:
        rows = conn.execute(
            "SELECT id, title, created_at, updated_at, turns FROM sessions"
        ).fetchall()
    except Exception:  # noqa: BLE001 - 读库失败以空历史继续
        logger.warning("会话历史加载失败(%s),以空历史继续", path, exc_info=True)
        return
    for row in rows:
        tid, title, created_at, updated_at, turns_raw = row
        try:
            turns = json.loads(turns_raw or "[]")
        except (TypeError, ValueError):
            turns = []
        rec = ThreadRecord(str(tid), title or "新对话")
        rec.created_at = created_at or rec.created_at
        rec.updated_at = updated_at or rec.updated_at
        rec.turns = turns if isinstance(turns, list) else []
        if _repair_legacy_inline_proactive_locked(rec):
            _persist_locked(rec)  # 一次性改写落盘,下次启动不必再扫
        _threads[str(tid)] = rec


def _persist_locked(thread: "ThreadRecord") -> None:
    """写穿一条会话(INSERT OR REPLACE);任何失败告警后吞掉。"""
    path = _disk_path()
    if path is None:
        return
    conn = _conn_for_locked(path)
    if conn is None:
        return
    try:
        conn.execute(
            "INSERT OR REPLACE INTO sessions (id, title, created_at, updated_at, turns) "
            "VALUES (?, ?, ?, ?, ?)",
            (thread.id, thread.title, thread.created_at, thread.updated_at,
             json.dumps(thread.turns, ensure_ascii=False)),
        )
        conn.commit()
    except Exception:  # noqa: BLE001 - 落盘失败该轮仍留在内存
        logger.warning("会话落盘失败(id=%r),仅保留在内存", thread.id, exc_info=True)


def _delete_row_locked(thread_id: str) -> None:
    """从磁盘删除一条会话;任何失败告警后吞掉。"""
    path = _disk_path()
    if path is None:
        return
    conn = _conn_for_locked(path)
    if conn is None:
        return
    try:
        conn.execute("DELETE FROM sessions WHERE id = ?", (thread_id,))
        conn.commit()
    except Exception:  # noqa: BLE001 - 内存已删,磁盘残留可容忍
        logger.warning("会话删除落盘失败(id=%r)", thread_id, exc_info=True)


def _clear_disk_locked() -> None:
    """清空磁盘会话表;任何失败告警后吞掉。"""
    path = _disk_path()
    if path is None:
        return
    conn = _conn_for_locked(path)
    if conn is None:
        return
    try:
        conn.execute("DELETE FROM sessions")
        conn.commit()
    except Exception:  # noqa: BLE001 - 内存已清,磁盘残留可容忍
        logger.warning("会话清库失败(%s),仅清空内存", path, exc_info=True)


def set_state_path(path: str) -> None:
    """重定向会话库(测试/部署用);空串回到 config 默认(SESSIONS_DB_PATH)。

    换路径 = 关闭旧连接、丢弃内存态、从新库重载 —— 等价于一次"进程重启",
    可用来验证持久化。旧连接会被显式关闭(Windows 上不关闭会锁住文件)。
    """
    global _conn, _state_path, _loaded
    with _lock:
        old, _conn = _conn, None
        if old is not None:
            try:
                old.close()
            except Exception:  # noqa: BLE001 - 关闭失败不阻断重定向
                logger.debug("关闭旧会话库连接失败: %s", old, exc_info=True)
        _state_path = path.strip() if path and path.strip() else None
        _threads.clear()
        _loaded = False
        _ensure_loaded_locked()


# ============================ 存取 ============================

def reset() -> None:
    """清空全部会话(测试隔离用);磁盘后端同时清空整张表。"""
    global _loaded
    with _lock:
        _threads.clear()
        _loaded = False
        _clear_disk_locked()


def get(thread_id: str):
    """取会话记录(不存在返回 None)。返回的是**活对象**,可原地修改。"""
    with _lock:
        _ensure_loaded_locked()
        return _threads.get(thread_id)


def snapshot() -> list:
    """当前全部会话的快照(排序由调用方决定)。

    先快照再排序:关怀调度线程可能同时在新建会话或改 ``updated_at``,
    直接迭代 ``_threads.values()`` 有 "dictionary changed size" 风险。
    """
    with _lock:
        _ensure_loaded_locked()
        return list(_threads.values())


def get_or_create(thread_id: str | None) -> str:
    if thread_id is None:
        thread_id = str(uuid.uuid4())
    with _lock:
        _ensure_loaded_locked()
        if thread_id not in _threads:
            _threads[thread_id] = ThreadRecord(thread_id, "新对话")
            _persist_locked(_threads[thread_id])
    return thread_id


def delete(thread_id: str) -> None:
    with _lock:
        _ensure_loaded_locked()
        _threads.pop(thread_id, None)
        _delete_row_locked(thread_id)


def rename(thread_id: str, title: str):
    """改标题并刷新时间;会话不存在返回 None。"""
    with _lock:
        _ensure_loaded_locked()
        t = _threads.get(thread_id)
        if t is None:
            return None
        t.title = title
        t.updated_at = now()
        _persist_locked(t)
        return t


def title_from(message: str) -> str:
    """由首条消息生成会话标题。"""
    t = " ".join(str(message or "").split())
    return t[:18] + ("…" if len(t) > 18 else "") if t else "新对话"


def commit_turn(thread: "ThreadRecord", message: str, reply: str, analysis: dict,
                user: "str | None" = None) -> None:
    """把用户与 AI 的一轮写入会话记录并刷新时间。

    ``user``:长期记忆/关怀归属的用户键;不传 = 默认用户(既有调用方行为不变)。
    """
    with _lock:
        thread.turns.append({
            "ts": now(),
            "user": message,
            "ai": reply,
            "analysis": analysis,
            "kind": "chat",
        })
        thread.updated_at = now()
        _persist_locked(thread)
    _note_user_turn(message, user)


def _note_user_turn(message: str, user: "str | None" = None) -> None:
    """把"用户刚说了什么"告诉关怀域(在场判定用)。

    为什么在这里而不是 care 自己去读会话:会话历史是 web 层的数据,
    care 域只接受**被通知**的在场事件(它不该反过来依赖 web)。判定逻辑
    (什么话算"我要睡了")仍在 care.runtime,本函数只负责转达。
    任何异常都不影响对话落库 —— 记不上离场标记,最坏退回"没有这条 gate"。
    """
    try:
        from gzhu_cup2.agent.care import runtime as cruntime
        cruntime.note_user_turn(user or default_user(), message)
    except Exception:  # noqa: BLE001 - 关怀域不可用不得阻断对话
        pass


# ============================ 主动消息投递 ============================

def pet_thread() -> "ThreadRecord":
    """选一条「桌宠继续说话」的会话:最近更新的那条;一条都没有则新建。

    之所以挂在最近会话上:桌宠的心智是"一直在跟你聊",网页端的多会话只是历史视图。
    新建时标题取角色卡里的角色名(不写死),取不到再回落为「新对话」。
    """
    with _lock:
        _ensure_loaded_locked()
        if _threads:
            return max(_threads.values(), key=lambda t: t.updated_at)
        title = ""
        try:
            from gzhu_cup2.agent.pet.character import prompts as pprompts
            title = pprompts.active()[0].char_name
        except Exception:  # noqa: BLE001 - 角色卡异常不得阻断主动消息投递
            title = ""
        thread = ThreadRecord(str(uuid.uuid4()), title or "新对话")
        _threads[thread.id] = thread
        _persist_locked(thread)
        return thread


def deliver_proactive(msg: dict) -> None:
    """把一条主动消息落进会话历史 —— 桌宠说的话就是聊天记录的一部分。

    分工(见 PET_POLICY.md):
      - **会话历史** 负责"内容":持久化在对话流里,刷新/切会话都不丢;
      - **outbox**(``care/runtime``)负责"读过没有":给前端做未读角标,读到即 dismiss。

    空文本不投递(不产生空气泡)。
    """
    if not msg or not str(msg.get("text") or "").strip():
        return
    # 只投递当前用户的消息(会话存储是单用户的,后台调度会遍历所有用户)
    who = str(msg.get("user") or "").strip()
    if who and who != default_user():
        logger.warning("跳过非当前用户的主动消息(user=%s)", who)
        return
    created = float(msg.get("created_ts") or 0.0) or time.time()
    # 手动触发且用户正在聊天(coordinator 判定)→ 不摆「主动消息 · 问候」卡片,
    # 就当桌宠在对话里接着说一句:这时它本来就在场,再插一张"问候"卡很出戏。
    #
    # 但**不能因此把 kind 改成 "chat"**:chat 轮的语义是
    # "用户说一句 + AI 回一句",这里根本没有用户那一句,前端按 chat 渲染就会
    # 多出一个 user="" 的空气泡,而且"是它先开口"的标记也没了。
    # kind 只表达"这一轮谁发起",是否摆卡片交给 inline。
    inline = bool(msg.get("inline"))
    with _lock:
        thread = pet_thread()
        turn = {
            "ts": datetime.fromtimestamp(created).strftime("%Y-%m-%d %H:%M:%S"),
            "user": "",
            "ai": str(msg["text"]),
            "kind": "proactive",
            "care_kind": msg.get("kind"),
            # 前端据此把对应 outbox 条目标为已读,避免"看过还显示未读"
            "care_msg_id": msg.get("id"),
        }
        if inline:
            # 只作呈现提示:inline 时前端用"主动搭话"这种轻量标注,
            # 而不是「主动消息 · 问候」那套卡片式文案。
            turn["inline"] = True
        thread.turns.append(turn)
        thread.updated_at = now()
        _persist_locked(thread)
        thread_id = thread.id

    # 同步进图状态:否则模型不知道自己刚才说过这句(见 engine.push_assistant_line)。
    # 放在锁外:构建/更新图比写文件慢,不该占着会话锁。
    try:
        from gzhu_cup2.agent.web import engine as _engine
        _engine.push_assistant_line(thread_id, str(msg["text"]), created)
    except Exception:  # noqa: BLE001 - 上下文连续性失败不得影响消息投递
        logger.warning("主动消息同步进图失败 thread=%s", thread_id, exc_info=True)
