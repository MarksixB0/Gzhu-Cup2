# -*- coding: utf-8 -*-
"""候选句打分预览:写语料前先看每句在真实通道下的实际得分。

用法:
    .venv\\Scripts\\python.exe src/gzhu_cup2/agent/tools/demo_score_probe.py
    .venv\\Scripts\\python.exe src/gzhu_cup2/agent/tools/demo_score_probe.py --file cand.txt

为什么要它:第一版语料是"凭感觉写"的,写完才发现 triage 单次命中只有 0.45、
lexicon 单次命中只有 0.30,远低于当时判定的参考线。
先量化、再写语料,避免在演示数据里"为了好看而调参"。
"""
import argparse
import os
import sys


from gzhu_cup2.agent.algo.components.lexicon import score_neg          # noqa: E402
from gzhu_cup2.agent.algo.components import triage                            # noqa: E402

# 默认:当前语料里 day 11-14 的句子 + 若干候选(更密集的真实求助语言)
DEFAULT = [
    # ---- 当前语料(day 11-14)----
    "又失眠了，睡不着，心里很难受",
    "今天很焦虑，什么都不想干",
    "压力好大，觉得自己挺没用的",
    "半夜醒了就睡不着了，心里很难受",
    "很累，没力气，不想说话",
    "最近总是失眠，很疲惫，提不起劲",
    "一直睡不着，很难受，很烦",
    "很孤独，不想见人，觉得没意义",
    "又失眠了，睡不着，好累，撑不住了",
    "很绝望，很无助，感觉撑不下去",
    # ---- 候选:更密集(真实求助语言的典型强度)----
    "头痛得厉害，睡不着，很焦虑，很烦，什么都不想干",
    "最近总是失眠，心里很难受，很压抑，很孤独，不想说话",
    "压力好大，觉得自己很没用，很自卑，做什么都没意义",
    "每天都睡不着，很累很疲惫，提不起劲，不想见人",
    "心里很难受，很绝望，很无助，感觉撑不住了，活着没意思",
    "很焦虑很烦，静不下来，睡不着，头痛，什么都不想干",
    "觉得自己很没用，很内疚，很自责，什么都没意义",
    "很孤独，很空虚，不想见人，也不想说话，每天都很难受",
    "失眠很久了，很累，很烦，心里堵得慌，不想跟人说话",
    "什么都提不起劲，很迷茫，很自卑，觉得自己是负担",
]


def main() -> int:
    p = argparse.ArgumentParser(description="预演候选句的真实通道得分")
    p.add_argument("--file", help="从文本文件读候选句(一行一句)")
    args = p.parse_args()

    lines = DEFAULT
    if args.file:
        with open(args.file, "r", encoding="utf-8") as f:
            lines = [ln.strip() for ln in f if ln.strip()]

    print("%-52s %-10s %-6s %-7s %-8s %s" %
          ("候选句", "emotion", "neg", "lex", "命中负词", "risk"))
    print("-" * 118)
    lex_all = []
    neg_all = []
    for s in lines:
        c = triage.classify(s)
        risk, _ = triage.risk_from(c)
        lex, neg_hits, pos_hits = score_neg(s)
        lex_all.append(lex)
        neg_all.append(c["negative_score"])
        disp = s if len(s) <= 50 else s[:48] + "…"
        print("%-52s %-10s %-6.2f %-7.2f %-8d %s" %
              (disp, c["emotion"], c["negative_score"], lex, len(neg_hits), risk))

    print()
    print("统计:lex  均值=%.3f  最小=%.2f  最大=%.2f"
          % (sum(lex_all) / len(lex_all), min(lex_all), max(lex_all)))
    print("      neg  均值=%.3f  最小=%.2f  最大=%.2f"
          % (sum(neg_all) / len(neg_all), min(neg_all), max(neg_all)))
    print("      达标 lex>=0.50 的句子:%d / %d"
          % (sum(1 for v in lex_all if v >= 0.50), len(lex_all)))
    return 0


if __name__ == "__main__":
    from gzhu_cup2.agent.common.console import force_utf8_stdout
    force_utf8_stdout()   # Windows GBK 控制台兜底
    raise SystemExit(main())
