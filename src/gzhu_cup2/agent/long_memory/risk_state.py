# -*- coding: utf-8 -*-
"""心理风险标记层:把守护层的纵向判定结果,落成用户维度的当前标记 + 历史。

边界:
  - 不接外部功能:不通知第三方、不生成报告、不弹热线;
  - 不切换危机协议:对话内危机仍由前台 ``triage → handle_crisis`` 处理;
  - 不施测、不问询:标记只影响桌宠的说话方式;
  - 不向用户展示内部标签与分数。

存储:命名空间 ``("memory", user, "risk_state")``,单键 ``state``。
与 ``risk_events`` 的区别:后者是用户原话(受保护、永不遗忘),
前者是系统对当前纵向档位的标记(随数据变化)。
"""
import logging
import time
from typing import Optional

from gzhu_cup2.agent.algo.components.machine import TIER_RANK
from gzhu_cup2.agent.config import config as cfg
from gzhu_cup2.agent.long_memory import store as mstore

logger = logging.getLogger(__name__)

# 历史最多保留条数(防无界增长;状态本身只保留最新一条)
MAX_HISTORY = 200


def ns_risk_state(user: str):
    return ("memory", user, "risk_state")


def _state_key() -> str:
    return "state"


def _history_key() -> str:
    return "history"


def read(user: str) -> Optional[dict]:
    """读当前标记;从未标记过返回 None。"""
    if not user:
        return None
    return mstore.get_item(ns_risk_state(user), _state_key())


def read_history(user: str) -> list:
    """读标记历史(升序)。仅记录**档位发生变化**的时刻,不是每日快照。"""
    if not user:
        return []
    v = mstore.get_item(ns_risk_state(user), _history_key()) or {}
    return list(v.get("entries") or [])


def _crisis_protect_until(user: str, now: float) -> Optional[float]:
    """危机后脆弱窗的截止时刻;无危机记录或窗长为 0 → None。

    update(写盘口径)与 effective_tier(只读口径)共用,保证两层档位
    对"危机后多久不许降档"的口径一致。
    """
    from gzhu_cup2.agent.long_memory import ticks as mticks

    crisis_ts = mticks.last_risk_ts(user, min_rank=3, now=now)
    if not crisis_ts:
        return None
    hours = float(getattr(cfg, "GUARD_CRISIS_PROTECT_HOURS", 72.0) or 0.0)
    return float(crisis_ts) + hours * 3600.0


def effective_tier(user: str, now: Optional[float] = None,
                   instant_tier: Optional[str] = None) -> str:
    """对话分寸等**实时消费方**应使用的当前档位(只读,不落盘)。

    ``tier_of`` 是无状态瞬时判定;危机后分数回落(危机轮不聚合情绪数据
    是常态)会让分寸在下一轮对话就掉回 stable —— 与落盘档位脱节。
    本函数在危机脆弱窗内保证不低于落盘档位,窗外即瞬时档。
    ``instant_tier`` 由调用方传入已算好的瞬时档,避免重复计算。
    """
    if not user:
        return "stable"
    now = float(now) if now is not None else time.time()
    if instant_tier is None:
        from gzhu_cup2.agent.algo import skeleton as guard_mod

        instant_tier = guard_mod.tier_of(user, now=now)
    prot = _crisis_protect_until(user, now)
    prev = read(user) or {}
    prev_tier = str(prev.get("tier") or "stable")
    if prot is not None and now < prot \
            and TIER_RANK.get(instant_tier, 0) < TIER_RANK.get(prev_tier, 0):
        return prev_tier
    return instant_tier


