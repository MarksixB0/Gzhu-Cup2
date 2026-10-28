# -*- coding: utf-8 -*-
"""长期记忆存储层:唯一直接接触 LangGraph Store 的模块。

设计约定:
  - 记忆数据以 Store 的「命名空间」组织:
        ("memory", <user>, "pending")   待固化段(单条目,累积轮次)
        ("memory", <user>, "facts")     稳定事实(每事实一条,键=规范化文本哈希)
        ("memory", <user>, "episodes")  固化后的会话摘要(键=段起点确定性哈希)
        ("memory", <user>, "risk_events") 危机分面证据(意念/意图/计划/行为,受保护,永不遗忘)
  - 进程内单例 get_store();测试用环境变量 LONG_MEMORY_BACKEND=memory 强制内存后端,
    并调用 reset_store() 做用例隔离。
  - 持久化后端优先 langgraph SqliteStore(需安装 langgraph-checkpoint-sqlite);
    缺失或初始化失败时回落 InMemoryStore 并告警(演示可用,重启即失)。
"""
import hashlib
import os
import re
import threading
import time
import logging

from gzhu_cup2.agent.config import config as cfg
from gzhu_cup2.agent.long_memory import dates as _dates
from gzhu_cup2.agent.long_memory import paths as _paths
from gzhu_cup2.agent.long_memory import similarity as _similarity
from gzhu_cup2.agent.long_memory import tags as _tags

logger = logging.getLogger(__name__)

_PENDING_KEY = "segment"
_LOCK = threading.RLock()
_STORE = None


# ============================== 后端 ==============================
def _backend_env() -> str:
    return os.getenv("LONG_MEMORY_BACKEND", "sqlite").strip().lower()


def _try_sqlite():
    """SqliteStore 需要 sqlite3.Connection(check_same_thread=False,长期持有)。

    注意:SqliteStore.from_conn_string 是 contextmanager,退出会关闭连接,
    不适合本模块的长生命周期单例,故自行建连接并持有。
    """
    try:
        import sqlite3
        from langgraph.store.sqlite import SqliteStore  # type: ignore

        path = cfg.LONG_MEMORY_DB_PATH
        _paths.ensure_memory_dir()   # 建目录 + 旧数据一次性迁移
        conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        return SqliteStore(conn)
    except Exception as e:  # noqa: BLE001 - 后端缺失/初始化失败一律降级
        logger.warning(
            "长期记忆未启用持久化后端(%s),回落到内存 Store(重启即失)。"
            "如需落盘请 uv add langgraph-checkpoint-sqlite。", e,
        )
        from langgraph.store.memory import InMemoryStore
        return InMemoryStore()


def get_store():
    """返回进程内共享的 Store 实例。"""
    global _STORE
    if _STORE is None:
        with _LOCK:
            if _STORE is None:
                if _backend_env() == "memory":
                    from langgraph.store.memory import InMemoryStore
                    _STORE = InMemoryStore()
                else:
                    _STORE = _try_sqlite()
    return _STORE


def reset_store() -> None:
    """清空单例(测试隔离用;调用方须自行保证无并发使用)。"""
    global _STORE
    with _LOCK:
        _STORE = None


def store_backend_name() -> str:
    try:
        return type(get_store()).__name__
    except Exception:
        return "unknown"


# ============================== 命名空间 ==============================
def _ns(user: str, kind: str):
    return ("memory", user, kind)


def ns_pending(user: str):
    return _ns(user, "pending")


def ns_pending_ready(user: str):
    """已被长时间间隔切开的待固化段队列。"""
    return _ns(user, "pending_ready")


def ns_retrieval_trace(user: str):
    """读侧检索留痕(审计/排障;独立于记忆本体,绝不参与检索与遗忘,见 trace.py)。"""
    return _ns(user, "retrieval_trace")


def ns_facts(user: str):
    return _ns(user, "facts")


def ns_episodes(user: str):
    return _ns(user, "episodes")


def ns_risk(user: str):
    """危机分面命名空间:独立于情绪/趋势数据,受保护、永不参与遗忘与聚合。"""
    return _ns(user, "risk_events")


def ns_crisis_confirm(user: str):
    """危机确认链状态命名空间(单键 "state";纯内部计数,不含用户内容)。"""
    return _ns(user, "crisis_confirm")


def ns_agenda(user: str):
    """日程命名空间:用户自己录入的待办与纪念日(不进 FTS,不参与遗忘)。"""
    return _ns(user, "agenda")


CONFIRM_STATE_KEY = "state"


def get_crisis_confirm(user: str) -> dict | None:
    """读取危机确认链状态;无则 None。"""
    if not user:
        return None
    return get_item(ns_crisis_confirm(user), CONFIRM_STATE_KEY)


def set_crisis_confirm(user: str, state: dict | None) -> None:
    """写危机确认链状态;传 None 清除(撤销/升级后由调用方决定是否留痕)。"""
    if not user:
        return
    if state is None:
        delete_item(ns_crisis_confirm(user), CONFIRM_STATE_KEY)
    else:
        put_item(ns_crisis_confirm(user), CONFIRM_STATE_KEY, state)


CONFIRM_ESCALATE_KEY = "escalate"


