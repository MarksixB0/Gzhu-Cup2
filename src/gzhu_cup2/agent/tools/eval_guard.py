# -*- coding: utf-8 -*-
"""守护层评估:留出集 + 常数阈值对照。

为什么不用标定集(26 人格)做最终评估
====================================
那 26 个人格既是标定集又是报告集:在它们上面对比,只能得到"看过答案"的数字。

所以这里换一套**参数化生成**的留出语料:
  · 文本由模板拼装,**不**复用 eval_personas 的任何句子(避免"背答案");
  · onset 在 5~20 天均匀采样、恶化幅度连续采样、日活跃率随机,
    而不是像标定集那样固定在"第 8 天 / 第 17 天";
  · 生成器参数与守护层的刻度**不同源**(DELTA_FULL 与阈值见 algo/components/score.py,
    这里的 delta 在 0.05~0.30 连续取值);
  · 固定随机种子 → 结果可复现、可被第三方重跑。

⚠ 结论的边界(必须如实说)
  这仍然是**合成**语料,不是真实用户数据。它能回答"算法在没见过的参数组合上
  是否仍然工作、是否比常数阈值更好",**不能**回答"真实人群上的准确率"。
  真实准确率需要一个保留原文的标注集,而当前固化流程会删除 pending 原文
  (MEMORY_REDESIGN §10.5)—— 这是产品侧要解决的硬约束,不是算法能绕过的。

用法
====
    .venv\\Scripts\\python.exe src/gzhu_cup2/agent/tools/eval_guard.py
    .venv\\Scripts\\python.exe src/gzhu_cup2/agent/tools/eval_guard.py --n 200 --seed 7
"""
import argparse
import os
import random
import sys
from datetime import timedelta

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))
os.environ.setdefault("LONG_MEMORY_BACKEND", "memory")
os.environ["LONG_MEMORY_DIR"] = os.path.join(_ROOT, "demo_eval")
sys.path.insert(0, os.path.join(_ROOT, "src"))

from gzhu_cup2.agent.config import config as cfg                 # noqa: E402
from gzhu_cup2.agent.algo import skeleton as guard_mod        # noqa: E402
from gzhu_cup2.agent.long_memory import store as mstore          # noqa: E402
from gzhu_cup2.agent.long_memory import ticks as mticks          # noqa: E402
from gzhu_cup2.agent.long_memory import trend as mtrend          # noqa: E402
from gzhu_cup2.agent.algo.components.lexicon import behavior_flags, neg_channel  # noqa: E402
from gzhu_cup2.agent.algo.components import triage                         # noqa: E402

TOTAL_DAYS = 21

# ------------------------------------------------------------------ 语料模板
# 刻意与 eval_personas 的句子**不重叠**:那边是"标定集",这边必须是"没见过的说法"。
_T = {
    "neutral": ["上课去了", "中午在食堂吃的", "把作业交了", "看了会儿书",
                "跟室友聊了两句", "去操场走了走", "今天没什么特别的", "还行吧"],
    "mild": ["有点累", "今天挺烦的", "睡不太好", "压力有点大", "没什么精神",
             "不想动", "事情有点多", "心里有点堵"],
    "strong": ["一直缓不过来", "特别难受", "感觉撑得很勉强", "提不起兴趣",
               "什么都不想做", "心里空落落的", "不想跟人讲话", "累到不想睁眼"],
    "withdraw": ["把聚会推掉了", "不想出门", "谁都不想见", "窗帘一天没拉开",
                 "消息一个都没回", "跟他们说我有事不去了"],
    "conflict": ["又跟人吵了一架", "火气特别大", "看谁都不顺眼", "摔了个杯子",
                 "控制不住地发火", "跟室友闹掰了"],
    "anhedonia": ["做什么都没意思", "提不起劲", "什么都不想干", "坐着发呆",
                  "感觉像被抽空了", "对什么都没兴趣", "打游戏也没感觉"],
    "somatic": ["头一直疼", "胃里难受", "心跳得很快", "吃不下东西",
                "晚上翻来覆去睡不着", "浑身没力气", "恶心想吐"],
    "recover": ["今天好像好一点了", "出去晒了晒太阳", "跟朋友吃了顿饭",
                "感觉没那么糟了", "睡了很久，缓过来一些"],
}
_WITHDRAW, _CONFLICT, _ANHEDONIA, _SOMATIC = (
    "withdraw", "conflict", "anhedonia", "somatic")


