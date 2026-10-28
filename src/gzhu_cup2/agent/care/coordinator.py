# -*- coding: utf-8 -*-
"""关怀协调层:组装一次「该不该主动说话」评估并下发。

职责与边界:
  - 本模块只读调用 long_memory 的对外 API(ticks / trend / ledger / retrieve),
    绝不写记忆数据;
  - 决策仍走 care.policy(纯函数),文案走 care.content(LLM 生成,**无模板兜底**),
    下发的已读状态只记入 care.runtime(关怀自己的文件状态);
  - 危机/高风险绝不进入主动关怀:recent_distress 只看低风险低落信号,
    crisis/high 的记忆属于守护引擎职责(见 retrieve 白名单与 policy 注释)。

`evaluate` 是这里的唯一入口,但它本身只负责**流程**:
前置门槛 → 守护档位 → 装配快照 → 决策 → 生成文案 → 落库。
两处体积较大的装配/判定逻辑抽成了独立函数(纯搬运,行为不变),
以免主流程被 20 多行字段赋值淹没。
"""
import re
import time

from gzhu_cup2.agent.config import config as cfg
from gzhu_cup2.agent.care import policy as cpolicy
from gzhu_cup2.agent.care import content as ccontent
from gzhu_cup2.agent.care import runtime as cruntime
from gzhu_cup2.agent.long_memory import ticks
from gzhu_cup2.agent.long_memory import trend
from gzhu_cup2.agent.long_memory import ledger
from gzhu_cup2.agent.long_memory import retrieve
from gzhu_cup2.agent.long_memory import store as mstore
from gzhu_cup2.agent.algo import skeleton as guard_mod


def _recent_distress(user: str, now: float) -> bool:
    """用户最近一轮(≤ CARE_SOOTHE_RECENT_HOURS)是否为非危机低落收尾。

    ⚠ ``now`` 必须透传给 ``last_activity_ts``:它是**上界**(见 ticks 模块说明),
      历史回放(把"现在"锚定到过去某天)时若省略,就会读到未来的日桶,
      得出负数或错误的"距最近活跃"从而误判低落收尾。
    """
    last = ticks.last_activity_ts(user, now=now)
    if not last:
        return False
    if (float(now) - last) > cfg.CARE_SOOTHE_RECENT_HOURS * 3600.0:
        return False
    st = trend.window_stats(user, days=1, now=now)
    if not st or not st.get("turn_count"):
        return False
    neg = st.get("neg_avg")
    if neg is None or neg < cfg.CARE_SOOTHE_NEG_THRESHOLD:
        return False
    if int(st.get("risk_max_rank", 0)) > 1:  # high/crisis 不走主动关怀
        return False
    return True


def _probe_candidates(user: str, now: float) -> list:
    """缺口探询候选:有固化记忆 + 窗口内无证据 + 近期未问过(≤2 个)。

    总开关关闭时一律不产生候选(桌宠不发主动提问,改由陪伴型通道兜底)。
    """
    if not cfg.CARE_PROBE_ENABLED:
        return []
    if not ledger.has_episodes(user):
        return []
    uncovered = ledger.uncovered_dims(
        user, days=cfg.MEMORY_TRAIT_WINDOW_DAYS, now=now)
    dims = cruntime.dims_map(user)
    out = []
    for k in uncovered:
        last = dims.get(k)
        if last is None or (float(now) - float(last)) >= cfg.CARE_PROBE_MIN_GAP_DAYS * 86400.0:
            out.append(k)
        if len(out) >= int(cfg.CARE_PROBE_MAX_CANDIDATES):
            break
    return out


_PROFILE_LIKE_SPLIT = re.compile(r"[\n、,，;；]+")
_PROFILE_LIKE_MAX = 3
_PROFILE_LIKE_ITEM_CHARS = 40


def _profile_likes() -> list:
    """用户自述 likes → 最多 3 个短话题;失败/为空返回 []。

    care 只用 likes,不用 dislikes:主动关怀不该主动提用户讨厌的东西。
    """
    try:
        from gzhu_cup2.agent.core import user_profile as uprofile
        raw = uprofile.get_field("likes")
    except Exception:  # noqa: BLE001 - 偏好是增强素材,失败不影响关怀
        return []
    out = []
    for part in _PROFILE_LIKE_SPLIT.split(str(raw or "")):
        item = " ".join(part.split()).strip()
        if item:
            out.append(item[:_PROFILE_LIKE_ITEM_CHARS])
        if len(out) >= _PROFILE_LIKE_MAX:
            break
    return out


_RISK_RANK = {"low": 0, "medium": 1, "high": 2, "crisis": 3}