def mark_crisis_escalated(user: str, ts: float) -> None:
    """固化侧 level≥2 危机证据 → 写升级标记,前台下一轮进硬协议(见 triage)。"""
    if user:
        put_item(ns_crisis_confirm(user), CONFIRM_ESCALATE_KEY,
                 {"ts": float(ts)})


def pop_crisis_escalated(user: str) -> bool:
    """读并清除升级标记(前台消费一次即失效)。"""
    if not user:
        return False
    it = get_item(ns_crisis_confirm(user), CONFIRM_ESCALATE_KEY)
    if it is None:
        return False
    delete_item(ns_crisis_confirm(user), CONFIRM_ESCALATE_KEY)
    return True


# ---- 复核调查通道(⑦):任务队列 + 报告留痕,受保护命名空间 ----

def ns_review(user: str):
    """复核调查命名空间:任务/报告落盘可审计;永不参与遗忘与聚合。"""
    return _ns(user, "review")


_REVIEW_TASK_KEY = "task"
_REVIEW_LAST_KEY = "last"


def set_review_task(user: str, task: dict | None) -> None:
    """写/清待复核任务(单槽:同一用户同时至多一个待办)。"""
    if not user:
        return
    if task is None:
        delete_item(ns_review(user), _REVIEW_TASK_KEY)
    else:
        put_item(ns_review(user), _REVIEW_TASK_KEY, task)


def get_review_task(user: str) -> dict | None:
    if not user:
        return None
    return get_item(ns_review(user), _REVIEW_TASK_KEY)


def last_review_report(user: str) -> dict | None:
    """最近一次复核报告(完整落盘:案卷+结论+时间,全程可审计)。"""
    if not user:
        return None
    return get_item(ns_review(user), _REVIEW_LAST_KEY)


def save_review_report(user: str, report: dict) -> None:
    if not user:
        return
    put_item(ns_review(user), _REVIEW_LAST_KEY, report)


# ============================== 通用原语 ==============================
def get_item(namespace: tuple, key: str):
    item = get_store().get(namespace, key)
    return item.value if item is not None else None


def put_item(namespace: tuple, key: str, value: dict) -> None:
    get_store().put(namespace, key, value)
    try:
        from gzhu_cup2.agent.long_memory import fts
        fts.upsert(namespace, key, value)
    except Exception as exc:  # noqa: BLE001 - 索引是派生增强，不拖垮主链
        logger.warning("记忆 FTS 同步跳过: %s", exc)


def delete_item(namespace: tuple, key: str) -> None:
    get_store().delete(namespace, key)
    try:
        from gzhu_cup2.agent.long_memory import fts
        fts.remove(namespace, key)
    except Exception as exc:  # noqa: BLE001
        logger.warning("记忆 FTS 删除同步跳过: %s", exc)


def list_items(namespace: tuple, limit: int = 1000) -> list:
    """列出命名空间内全部条目(返回 SearchItem,含 .key/.value)。

    底层按 ``updated_at DESC`` 取前 limit 条,超出部分对上层不可见且不报错,
    故超出时会告警(见 _warn_truncation)。
    """
    items = get_store().search(namespace, limit=limit)
    _warn_truncation(namespace, len(items), limit)
    return items


# 同一命名空间只告警一次,避免每轮读侧都刷日志
_TRUNCATION_WARNED: set = set()


def _warn_truncation(namespace: tuple, got: int, limit: int) -> None:
    """条目数触达 limit 时告警一次 —— 把"静默截断"变成"已知边界"。"""
    if got < limit:
        return
    ns_name = ".".join(str(p) for p in (namespace or ()))
    if ns_name in _TRUNCATION_WARNED:
        return
    _TRUNCATION_WARNED.add(ns_name)
    logger.warning(
        "命名空间 %s 已取满 limit=%d 条:更早的条目对检索/遗忘/去重均不可见。"
        "请先处理容量(归档/清理),不要在此基础上继续采集。", ns_name, limit)


def list_memory_users() -> list:
    """枚举有过记忆痕迹的用户 id(通过命名空间发现,免维护注册表)。

    ⚠ 同样存在静默截断:max_depth=2 + limit=200,即**最多发现 200 个用户**,
    超出者不会被固化、不会被遗忘、也不会出现在任何统计里。
    """
    _USERS_LIMIT = 200
    nss = get_store().list_namespaces(prefix=("memory",), max_depth=2,
                                      limit=_USERS_LIMIT)
    users = []
    for ns in nss:
        if len(ns) == 2 and ns[0] == "memory" and ns[1] not in users:
            users.append(ns[1])
    if len(users) >= _USERS_LIMIT and "users" not in _TRUNCATION_WARNED:
        _TRUNCATION_WARNED.add("users")
        logger.warning(
            "已发现用户数触达上限 %d:更多用户不会被固化/遗忘/统计。"
            "多用户部署前必须先解决此上限。", _USERS_LIMIT)
    return sorted(users)


