# -*- coding: utf-8 -*-
"""复核调查通道(⑦):算术层边界信号 → LLM 按固定 schema 调查 → 结论写回账本。

双层架构的复核腿(probe 取证腿已删,调查是唯一腿):
  - 触发(algo/components/review.should_review,确定性):
    crisis_soft 确认窗 / watch 候选且弱结构腿支撑 / lapse_signal 例外放行;
  - 案卷(确定性):分数分解 + 行为信号 + 分时点基线偏差(daytools)+
    历史相似日(daytools)+ 账本风险证据计数 —— LLM 只判定不取数;
  - 调查(单轮结构化,后台 worker 线程执行):结论 support/refute/watch;
  - 落盘:完整报告存 ns_review(受保护,全程可审计);support 结论另记一条
    risk_event(facet=review_support)进证据管线。**不直接改档位** ——
    状态机语义不变,结论与抽取层同地位(过账本的证据)。

成本控制:每用户冷却 REVIEW_COOLDOWN_H;LLM 失败按任务内计数退避,超限丢弃。
"""
import logging
import time

from gzhu_cup2.agent.algo.components import crisis_chain
from gzhu_cup2.agent.algo.components import review
from gzhu_cup2.agent.config import config as cfg
from gzhu_cup2.agent.core.schemas import ReviewVerdict
from gzhu_cup2.agent.long_memory import store as mstore
from gzhu_cup2.agent.long_memory import ticks as mticks

logger = logging.getLogger(__name__)


# ============================ 触发(前台轻量,零 LLM) ============================

def maybe_trigger(user: str, res: dict, now: float | None = None) -> bool:
    """守护层判定结果落到边界信号时挂一个复核任务;返回是否新挂。

    冷却期内/已有待办/通道关闭时不重复挂。本函数零 LLM,可在每轮安全调用。
    """
    if not user or not getattr(cfg, "REVIEW_ENABLED", True):
        return False
    now = float(now) if now is not None else time.time()
    last = mstore.last_review_report(user)
    if last and (now - float(last.get("ts") or 0.0)) < float(
            getattr(cfg, "REVIEW_COOLDOWN_H", 24.0)) * 3600.0:
        return False
    if mstore.get_review_task(user) is not None:
        return False
    st = mstore.get_crisis_confirm(user)
    trig = review.should_review(
        res.get("features"), res.get("score"),
        confirm_open=crisis_chain.window_open(st))
    if trig is None:
        return False
    mstore.set_review_task(user, {
        "ts": now, "reason": trig.get("reason"), "leg": trig.get("leg"),
        "failures": 0,
        # 触发时刻的判定快照:调查时用同一份特征/分数,保证案卷与触发一致
        "features": res.get("features"), "score": res.get("score"),
    })
    logger.info("复核任务已挂: user=%s reason=%s", user, trig.get("reason"))
    return True


def last_summary(user: str) -> dict | None:
    """最近复核结论的摘要(供 risk_state evidence 审计,不改档位)。"""
    rep = mstore.last_review_report(user)
    if not rep:
        return None
    v = rep.get("verdict") or {}
    return {"conclusion": v.get("conclusion"), "confidence": v.get("confidence"),
            "ts": rep.get("ts"), "reason": rep.get("reason")}


# ============================ 调查(后台 worker 执行) ============================

def _build_case(user: str, task: dict, now: float) -> dict:
    """从任务快照 + 确定性工具组装完整案卷。"""
    from gzhu_cup2.agent.algo.components import daytools
    buckets = mticks.list_day_buckets(user, days=daytools.BASELINE_DAYS, now=now)
    f = task.get("features") or {}
    sc = task.get("score") or {}
    # 当前负面水平:近 3 天负面均值(与触发判定同一数据面)
    recent = buckets[-3:] if buckets else []
    neg_vals = []
    for _, b in recent:
        n = int(b.get("neg_n", 0) or 0)
        if n > 0:
            neg_vals.append(float(b.get("neg_sum", 0.0)) / n)
    cur_neg = sum(neg_vals) / len(neg_vals) if neg_vals else None
    slot_cmp = daytools.compare_to_baseline(buckets, cur_neg, now=now) \
        if cur_neg is not None else None
    similar = daytools.find_similar_days(buckets, days=daytools.SIMILAR_DAYS)
    last = mstore.last_review_report(user)
    return review.build_case(
        f, sc, {"reason": task.get("reason"), "leg": task.get("leg")},
        slot_cmp=slot_cmp, similar_days=similar,
        risk_counts=mstore.risk_facet_counts(user, days=14, now=now),
        confirm_state=mstore.get_crisis_confirm(user),
        last_review=(last or {}).get("verdict"))


def _investigate(case: dict):
    """单轮结构化调查:LLM 拿到案卷输出 ReviewVerdict。"""
    from gzhu_cup2.agent.core.models.LLM_base import get_llm, structured_llm
    from gzhu_cup2.agent.prompts.long_memory_prompts import (
        build_review_investigation_prompt)
    llm = get_llm(max_tokens=int(getattr(cfg, "REVIEW_MAX_TOKENS", 1200)), temperature=0.0)
    messages = build_review_investigation_prompt(case)
    raw = structured_llm(llm, ReviewVerdict).invoke(messages)
    if raw is None:
        raise RuntimeError("复核未取得结构化结论:模型未按 schema 返回")
    return review.normalize_verdict(raw.model_dump())


def run_due_review(user: str, now: float | None = None) -> dict | None:
    """执行该用户的待复核任务(供后台 worker 调用);返回报告或 None。

    失败时任务保留并计数,连续超过 REVIEW_MAX_RETRY 丢弃等下次触发。
    """
    task = mstore.get_review_task(user)
    if not task:
        return None
    now = float(now) if now is not None else time.time()
    case = _build_case(user, task, now)
    try:
        verdict = _investigate(case)
    except Exception as e:  # noqa: BLE001 - 单次调查失败不拖垮 worker
        n = int(task.get("failures") or 0) + 1
        if n > int(getattr(cfg, "REVIEW_MAX_RETRY", 3)):
            logger.warning("复核调查连续失败已丢弃: user=%s (%d 次): %s",
                           user, n, e)
            mstore.set_review_task(user, None)
        else:
            task["failures"] = n
            mstore.set_review_task(user, task)
        logger.warning("复核调查失败 user=%s(第 %d 次): %s", user, n, e)
        return None
    mstore.set_review_task(user, None)
    report = {
        "type": "review_report", "ts": now,
        "reason": task.get("reason"), "leg": task.get("leg"),
        "case": case, "verdict": verdict,
    }
    mstore.save_review_report(user, report)
    # 结论过账本:support 才是风险证据(进计数/脆弱窗管线);refute/watch 只留审计
    if verdict and verdict.get("conclusion") == "support":
        quote = ""
        for it in verdict.get("evidence") or []:
            if it.get("quote"):
                quote = it["quote"]
                break
        mstore.add_risk_event(user, "review_support",
                              quote or verdict.get("note") or "复核支持关注",
                              ts=now)
    return report


def drain_due(now: float | None = None) -> int:
    """扫描全部用户,执行所有待复核任务(worker tick 调用);返回完成数。"""
    now = float(now) if now is not None else time.time()
    done = 0
    for user in mstore.list_memory_users():
        if mstore.get_review_task(user) is None:
            continue
        try:
            if run_due_review(user, now=now) is not None:
                done += 1
        except Exception:  # noqa: BLE001 - 单用户失败不拖垮整轮
            logger.exception("复核通道 drain 失败 user=%s", user)
    return done