def _pick_day_text(rng, intensity, symptom_family=None, symptom_p=0.0,
                   grumble_p=0.0):
    """按当天强度与是否带症状族生成 2-4 轮文本。

    ``grumble_p`` = 这个用户"平时说话就带点丧"的基线倾向。
    ⚠ 它存在的理由:真实用户的**基线水平并不相同** —— 有人习惯说"好累""挺烦的"
    (口头禅型),有人几乎不抱怨。一个固定常数阈值(如"近 7 天均值≥0.32")会在
    高基线用户身上**永久报警**,而个体基线不会。留出集若没有基线多样性,
    对比就变成了"常数阈值 vs 常数阈值",测量不到个体化本身的价值。
    """
    n = rng.choice((2, 3, 3, 4))
    out = []
    for _ in range(n):
        r = rng.random()
        if symptom_family and r < symptom_p:
            out.append(rng.choice(_T[symptom_family]))
        elif intensity < 0.15:
            out.append(rng.choice(_T["mild"]) if rng.random() < grumble_p
                       else rng.choice(_T["neutral"]))
        elif intensity < 0.45:
            out.append(rng.choice(_T["mild"]) if r < 0.5 else rng.choice(_T["neutral"]))
        elif intensity < 0.75:
            out.append(rng.choice(_T["strong"]) if r < 0.45 else rng.choice(_T["mild"]))
        else:
            out.append(rng.choice(_T["strong"]) if r < 0.6 else rng.choice(_T["mild"]))
    return out


def _intensity(day, onset, ramp, peak):
    """分段线性:onset 前为 0,之后 ramp 天爬升到 peak,可再叠加日内噪声。"""
    if day < onset:
        return 0.0
    k = min(1.0, (day - onset + 1) / float(max(1, ramp)))
    return peak * k


def gen_trajectory(rng):
    """生成一条轨迹(days: {day: [(hour, text), ...]}, meta: 真值与参数)。"""
    kind = rng.choices(
        ["worsening", "chronic", "symptomatic", "recovery", "stable",
         "stress", "fluctuation", "lowuse", "quiet"],
        weights=[20, 11, 20, 7, 13, 7, 7, 5, 10])[0]
    onset, peak, ramp = None, 0.0, 1
    family, symptom_p, active_p = None, 0.0, 1.0
    # 基线倾向:0 = 从不说丧话;0.45 ≈ 每天都有一句"好累/挺烦的"(口头禅型)
    grumble_p = rng.choice((0.0, 0.0, 0.15, 0.30, 0.45))

    if kind == "worsening":
        onset = rng.randint(5, 17)
        peak = rng.uniform(0.55, 1.0)
        ramp = rng.randint(1, 6)
    elif kind == "chronic":
        onset, peak, ramp = 1, rng.uniform(0.35, 0.55), 21
    elif kind == "symptomatic":
        # 情绪几乎不动,只有症状族 —— 对应"社交退缩/躯体化"这类否认型
        onset = rng.randint(5, 14)
        peak = rng.uniform(0.05, 0.25)
        family = rng.choice((_WITHDRAW, _CONFLICT, _ANHEDONIA, _SOMATIC))
        symptom_p = rng.uniform(0.60, 0.90)
    elif kind == "quiet":
        # 安静恶化:文字侧几乎不动(peak 低、无症状族),唯一信号是"来得越来越少"。
        # 没有这类轨迹,前驱确认增益(engagement/BOCPD)在留出集上不可测。
        onset = rng.randint(6, 12)
        peak = rng.uniform(0.15, 0.40)
        ramp = rng.randint(3, 7)
        active_p = rng.uniform(0.85, 1.0)          # 恶化前来得勤
    elif kind == "recovery":
        onset = rng.randint(4, 9)
        peak = rng.uniform(0.55, 0.95)
        ramp = rng.randint(1, 3)
    elif kind == "stress":
        onset = rng.randint(6, 15)          # 短压力期,4-6 天后自行恢复
        peak = rng.uniform(0.35, 0.6)
        ramp = 2
    elif kind == "fluctuation":
        peak = 0.0
    elif kind == "lowuse":
        active_p = rng.uniform(0.35, 0.55)  # 每 2-3 天来一次,但状态正常

    days = {}
    # ⚠ 恢复日必须**在循环外**采样一次。此前写在循环里,每轮都重新 randint,
    #   于是"第几天恢复"变成随机抖动,同一条轨迹会在恶化/恢复之间反复横跳 ——
    #   那不是算法在测,是语料自己在抖。
    rec_at = (onset + rng.randint(6, 10)) if (kind == "recovery" and onset) else None
    stress_end = (onset + rng.randint(4, 6)) if (kind == "stress" and onset) else None
    fluct_days = set()
    if kind == "fluctuation":
        start = rng.randint(6, 14)
        fluct_days = {start, start + 1}          # 只连续 1-2 天,其余正常
    for d in range(1, TOTAL_DAYS + 1):
        ap = active_p
        if kind == "quiet" and onset and d > onset:
            ap = rng.uniform(0.12, 0.25)     # 恶化后骤降:参与度塌陷本身是信号
        if rng.random() > ap:
            continue
        inten = _intensity(d, onset or 99, ramp, peak)
        if stress_end and d >= stress_end:
            inten = 0.0
        if kind == "fluctuation" and d in fluct_days:
            inten = rng.uniform(0.45, 0.8)
        if rec_at and d >= rec_at:
            inten = 0.0
        fam = family if (family and onset and d >= onset) else None
        texts = _pick_day_text(rng, inten, fam, symptom_p, grumble_p)
        if rec_at and d >= rec_at:
            texts = [rng.choice(_T["recover"])] + texts[:1]
        days[d] = [(rng.randint(8, 23), t) for t in texts]

    pos = kind in ("worsening", "chronic", "symptomatic", "recovery", "quiet")
    return {"days": days, "label": "positive" if pos else "negative",
            "kind": kind, "onset": onset, "peak": round(peak, 2),
            "ramp": ramp, "family": family, "grumble": grumble_p}