# ============================== 捕获(每轮,免费) ==============================
def capture_turn(user: str, thread_id: str, user_text: str, ai_text: str,
                 meta: dict | None = None) -> None:
    """把一轮对话追加进该用户的待固化段(纯 KV 追加,毫秒级,无 LLM)。

    ⚠ pending 段有**硬上限** ``MEMORY_PENDING_MAX_TURNS``(默认 40):超限时
    最老的轮次被丢弃并告警一次(见 _truncate_pending)。正常路径下 worker 会在
    5 轮 / 空闲 300 秒时固化,远早于触顶。
    """
    ns = ns_pending(user)
    now = time.time()
    with _LOCK:
        val = get_item(ns, _PENDING_KEY) or {}
        val = dict(val)
        turns = list(val.get("turns") or [])
        # 长时间离开后不可继续复用旧段的日期锚点。旧段进入 ready 队列，
        # 新轮次从独立段开始；worker 会优先逐个消费 ready 段。
        if turns:
            try:
                gap = now - float(val.get("last_ts", turns[-1].get("ts", now)))
            except (TypeError, ValueError):
                gap = 0.0
            if gap > float(getattr(cfg, "MEMORY_CAPTURE_SESSION_GAP_SECONDS", 1800)):
                ready_key = _ready_key(user, val)
                put_item(ns_pending_ready(user), ready_key, val)
                val = {}
                turns = []
        if len(turns) >= cfg.MEMORY_PENDING_MAX_TURNS:
            dropped = len(turns) - (cfg.MEMORY_PENDING_MAX_TURNS - 1)
            turns = turns[-(cfg.MEMORY_PENDING_MAX_TURNS - 1):]
            logger.warning(
                "待固化段触顶(%d 轮上限),丢弃最老 %d 轮 user=%s —— "
                "通常意味着固化持续失败(检查 LLM 可用性与 worker 日志)",
                cfg.MEMORY_PENDING_MAX_TURNS, dropped, user)
        seq = int(val.get("next_seq", 0))
        turns.append({
            "seq": seq, "ts": now, "thread": thread_id,
            "user": user_text, "ai": ai_text, "meta": dict(meta or {}),
        })
        threads = list(val.get("threads") or [])
        if thread_id not in threads:
            threads.append(thread_id)
        put_item(ns, _PENDING_KEY, {
            "turns": turns, "next_seq": seq + 1,
            "started_at": float(val.get("started_at", now)),
            "last_ts": now, "threads": threads,
        })

        # 数值信号另存"日桶"(与 pending 生命周期解耦);日桶失败不影响本轮捕获。
        try:
            from gzhu_cup2.agent.long_memory import ticks as _ticks
            _ticks.bump_day_bucket(user, meta, ts=now)
        except Exception as e:  # noqa: BLE001 - tick 是增强信号,不得拖垮捕获
            logger.warning("日桶 tick 写入失败 user=%s: %s", user, e)


def get_pending(user: str) -> dict | None:
    """读取待固化段(返回副本,None 表示无)。"""
    ready = list_items(ns_pending_ready(user), limit=100)
    if ready:
        item = sorted(ready, key=lambda it: float((it.value or {}).get("started_at", 0)))[0]
        result = dict(item.value or {})
        result["_ready_key"] = item.key
        return result
    val = get_item(ns_pending(user), _PENDING_KEY)
    return dict(val) if val else None


def recent_turns(user: str, limit: int = 3) -> list:
    """最近的几轮对话(新→旧),把「待固化段」与「ready 段」合并后取最新的。

    为什么两段都要看:``get_pending`` 有 ready 段时只返回 ready —— 若 worker 落后,
    ready 可能是几小时前那一段,而用户刚刚说的话还在 pending 里。关怀文案要接的是
    **最近一句**,不能被一处积压挡住。

    只读,不写任何数据;调用方(``care.coordinator``)自己做时效与风险过滤。
    """
    rows = []
    live = get_item(ns_pending(user), _PENDING_KEY) or {}
    rows += list(live.get("turns") or [])
    for it in list_items(ns_pending_ready(user), limit=100):
        rows += list((it.value or {}).get("turns") or [])
    rows = [t for t in rows if isinstance(t, dict)]
    rows.sort(key=lambda t: float(t.get("ts") or 0), reverse=True)
    return rows[:max(1, int(limit))]


def clear_pending(user: str) -> dict:
    """删除该用户的待固化段,并如实返回删掉了什么(条目数 / 轮数)。

    只动尚未固化的这一段;已固化的 facts / episodes 需走 ``DELETE /api/memory/*``。
    """
    ns = ns_pending(user)
    val = get_item(ns, _PENDING_KEY)
    ready = list_items(ns_pending_ready(user), limit=1000)
    turns = len((val or {}).get("turns") or []) if isinstance(val, dict) else 0
    turns += sum(len((it.value or {}).get("turns") or []) for it in ready)
    if val:
        delete_item(ns, _PENDING_KEY)
    for it in ready:
        delete_item(ns_pending_ready(user), it.key)
    return {"deleted": (1 if val else 0) + len(ready), "turns": turns}


def pending_is_due(pending: dict | None, now: float | None = None) -> bool:
    """固化是否到期:轮数达标 / 空闲超时 / 出现过跨会话的新线程。"""
    if not pending:
        return False
    if pending.get("_ready_key"):
        return True
    turns = pending.get("turns") or []
    if not turns:
        return False
    now = now if now is not None else time.time()
    if len(turns) >= cfg.MEMORY_CONSOLIDATE_MIN_TURNS:
        return True
    if now - float(pending.get("last_ts", now)) >= cfg.MEMORY_IDLE_SECONDS:
        return True
    if len(pending.get("threads") or []) > 1:
        return True
    return False


