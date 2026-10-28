# -*- coding: utf-8 -*-
"""关怀运行时状态与待下发 outbox(纯文件 IO;与长期记忆数据完全分离)。

本模块只存「关怀自己的状态」:
  - 用户开关(主动关怀可关闭);
  - 当日已发数 / 最近一条主动消息时间(按自然日滚动);
  - 各维度最近一次探询时间(配合 CARE_PROBE_MIN_GAP_DAYS 防止绕同一问题转);
  - 待下发 outbox:调度线程评估后写入,前端/桌宠拉取并逐条确认。

设计约定:
  - 记忆归 long_memory;本文件位于 care/ 数据路径(CARE_STATE_PATH);
  - 锁 / 原子替换等样板由 ``common.json_state.JsonStateFile`` 提供;
  - 测试用 ``set_state_path()`` 指到临时文件隔离。

与 pet.runtime 各自独立的域,各写各的文件,不共享状态。
"""
import re
import time
import uuid
from datetime import date

from gzhu_cup2.agent.common.json_state import JsonStateFile
from gzhu_cup2.agent.config import config as cfg


def _empty() -> dict:
    return {"users": {}, "pending": []}


def _ensure_memory_dir() -> None:
    """使用默认路径时,确保统一记忆目录存在并完成旧数据一次性迁移。

    重定向到自定义路径(测试/部署)时**不会**被调用,避免无端创建记忆目录。
    """
    from gzhu_cup2.agent.long_memory import paths
    paths.ensure_memory_dir()


_STATE = JsonStateFile(cfg.CARE_STATE_PATH, _empty, on_default_path=_ensure_memory_dir)


# ============================ 路径与重置 ============================

def set_state_path(path: str) -> None:
    """重定向状态文件(测试/部署用);空串回到 config 默认。"""
    _STATE.set_path(path)


def reset() -> None:
    """清空全部关怀状态(测试隔离用)。"""
    _STATE.reset()


# ============================ 领域辅助 ============================

def _today() -> str:
    return date.today().isoformat()


def _entry(data: dict, user: str) -> dict:
    entry = data["users"].get(user)
    if entry is None:
        entry = {"enabled": bool(cfg.CARE_DEFAULT_ENABLED), "date": _today(), "date_count": 0,
                 "last_proactive_ts": None, "last_petlife_ts": None, "dims": {},
                 "last_delivered_ts": None, "unanswered_streak": 0,
                 "last_farewell_ts": None, "last_profile_like_ts": None,
                 "recent_texts": []}
        data["users"][user] = entry
    entry.setdefault("dims", {})
    entry.setdefault("last_petlife_ts", None)
    entry.setdefault("last_delivered_ts", None)
    entry.setdefault("unanswered_streak", 0)
    entry.setdefault("last_farewell_ts", None)
    entry.setdefault("last_profile_like_ts", None)
    entry.setdefault("recent_texts", [])
    return entry


def _roll(entry: dict) -> None:
    """跨自然日滚动清零当日计数。"""
    today = _today()
    if entry.get("date") != today:
        entry["date"] = today
        entry["date_count"] = 0


def _counters_of(entry: dict) -> dict:
    """counters 视图(单一来源,供 counters()/status()/reset_unanswered() 共用)。"""
    return {
        "date": entry.get("date"),
        "date_count": int(entry.get("date_count", 0)),
        "last_proactive_ts": entry.get("last_proactive_ts"),
        "last_petlife_ts": entry.get("last_petlife_ts"),
        # 只有**真正投递出去**的消息才更新这两项(见 mark_sent / mark_blocked)
        "last_delivered_ts": entry.get("last_delivered_ts"),
        "unanswered_streak": int(entry.get("unanswered_streak", 0)),
        # 用户宣告离场(睡了/晚安)的时刻;见 note_user_turn
        "last_farewell_ts": entry.get("last_farewell_ts"),
        # 最近一次"用用户自述 likes 作为话题"的主动消息;见 CARE_PROFILE_LIKE_MIN_GAP_DAYS
        "last_profile_like_ts": entry.get("last_profile_like_ts"),
    }