# ------------------------------------------------------------------ 灌库 / 判定
def _day_anchor(day_index: int):
    from datetime import datetime
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    return today - timedelta(days=(TOTAL_DAYS - day_index))


def seed(rng_id: str, days: dict) -> None:
    from datetime import datetime
    for ns in (mticks.ns_ticks(rng_id),):
        for it in list(mstore.list_items(ns)):
            mstore.delete_item(ns, it.key)
    for d, turns in days.items():
        anchor = _day_anchor(d)
        for hour, text in turns:
            c = triage.classify(text)
            risk, _ = triage.risk_from(c)
            lx = neg_channel(text)
            mticks.bump_day_bucket(rng_id, {
                "risk_level": risk, "emotion": c["emotion"],
                "intensity": c["intensity"], "negative_score": c["negative_score"],
                "lex_neg": lx,
                "behavior": sorted(behavior_flags(text)),
            }, ts=(anchor + timedelta(hours=hour, minutes=30)).timestamp())


def _now(day_index: int) -> float:
    return (_day_anchor(day_index) + timedelta(hours=23, minutes=59)).timestamp()


def path_baseline(user: str) -> list:
    """对照基线:近 7 天日级 neg 均值 ≥ 0.32 → 报警(一条常数阈值,无状态)。

    ⚠ 要求近 7 天至少 3 天有记录:否则第 1 天只要说一句"有点累"(0.45),
      7 天均值就是 0.45 → 立刻报警。这个"冷启动误报"是朴素阈值法的真实缺陷,
      但与"延迟"指标混在一起会让人误读(它看起来"报得很早")。加上最小样本量,
      是对照组应得的公平性,不是给它开后门。
    """
    out = []
    for d in range(1, TOTAL_DAYS + 1):
        w = mtrend.window_stats(user, days=7, now=_now(d)) or {}
        v = w.get("neg_avg")
        enough = int(w.get("days") or 0) >= 3
        out.append("watch" if (v is not None and enough and v >= 0.32) else "stable")
    return out


SILENCE_DAYS = 14   # 第 21 天之后"用户不再出现"要额外推进的天数