# ============================== 固化落库 ==============================
def norm_text(text: str) -> str:
    """事实文本的规范化(去空白 + 去句末标点)—— 全仓唯一实现。

    同时作为事实键(_canonical)与支持度字典的键,两者必须逐字一致。
    """
    return " ".join(str(text or "").split()).strip("。.!！?？")


def _canonical(text: str) -> str:
    return hashlib.sha1(norm_text(text).encode("utf-8")).hexdigest()[:16]


def _episode_key(user: str, pending: dict) -> str:
    raw = f"{user}|{pending.get('started_at')}|{pending.get('next_seq')}"
    return "seg_" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]


def _ready_key(user: str, pending: dict) -> str:
    raw = f"{user}|{pending.get('started_at')}|{pending.get('next_seq')}"
    return "ready_" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def _risk_key(user: str, facet: str, quote: str, ts: float) -> str:
    """确定性键:同用户/同分面/同引文/同时刻重复固化不产生重复事件。"""
    raw = f"{user}|{facet}|{quote}|{float(ts)}"
    return "risk_" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]


def _turn_has_risk(turn: dict) -> bool:
    meta = turn.get("meta") or {}
    return meta.get("risk_level") in ("high", "crisis")


def pending_is_urgent(pending: dict | None) -> bool:
    """段内是否含 high/crisis 级风险证据 → 应立即固化(不等轮数/空闲/跨线程)。

    与 pending_is_due 的关系:urgent 优先于 due;两者都 False 才不固化。
    """
    if not pending:
        return False
    if pending.get("_ready_key"):
        return True
    for t in (pending.get("turns") or []):
        if _turn_has_risk(t):
            return True
    return False


def _similarity_kwargs() -> dict:
    """近义判定阈值;总开关关闭时退化为"仅规范化后完全相同才算相同"。"""
    if not cfg.MEMORY_FACT_SIMILARITY_ENABLED:
        return {"bi_jaccard": 1.0, "bi_containment": 1.0}
    return {
        "bi_jaccard": cfg.MEMORY_FACT_BI_JACCARD,
        "bi_containment": cfg.MEMORY_FACT_BI_CONTAINMENT,
    }


# 落库文本里的绝对日期(由 dates.absolutize 写成「在YYYY-MM-DD」;整周/整月形如
# 「在YYYY-MM-DD那一周」「在YYYY-MM月」,日期部分同样被掩码)。相似度比较前
# 先把它掩码掉:同一件事在不同日子被重新锚定,字面只差日期,若不掩码会被判成两条
# 不同事实。月份粒度的「YYYY-MM」也一并算日期。
# 两侧要卡边界:否则「2026-2027学年」会被吃成「2026-20」只剩「27学年」,
# 文本因此被误判成"带日期"而走进放宽阈值,两条不同事实被合并掉。
_RE_ISO_DATE = re.compile(r"(?<![\d-])\d{4}-\d{1,2}(?:-\d{1,2})?(?![\d-])")

# 日期变体的放宽阈值:仅用于「新文本确实带 ISO 日期」的事实。去掉日期后比较
# 单字重合度 —— 同一件事的两次复述常只差虚词(「有个模拟考」vs「有模拟考」),
# 双字双阈值(0.55/0.90)会漏合并;阈值上限以"不同事件不会被糊成一条"为界。
_DATE_VARIANT_JACCARD = 0.75
_DATE_VARIANT_CONTAINMENT = 0.80


def _strip_dates(text: str) -> str:
    """去掉 ISO 日期,得到"事件骨架"(用于判断两支是否只差日期)。"""
    return _RE_ISO_DATE.sub("", str(text or ""))


def _is_date_variant(old_text: str, new_text: str) -> bool:
    """两支是否「同一件事、只差日期」(调用方须先确认新文本带日期)。"""
    a, b = _strip_dates(old_text), _strip_dates(new_text)
    if not a or not b:
        return False
    return (_similarity.jaccard(a, b, 1) >= _DATE_VARIANT_JACCARD
            and _similarity.containment(a, b, 1) >= _DATE_VARIANT_CONTAINMENT)

# 「沿用原句 + 补细节」的结构判据(见 cfg.MEMORY_FACT_EXTENSION_* 的说明)。
# 与 similarity 的模糊阈值**无关**:这里只回答一个精确问题 ——
# 短的那条是不是长的**严格前缀**?是的话两者就是"同一条的详略两版",收成一条。
# 抽取提示(规则 2c)会引导模型沿用原句再补细节,所以这条判据有稳定的命中面;
# 即使模型没照做,判据也只是不命中,不会误合并(纯前缀,无分数、无阈值摇摆)。
# 两道护栏:
#   · 短文本 ≥ MIN_CHARS —— 「用户大三」这类过短泛句做前缀会吞掉任何以它开头的事实;
#   · 长度比 ≥ MIN_RATIO —— 「用户害怕考研」+ 一大段新内容 不该被当成扩写。
_RE_BARE_STRIP = re.compile(r"[\s，。！？、；：,.!?;:~…—\-—「」『』\"'（）()《》]+")


