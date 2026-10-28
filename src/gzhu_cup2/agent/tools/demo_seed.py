# -*- coding: utf-8 -*-
"""把 14 天合成语料灌进长期记忆的 ticks 日桶(内部调参/回归用)。

真实链路(走项目自己的代码):
    - 每轮情绪/风险:      algo.components.triage.classify() / risk_from()
    - 词表:               lexicon.neg_channel()
    - 日桶聚合与落盘:     ticks.bump_day_bucket()  (与 capture_memory 同一条路径)

仿真(非真实):
    - 对话文本本身(合成语料,见 demo_corpus.py)
    - 未跑 LLM 固化(memory_graph),因此没有 facts / episodes
"""
import argparse
import os
from datetime import datetime, timedelta


# 先把记忆目录指向独立数据集,**再**导入 config(避免污染真实记忆库)
_pre = argparse.ArgumentParser(add_help=False)
_pre.add_argument("--dir", default="demo_data")
_pre_args, _ = _pre.parse_known_args()
# 仓库根:本文件在 <repo>/src/gzhu_cup2/agent/tools/,从 __file__ 上溯 5 层
_repo_root = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))
os.environ["LONG_MEMORY_DIR"] = os.path.abspath(
    os.path.join(_repo_root, _pre_args.dir))
os.environ.setdefault("LONG_MEMORY_BACKEND", "sqlite")

from gzhu_cup2.agent.config import config as cfg              # noqa: E402
from gzhu_cup2.agent.long_memory import store as mstore       # noqa: E402
from gzhu_cup2.agent.long_memory import ticks as mticks       # noqa: E402
from gzhu_cup2.agent.algo.components.lexicon import neg_channel  # noqa: E402
from gzhu_cup2.agent.algo.components import triage                      # noqa: E402
from gzhu_cup2.agent.tools.demo_corpus import TRACE           # noqa: E402
from gzhu_cup2.agent.tools import demo_memory_data as dmem    # noqa: E402

HOUR = 3600.0

_RISK_RANK = {"low": 0, "medium": 1, "high": 2, "crisis": 3}


def _day_anchor(day_index: int, total_days: int) -> datetime:
    """把 day_index(1..total)映射到一个真实日期:最后一天 = 今天。

    这样演示数据永远是"最近 14 天",窗口统计(MEMORY_TREND_RECENT_DAYS=3)
    与基线窗口(14 天)都能正常落地。
    """
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    offset = total_days - day_index
    return today - timedelta(days=offset)


def reference_now() -> float:
    """判定的基准时刻 = 最后一天(day 14)的 23:59。

    ⚠ 必须显式传这个时刻进去,不能用 now=time.time():
    本脚本把 day 14 锚定在"今天",但脚本运行时刻是白天,而 day 14 的最后一条
    消息在 14:30 —— 用真实 wall clock 判定时,`last_activity_ts` 落在同一天,
    idle 恰好为 0,看起来正常;但在**预热回放**里(只灌到第 N 天),
    数据止于第 N 天而 wall clock 仍是今天,于是算出 idle = 14-N 天,
    凭空造出"用户已疏离"的假信号,把 C 通道点亮。
    这是脚本的伪影,不是 guard 的行为,必须消除。
    """
    anchor = _day_anchor(len(TRACE), len(TRACE))
    return (anchor + timedelta(hours=23, minutes=59)).timestamp()


def seed(user: str, reset: bool = True, upto: int | None = None,
         quiet: bool = False) -> list:
    """把语料灌进日桶;返回逐日汇总,供打印与可视化复用。

    upto: 只灌 day_index <= upto 的天数。用于**预热回放**(warm-up replay)——
    这是唯一诚实的历史回放方式:若把 14 天全灌进去再逐日判定,
    基线计算会读到未来数据(前视偏差),导致第 1 天就报"持续恶化"。
    """
    ns = mticks.ns_ticks(user)

    if reset:
        removed = 0
        for it in list(mstore.list_items(ns)):
            mstore.delete_item(ns, it.key)
            removed += 1
        if removed and not quiet:
            print("[reset] 清空 user=%s 的 ticks:%d 个旧日桶" % (user, removed))

    total = len(TRACE)
    trace = [(i, t) for i, t in TRACE if upto is None or i <= upto]
    per_day = []

    for day_index, turns in trace:
        anchor = _day_anchor(day_index, total)
        day_key = anchor.strftime("%Y-%m-%d")
        risk_seen = []

        for hour, text in turns:
            ts = (anchor + timedelta(hours=hour, minutes=30)).timestamp()
            c = triage.classify(text)
            risk, _score = triage.risk_from(c)

            lex_val = neg_channel(text)
            meta = {
                "risk_level": risk,
                "emotion": c["emotion"],
                "intensity": c["intensity"],
                "negative_score": c["negative_score"],
                "lex_neg": lex_val,
            }
            risk_seen.append(risk)

            # 与 capture_memory 相同的落库路径
            mticks.bump_day_bucket(user, meta, ts=ts)

        bucket = mstore.get_item(ns, day_key) or {}
        n = int(bucket.get("neg_n", 0)) or 1
        per_day.append({
            "day": day_key,
            "count": int(bucket.get("count", 0)),
            "neg_avg": round(float(bucket.get("neg_sum", 0.0)) / n, 4),
            "lex_n": int(bucket.get("lex_neg_n", 0)),
            "lex_avg": (round(float(bucket.get("lex_neg_sum", 0.0))
                              / int(bucket["lex_neg_n"]), 4)
                        if bucket.get("lex_neg_n") else None),
            "night": int(bucket.get("night_count", 0)),
            "risk_max": int(bucket.get("risk_max_rank", 0)),
            "risk_max_name": max(risk_seen, key=lambda r: _RISK_RANK.get(r, 0)),
            "emotions": dict(bucket.get("emotions") or {}),
            "texts": [t for _, t in turns],
        })

    return per_day