def _last_user_line(user: str, now: float) -> str | None:
    """用户最后说的一句话(只作"接话"素材);不合格返回 None。

    三条硬边界,都是为了不让主动消息变成"翻旧账":

      · **时效**:超过 ``CARE_LAST_USER_MAX_AGE_MINUTES`` 就不是接话了 ——
        两小时前的一句话被拿来搭话,比不搭更奇怪;
      · **风险**:high/crisis 轮与负向分过高的轮次一律不用。主动关怀不主动
        揭沉重话题(与 ``recent_distress`` 只走 soothe 的分工一致);
      · **长度**:折叠空白后截断,避免长段粘贴塞满上下文。

    读的是"待固化段 + ready 段"的最近一轮(见 ``store.recent_turns``):
    用户刚说完话时,那一轮通常还没固化,但正是最该被接上的。
    """
    if not bool(getattr(cfg, "CARE_REFER_LAST_USER", True)):
        return None
    try:
        rows = mstore.recent_turns(user, limit=3)
    except Exception:  # noqa: BLE001 - 接话素材是增强,读失败不影响关怀
        return None
    max_age = float(getattr(cfg, "CARE_LAST_USER_MAX_AGE_MINUTES", 120)) * 60.0
    max_chars = int(getattr(cfg, "CARE_LAST_USER_MAX_CHARS", 60))
    max_neg = float(getattr(cfg, "CARE_LAST_USER_MAX_NEG", 0.6))
    for t in rows:
        text = " ".join(str(t.get("user") or "").split())
        if not text:
            continue
        ts = float(t.get("ts") or 0.0)
        if not ts or (float(now) - ts) > max_age:
            return None
        meta = t.get("meta") or {}
        rank = _RISK_RANK.get(str(meta.get("risk_level") or "").strip().lower(), 0)
        if rank >= 2:  # high / crisis 不进主动消息
            return None
        try:
            if float(meta.get("negative_score") or 0.0) >= max_neg:
                return None
        except (TypeError, ValueError):
            pass
        return text[:max_chars]
    return None


def _refer_last_user(user: str, now: float, last_activity,
                     counters: dict) -> str | None:
    """本次要不要引用"用户最后一句"。

    **同一句话只接一次**:若上一条主动消息已经是在用户这句话之后发出的
    (``last_proactive_ts >= last_activity``),说明那次就有机会接这句 ——
    再点一次还接同一句,只会变成复读。
    用户再说一句新的,``last_activity`` 前移,接话资格自动恢复。
    """
    last_proactive = counters.get("last_proactive_ts")
    if last_proactive and last_activity and \
            float(last_proactive) >= float(last_activity):
        return None
    return _last_user_line(user, now)


def _my_last_line(user: str, now: float) -> str | None:
    """桌宠上一句在**对话里**说过的话(不是主动消息);一小时内的才算。

    用途:把桌宠自己刚说过的话喂给生成层,避免主动消息
    "接着自己没说过的话往下演"。
    """
    try:
        rows = mstore.recent_turns(user, limit=3)
    except Exception:  # noqa: BLE001 - 上下文素材是增强,读失败不影响关怀
        return None
    for t in rows:
        text = " ".join(str(t.get("ai") or "").split())
        if not text:
            continue
        ts = float(t.get("ts") or 0.0)
        if not ts or (float(now) - ts) > 3600.0:
            return None
        return text[:80]
    return None