def _bare_text(text: str) -> str:
    """去掉空白与标点,只留字符序列(用于前缀比较,不受标点/分词影响)。"""
    return _RE_BARE_STRIP.sub("", str(text or ""))


def _is_wording_extension(old_text: str, new_text: str) -> bool:
    """新文本是否只是旧文本「沿用原句 + 末尾补细节」。

    纯结构判据(严格前缀),不参与 similarity 的模糊打分,所以**不需要校准阈值**;
    但它属"合并"通道,调用方 ``_find_similar_fact`` 已被总开关
    ``MEMORY_FACT_SIMILARITY_ENABLED`` 拦住,关掉总开关即退回"仅完全相同文本才合并"。
    """
    if not bool(getattr(cfg, "MEMORY_FACT_EXTENSION_ENABLED", True)):
        return False
    a, b = _bare_text(old_text), _bare_text(new_text)
    if not a or not b or a == b:
        return False
    min_chars = int(getattr(cfg, "MEMORY_FACT_EXTENSION_MIN_CHARS", 6) or 0)
    if len(a) < min_chars or len(b) < min_chars:
        return False
    short, long_ = (a, b) if len(a) < len(b) else (b, a)
    ratio = len(short) / float(len(long_))
    if ratio < float(getattr(cfg, "MEMORY_FACT_EXTENSION_MIN_RATIO", 0.5) or 0.0):
        return False
    return long_.startswith(short)


# 自伤/自杀相关事实:分类必须是 health,并置 protected(永不遗忘、不参与近义合并)。
# LLM 抽取可能把这类事实标错类别,错位会污染按主题检索与触发词统计,
# 故在落库层做确定性兜底。
_SAFETY_MARKERS = (
    "自杀", "自伤", "自残", "轻生", "不想活", "活着没意思", "活着挺没意思",
    "结束生命", "自尽", "割腕", "跳楼", "撑不下去",
)


def _safety_category(text: str, category: str) -> tuple:
    """返回 (修正后的 category, 是否受保护)。"""
    t = str(text or "")
    if any(m in t for m in _SAFETY_MARKERS):
        return "health", True
    return str(category or "other"), False


def _merge_compatible(old_value: dict, category: str) -> bool:
    """该条已有事实是否允许与新事实做「近义合并」。

    保护类(risk/care)与受保护条目一律不参与近义合并 —— 宁可留下冗余,
    也不把不同严重度的风险相关事实糊成一条。
    """
    v = old_value or {}
    if v.get("protected"):
        return False
    cats = {str(v.get("category") or "other"), str(category or "other")}
    if cats & set(cfg.MEMORY_PROTECTED_CATEGORIES):
        return False
    return True


def _find_similar_fact(user: str, text: str, category: str):
    """在已有事实里找一条可合并的条目 → ``(key, value, kind)`` 或 None。

    kind 取值(按优先级,只会返回一个):
      · ``"date_variant"`` —— 去掉 ISO 日期后骨架重合(见 _is_date_variant):
        同一件事被重新锚定了日期,落库侧必须保留首次断言(见 merge_fact);
      · ``"extension"``    —— 短的是长的严格前缀(见 _is_wording_extension):
        同一件事的详略两版,落库侧保留更长的那版;
      · ``None``           —— 普通近义(见 long_memory/similarity.py)。

    近义判定见 long_memory/similarity.py;此处负责:
      - 跳过已归档条目;
      - 用极性守卫挡住"好转 / 加重"这类字面像但语义相反的合并;
      - 多条候选时取相似度最高者(再按 key 兜底,保证确定性)。
    """
    if not cfg.MEMORY_FACT_SIMILARITY_ENABLED or not str(text or "").strip():
        return None
    has_date = _strip_dates(text) != text   # 新文本确实带绝对日期才做变体识别
    variant = None
    ext = None
    best = None
    for it in list_items(ns_facts(user), limit=cfg.MEMORY_FACT_SIMILARITY_SCAN_LIMIT):
        v = it.value or {}
        if v.get("archived"):
            continue
        old_text = str(v.get("text") or "")
        if not old_text or not _merge_compatible(v, category):
            continue
        if _similarity.polarity_conflict(old_text, text):
            continue
        if has_date and _is_date_variant(old_text, text):
            # 日期变体优先级最高(比普通近义更确定);同分取 key 最小,保证确定性。
            if variant is None or it.key < variant[0]:
                variant = (it.key, v)
            continue
        if _is_wording_extension(old_text, text):
            # 详略两版:结构判据(前缀),比模糊相似度更硬,优先级仅次于日期变体
            if ext is None or it.key < ext[0]:
                ext = (it.key, v)
            continue
        if _similarity.is_similar(old_text, text, **_similarity_kwargs()):
            score = _similarity.similarity_score(old_text, text)
            if best is None or (score, it.key) > (best[0], best[1]):
                best = (score, it.key, v)
    if variant is not None:
        return (variant[0], variant[1], "date_variant")
    if ext is not None:
        return (ext[0], ext[1], "extension")
    return (best[1], best[2], None) if best else None