def _run_guard(user: str, tail_days: int = 0) -> tuple:
    """推进守护层状态机,返回 (tier 列表, 分数列表)。

    ``tail_days`` >0 表示正常跑完 21 天后,再按"用户不再出现"推进若干天 ——
    这正是守护层与常数阈值之间**唯一不可替代**的差别,所以必须单独度量:
      · 常数阈值读的是"近 7 天均值",人一不来,窗口就空了 → 立刻回到 stable,
        等于"你不说话 = 你好了";
      · 守护层把"不来"本身当作状态(lapse):3~21 天保持档位不降,超过 21 天才按
        每 14 天降一档。理由很直接 —— 这类产品里,停用往往发生在状态最差的时候。

    prev / since 逐日传递,与 ``risk_state.update`` 的用法一致(每天只看当天及以前)。
    沉默段**不写桶**,只是把 now 往后推,所以它检验的正是"没数据时怎么判"。
    """
    tiers, scores = [], []
    prev, since = None, None
    end = _now(TOTAL_DAYS)
    for d in range(1, TOTAL_DAYS + 1 + tail_days):
        now = _now(d) if d <= TOTAL_DAYS else end + (d - TOTAL_DAYS) * 86400.0
        r = guard_mod.analyze(user, now=now, prev_tier=prev, prev_since=since) or {}
        t = r.get("tier", "stable")
        if prev is not None and t != prev:
            since = now
        prev = t
        tiers.append(t)
        scores.append(float((r.get("score") or {}).get("s_total") or 0.0))
    return tiers, scores


def path_guard(user: str) -> tuple:
    """正常 21 天的 (tier 列表, 分数列表)。"""
    return _run_guard(user)


def _first(path) -> int | None:
    for i, t in enumerate(path, 1):
        if t in ("watch", "elevated"):
            return i
    return None


def _auc(pos, neg) -> float | None:
    """AUROC(Mann-Whitney),分数越高越该报警。"""
    if not pos or not neg:
        return None
    s = 0.0
    for p in pos:
        for n in neg:
            s += 1.0 if p > n else (0.5 if p == n else 0.0)
    return s / (len(pos) * len(neg))


def run(n: int, seed_no: int) -> dict:
    rng = random.Random(seed_no)
    rows = []
    for i in range(n):
        traj = gen_trajectory(rng)
        user = "holdout_%04d" % i
        seed(user, traj["days"])
        b = path_baseline(user)
        # 一次跑满 21+14 天:沉默段前 21 天与正常段完全同一条轨迹,
        # 分成两次调用会把状态机推两遍(旧实现就是这样,白白多跑 21 天)。
        tiers_all, scores_all = _run_guard(user, tail_days=SILENCE_DAYS)
        gpath = tiers_all[:TOTAL_DAYS]
        gscore = scores_all[:TOTAL_DAYS]
        sil = tiers_all[TOTAL_DAYS:]
        rows.append({"traj": traj, "base": b, "guard": gpath, "s2": gscore,
                     "sil": sil})
        for it in list(mstore.list_items(mticks.ns_ticks(user))):
            mstore.delete_item(mticks.ns_ticks(user), it.key)

    def summarize(key):
        tp = fp = fn = tn = 0
        delays, transient = [], 0
        pos_scores, neg_scores = [], []
        for r in rows:
            lab = r["traj"]["label"]
            path = r["guard"] if key == "guard" else r[key]
            final = path[-1]
            alert_final = final in ("watch", "elevated")
            fa = _first(path)
            if lab == "positive":
                # 恢复型:报过就算命中(它本来就该回落)
                hit = (fa is not None) if r["traj"]["kind"] == "recovery" else alert_final
                if hit:
                    tp += 1
                else:
                    fn += 1
                if fa is not None and r["traj"]["onset"]:
                    delays.append(fa - r["traj"]["onset"])
                pos_scores.append(r["s2"][-1] if key == "guard" else (1.0 if hit else 0.0))
            else:
                if alert_final:
                    fp += 1
                else:
                    tn += 1
                if fa is not None:
                    transient += 1
                neg_scores.append(r["s2"][-1] if key == "guard" else (1.0 if alert_final else 0.0))
        prec = tp / (tp + fp) if (tp + fp) else None
        rec = tp / (tp + fn) if (tp + fn) else None
        return {
            "tp": tp, "fp": fp, "fn": fn, "tn": tn,
            "precision": prec, "recall": rec,
            "f1": (2 * prec * rec / (prec + rec)) if (prec and rec) else None,
            "delay_mean": (sum(delays) / len(delays)) if delays else None,
            "transient": transient,
            "auc": _auc(pos_scores, neg_scores) if key == "guard" else None,
        }

    # 沉默保持:只统计"第 21 天仍在报警"的正例 —— 它们才是"系统已经看见了、
    # 接下来用户不来了"的那批人。+7 / +14 天内仍保持报警的比例。
    seen = [r for r in rows
            if r["traj"]["label"] == "positive" and r["guard"][-1] in ("watch", "elevated")]
    hold7 = (sum(1 for r in seen if r["sil"][6] in ("watch", "elevated"))
             / len(seen)) if seen else None
    hold14 = (sum(1 for r in seen if r["sil"][13] in ("watch", "elevated"))
              / len(seen)) if seen else None
    # 常数阈值不具备任何状态:人不来,7 天窗口为空 → 立刻 stable(恒为 0)。
    return {"n": n, "summary": {k: summarize(k) for k in ("base", "guard")},
            "silence": {"n_seen": len(seen), "hold_7d": hold7, "hold_14d": hold14},
            "rows": rows}