def _build_snapshot(
    user: str,
    now: float,
    counters: dict,
    last_activity,
    guard_tier: str,
    petlife_text: str | None,
    force: bool = False,
) -> "cpolicy.CareSnapshot":
    """把运行时计数 + 配置阈值 + 记忆只读视图装配成决策快照。

    阈值一律**显式读 config**(运行时可变,便于测试与统一改参)。
    `enabled=True` 是写死的:调用方已在前置门槛里检查过总开关与用户开关。
    ``force``(手动触发)透传给 policy:时机闸让位于用户的显式点击。
    """
    return cpolicy.CareSnapshot(
        enabled=True,
        now=now,
        quiet_start_hour=cfg.CARE_QUIET_START_HOUR,
        quiet_end_hour=cfg.CARE_QUIET_END_HOUR,
        max_per_day=cfg.CARE_MAX_PER_DAY,
        cooldown_hours=cfg.CARE_COOLDOWN_HOURS,
        recall_min_gap_hours=cfg.CARE_RECALL_MIN_GAP_HOURS,
        checkin_gap_hours=cfg.CARE_CHECKIN_GAP_HOURS,
        soothe_min_gap_hours=cfg.CARE_SOOTHE_MIN_GAP_HOURS,
        probe_min_gap_hours=cfg.CARE_PROBE_MIN_GAP_HOURS,
        probe_max_gap_hours=cfg.CARE_PROBE_MAX_GAP_HOURS,
        last_proactive_ts=counters["last_proactive_ts"],
        date_count=counters["date_count"],
        last_user_ts=last_activity,
        # 用户宣告离场(睡了/晚安)的时刻:policy 据此在若干小时内一律不开口
        last_farewell_ts=counters.get("last_farewell_ts"),
        min_user_silence_minutes=cfg.CARE_MIN_USER_SILENCE_MINUTES,
        farewell_quiet_hours=cfg.CARE_FAREWELL_QUIET_HOURS,
        initiable_topics=list(retrieve.list_initiable(user) or []),
        profile_likes=_profile_likes(),
        last_profile_like_ts=counters.get("last_profile_like_ts"),
        profile_like_min_gap_days=cfg.CARE_PROFILE_LIKE_MIN_GAP_DAYS,
        recent_distress=_recent_distress(user, now),
        probe_candidates=_probe_candidates(user, now),
        petlife_text=(str(petlife_text).strip() or None) if petlife_text else None,
        last_petlife_ts=counters.get("last_petlife_ts"),
        # 未获回应退避:只认"成功投递"的时间戳(见 care/runtime.mark_sent)
        last_delivered_ts=counters.get("last_delivered_ts"),
        unanswered_streak=int(counters.get("unanswered_streak", 0)),
        guard_tier=guard_tier,
        force=bool(force),
    )


def _resolve_kind(
    user: str, kind: str, now: float, guard_tier: str,
    analyzed: dict | None = None,
) -> tuple[str, dict | None]:
    """决定最终用哪一类文案:持续关切(elevated)命中 soothe/recall 时升为 warm。

    返回 `(kind, warm_facts)`。**只有确实观察到变化才升级**,否则维持原 kind ——
    避免"没看出问题却用观察式口吻点名",那会显得像在监视用户。
    守护信号取用失败时静默退回原 kind(不阻断关怀)。

    ``analyzed`` 是调用方已经算好的 ``guard.analyze()`` 结果;为 None 时才自己算
    (保留旧调用方式,便于单独测试本函数)。
    """
    if kind not in ("soothe", "recall") or guard_tier != "elevated":
        return kind, None
    if analyzed is None:
        try:
            # 用统一入口 analyze
            analyzed = guard_mod.analyze(user, now=now)
        except Exception:  # noqa: BLE001 - 守护信号失败不阻断,退回原 kind
            return kind, None
    a = analyzed or {}
    if not a:
        return kind, None

    sc = a.get("score") or {}
    if not sc:
        return kind, None
    det = sc.get("detail") or {}
    facts = {"mood_down": float(sc.get("s_change") or 0.0) >= 0.5}
    facts["quieter"] = bool(
        float(det.get("engagement_drop") or 0.0) >= cfg.GUARD_ENGAGEMENT_DROP
        or int(det.get("idle_days") or 0) >= cfg.GUARD_INACTIVE_DAYS
        or a.get("mode") == "lapse")
    if facts["mood_down"] or facts["quieter"]:
        return "warm", facts
    return kind, None


