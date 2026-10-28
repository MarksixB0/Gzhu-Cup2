# -*- coding: utf-8 -*-
"""主动关怀·决策层:判断「现在该不该主动说话、说什么类型」。

只做四种低风险关怀:
    soothe   用户刚在低落情绪中离开(非危机)时的温柔回访(短冷却);
    probe    维度账本缺口的间接探询(低气压、不暴露量表);
    recall   隔天回访用户近期提过的安全话题(经 list_initiable 白名单);
    checkin  用户久未联系时的轻声问候。
不产生危机/风险触发式关怀,不提及任何分数、等级或内部标记。
本模块零 IO、零 LLM:输入由调用方以 CareSnapshot 传入。
"""
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal, Optional

from gzhu_cup2.agent.config import config as cfg

# `ritual` 是计划内的固定锚点(定时仪式,见 care/ritual.py):复用同一套
# 「必过 LLM + 禁词过滤」链路,但不占当日额度、不参与未获回应退避。
CareKind = Literal["recall", "checkin", "soothe", "probe", "petlife", "ritual"]


@dataclass(frozen=True)
class CareSnapshot:
    """一次「是否主动说话」判定所需的全部输入(由调用方组装)。"""

    enabled: bool = True
    # 判定时刻(秒级时间戳);None = 现在
    now: Optional[float] = None
    # ---- 以下为策略常量(默认取 config,测试可覆盖) ----
    quiet_start_hour: int = cfg.CARE_QUIET_START_HOUR
    quiet_end_hour: int = cfg.CARE_QUIET_END_HOUR
    max_per_day: int = cfg.CARE_MAX_PER_DAY
    cooldown_hours: float = cfg.CARE_COOLDOWN_HOURS
    recall_min_gap_hours: float = cfg.CARE_RECALL_MIN_GAP_HOURS
    checkin_gap_hours: float = cfg.CARE_CHECKIN_GAP_HOURS
    soothe_min_gap_hours: float = cfg.CARE_SOOTHE_MIN_GAP_HOURS
    probe_min_gap_hours: float = cfg.CARE_PROBE_MIN_GAP_HOURS
    probe_max_gap_hours: float = cfg.CARE_PROBE_MAX_GAP_HOURS
    # 与 kind 无关的在场门槛(见 config 同名注释与下方 decide 的前置 gate)
    min_user_silence_minutes: float = cfg.CARE_MIN_USER_SILENCE_MINUTES
    farewell_quiet_hours: float = cfg.CARE_FAREWELL_QUIET_HOURS
    # ---- 运行时状态(由调用方持久化后传入) ----
    last_proactive_ts: Optional[float] = None   # 上一条主动消息时间戳
    date_count: int = 0                          # 今日已发主动消息数
    last_user_ts: Optional[float] = None         # 用户最近活跃时间(来自记忆 ticks)
    # 用户明确宣告离场(「睡了」「晚安」)的时刻;见 care/runtime.note_user_turn
    last_farewell_ts: Optional[float] = None
    initiable_topics: list = field(default_factory=list)  # retrieve.list_initiable() 结果
    # 用户自述的 likes(用户自报,不经固化/风险链);仅在没有近期事实可回访时兜底。
    profile_likes: list = field(default_factory=list)
    last_profile_like_ts: Optional[float] = None
    profile_like_min_gap_days: float = cfg.CARE_PROFILE_LIKE_MIN_GAP_DAYS
    # 用户最近一轮是否以低落/负向收尾(由调用方按 CARE_SOOTHE_RECENT_HOURS 组装)
    recent_distress: bool = False
    # 缺口探询候选:维度账本中窗口内缺失证据、且未在最近探询过的维度 key(最多给 1-2 个)
    probe_candidates: list = field(default_factory=list)
    # ---- 桌宠分享日常(petlife):低频,以桌宠自己为主语分享今日生活 ----
    petlife_text: Optional[str] = None           # 今日桌宠生活叙事文本(有内容才有资格发)
    petlife_min_gap_hours: float = cfg.CARE_PETLIFE_MIN_GAP_HOURS  # 距上一条 petlife 的最短间隔
    last_petlife_ts: Optional[float] = None      # 上一条 petlife 的时间戳
    # ---- 未获回应退避 ----
    # last_delivered_ts 只由 mark_sent 推进
    last_delivered_ts: Optional[float] = None    # 最近一条成功投递的主动消息时间
    unanswered_streak: int = 0                   # 该消息之后用户尚未回话时的累计条数(0 = 已回过/从未发)
    # ---- 守护档位(algo 实时算) ----
    # 只用于关怀升温/节流放宽,不进入任何文案。
    guard_tier: str = "stable"                   # stable / watch / elevated(危机走前台同步协议)
    # ---- 手动强制(用户点了"让桌宠现在说点什么") ----
    # 用户显式点击本身就是知情同意:**时机闸**让位 —— 免扰时段/沉默门槛/晚安禁言/
    # 未回应退避/冷却/每日上限全部跳过,且 checkin 兜底无条件可用(保证必有一条)。
    # **开关与身份门不在这里**:总开关/用户开关/打扰等级由 coordinator 前置拦截,
    # 永不被 force 绕过;安全链(content 禁词过滤)同样照常。
    force: bool = False