def merge_fact(user: str, text: str, category: str, importance: float,
               event_date: str | None = None,
               support: int | None = None, vote_samples: int | None = None) -> None:
    """事实合并去重:完全相同的规范化文本 → 命中同一个键直接合并。

    三条合并通道(优先级从高到低,由 ``_find_similar_fact`` 判定):
      1. **完全同键** —— 规范化文本逐字相同(抽取提示要求"措辞稳定",这是主路径);
      2. **date_variant** —— 同一件事被重新锚定了不同日期,保留首次断言;
      3. **extension**   —— 短的是长的严格前缀,即"沿用原句 + 补细节"的详略两版,
         保留更长(信息更全)的那版;
      4. 其余走近义阈值(``_similarity.is_similar``,高精度,宁漏不误)。

    近义合并:文本不同但被判定为"同一件事的另一种说法"时,写入已有那条的键
    (mentions+1、文本取更长、重要度取 max),而不是新建一条近似重复的事实。
    键始终是**首次落库时**那条文本的哈希,保证确定性(重复固化不产生新条目)。

    event_date:由 dates.resolve_event_date 在固化时解析出的事件日期(ISO 字符串,
    可为 None)。仅在 Python 侧计算,不进 MemoryFact schema、不由 LLM 输出;
    合并时「最新的非空值」获胜(新近提及的日期比旧的更可信)。

    support / vote_samples:该条事实被几个采样抽到 / 该段共采样几次,落成三个字段:
        · ``support``      被抽到的采样数
        · ``vote_samples`` 采样总数
        · ``weak``         ``support < vote_samples``(只被部分采样抽到)
      弱证据只降权不丢弃。跨段合并时 support 取 max。
      直写路径不传 support ⇒ ``weak=False``。
    """
    ns = ns_facts(user)
    key = _canonical(text)
    now = time.time()
    # 分类先收敛到词表:模型可能给出 sleep/学业 一类词表外的说法,
    # 直接落库会让后续按类别聚合与保护判定读到无法识别的值。
    # 再做安全类兜底:命中风险标记时强制 health + 受保护(见 _safety_category)。
    category, _safety = _safety_category(text, _tags.clean_fact_category(category, text))
    _sup = int(support) if support is not None else None
    _tot = int(vote_samples) if vote_samples is not None else None

    def _merged_weak(old_v: dict) -> tuple:
        """返回 (support, vote_samples, weak);缺字段的旧数据视为强证据。"""
        old_sup = old_v.get("support")
        old_tot = old_v.get("vote_samples")
        sup = max([x for x in (_sup, old_sup) if isinstance(x, int)] or [0]) or None
        tot = max([x for x in (_tot, old_tot) if isinstance(x, int)] or [0]) or None
        weak = bool(isinstance(sup, int) and isinstance(tot, int) and sup < tot)
        return sup, tot, weak
    with _LOCK:
        old = get_item(ns, key) or {}
        kind = None
        if not old:
            hit = _find_similar_fact(user, text, category)
            if hit:
                key, old, kind = hit
        if old:
            old_text = str(old.get("text") or text)
            merged_event_date = event_date or old.get("event_date")
            if kind == "date_variant":
                # 同一件事、只有日期字面量不同:保留**首次断言**的文本与日期。
                # 相对时间(如「下周三」)在不同日子会被重新锚定成不同日期,回忆型
                # 复述尤其容易造出第二个矛盾日期;首次说出口那次才是原始事实。
                # 例外:明确更正(「改到/推迟/不是…」)在提取文本里会带更正词,
                # 那种情况不会命中 date_variant(文本结构已变),会作为新事实落库。
                if (event_date and old.get("event_date")
                        and str(event_date) != str(old.get("event_date"))):
                    logger.info(
                        "事实日期冲突,保留首次断言 %s(丢弃 %s): %s",
                        old.get("event_date"), event_date, old_text[:40])
                merged_event_date = old.get("event_date")
            elif kind == "extension":
                # 详略两版(见 _is_wording_extension):保留更长的那版 —— 通常是
                # 新来的、带新增细节的那版;反过来(新的是截短版)则保留旧的详版。
                if len(str(text)) > len(old_text):
                    logger.info("事实是已有条目的扩写,合并且保留详版: %s", str(text)[:40])
                    old_text = str(text)
            elif len(str(text)) > len(old_text):
                old_text = str(text)
            # 归档状态:重新被提及即解除归档,并清掉 archived_at
            # (缺失 archived_at 会让该行既不可见也删不掉)。
            was_archived = bool(old.get("archived", False))
            _sup_m, _tot_m, _weak_m = _merged_weak(old)
            put_item(ns, key, {
                "type": "fact", "text": old_text,
                # 安全类事实即使旧行分类错了,也以修正后的 health 为准(_safety)
                "category": ("health" if _safety else
                             (old.get("category") if old.get("category") not in ("", None, "other")
                              else category)),
                "importance": max(float(old.get("importance", 0.0)), float(importance)),
                "mentions": int(old.get("mentions", 0)) + 1,
                "first_seen": float(old.get("first_seen", now)),
                "last_seen": now,
                "event_date": merged_event_date,
                "archived": False,
                "archived_at": None,
                "protected": bool(old.get("protected", False)) or _safety,
                "support": _sup_m, "vote_samples": _tot_m, "weak": _weak_m,
            })
            if was_archived:
                logger.info("事实被重新提及,已解除归档: %s", old_text[:40])
        else:
            put_item(ns, key, {
                "type": "fact", "text": str(text), "category": category,
                "importance": float(importance), "mentions": 1,
                "first_seen": now, "last_seen": now,
                "event_date": event_date,
                "archived": False, "protected": bool(_safety),
                "support": _sup, "vote_samples": _tot,
                "weak": bool(isinstance(_sup, int) and isinstance(_tot, int) and _sup < _tot),
            })


