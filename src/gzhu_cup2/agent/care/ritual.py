# -*- coding: utf-8 -*-
"""定时仪式:用户自己设的日常锚点(当前只有「晚安」)。

行为约定:
  - 复用主动消息的整条链路(必过 LLM、禁词过滤、落 outbox 与对话流);
  - 不占当日额度、不参与未获回应退避(care/runtime.mark_sent 的 ritual 参数);
  - 尊重打扰等级(安静及以上不发);
  - 当天幂等,只在设定时刻至午夜之间发送,跨过午夜不补发;
  - 不走 care 的免扰时段判断(时刻由用户自己设定)。
"""
import time
from datetime import datetime

from gzhu_cup2.agent.config import config as cfg
from gzhu_cup2.agent.care import content as ccontent
from gzhu_cup2.agent.care import runtime as cruntime


def _minutes_of_day(ts: float) -> int:
    t = datetime.fromtimestamp(float(ts))
    return t.hour * 60 + t.minute


def in_window(now: float) -> bool:
    """是否已到今天的仪式时刻。

    窗口 = 设定时刻 ~ 当天 24:00(不跨午夜):用「当日分钟数 >= 设定分钟数」表达,
    天然排除了次日凌晨 —— 无需额外判断。
    """
    if not cfg.CARE_RITUAL_ENABLED:
        return False
    target = int(cfg.CARE_RITUAL_HOUR) * 60 + int(cfg.CARE_RITUAL_MINUTE)
    return _minutes_of_day(now) >= target


def due(user: str, now: float | None = None) -> bool:
    """今天该不该发这条仪式(纯判断,不产生副作用)。"""
    now = float(now) if now is not None else time.time()
    if not in_window(now):
        return False
    # 用户的「永久关闭主动关怀」仍然优先 —— 与主动消息共用同一个总开关
    if not cruntime.enabled(user):
        return False
    # 当天已处理(发出或放弃)
    today = datetime.fromtimestamp(now).date().isoformat()
    if cruntime.last_ritual_day(user) == today:
        return False
    # 打扰等级:与主动消息同一把尺子。局部导入,避免 pet↔care 顶层成环。
    from gzhu_cup2.agent.pet import mode as pmode
    from gzhu_cup2.agent.pet import runtime as pruntime
    if pmode.at_least(pruntime.current_mode(user, now=now)["level"], "quiet"):
        return False
    return True


def ensure(user: str, now: float | None = None) -> dict | None:
    """调度线程每轮调用:到点且今天未处理 → 生成一条晚安并落 outbox。

    返回落库的消息 dict(与 ``care.coordinator.evaluate`` 同形状),未发则 None。
    """
    now = float(now) if now is not None else time.time()
    if not due(user, now):
        return None

    text = ccontent.generate_message("ritual")
    if not text:
        # LLM 不可用 / 命中禁词被丢弃 → 记冷却,并**标记今天已处理**。
        # 不标记的话,调度线程会每 60s 重打一次 LLM(与 pet.narrative 的
        # 「当日不再重试」同一考虑)。
        cruntime.mark_blocked(user, "ritual", now=now)
        cruntime.mark_ritual_done(user, now=now)
        return None

    # ritual=True:入 outbox、推冷却,但不占当日额度、不动退避(见 mark_sent)
    msg_id = cruntime.mark_sent(user, "ritual", text, now=now, ritual=True)
    return {
        "id": msg_id,
        "user": user,
        "kind": "ritual",
        "topic": None,
        "text": text,
        "reason": "ritual_due",
        "created_ts": now,
    }