def update(user: str, now: Optional[float] = None,
           reason: str = "scheduler") -> Optional[dict]:
    """按守护层当前判定刷新标记;档位变化时追加一条历史。返回最新标记。

    历史只记跃迁点(不做每日快照)。

    两层档位:
      · ``tier_raw``  —— 守护层的瞬时判断;
      · ``tier``      —— 施加降档保护期后的对外标记(care/洞察消费的就是它)。
    字段语义:
      · ``since``          = 当前对外档位从何时开始(档位变化时重置);
      · ``tier_raw_since`` = 原始判断最近一次变化的时刻,用于给"已经稳定多久"计时。
    """
    if not user:
        return None
    now = float(now) if now is not None else time.time()

    # 延迟导入:algo.skeleton 依赖 long_memory(ticks/ledger),本模块也在
    # long_memory 包内,顶层互相 import 会形成环。
    from gzhu_cup2.agent.algo import skeleton as guard_mod

    prev = read(user)

    # 危机后脆弱窗:窗口内禁止降档(升档仍立即生效)
    prot_until = _crisis_protect_until(user, now)

    prev_tier = (prev or {}).get("tier")
    res = guard_mod.analyze(
        user, now=now, prev_tier=prev_tier,
        prev_since=(prev or {}).get("since"), protected_until=prot_until) or {}
    tier_raw = str(res.get("tier_instant") or res.get("tier") or "stable")

    # 原始判断未变 → 沿用上次的起始时刻;变了 → 从此刻重新计时
    if prev is not None and str(prev.get("tier_raw") or "") == tier_raw \
            and prev.get("tier_raw_since"):
        raw_since = float(prev["tier_raw_since"])
    else:
        raw_since = now

    # 档位由 machine 内部完成迟滞(进入/退出双阈值 + 最短停留 + 沉默保持),
    # 这里不再叠加降档迟滞 —— 双层迟滞会让降档慢到失真。
    tier = str(res.get("tier") or "stable")
    held = bool(res.get("hold")) and tier != tier_raw

    # 危机后 72h 只禁降档 —— analyze 的 protected_until 之外再兜一次,
    # 防止"刚说过买好了绳子、第二天因趋势平稳被判 stable"。
    if prot_until is not None and now < float(prot_until) and prev_tier:
        if TIER_RANK.get(tier, 0) < TIER_RANK.get(prev_tier, 0):
            tier = str(prev_tier)
            held = True

    sc = res.get("score") or {}
    # 复核调查通道(⑦):边界信号挂复核任务(零 LLM,失败不影响守护判定)
    try:
        from gzhu_cup2.agent.long_memory import review_channel
        review_channel.maybe_trigger(user, res, now=now)
    except Exception:  # noqa: BLE001 - 触发检查失败不影响守护判定
        logger.debug("复核触发检查失败 user=%s", user, exc_info=True)

    evidence = {"mode": res.get("mode"), "reason": res.get("reason"),
                "s_total": sc.get("s_total"),
                "s_change": sc.get("s_change"),
                "s_severity": sc.get("s_severity"),
                "s_floor": sc.get("s_floor"),
                "s_chronic": sc.get("s_chronic"),
                "s_behavior": sc.get("s_behavior"),
                "s_fast": sc.get("s_fast"),
                "s_dimension": sc.get("s_dimension"),
                "s_precursor": sc.get("s_precursor"),
                "quality": sc.get("quality"),
                **(sc.get("detail") or {})}
    # 最近一次复核结论摘要(观察量,不参与档位;结论经账本供后续证据消费)
    try:
        from gzhu_cup2.agent.long_memory import review_channel as _rc
        evidence["review"] = _rc.last_summary(user)
    except Exception:  # noqa: BLE001
        pass

    state = {
        "user": user,
        "tier": tier,
        "tier_rank": TIER_RANK.get(tier, 0),
        "tier_raw": tier_raw,
        "tier_raw_since": raw_since,
        "held_by_hysteresis": held,
        "updated_at": now,
        "since": (prev or {}).get("since") or now,
        # 便于内部核对:标记依据的通道真值与数据规模(不做任何用户可见输出)
        "evidence": evidence,
        "note": "内部标记,不对外通知、不对用户展示;详见 long_memory/risk_state.py",
    }

    changed = prev is None or prev.get("tier") != tier
    if changed:
        state["since"] = now           # 档位变化 → 重新计时
        hist = mstore.get_item(ns_risk_state(user), _history_key()) or {}
        entries = list(hist.get("entries") or [])
        entries.append({
            "ts": now,
            "from": (prev or {}).get("tier"),
            "to": tier,
            "reason": reason,
        })
        entries = entries[-MAX_HISTORY:]
        mstore.put_item(ns_risk_state(user), _history_key(),
                        {"entries": entries})
        logger.info("风险标记变化 user=%s %s → %s (%s)",
                    user, (prev or {}).get("tier"), tier, reason)

    mstore.put_item(ns_risk_state(user), _state_key(), state)
    return state


def refresh_all(users=None, now: Optional[float] = None,
                reason: str = "scheduler") -> dict:
    """刷新全部用户的标记(供后台调度周期性调用)。返回统计。"""
    now = float(now) if now is not None else time.time()
    users = list(users) if users is not None else mstore.list_memory_users()
    stats = {"checked": 0, "changed": 0, "elevated": 0, "watch": 0, "stable": 0}
    for u in users:
        prev = read(u)
        st = update(u, now=now, reason=reason)
        if st is None:
            continue
        stats["checked"] += 1
        if (prev or {}).get("tier") != st["tier"]:
            stats["changed"] += 1
        stats[st["tier"]] = stats.get(st["tier"], 0) + 1
    return stats