# ============================== 危机分面(risk_events,受保护) ==============================
def add_risk_event(user: str, facet: str, quote: str,
                   ts: float | None = None, thread: str | None = None) -> None:
    """记录一条危机分面证据(幂等:确定性键,重复调用不重复落)。

    facet 取值:
      · ``ideation`` / ``intent`` / ``plan`` / ``behavior`` —— LLM 抽取的危机四要素;
      · ``signal`` —— **兜底留痕**:段内出现过 high/crisis 轮次,
        或 LLM 只留了自由文本 risk_note 时,由 ``commit_segment`` 直接写一条,
        引文取**用户原话**,不经过 LLM。它回答的是"这一段被判定过风险、原话是什么",
        与"四要素"语义不同,故不混用 facet 名;
      · ``soft_alert`` / ``retracted`` —— 危机确认链的 soft 警铃开窗 / 撤销
        (nodes/triage._confirm);
      · ``uncertain`` —— 固化抽取的疑似危机留痕(graphs/memory_graph._crisis_hook);
      · ``review_support`` —— 复核调查 support 结论
        (long_memory/review_channel.run_due_review)。
    quote 必须是用户原话(截断清洗)。
    该命名空间受保护:永不参与遗忘归档/删除,也不进任何情绪/趋势聚合。
    """
    if not user or not facet or not quote:
        return
    quote = " ".join(str(quote).split())[:200]
    ts = float(ts) if ts is not None else time.time()
    with _LOCK:
        put_item(ns_risk(user), _risk_key(user, facet, quote, ts), {
            "type": "risk_event", "facet": str(facet), "quote": str(quote),
            "ts": ts, "thread": thread, "protected": True, "archived": False,
        })


def list_risk_events(user: str, limit: int = 500) -> list:
    """列出该用户全部危机分面事件(SearchItem,含 .key/.value)。"""
    if not user:
        return []
    return list_items(ns_risk(user), limit=limit)


def risk_facet_counts(user: str, days: int | None = None,
                      now: float | None = None) -> dict:
    """近 days 天各分面的独立事件数 {facet: n}(供后台留痕/未来人工评审)。"""
    if not user:
        return {}
    now = now if now is not None else time.time()
    out: dict[str, int] = {}
    for it in list_items(ns_risk(user)):
        v = it.value or {}
        ts = float(v.get("ts") or 0.0)
        if days is not None and (ts <= 0 or (now - ts) > float(days) * 86400.0):
            continue
        facet = str(v.get("facet") or "other")
        out[facet] = out.get(facet, 0) + 1
    return out