def evaluate(user: str, now: float | None = None, petlife_text: str | None = None,
             force: bool = False):
    """对单个用户做一次关怀评估;有可下发消息则记 outbox 并返回 dict,否则 None。

    petlife_text:桌宠今日生活叙事(由调度方调用 pet.narrative 后传入;
    为 None/空 = 本评估不产生桌宠分享日常)。

    force(手动触发,用户点了"让桌宠现在说点什么"):
      - **跳过**「从未活跃」这道调度门槛(用户的点击本身就是在场证据);
      - **时机闸**(免扰/沉默/冷却/每日上限/退避)由 policy 层按 snap.force 跳过;
      - **绝不跳过**:总开关、用户开关、打扰等级(quiet 及以上仍返回 None,
        路由层先一步用 blocked="quiet_mode" 给前端置灰信号)与安全链。
    """
    now = float(now) if now is not None else time.time()

    # ---- 前置门槛(force 也不绕:开关与打扰等级是知情权本身) ----
    # 全局总开关:关闭后无论后台调度还是 /api/care/evaluate 都不产生任何主动消息。
    if not cfg.CARE_ENABLED:
        return None
    if not cruntime.enabled(user):
        return None
    # 打扰等级:quiet 及以上不发任何主动消息(用户临时静默,可到期)。
    # 凌晨不出消息另由 policy 的免扰时段控制,两条独立叠加。
    from gzhu_cup2.agent.pet import mode as pmode       # 局部导入:care.coordinator → pet.runtime → pet.mode
    from gzhu_cup2.agent.pet import runtime as pruntime # → care.policy,顶层导入会成环(同 care/mode.py 的做法)
    if pmode.at_least(pruntime.current_mode(user, now=now)["level"], "quiet"):
        return None
    # 从未活跃过的用户不出现在调度中(cfg.CARE_REQUIRE_ACTIVITY 可关闭)。
    # now 为活动记录的上界,回放时只看该时刻之前的数据。
    # 手动强制例外:点击本身就是"在场",从未活跃的新用户也有资格被自己叫出来。
    last_activity = ticks.last_activity_ts(user, now=now)
    if not last_activity and cfg.CARE_REQUIRE_ACTIVITY and not force:
        return None

    # 纵向守护档位:实时算,只用于关怀升温/文案语气。
    # 用统一入口 analyze,并复用同一份结果(下面 _resolve_kind 也要用);
    # 档位再经危机脆弱窗校准 —— 窗内瞬时分数回落不代表状态回落。
    analyzed: dict | None = None
    guard_tier = "stable"
    try:
        analyzed = guard_mod.analyze(user, now=now)
        guard_tier = str((analyzed or {}).get("tier") or "stable")
        from gzhu_cup2.agent.long_memory import risk_state as mrs
        guard_tier = mrs.effective_tier(user, now=now, instant_tier=guard_tier)
    except Exception:  # noqa: BLE001 - 守护判定失败不阻断关怀,退回 stable
        analyzed, guard_tier = None, "stable"

    counters = cruntime.counters(user)
    # 用户在最后一条**已投递**的主动消息之后说过话 → 这是新的一轮:
    # 退避计数清零,提问型消息随之解封。
    delivered = counters.get("last_delivered_ts")
    if last_activity and delivered and float(last_activity) > float(delivered):
        counters = cruntime.reset_unanswered(user)

    # ---- 决策 ----
    snap = _build_snapshot(user, now, counters, last_activity, guard_tier,
                           petlife_text, force=force)
    decision = cpolicy.decide(snap)
    if not decision.due:
        return None

    # ---- 生成一条 LLM 主动消息(无模板兜底;失败/违规 → mark_blocked 静默)----
    gen_kind, warm_facts = _resolve_kind(
        user, decision.kind, now, guard_tier, analyzed=analyzed)
    # 反重复素材:模型每次都是独立调用,不告诉它"上一条说了什么"就会复读。
    # 距上次对话用 last_activity(而不是 last_delivered)反映真实的聊天间隔。
    since_minutes = None
    if last_activity:
        since_minutes = max(0.0, (now - float(last_activity)) / 60.0)
    text = ccontent.generate_message(
        gen_kind, topic=decision.topic, warm_facts=warm_facts,
        extra={
            # 取 3 条:角度去重要看最近三条(连点第 3 条常绕回更早的角度)
            "_recent": cruntime.recent_sent_texts(user, 3),
            "_since_user_minutes": since_minutes,
            # 用户最后一句:给模型一个"能接上的话头",而不是只会说"这么晚还没睡"。
            # 同一句只接一次,避免连点两次都在回应同一句话。
            "_last_user": _refer_last_user(user, now, last_activity, counters),
            # 今日叙事:桌宠说"自己在做什么"只能取自这里,避免凭空编造经历。
            # petlife 已把叙事作为 topic 传入,不重复塞一遍。
            "_self_life": (str(petlife_text).strip()[:200]
                           if petlife_text and gen_kind != "petlife" else None),
            # 自己刚在对话里说过的话:避免"上一句已经念过诗,这一句又说′来了来了′"。
            "_my_last_line": _my_last_line(user, now),
        },
    )
    if not text:
        # 生成不可用或违规被过滤 → 记冷却(不占当日额度),本次静默
        cruntime.mark_blocked(user, gen_kind, now=now)
        return None

    # ---- 落库并返回 ----
    # 注意:投递记录用 decision.kind(用户看到的"这类消息"),而非可能的 warm 升格
    msg_id = cruntime.mark_sent(user, decision.kind, text,
                                topic=decision.topic, now=now,
                                profile_like=(decision.reason == "profile_like"))
    return {
        "id": msg_id,
        "user": user,
        "kind": decision.kind,
        "topic": decision.topic,
        "text": text,
        "reason": decision.reason,
        "created_ts": now,
        # 用户还在聊天时,这条不该以「主动消息 · 问候」的形式插进来:
        # 桌宠本来就在场,摆一张问候卡会显得它"忽然又出现一次"。
        "inline": bool(
            since_minutes is not None
            and since_minutes <= float(getattr(cfg, "CARE_INLINE_CHAT_MINUTES", 10))),
        # 排障留痕:以后不用再靠推断"这条到底是自动还是手动触发的"。
        "trigger": "manual" if force else "auto",
    }
