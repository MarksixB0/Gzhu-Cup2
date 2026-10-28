# -*- coding: utf-8 -*-
"""主动关怀调度线程(零 FastAPI 依赖)。

职责:周期性对每个"有记忆痕迹的用户"做一次关怀评估;命中的消息**同时**
写入 outbox(``care/runtime``)与会话历史(``web/sessions``)。

同时负责**刷新心理风险标记**(``long_memory.risk_state``):把守护层的纵向判定
落成用户维度的当前标记与跃迁历史。

设计约定:
  - 单用户失败不得拖垮整个调度(逐用户 try/except);
  - 调度线程永不死(整轮 try/except);
  - 评估间隔与各项门槛全部来自 ``config``(见该文件"主动关怀"段与 DEV_FAST_MODE);
  - **风险标记只在系统内部留痕**:不通知任何外部方、不向用户展示内部标签
    (边界见 ``long_memory/risk_state.py`` 的模块 docstring);
  - 本模块是 care 域的**组装层例外**:投递要同时落会话历史,经 ``web.sessions``
    的单向引用是有意保留的(与 ``sessions`` 惰性引用 ``engine`` 同一性质)。
"""
import logging
import threading

from gzhu_cup2.agent.agenda import reminder as areminder
from gzhu_cup2.agent.care import coordinator as ccoord
from gzhu_cup2.agent.care import ritual as critual
from gzhu_cup2.agent.care import runtime as cruntime
from gzhu_cup2.agent.config import config as cfg
from gzhu_cup2.agent.long_memory import risk_state as mrisk
from gzhu_cup2.agent.long_memory import store as mstore
from gzhu_cup2.agent.pet import narrative as pnarrative
from gzhu_cup2.agent.web import sessions

logger = logging.getLogger(__name__)

_state = {"thread": None, "stop": None}


def tick_once() -> None:
    """先刷新风险标记,再对每个有记忆痕迹的用户做一次关怀评估(无命中则静默)。

    每次调度先懒生成"桌宠今日生活"(每天每用户至多一次 LLM;失败则当日不产文本),
    再把叙事作为 petlife 素材传入评估。

    定时仪式(当前只有晚安)也在这一轮里判:到点且当天未处理才发,见 care/ritual.py。
    用户自己设的日程提醒紧随其后(agenda/reminder.py),不受关怀开关与打扰等级约束。
    """
    users = list(dict.fromkeys([sessions.default_user()] + list(mstore.list_memory_users())))

    # 刷新风险标记:纯算术、零 LLM,失败不影响后续关怀评估
    try:
        stats = mrisk.refresh_all(users)
        if stats.get("changed"):
            logger.info("风险标记刷新:%s", stats)
    except Exception as e:  # noqa: BLE001 - 标记失败不阻断关怀
        logger.warning("risk_state refresh failed: %s", e)

    # 物理清除过期未读的主动消息:否则"不再收到新消息的用户"的 outbox 会永久留痕
    # (pending() 只在读取时过滤,mark_sent 只在发送时清理 —— 见 care/runtime.prune_expired)
    try:
        dropped = cruntime.prune_expired()
        if dropped:
            logger.info("清理过期主动消息 %d 条", dropped)
    except Exception as e:  # noqa: BLE001 - 清理失败不阻断关怀
        logger.warning("care pending prune failed: %s", e)

    for u in users:
        try:
            # 定时仪式先判:它**不走** policy 的免扰时段(那个时段管"别在深夜搭话"),
            # 但自己尊重用户总开关、打扰等级与凌晨红线 —— 见 care/ritual.py。
            ritual = critual.ensure(u)
            if ritual:
                sessions.deliver_proactive(ritual)
            # 日程到点提醒:不受关怀开关与打扰等级约束,失败不阻断后续评估。
            try:
                for msg in areminder.ensure(u):
                    sessions.deliver_proactive(msg)
            except Exception as e:  # noqa: BLE001 - 提醒失败不拖垮调度
                logger.warning("agenda reminder failed user=%s: %s", u, e)
            life = pnarrative.today_or_ensure(u)
            petlife = (life or {}).get("text") or None
            msg = ccoord.evaluate(u, petlife_text=petlife)
            if msg:
                sessions.deliver_proactive(msg)
        except Exception as e:  # noqa: BLE001 - 单用户失败不拖垮调度
            logger.warning("care evaluate failed user=%s: %s", u, e)


def _loop() -> None:
    poll = max(float(cfg.CARE_MIN_EVAL_INTERVAL_SECONDS),
               float(cfg.CARE_EVAL_INTERVAL_SECONDS))
    while not _state["stop"].is_set():
        try:
            tick_once()
        except Exception as e:  # noqa: BLE001 - 调度线程永不死
            logger.exception("care scheduler tick error: %s", e)
        _state["stop"].wait(poll)


def ensure_running() -> bool:
    """幂等启动关怀调度线程。"""
    t = _state["thread"]
    if t is not None and t.is_alive():
        return True
    _state["stop"] = threading.Event()
    th = threading.Thread(target=_loop, name="care-scheduler", daemon=True)
    _state["thread"] = th
    th.start()
    return True


def stop() -> None:
    t = _state["thread"]
    ev = _state["stop"]
    if ev is not None:
        ev.set()
    if t is not None:
        t.join(timeout=3)
    _state["thread"] = None