def commit_segment(user: str, pending: dict, extraction, support: dict | None = None) -> None:
    """把固化结果落库并推进 pending 段(幂等:事实合并、摘要键确定性)。

    pending 在 LLM 期间可能被新轮次追加;此处只消费快照之前的轮次,
    快照之后新增的轮次保留在 pending 里,等待下一轮固化。

    support:``consolidate.merge_extractions`` 的并集产物
    ``{"facts": {规范化文本: 支持度}, "vote_samples": n}``。
    传入后每条事实会带上 ``support``/``vote_samples``/``weak`` 三个字段
    (弱证据 = 只被部分采样抽到),由检索排序侧降权而非丢弃 —— 详见 merge_fact 的 docstring。
    """
    snap_seq = int(pending.get("next_seq", 0))
    turns = pending.get("turns") or []
    now = time.time()
    # 相对时间锚点:以段首轮次的 ts 为「对话发生的时刻」,缺失/非法回落 now。
    anchor_dt = _dates.anchor_datetime(pending)
    facets = _tags.clean_crisis_facets(getattr(extraction, "crisis", None))
    risk = bool(extraction.risk_note and extraction.risk_note.strip()) or bool(facets) or any(
        _turn_has_risk(t) for t in turns
    )

    with _LOCK:
        risk_ns = ns_risk(user)
        risk_keys_before = {it.key for it in list_items(risk_ns)}
        # 是否首次提交这一段 —— 决定 2.7 的自检是否告警(幂等重提交不算故障)。
        first_commit = get_item(ns_episodes(user), _episode_key(user, pending)) is None
        # 1) 事实合并入库:先段内近义去重,再跨段合并。
        #    去重跑在原始提取事实之上;时间词绝对化在合并这一步做。
        for f in _similarity.dedupe(
                list(extraction.facts or []),
                key_fn=lambda x: getattr(x, "text", ""),
                **_similarity_kwargs()):
            raw_text = str(getattr(f, "text", "") or "")
            # 支持度按原始提取文本的规范化键查;规范化复用 norm_text,
            # 与 consolidate.merge_extractions 写出的键同源。
            _sup, _tot = None, None
            if support:
                _v = (support.get("facts") or {}).get(norm_text(raw_text))
                _sup = int(_v) if isinstance(_v, int) else None
                _n = support.get("vote_samples")
                _tot = int(_n) if isinstance(_n, int) and _n else None
            merge_fact(user, _dates.absolutize(raw_text, anchor_dt),
                       f.category, f.importance,
                       event_date=_dates.resolve_event_date(raw_text, anchor_dt),
                       support=_sup, vote_samples=_tot)
        # 2) 会话摘要入库(确定性键 → 重试覆盖不重复);同时落「标签卡」
        importance = max(float(getattr(extraction, "importance", 0.5) or 0.5),
                         0.85 if risk else 0.0)
        arc = _tags.state_arc(turns) or {}
        shift = arc.get("emotion_shift") or {}
        evidence = _tags.clean_evidence(
            getattr(extraction, "dimension_evidence", None) or {})
        topics = _tags.clamp_topics(getattr(extraction, "topics", None) or [])
        triggers = _tags.clamp_short_list(getattr(extraction, "triggers", None) or [])
        coping = _tags.clamp_short_list(getattr(extraction, "coping", None) or [])
        put_item(ns_episodes(user), _episode_key(user, pending), {
            "type": "episode",
            # 摘要与事实一样做时间词绝对化(digest 会被原样读回提示词)。
            "digest": _dates.absolutize(
                _tags.clean_digest(getattr(extraction, "digest", "")), anchor_dt),
            "importance": importance, "risk_note": str(extraction.risk_note or ""),
            "category": "risk" if risk else "episode",
            "protected": bool(risk),
            "ts_start": float(pending.get("started_at", now)),
            "ts_end": float(pending.get("last_ts", now)),
            "turn_count": len(turns),
            "first_seen": now, "last_seen": now,
            "archived": False,
            # ---- 标签卡(闭合词表,粗粒度;见 long_memory/tags.py) ----
            "topics": topics,
            "dimension_evidence": evidence,
            "triggers": triggers,
            "coping": coping,
            "dominant_emotion": arc.get("dominant_emotion", "neutral"),
            "valence_arc": arc.get("valence_arc", "stable"),
            "emotion_shift": "{0}→{1}".format(
                shift.get("from", "neutral"), shift.get("to", "neutral")),
            "arousal": arc.get("arousal"),
        })
        # 2.5) 危机分面独立落库(受保护命名空间;绝不参与情绪聚合与遗忘)
        #      统一走 add_risk_event,避免与它重复实现同一套落库逻辑
        if facets:
            base_ts = float(pending.get("last_ts", now))
            thread = (pending.get("threads") or [None])[0]
            for facet, quote in facets.items():
                add_risk_event(user, facet, quote, ts=base_ts, thread=thread)
        # 2.6) 兜底留痕:不依赖 LLM,仅在分面为空时写入 signal。
        signal_quote = ""
        signal_ts = float(pending.get("last_ts", now))
        if not facets:
            risk_turns = [t for t in turns if _turn_has_risk(t)]
            if risk_turns:
                # 优先用用户原话(运行时判定的那一轮),其次用 LLM 的 risk_note
                signal_quote = " ".join(str(risk_turns[0].get("user") or "").split())
                signal_ts = float(risk_turns[0].get("ts") or signal_ts)
            if len(signal_quote) < 2 and extraction.risk_note and str(extraction.risk_note).strip():
                signal_quote = " ".join(str(extraction.risk_note).split())
            if len(signal_quote) >= 2:
                add_risk_event(user, "signal", signal_quote, ts=signal_ts,
                               thread=(pending.get("threads") or [None])[0])
        # 2.7) 自检:风险段未留下任何新留痕 → 告警。仅在首次提交时判定。
        if risk and first_commit:
            risk_keys_after = {it.key for it in list_items(risk_ns)}
            if not (risk_keys_after - risk_keys_before):
                logger.warning(
                    "固化自检:该段被判定为风险段(risk=True),但没有写入任何 risk_event。"
                    "user=%s turns=%d facets=%s risk_note=%r —— "
                    "请检查危机分面抽取与 signal 兜底(见 store.commit_segment 2.6/2.7)",
                    user, len(turns), sorted(facets),
                    str(getattr(extraction, "risk_note", "") or "")[:40])
        # 3) 推进 pending:只清掉快照覆盖的轮次,保留期间新增的
        # 被时间间隔切出的旧段来自 ready 队列，不得误删新开启的当前段。
        ready_key = str(pending.get("_ready_key") or "")
        if ready_key:
            delete_item(ns_pending_ready(user), ready_key)
            return
        cur = get_item(ns_pending(user), _PENDING_KEY)
        if not cur:
            return
        cur = dict(cur)
        cur_seq = int(cur.get("next_seq", 0))
        if cur_seq <= snap_seq:
            delete_item(ns_pending(user), _PENDING_KEY)
            return
        leftover = [t for t in (cur.get("turns") or []) if int(t.get("seq", 0)) >= snap_seq]
        threads = []
        for t in leftover:
            if t.get("thread") not in threads:
                threads.append(t.get("thread"))
        put_item(ns_pending(user), _PENDING_KEY, {
            "turns": leftover, "next_seq": cur_seq,
            "started_at": float(leftover[0].get("ts", cur.get("started_at", now)))
            if leftover else float(cur.get("started_at", now)),
            "last_ts": float(cur.get("last_ts", now)), "threads": threads,
        })