# ============================ 用户在场(离场宣告) ============================
# 只在**用户那一轮真的像"我要去睡了"**时落一个时间戳,由 policy 的
# farewell gate 消费(见 config.CARE_FAREWELL_QUIET_HOURS)。
# 为什么放在 care 域而不是 web 层:这是"什么时候不该说话"的知识,
# 与 care.policy 是同一个关注点;web 只负责在落轮次时**通知**一声。
_FAREWELL_CUES = (
    "晚安", "睡了", "睡啦", "睡咯", "我先睡", "去睡", "睡觉了", "该睡了",
    "我困了", "困了", "去躺了", "先躺了",
)
# 分句:离场词必须落在**某一句的句尾**才算数(见 is_farewell)
_CLAUSE_SPLIT = re.compile(r"[，。！？、；：,.!?;:~～\s]+")


def is_farewell(text: str) -> bool:
    """这条用户输入是否在**宣告离场**(去睡觉/道晚安)。

    两条刻意收紧的口径,都是为了别把"不是离场"的话判成离场 ——
    误判的代价是桌宠整晚静默(真实的错过关心):

      · 疑问句不算:「你睡了吗」是在问对方;
      · 离场词必须落在**句尾**:「我今天睡了一下午」里的"睡了"是叙述,
        不是宣告。
    """
    t = str(text or "").strip()
    if not t:
        return False
    if "吗" in t or "?" in t or "？" in t:
        return False
    for clause in _CLAUSE_SPLIT.split(t):
        if clause and any(clause.endswith(c) for c in _FAREWELL_CUES):
            return True
    return False


def note_user_turn(user: str, text: str, now: float | None = None) -> bool:
    """记录「用户刚说了一句话」,返回状态是否发生变化。

    · 命中离场词 → 记下时刻(policy 在 CARE_FAREWELL_QUIET_HOURS 内不开口);
    · 未命中     → **清除**旧标记(用户又说话了,说明他回来了)。

    只在需要变化时写盘:正常对话轮次不该为"没有离场"这件事反复落文件。
    """
    now = float(now) if now is not None else time.time()
    want = now if is_farewell(text) else None
    with _STATE.view() as data:
        cur = _entry(data, user).get("last_farewell_ts")
        if cur == want or (cur is None and want is None):
            return False
    with _STATE.locked() as data:
        _entry(data, user)["last_farewell_ts"] = want
    return True


# ============================ 开关与计数 ============================

def enabled(user: str) -> bool:
    with _STATE.view() as data:
        return bool(_entry(data, user).get("enabled", True))


def set_enabled(user: str, on: bool) -> bool:
    with _STATE.locked() as data:
        _entry(data, user)["enabled"] = bool(on)
        return bool(on)


def dims_map(user: str) -> dict:
    """该用户各维度最近探询时间 {dim_key: ts}。"""
    with _STATE.view() as data:
        return dict(_entry(data, user).get("dims") or {})


def counters(user: str) -> dict:
    """{date, date_count, last_proactive_ts, last_petlife_ts,
    last_delivered_ts, unanswered_streak}(自动跨日滚动并落盘)。"""
    with _STATE.locked() as data:
        entry = _entry(data, user)
        _roll(entry)
        return _counters_of(entry)


def recent_sent_texts(user: str, limit: int = 2) -> list:
    """最近几条**已下发**的主动消息文本(新→旧);生成层用它避免重复开头。

    与 outbox 分开存:用户读完消息就会 dismiss,outbox 那条随之消失,
    但它"已经说过"这件事仍然要拦着下一句别再重复。
    """
    if not user:
        return []
    with _STATE.view() as data:
        entry = _entry(data, user)
        rows = [str(t) for t in (entry.get("recent_texts") or []) if str(t).strip()]
    return list(reversed(rows))[:max(1, int(limit))]


def reset_unanswered(user: str) -> dict:
    """用户回过话 → 这是新的一轮:退避计数清零。返回刷新后的 counters。

    由 coordinator 在"发现用户在最后一条主动消息之后说过话"时调用;
    清零后 policy 不再施加退避,提问型消息也随之解封。
    """
    with _STATE.locked() as data:
        entry = _entry(data, user)
        entry["unanswered_streak"] = 0
        entry["last_delivered_ts"] = None
        return _counters_of(entry)


# ============================ outbox ============================

def _day_of(ts: float) -> str:
    """某时间戳所在自然日(本地时区)。

    与 ``_today()`` 的区别:后者永远取「真实今天」。仪式相关一律走这里,
    以保证判定与落库用同一基准。
    """
    from datetime import datetime
    return datetime.fromtimestamp(float(ts)).date().isoformat()