@dataclass(frozen=True)
class CareDecision:
    due: bool
    kind: Optional[CareKind] = None
    topic: Optional[str] = None
    reason: Optional[str] = None


def _quiet(reason: str) -> CareDecision:
    return CareDecision(due=False, reason=reason)


def is_quiet_hour(now_ts: float, start_hour: int, end_hour: int) -> bool:
    """是否处于免扰时段(支持跨午夜,如 23:00-08:00;start==end 视为全天无免扰)。"""
    if start_hour == end_hour:
        return False
    h = datetime.fromtimestamp(float(now_ts)).hour
    if start_hour < end_hour:
        return start_hour <= h < end_hour
    return h >= start_hour or h < end_hour


def _backoff_hours(streak: int) -> float:
    """未获回应时的最小重试间隔(小时):按 streak 取表,用尽后保持最后一个值。"""
    table = tuple(getattr(cfg, "CARE_UNANSWERED_BACKOFF_HOURS", ()) or ())
    if not table:
        return 0.0
    idx = max(0, min(int(streak) - 1, len(table) - 1))
    return float(table[idx])


def decide(snap: CareSnapshot) -> CareDecision:
    """按序短路判定。返回 CareDecision,调用方据此决定是否下发主动消息。

    ``snap.force=True``(用户手动触发)跳过全部**时机闸**(quiet_hour / daily_cap /
    user_active / user_farewell / unanswered_backoff / cooldown),且 checkin 兜底
    无条件可用 —— 手动点击保证"必有一条消息的资格";kind 仍按既有优先级链选。
    """
    now = snap.now if snap.now is not None else time.time()
    force = bool(snap.force)

    # 手动触发也要挡"连点":1 分钟内连发两条几乎相同的话,比不发更像机器。
    # 只挡极短间隔,不改变"点了就有一条"的总体承诺(见 CARE_FORCE_MIN_GAP_SECONDS)。
    if force and snap.last_proactive_ts is not None:
        force_gap = float(getattr(cfg, "CARE_FORCE_MIN_GAP_SECONDS", 0) or 0)
        if force_gap > 0 and (now - float(snap.last_proactive_ts)) < force_gap:
            return _quiet("force_too_soon")

    if not snap.enabled:
        return _quiet("disabled")

    if not force and is_quiet_hour(now, snap.quiet_start_hour, snap.quiet_end_hour):
        return _quiet("quiet_hour")

    if not force and snap.date_count >= snap.max_per_day:
        return _quiet("daily_cap")

    # ---- 在场门槛(与 kind 无关) ----
    # 用户从未活跃(或时刻未知)→ 视为足够久,交给下面各 kind 自己的间隔门控。
    gap_hours = float("inf")
    if snap.last_user_ts:
        gap_hours = (now - snap.last_user_ts) / 3600.0

    if not force and gap_hours * 60.0 < float(snap.min_user_silence_minutes or 0.0):
        return _quiet("user_active")

    # 用户已宣告离场(睡了/晚安):在一段时间内一律不开口,无论距上一条主动多久。
    farewell = snap.last_farewell_ts
    if not force and farewell is not None and \
            (now - float(farewell)) < float(snap.farewell_quiet_hours or 0.0) * 3600.0:
        return _quiet("user_farewell")

    # 守护升温:elevated(持续关切,多信号)时,允许打破"冷却"这个红线的前提是
    # 距上次主动已超过 soothe 最短间隔;绝不提升"打扰强度",只是把一次本就会发的
    # 关怀选得更贴。仅当用户刚以低落收尾(recent_distress)才优先走 soothe,
    # 否则仍交给下方正常决策。
    elevated = str(snap.guard_tier or "stable").strip().lower() == "elevated"
    # 档位非 stable(watch / elevated):用于"陪伴型优先于打探型"的排序
    watched = str(snap.guard_tier or "stable").strip().lower() in ("watch", "elevated")
    if elevated and snap.recent_distress:
        # 冷却若仍挡着,但已过 soothe 间隔 → 温柔回访一次(仍是关怀类,非危机)
        if snap.last_proactive_ts is None or \
                (now - snap.last_proactive_ts) >= snap.soothe_min_gap_hours * 3600.0:
            return CareDecision(
                due=True, kind="soothe",
                reason="guard_elevated_after_distress",
            )

    # ---- 未获回应退避 ----
    # 放在守护升温分支之后,保证"状态差时破冷却"的通道不被退避拦掉。
    # 间隔随沉默条数递增并封顶;手动强制时跳过(用户在场,追问权在他自己手上)。
    unanswered = int(snap.unanswered_streak or 0) >= 1
    if not force and unanswered and snap.last_delivered_ts is not None:
        need = _backoff_hours(snap.unanswered_streak)
        if need > 0 and (now - snap.last_delivered_ts) < need * 3600.0:
            return _quiet("unanswered_backoff")

    # 未获回应期间只发存在感型(petlife / checkin),不发提问型(probe / recall / soothe)。
    quiet_ask = (not force) and unanswered and bool(
        getattr(cfg, "CARE_QUIET_ASK_WHEN_UNANSWERED", True))

    if not force and snap.last_proactive_ts is not None and \
            (now - snap.last_proactive_ts) < snap.cooldown_hours * 3600.0:
        return _quiet("cooldown")

    # 0) 低落回访:用户刚在低落情绪中离开且已过短冷却 → 温柔回访(不携带任何风险语言)
    #    守护档位非 stable 时同样走这里 —— 此时"陪伴"正是我们要优先给的,
    #    且它天然排在 probe/recall 之前,等价于"关心强度上升、方向不变"
    #    (reason 只用于排障与答辩追溯,用户看不到)。
    if not quiet_ask and snap.recent_distress and gap_hours >= snap.soothe_min_gap_hours:
        return CareDecision(
            due=True, kind="soothe",
            reason=("guard_soothe_over_probe" if watched else "after_distress"),
        )

    # 0.5) 档位非 stable:先递一句纯陪伴(checkin),不新增追问型。
    # stable 用户不受影响;可整体关闭(见 cfg 同名开关)。
    if not quiet_ask and watched and gap_hours >= snap.soothe_min_gap_hours:
        return CareDecision(
            due=True, kind="checkin", reason="guard_companion_first",
        )

    # 1) 缺口探询:有未覆盖的维度候选,且用户处在「离开了一会儿但不算太久」的窗口内
    if not quiet_ask and snap.probe_candidates and \
            snap.probe_min_gap_hours <= gap_hours <= snap.probe_max_gap_hours:
        return CareDecision(
            due=True, kind="probe", topic=str(snap.probe_candidates[0]),
            reason="fill_gap",
        )

    # 1.5) 档位非 stable:recall 提前到 probe 之前(陪伴型优先于打探型)。
    if not quiet_ask and watched and snap.initiable_topics and \
            gap_hours >= snap.recall_min_gap_hours:
        return CareDecision(
            due=True, kind="recall", topic=str(snap.initiable_topics[0]),
            reason="guard_recall_over_probe",
        )

    # 2) 有近期可主动话题,且用户已有一段时间没来 → 回访该话题(优先)
    if not quiet_ask and snap.initiable_topics and gap_hours >= snap.recall_min_gap_hours:
        return CareDecision(
            due=True, kind="recall", topic=str(snap.initiable_topics[0]),
            reason="recall_topic",
        )

    # 2.2) 没有可回访的近期事实时,用用户自述 likes 做"好奇钩子"。
    #      与 recall 共用文案通道,但频控更严(默认 7 天):稳定偏好不是近况,
    #      不能天天问成打卡;dislikes 不进 care,避免主动提负向内容。
    if not quiet_ask and snap.profile_likes and gap_hours >= snap.recall_min_gap_hours:
        last_like = snap.last_profile_like_ts
        if last_like is None or \
                (now - float(last_like)) >= float(snap.profile_like_min_gap_days) * 86400.0:
            return CareDecision(
                due=True, kind="recall", topic=str(snap.profile_likes[0]),
                reason="profile_like",
            )

    # 2.5) 桌宠分享日常(petlife):低频;有今日叙事且距上一条 petlife 足够久即可发。
    if snap.petlife_text and snap.petlife_min_gap_hours > 0:
        last_p = snap.last_petlife_ts
        if last_p is None or (now - last_p) >= snap.petlife_min_gap_hours * 3600.0:
            return CareDecision(
                due=True, kind="petlife", topic=str(snap.petlife_text),
                reason="pet_share",
            )

    # 3) 用户久未联系 → 轻声问候(不携带具体话题)
    # 手动强制时无条件可用:上面的 kind 链全部落空(没有话题/没有叙事/没有 distress)
    # 也要保证"必有一条消息的资格",否则用户点了按钮却毫无回应,体验比不加按钮更差。
    if force or gap_hours >= snap.checkin_gap_hours:
        return CareDecision(
            due=True, kind="checkin",
            reason="manual_force" if force else "long_absent")
    return _quiet("not_due")