def main() -> int:
    ap = argparse.ArgumentParser(description="守护层留出集评估")
    ap.add_argument("--n", type=int, default=160)
    ap.add_argument("--seed", type=int, default=20261023)
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument("--no-precursor", action="store_true",
                    help="把前驱路径(BOCPD 确认的参与度塌陷/作息后移)置零后评估,"
                         "用于度量前驱路径的真实贡献")
    a = ap.parse_args()
    if a.no_precursor:
        from gzhu_cup2.agent.algo.components import score as score_mod
        score_mod.precursor_score = lambda f: {"score": 0.0, "leg": None,
                                               "legs": {}}
    ev = run(a.n, a.seed)

    print("\n留出集(参数化生成,seed=%d,n=%d,每条 %d 天)" % (a.seed, a.n, TOTAL_DAYS))
    print("=" * 78)
    print("%-10s %-14s %-8s %-8s %-8s %-12s %s" % (
        "判定路径", "TP/FP/FN/TN", "精确", "召回", "F1", "平均延迟(天)", "负例瞬时误报"))
    print("-" * 78)
    names = {"base": "常数阈值", "guard": "分数+状态机"}
    for k in ("base", "guard"):
        s = ev["summary"][k]
        print("%-10s %-14s %-8s %-8s %-8s %-12s %s" % (
            names[k],
            "%d/%d/%d/%d" % (s["tp"], s["fp"], s["fn"], s["tn"]),
            "%.3f" % s["precision"] if s["precision"] is not None else "-",
            "%.3f" % s["recall"] if s["recall"] is not None else "-",
            "%.3f" % s["f1"] if s["f1"] is not None else "-",
            "%.2f" % s["delay_mean"] if s["delay_mean"] is not None else "-",
            s["transient"]))
    print("\n  常数阈值 = 近 7 天日级负向均值 ≥ 0.32(无状态、无基线、无个体化)")
    print("  守护层第 21 天分数 AUROC = %s" % (
        "%.3f" % ev["summary"]["guard"]["auc"] if ev["summary"]["guard"]["auc"] else "-"))
    print("  延迟 = 首次报警日 − 真值 onset(负数 = 晚于恶化起点,是**延迟**不是提前)")

    sil = ev["silence"]
    print("\n沉默保持(第 21 天仍在报警的 %d 条正例,之后不再出现):" % sil["n_seen"])
    print("  守护层保持 7 天 = %s   保持 14 天 = %s" % (
        "%.0f%%" % (sil["hold_7d"] * 100) if sil["hold_7d"] is not None else "-",
        "%.0f%%" % (sil["hold_14d"] * 100) if sil["hold_14d"] is not None else "-"))
    print("  常数阈值 = 0%(窗口读不到数据即回到 stable —— 「你不说话=你好了」)")

    if a.verbose:
        print("\n逐条(按类别):")
        for r in ev["rows"]:
            t = r["traj"]
            fa2 = _first(r["guard"])
            print("  %-12s onset=%-3s peak=%.2f ramp=%d fam=%-9s | guard:%-6s S=%.2f" % (
                t["kind"], t["onset"], t["peak"], t["ramp"], t["family"] or "-",
                ("d%d" % fa2) if fa2 else "-",
                r["s2"][-1]))
    return 0


if __name__ == "__main__":
    from gzhu_cup2.agent.common.console import force_utf8_stdout
    force_utf8_stdout()
    raise SystemExit(main())