def last_ritual_day(user: str) -> str | None:
    """最近一次**处理过**定时仪式的自然日(YYYY-MM-DD);无则 None。

    当天幂等靠它 —— 「发出」与「生成失败放弃」两条路径都会写它(见 care/ritual.py)。
    """
    with _STATE.view() as data:
        return _entry(data, user).get("last_ritual_day")


def mark_ritual_done(user: str, now: float | None = None) -> str:
    """标记**该时刻所在自然日**的仪式已处理(不写 outbox)。

    用于「生成了但发不出去」的放弃路径(不标记的话调度线程会每轮重打一次 LLM)。
    日期取自 ``now``,与判定同基准(传历史时刻时不会错位)。
    """
    day = _day_of(now if now is not None else time.time())
    with _STATE.locked() as data:
        _entry(data, user)["last_ritual_day"] = day
    return day


def mark_sent(user: str, kind: str, text: str,
              topic=None, now: float | None = None, *, ritual: bool = False,
              profile_like: bool = False) -> str:
    """记录一条主动消息已下发:滚动计数、写 outbox;probe 记录维度时间。

    ``ritual=True`` 用于**定时仪式**(见 care/ritual.py):照常入 outbox、照常推进冷却,
    但
      - **不占当日额度**;
      - **不推进未获回应退避**;
    并记下 ``last_ritual_day`` 供当天幂等。

    ``profile_like=True`` 标记"这条话题来自用户自述 likes",单独记
    ``last_profile_like_ts`` 供 policy 的 7 天频控使用(不影响 recall 计数)。
    """
    now = float(now) if now is not None else time.time()
    msg_id = uuid.uuid4().hex
    with _STATE.locked() as data:
        entry = _entry(data, user)
        _roll(entry)
        entry["last_proactive_ts"] = now
        if ritual:
            entry["last_ritual_day"] = _day_of(now)
        else:
            entry["date_count"] = int(entry.get("date_count", 0)) + 1
            # 只有真正投递出去的消息才推进这两项(mark_blocked 不碰)
            entry["last_delivered_ts"] = now
            entry["unanswered_streak"] = int(entry.get("unanswered_streak", 0)) + 1
        if kind == "petlife":
            entry["last_petlife_ts"] = now
        if profile_like:
            entry["last_profile_like_ts"] = now
        # 最近说过的话(定长环):生成层用它避开重复开头/口头禅。
        # 不能只靠 outbox:用户读完就会 dismiss,留痕随之消失,下一句又会"好久不见"。
        ring = list(entry.get("recent_texts") or [])
        ring.append(" ".join(str(text or "").split())[:200])
        entry["recent_texts"] = ring[-5:]
        if kind == "probe" and topic:
            entry.setdefault("dims", {})[str(topic)] = now

        # 顺带清理已过时效的旧消息(与 prune_expired 同一把尺子,见 _is_fresh)
        pending_kept = [m for m in data["pending"] if _is_fresh(m, now)]
        pending_kept.append({
            "id": msg_id, "user": str(user), "kind": str(kind),
            "topic": topic, "text": str(text), "created_ts": now,
        })
        data["pending"] = pending_kept
        return msg_id


def mark_reminder(user: str, text: str, now: float | None = None,
                  kind: str = "agenda") -> str:
    """记录一条定时提醒(日程)已下发:只入 outbox,不动关怀计数。"""
    now = float(now) if now is not None else time.time()
    msg_id = uuid.uuid4().hex
    with _STATE.locked() as data:
        pending_kept = [m for m in data["pending"] if _is_fresh(m, now)]
        pending_kept.append({
            "id": msg_id, "user": str(user), "kind": str(kind),
            "topic": None, "text": str(text), "created_ts": now,
        })
        data["pending"] = pending_kept
        return msg_id


def mark_blocked(user: str, kind: str, now: float | None = None) -> None:
    """记录一次「生成失败/违规被丢弃」:只推进冷却,不占当日额度、不入 outbox。

    把 last_proactive_ts 推到当前时刻,未来 cooldown_hours 内不再尝试;
    date_count 不增加。不碰 last_delivered_ts / unanswered_streak。
    """
    now = float(now) if now is not None else time.time()
    with _STATE.locked() as data:
        entry = _entry(data, user)
        _roll(entry)
        entry["last_proactive_ts"] = now