def seed_memory(user: str, reset: bool = True, quiet: bool = False) -> dict:
    """写入合成的**长期记忆内容**:稳定事实 / 会话片段 / 危机留痕。

    全部走项目自己的写入 API,因此 memory_score、读侧配额入选、画像信号、
    维度账本都是**真实计算**出来的,不是伪造的展示值:
        merge_fact      —— 真实事实(含近义合并与 mentions 累加)
        put_item        —— 会话片段(结构与 store.commit_segment 一致)
        add_risk_event  —— 危机分面(受保护、永不遗忘)

    刻意保留"同一件事多种说法"的重复事实,以便在洞察界面里复现真实库
    「6 条配额被同一件事占掉多条」的问题(见 demo_memory_data.py 的说明)。
    """
    ns_f = mstore.ns_facts(user)
    ns_e = mstore.ns_episodes(user)

    if reset:
        removed = 0
        for ns in (ns_f, ns_e, mstore.ns_risk(user)):
            for it in list(mstore.list_items(ns)):
                mstore.delete_item(ns, it.key)
                removed += 1
        if removed and not quiet:
            print("[reset] 清空 user=%s 的 facts/episodes/risk:%d 条" % (user, removed))

    # ---- 事实(经 merge_fact:同文本幂等,近义按阈值合并)----
    for text, category, importance, days_ago in dmem.FACTS:
        mstore.merge_fact(user, text, category, float(importance))
    n_facts = len([1 for it in mstore.list_items(ns_f)
                   if not (it.value or {}).get("archived")])

    # ---- 会话片段 ----
    for idx, (digest, importance, days_ago, topics, dom, arc, dims) in enumerate(dmem.EPISODES):
        ts = dmem._ts(days_ago)
        key = "demo_seg_%02d" % idx
        mstore.put_item(ns_e, key, {
            "type": "episode",
            "digest": digest,
            "importance": float(importance),
            "risk_note": "",
            "category": "episode",
            "protected": False,
            "topics": list(topics),
            "dimension_evidence": dict(dims),
            "triggers": [],
            "coping": [],
            "dominant_emotion": dom,
            "valence_arc": arc,
            "emotion_shift": "neutral→%s" % dom,
            "arousal": "high" if arc == "falling" else "low",
            "turn_count": 4,
            "threads": ["demo-thread"],
            "ts_start": ts - 1800,
            "ts_end": ts,
            "first_seen": ts,
            "last_seen": ts,
            "archived": False,
        })
    n_eps = len([1 for it in mstore.list_items(ns_e)
                 if not (it.value or {}).get("archived")])

    # ---- 危机分面留痕(受保护,永不遗忘)----
    for facet, quote, days_ago in dmem.RISK_EVENTS:
        mstore.add_risk_event(user, facet, quote, ts=dmem._ts(days_ago))
    n_risk = len(mstore.list_risk_events(user))

    return {"facts": n_facts, "episodes": n_eps, "risk_events": n_risk}


def main() -> int:
    p = argparse.ArgumentParser(description="灌入 14 天仿真日桶与合成记忆")
    p.add_argument("--dir", default="demo_data", help="目标记忆目录(勿指向真实 memory_data)")
    p.add_argument("--user", default="demo_user")
    p.add_argument("--keep", action="store_true", help="不先清空该用户的 ticks")
    p.add_argument("--no-memory", action="store_true",
                   help="只灌 ticks 时序,不写事实/片段/危机留痕")
    args = p.parse_args()

    print("[env] LONG_MEMORY_DIR = %s" % cfg.MEMORY_DIR)
    print("[env] backend         = %s" % mstore.store_backend_name())
    print("[env] user            = %s" % args.user)
    print("[env] 语料天数        = %d" % len(TRACE))
    print()

    per_day = seed(args.user, reset=not args.keep)

    if not args.no_memory:
        stats = seed_memory(args.user, reset=True)
        print("[memory] 已写入合成长期记忆:事实 %d 条 · 片段 %d 条 · 危机留痕 %d 条"
              % (stats["facts"], stats["episodes"], stats["risk_events"]))
        print()

    print("%-11s %-5s %-7s %-7s %-6s %-8s %s" %
          ("日期", "轮次", "neg均值", "lex均值", "午夜", "风险", "情绪直方"))
    print("-" * 88)
    for d in per_day:
        print("%-11s %-5s %-7.2f %-7s %-6s %-8s %s" % (
            d["day"], d["count"], d["neg_avg"],
            ("%.2f" % d["lex_avg"]) if d["lex_avg"] is not None else "-",
            d["night"], d["risk_max_name"], d["emotions"]))

    return 0


if __name__ == "__main__":
    from gzhu_cup2.agent.common.console import force_utf8_stdout
    force_utf8_stdout()   # Windows GBK 控制台兜底
    raise SystemExit(main())
