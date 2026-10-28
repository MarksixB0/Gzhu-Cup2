# -*- coding: utf-8 -*-
"""review 组件:复核调查通道的确定性部分(纯函数、零 IO、零 LLM)。

通道语义(2026-10-24 双层架构裁定):
  - 触发器:算术层给出边界信号时才触发(crisis-soft 确认窗开着 /
    watch 候选且主支撑腿是弱结构证据 / 例外放行的数据边界),不是每轮都跑;
  - 案卷:把 LLM 调查要看的确定性事实(分数分解、时段基线偏差、相似日、
    账本风险证据计数)组装成固定结构,LLM 只做判定不做取数 —— 取数全部
    走确定性工具,可离线复算与审计;
  - 结论规范化:support/refute/watch + 证据表(必须绑数据摘录),
    洗掉短引文与超量条目。

结论由调用方写回账本(带来源),不直接改档位;状态机语义不变。
"""
from __future__ import annotations

from typing import Optional

# watch 候选触发只看弱结构证据腿:它们正是 FP 高发区(PULSE"调查降误报"
# 的目标人群);emotion/chronic/dimension 等强结构腿不触发,控制成本。
WEAK_LEGS = ("fast", "precursor")

# 形状歧义腿:均值与低水位之差达到此值 = "有坏时段但也有好时段"的作息形状,
# 数值层无法区分"固定坏时段的口头禅"与"每晚真实崩溃",交由调查裁决。
REVIEW_GAP_MIN = 0.15
REVIEW_GAP_MIN_DAYS = 5   # 窗内有记录天数下限(更稀疏时形状不可信)

MAX_EVIDENCE = 5       # 案卷/结论里证据条目上限
MIN_QUOTE = 2          # 引文最少字符,短于此视为噪声丢弃


def should_review(f: Optional[dict], sc: Optional[dict],
                  confirm_open: bool) -> Optional[dict]:
    """确定性触发器:返回 {"reason"} 或 None(不触发)。

    四类边界信号(2026-10-24 规划裁定):
      crisis_soft     危机确认窗开着(soft 警铃在观察期);
      watch_candidate 分数够 watch 但主支撑是弱结构证据腿;
      mean_floor_gap  均值-低水位差大且分数未及 watch 线的形状歧义;
      abstain         数据边界被例外放行(lapse_signal:稀疏但塌陷被确认)。
    """
    if not f or not sc:
        return None
    if confirm_open:
        return {"reason": "crisis_soft"}
    s = float(sc.get("s_total") or 0.0)
    q_note = str(sc.get("quality_note") or "")
    if q_note == "lapse_signal":
        return {"reason": "abstain"}
    paths = (sc.get("detail") or {}).get("paths") or {}
    leg = max(paths, key=lambda k: paths[k]) if paths else ""
    if enter_watch() <= s < enter_elevated() and leg in WEAK_LEGS:
        return {"reason": "watch_candidate", "leg": leg}
    recent = f.get("recent") or {}
    med = recent.get("neg_median")
    fl = recent.get("floor_median")
    if (med is not None and fl is not None
            and int(recent.get("n_days") or 0) >= REVIEW_GAP_MIN_DAYS
            and float(med) - float(fl) >= REVIEW_GAP_MIN
            and s < enter_watch()):
        return {"reason": "mean_floor_gap", "leg": "slot_shape"}
    return None


def _thresholds() -> tuple:
    """进入线取 machine 的现行参数(单一事实源,eval 改 cfg 即同步)。"""
    from gzhu_cup2.agent.algo.components.machine import params
    p = params()
    return p["enter_watch"], p["enter_elevated"]


def enter_watch() -> float:
    return _thresholds()[0]


def enter_elevated() -> float:
    return _thresholds()[1]


def build_case(f: Optional[dict], sc: Optional[dict], trigger: dict,
               slot_cmp: Optional[dict] = None,
               similar_days: Optional[list] = None,
               risk_counts: Optional[dict] = None,
               confirm_state: Optional[dict] = None,
               last_review: Optional[dict] = None) -> dict:
    """组装调查案卷:全部字段来自确定性计算,LLM 不得自行取数。"""
    paths = ((sc or {}).get("detail") or {}).get("paths") or {}
    case = {
        "trigger": trigger,
        "score": {
            "s_total": (sc or {}).get("s_total"),
            "quality_note": (sc or {}).get("quality_note"),
            "paths": paths,
        },
        "signals": {
            "neg_median": ((f or {}).get("recent") or {}).get("neg_median"),
            "floor_median": ((f or {}).get("recent") or {}).get("floor_median"),
            "night_rate": (f or {}).get("night_rate"),
            "engagement_drop": (f or {}).get("engagement_drop"),
            "phase_shift_h": (f or {}).get("phase_shift_h"),
            "activity_cp": (f or {}).get("activity_cp"),
            "withdraw_rate": (f or {}).get("withdraw_rate"),
            "conflict_rate": (f or {}).get("conflict_rate"),
            "anhedonia_rate": (f or {}).get("anhedonia_rate"),
            "somatic_rate": (f or {}).get("somatic_rate"),
            "coverage": (f or {}).get("coverage"),
            "idle_days": (f or {}).get("idle_days"),
        },
        "slot_baseline": slot_cmp,          # 当前负面水平 vs 所在时段个人基线
        "similar_days": (similar_days or [])[:MAX_EVIDENCE],
        "risk_counts_14d": risk_counts or {},
        "confirm_state": confirm_state,
        "last_review": last_review,
    }
    return case


def normalize_verdict(raw) -> Optional[dict]:
    """清洗 LLM 的结构化结论:conclusion/confidence 必须合法,证据引文过短丢弃、超量截断。

    非法结论整体拒绝(返回 None),由调用方按失败处理,不猜意图。
    """
    if raw is None:
        return None
    if not isinstance(raw, dict):
        try:
            raw = dict(raw)
        except (TypeError, ValueError):
            return None
    conclusion = str(raw.get("conclusion") or "").strip().lower()
    if conclusion not in ("support", "refute", "watch"):
        return None
    confidence = str(raw.get("confidence") or "").strip().lower()
    if confidence not in ("low", "mid", "high"):
        confidence = "low"
    evidence = []
    for it in (raw.get("evidence") or [])[:MAX_EVIDENCE]:
        if not isinstance(it, dict):
            continue
        fact = " ".join(str(it.get("fact") or "").split())
        quote = " ".join(str(it.get("quote") or "").split())
        if len(fact) < MIN_QUOTE:
            continue
        evidence.append({
            "fact": fact[:200],
            "quote": quote[:300] if len(quote) >= MIN_QUOTE else "",
            "source": str(it.get("source") or "").strip()[:40],
        })
    conflicts = [" ".join(str(c).split())[:200]
                 for c in (raw.get("conflicts") or [])
                 if c and len(" ".join(str(c).split())) >= MIN_QUOTE]
    return {
        "conclusion": conclusion,
        "confidence": confidence,
        "evidence": evidence[:MAX_EVIDENCE],
        "conflicts": conflicts[:MAX_EVIDENCE],
        "note": " ".join(str(raw.get("note") or "").split())[:400],
    }