# 不同 kind 的「仍值得投递」窗口(小时)。时间敏感的话过期即丢,**不补发**:
#   - ritual(晚安)单独按「同一自然日」判(见 _is_fresh):22:30 的晚安不该在
#     次日 18:00 才弹出来 —— 这与 care/ritual.py「跨过午夜不再补发」是同一口径;
#   - soothe/checkin/petlife:几小时内还有意义,放一天就别再打扰;
#   - probe/recall:问题型消息可以多等几小时,但隔天再问「最近睡得好吗」很怪。
# 未列出的 kind 回落到 CARE_PENDING_MAX_AGE_HOURS(通用保留期)。
_KIND_MAX_AGE_HOURS = {
    "soothe": 6,
    "checkin": 6,
    "petlife": 6,
    "probe": 12,
    "recall": 12,
}


def _is_fresh(msg: dict, now: float) -> bool:
    """这条待读消息现在投递是否还合适(时效判定)。

    为什么不能只用全局保留期:``CARE_PENDING_MAX_AGE_HOURS=168`` 是「别把没读的
    永久留在文件里」的兜底,不是「晚一周弹出来也没关系」—— 补弹过期的晚安/问候,
    用户看到的是「它记错时间了」。按 kind 收紧后,过期消息只留在聊天记录里,不再补弹。
    """
    kind = str(msg.get("kind") or "")
    ts = float(msg.get("created_ts", 0) or 0)
    if kind in ("ritual", "agenda"):
        # 晚安与日程提醒只在「生成当天」有效:跨过午夜一律不补(与仪式窗口同一基准)。
        return _day_of(ts) == _day_of(now)
    cap = _KIND_MAX_AGE_HOURS.get(kind)
    if cap is None:
        cap = float(cfg.CARE_PENDING_MAX_AGE_HOURS)
    return ts >= now - float(cap) * 3600.0


def prune_expired(now: float | None = None) -> int:
    """物理清除已过时效的待读主动消息,返回清除条数。

    由 ``care/scheduler.tick_once`` 每轮调用(``pending()`` 与 ``mark_sent()``
    只在各自路径上顺带过滤/清理)。
    """
    now = float(now) if now is not None else time.time()
    with _STATE.locked() as data:
        before = len(data.get("pending", []))
        data["pending"] = [m for m in data.get("pending", []) if _is_fresh(m, now)]
        return before - len(data["pending"])


def pending(user: str) -> list:
    """该用户的待读主动消息(新→旧),已按 kind 时效 + 在场状态过滤。"""
    since = _last_user_ts(user) if bool(getattr(cfg, "CARE_DROP_STALE_ON_DELIVERY", True)) else None
    with _STATE.view() as data:
        now = time.time()
        out = [
            m for m in data.get("pending", [])
            if str(m.get("user")) == str(user) and _is_fresh(m, now)
            # 生成之后用户已经先说过话 → 这条的前提过期了,别补发。
            # 事例:21:11 生成的 petlife 压到 21:11:15 客户端轮询才弹,
            # 而用户 21:11:14 刚说完「睡了。」—— 弹出来就是"刚说完晚安又被搭话"。
            and (since is None or float(m.get("created_ts", 0) or 0) >= float(since))
        ]
    out.sort(key=lambda m: float(m.get("created_ts", 0)), reverse=True)
    return out


def _last_user_ts(user: str):
    """用户最近一次活跃时刻(读长期记忆的日桶);取不到返回 None。

    局部导入 + 异常兜底:outbox 是纯文件域,不该因为记忆侧读失败而整体报错。
    """
    try:
        from gzhu_cup2.agent.long_memory import ticks
        return ticks.last_activity_ts(user)
    except Exception:  # noqa: BLE001 - 读不到活跃时间 → 不做二次判活
        return None


def dismiss(msg_id: str) -> bool:
    """移除一条已读主动消息;不存在返回 False。"""
    with _STATE.locked() as data:
        before = len(data.get("pending", []))
        data["pending"] = [m for m in data.get("pending", [])
                           if str(m.get("id")) != str(msg_id)]
        return len(data["pending"]) != before


def status(user: str) -> dict:
    """对外状态视图(供 /api/care/status)。"""
    with _STATE.locked() as data:
        entry = _entry(data, user)
        _roll(entry)
        c = _counters_of(entry)
        n = sum(1 for m in data.get("pending", []) if str(m.get("user")) == str(user))
        return {
            "user": user,
            "enabled": bool(entry.get("enabled", True)),
            "date": c["date"],
            "date_count": c["date_count"],
            "last_proactive_ts": c["last_proactive_ts"],
            "pending_count": n,
        }
