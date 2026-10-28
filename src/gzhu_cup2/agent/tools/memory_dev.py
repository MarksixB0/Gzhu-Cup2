# -*- coding: utf-8 -*-
"""记忆调试 CLI:在一个目录里查看 / 增删 / 导入导出长期记忆(开发期专用)。

为什么需要它:
  - 记忆数据统一落在 cfg.MEMORY_DIR(默认 src/gzhu_cup2/agent/memory_data/),本工具是它的
    人机接口:不必写 Python 就能造/删记忆,便于复现"记忆过多 / 引用过重"等问题;
  - 危险操作(clear/seed)默认只影响指定 user 命名空间,不会碰桌宠角色卡等代码资产。

用法(在仓库根目录执行,后端默认就是落盘 sqlite):
    python src/gzhu_cup2/agent/tools/memory_dev.py dir
    python src/gzhu_cup2/agent/tools/memory_dev.py users
    python src/gzhu_cup2/agent/tools/memory_dev.py list    --user local_user
    python src/gzhu_cup2/agent/tools/memory_dev.py list    --user local_user --ns facts
    python src/gzhu_cup2/agent/tools/memory_dev.py add-fact --user local_user --text "用户养了一只猫叫年糕" \
                                                 --category life --importance 0.8
    python src/gzhu_cup2/agent/tools/memory_dev.py add-episode --user local_user --digest "聊了答辩压力" --topic school
    python src/gzhu_cup2/agent/tools/memory_dev.py add-risk --user local_user --facet ideation --quote "不想活了"
    python src/gzhu_cup2/agent/tools/memory_dev.py del     --user local_user --ns facts --text "用户养了一只猫叫年糕"
    python src/gzhu_cup2/agent/tools/memory_dev.py clear   --user local_user
    python src/gzhu_cup2/agent/tools/memory_dev.py seed    --file seed.json
    python src/gzhu_cup2/agent/tools/memory_dev.py dump    --user local_user --out dump.json
    python src/gzhu_cup2/agent/tools/memory_dev.py context --user local_user          # 看 LLM 实际会读到什么

环境变量:
    LONG_MEMORY_DIR      重定向记忆目录(换一份数据集,不动默认数据)
    LONG_MEMORY_BACKEND  sqlite(默认) / memory(不落盘,仅内存)
"""
import argparse
import hashlib
import json
import os
import sys
import time

# 让 src/gzhu_cup2/agent 可像顶层包一样被导入(与 server/main 相同假定)

from gzhu_cup2.agent.config import config as cfg                    # noqa: E402
from gzhu_cup2.agent.long_memory import paths as mpaths             # noqa: E402
from gzhu_cup2.agent.long_memory import store as mstore             # noqa: E402
from gzhu_cup2.agent.long_memory import ticks as mticks             # noqa: E402


# ============================== 命名空间 ==============================
def ns_of(user: str, name: str):
    """把 CLI 的短名映射到 Store 命名空间元组。"""
    return {
        "facts": mstore.ns_facts,
        "episodes": mstore.ns_episodes,
        "risk": mstore.ns_risk,
        "ticks": mticks.ns_ticks,
        "pending": mstore.ns_pending,
    }[name](user)


NS_NAMES = ("facts", "episodes", "ticks", "risk", "pending")


def _episode_key(user: str, digest: str) -> str:
    """CLI 造 episode 用的确定性键:同 user + 同 digest 重复 seed 不会重复落库。"""
    raw = "%s|%s" % (user, digest)
    return "seed_" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]


def _summarize(ns_name: str, key: str, v: dict) -> str:
    """把一条记忆压成单行摘要(便于肉眼扫)。"""
    v = v or {}
    if ns_name == "facts":
        return "imp=%-4s mentions=%-2s [%s] %s" % (
            v.get("importance"), v.get("mentions"), v.get("category"), v.get("text"))
    if ns_name == "episodes":
        return "imp=%-4s prot=%-5s %s" % (
            v.get("importance"), v.get("protected"), str(v.get("digest"))[:70])
    if ns_name == "ticks":
        return "count=%s neg=%.2f risk=%s emo=%s" % (
            v.get("count"), (v.get("neg_sum", 0) / v["neg_n"]) if v.get("neg_n") else 0.0,
            v.get("risk_max_rank"), v.get("emotions"))
    if ns_name == "risk":
        return "[%s] %s" % (v.get("facet"), str(v.get("quote"))[:70])
    if ns_name == "pending":
        return "turns=%d threads=%s" % (len(v.get("turns") or []), v.get("threads"))
    return json.dumps(v, ensure_ascii=False)[:90]


def _iter_ns(user: str, ns_name: str):
    ns = ns_of(user, ns_name)
    if ns_name == "pending":
        v = mstore.get_item(ns, "segment")
        return [("segment", v)] if v else []
    return [(it.key, it.value) for it in mstore.list_items(ns)]


# ============================== 命令实现 ==============================
def cmd_dir(args) -> int:
    info = mpaths.describe()
    print("记忆目录 :", info["dir"])
    print("存储后端 :", mstore.store_backend_name())
    print("后端来源 : LONG_MEMORY_BACKEND=%s" % os.getenv("LONG_MEMORY_BACKEND", "sqlite"))
    print("文件:")
    for name, size in info["files"].items():
        print("  %-20s %s" % (name, ("%d bytes" % size) if size is not None else "(不存在)"))
    print("\n提示:删除整个目录 = 记忆清零;设 LONG_MEMORY_DIR 可切换数据集。")
    return 0


def cmd_users(args) -> int:
    users = mstore.list_memory_users()
    if not users:
        print("(没有任何用户记忆)")
        return 0
    for u in users:
        print(u)
    return 0


def cmd_list(args) -> int:
    names = NS_NAMES if args.ns == "all" else (args.ns,)
    total = 0
    for name in names:
        rows = _iter_ns(args.user, name)
        print("== %s (%d 条) ==" % (name, len(rows)))
        for key, v in rows:
            print("  %-16s %s" % (key, _summarize(name, key, v)))
        total += len(rows)
    print("\n合计 %d 条" % total)
    return 0


def cmd_add_fact(args) -> int:
    mstore.merge_fact(args.user, args.text, args.category, args.importance)
    print("已写入 fact: key=%s" % mstore._canonical(args.text))
    print("（同文本重复写入只会 mentions+1,不会新增条目）")
    return 0


def cmd_add_episode(args) -> int:
    key = _episode_key(args.user, args.digest)
    mstore.put_item(mstore.ns_episodes(args.user), key, {
        "type": "episode", "digest": args.digest, "importance": args.importance,
        "risk_note": args.risk_note or "",
        "category": "risk" if args.risk_note else "episode",
        "protected": bool(args.risk_note),
        "ts_start": args.ts, "ts_end": args.ts, "turn_count": 0, "threads": ["dev-cli"],
        "first_seen": args.ts, "last_seen": args.ts, "archived": False,
        "topics": [t for t in (args.topic or []) if t],
        "dimension_evidence": {}, "triggers": [], "coping": [],
        "dominant_emotion": "neutral", "valence_arc": "stable",
        "emotion_shift": "neutral→neutral", "arousal": "low",
    })
    print("已写入 episode: key=%s" % key)
    return 0


def cmd_add_risk(args) -> int:
    mstore.add_risk_event(args.user, args.facet, args.quote, ts=args.ts)
    print("已写入 risk_event(facet=%s,受保护、永不遗忘)" % args.facet)
    return 0


def cmd_del(args) -> int:
    ns = ns_of(args.user, args.ns)
    removed = 0
    if args.key:
        mstore.delete_item(ns, args.key)
        removed += 1
    elif args.text and args.ns == "facts":
        key = mstore._canonical(args.text)
        if mstore.get_item(ns, key) is not None:
            mstore.delete_item(ns, key)
            removed += 1
    else:
        print("需要 --key,或在 --ns facts 时用 --text", file=sys.stderr)
        return 2
    print("已删除 %d 条" % removed)
    return 0


def cmd_clear(args) -> int:
    names = NS_NAMES if args.ns == "all" else (args.ns,)
    removed = 0
    for name in names:
        ns = ns_of(args.user, name)
        if name == "pending":
            if mstore.get_item(ns, "segment") is not None:
                mstore.delete_item(ns, "segment")
                removed += 1
            continue
        for it in list(mstore.list_items(ns)):
            mstore.delete_item(ns, it.key)
            removed += 1
    print("已清空 user=%s 的 %s,共删除 %d 条" % (args.user, ",".join(names), removed))
    return 0


def cmd_seed(args) -> int:
    """从 JSON 批量造记忆(幂等:facts 走合并键、episodes 走 digest 键)。"""
    with open(args.file, "r", encoding="utf-8") as f:
        data = json.load(f)
    user = args.user or data.get("user") or cfg.MEMORY_USER_KEY
    n = {"facts": 0, "episodes": 0, "risk": 0, "ticks": 0}
    for f_ in data.get("facts") or []:
        mstore.merge_fact(user, f_["text"], f_.get("category", "other"),
                          float(f_.get("importance", 0.5)))
        n["facts"] += 1
    for e in data.get("episodes") or []:
        args_e = argparse.Namespace(
            user=user, digest=e.get("digest", ""), importance=float(e.get("importance", 0.6)),
            risk_note=e.get("risk_note", ""), topic=e.get("topics") or [],
            ts=float(e.get("ts", time.time())),
        )
        cmd_add_episode(args_e)
        n["episodes"] += 1
    for r in data.get("risk") or []:
        mstore.add_risk_event(user, r.get("facet", ""), r.get("quote", ""))
        n["risk"] += 1
    for t in data.get("ticks") or []:
        mstore.put_item(mticks.ns_ticks(user), t["day"], {
            "day": t["day"], "count": int(t.get("count", 0)),
            "neg_sum": float(t.get("neg_sum", 0.0)), "neg_n": int(t.get("neg_n", 0)),
            "lex_neg_sum": float(t.get("lex_neg_sum", 0.0)),
            "lex_neg_n": int(t.get("lex_neg_n", 0)),
            "emotions": dict(t.get("emotions") or {}),
            "risk_max_rank": int(t.get("risk_max_rank", 0)),
            "last_ts": float(t.get("last_ts", time.time())),
        })
        n["ticks"] += 1
    print("user=%s 已写入: %s" % (user, n))
    return 0


def cmd_dump(args) -> int:
    out = {"user": args.user}
    for name in NS_NAMES:
        rows = _iter_ns(args.user, name)
        out[name] = [{**dict(v or {}), "_key": k} for k, v in rows]
    text = json.dumps(out, ensure_ascii=False, indent=2)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(text)
        print("已导出到 %s" % args.out)
    else:
        print(text)
    return 0


def cmd_context(args) -> int:
    """打印读侧真实会注入 LLM 的记忆块 —— 判断"记忆比重"最直接的方式。"""
    from gzhu_cup2.agent.long_memory import retrieve
    ctx = retrieve.build_memory_context(args.user, purpose=args.purpose)
    print("---- purpose=%s ----" % args.purpose)
    print(ctx if ctx else "(无记忆)")
    print("\n[统计] 字符数=%d,行数=%d" % (len(ctx or ""), len((ctx or "").splitlines())))
    if args.purpose == "reply":
        from gzhu_cup2.agent.config import config as _cfg
        n = len(ctx or "")
        print("[配额] facts<=%d episodes<=%d (cfg.MEMORY_RETRIEVE_MAX_*)"
              % (_cfg.MEMORY_RETRIEVE_MAX_FACTS, _cfg.MEMORY_RETRIEVE_MAX_EPISODES))
        print("[占比] 记忆块 %d 字符 / 目标回复 50-150 字符 ≈ %.1f 倍"
              % (n, n / 150.0))
    else:
        print("[配额] purpose=%s 为排障视图,不做业务配额裁剪" % args.purpose)
    return 0


def cmd_remerge(args) -> int:
    """对**已有**事实重跑近义合并(默认只预演;加 --apply 才真正改库)。

    为什么需要:近义合并只在"新事实落库"时生效,历史数据里的重复项不会被自动清理,
    而它们照样占读侧配额。
    幸存者按 memory_score 取最高者;合并后 mentions 求和、importance 取 max、
    text 取最长、first_seen 取最早、last_seen 取最晚。
    """
    from gzhu_cup2.agent.long_memory import forget
    now = time.time()
    ns = mstore.ns_facts(args.user)
    items = [(it.key, dict(it.value or {})) for it in mstore.list_items(ns)]
    items = [(k, v) for k, v in items if not v.get("archived")]
    kw = mstore._similarity_kwargs()

    ordered = sorted(items, key=lambda kv: forget.memory_score(kv[1], now), reverse=True)
    survivors: list = []          # [[key, value], ...]
    groups: dict = {}             # 幸存者 key -> [(key, value), ...]
    for key, val in ordered:
        text = str(val.get("text") or "")
        for s in survivors:
            stext = str(s[1].get("text") or "")
            if not mstore._merge_compatible(s[1], val.get("category") or "other"):
                continue
            if mstore._similarity.polarity_conflict(stext, text):
                continue
            if mstore._similarity.is_similar(stext, text, **kw):
                groups.setdefault(s[0], []).append((key, val))
                break
        else:
            survivors.append([key, val])

    if not groups:
        print("没有需要合并的近义事实(共 %d 条)" % len(items))
        return 0

    print("发现 %d 组近义事实:" % len(groups))
    for skey, members in groups.items():
        sval = next(v for k, v in survivors if k == skey)
        print("\n  保留: %s" % sval.get("text"))
        for k, v in members:
            print("    并入: %s" % v.get("text"))

    if not args.apply:
        print("\n(预演模式;加 --apply 才会真正合并)")
        return 0

    for skey, members in groups.items():
        sval = dict(next(v for k, v in survivors if k == skey))
        texts = [str(sval.get("text") or "")] + [str(v.get("text") or "") for _, v in members]
        sval["text"] = max(texts, key=len)
        sval["mentions"] = int(sval.get("mentions", 1)) + sum(
            int(v.get("mentions", 1)) for _, v in members)
        sval["importance"] = max(
            [float(sval.get("importance") or 0.0)]
            + [float(v.get("importance") or 0.0) for _, v in members])
        sval["first_seen"] = min(
            [float(sval.get("first_seen") or now)]
            + [float(v.get("first_seen") or now) for _, v in members])
        sval["last_seen"] = max(
            [float(sval.get("last_seen") or now)]
            + [float(v.get("last_seen") or now) for _, v in members])
        mstore.put_item(ns, skey, sval)
        for k, _ in members:
            mstore.delete_item(ns, k)

    after = len([1 for it in mstore.list_items(ns) if not (it.value or {}).get("archived")])
    print("\n已合并:%d 条 → %d 条" % (len(items), after))
    return 0


# ============================== 入口 ==============================
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="memory_dev", description="长期记忆调试工具")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("dir", help="显示记忆目录 / 后端 / 文件状态").set_defaults(fn=cmd_dir)
    sub.add_parser("users", help="列出有记忆的用户").set_defaults(fn=cmd_users)

    for name, help_text, fn in (
        ("list", "列出某用户的记忆", cmd_list),
    ):
        sp = sub.add_parser(name, help=help_text)
        sp.add_argument("--user", default=cfg.MEMORY_USER_KEY)
        sp.add_argument("--ns", default="all", choices=NS_NAMES + ("all",))
        sp.set_defaults(fn=fn)

    sp = sub.add_parser("add-fact", help="新增/合并一条稳定事实")
    sp.add_argument("--user", default=cfg.MEMORY_USER_KEY)
    sp.add_argument("--text", required=True)
    sp.add_argument("--category", default="other")
    sp.add_argument("--importance", type=float, default=0.6)
    sp.set_defaults(fn=cmd_add_fact)

    sp = sub.add_parser("add-episode", help="新增一条会话摘要")
    sp.add_argument("--user", default=cfg.MEMORY_USER_KEY)
    sp.add_argument("--digest", required=True)
    sp.add_argument("--importance", type=float, default=0.6)
    sp.add_argument("--risk-note", dest="risk_note", default="")
    sp.add_argument("--topic", action="append")
    sp.add_argument("--ts", type=float, default=time.time())
    sp.set_defaults(fn=cmd_add_episode)

    sp = sub.add_parser("add-risk", help="新增一条危机分面证据(受保护)")
    sp.add_argument("--user", default=cfg.MEMORY_USER_KEY)
    sp.add_argument("--facet", required=True,
                    choices=["ideation", "intent", "plan", "behavior", "signal"])
    sp.add_argument("--quote", required=True)
    sp.add_argument("--ts", type=float, default=None)
    sp.set_defaults(fn=cmd_add_risk)

    sp = sub.add_parser("del", help="删除一条记忆")
    sp.add_argument("--user", default=cfg.MEMORY_USER_KEY)
    sp.add_argument("--ns", default="facts", choices=NS_NAMES)
    sp.add_argument("--key")
    sp.add_argument("--text", help="仅 --ns facts 可用:按事实原文定位")
    sp.set_defaults(fn=cmd_del)

    sp = sub.add_parser("clear", help="清空某用户的记忆")
    sp.add_argument("--user", default=cfg.MEMORY_USER_KEY)
    sp.add_argument("--ns", default="all", choices=NS_NAMES + ("all",))
    sp.set_defaults(fn=cmd_clear)

    sp = sub.add_parser("seed", help="从 JSON 批量造记忆")
    sp.add_argument("--file", required=True)
    sp.add_argument("--user")
    sp.set_defaults(fn=cmd_seed)

    sp = sub.add_parser("dump", help="导出某用户全部记忆为 JSON")
    sp.add_argument("--user", default=cfg.MEMORY_USER_KEY)
    sp.add_argument("--out")
    sp.set_defaults(fn=cmd_dump)

    sp = sub.add_parser("remerge", help="对已有事实重跑近义合并(默认只预演)")
    sp.add_argument("--user", default=cfg.MEMORY_USER_KEY)
    sp.add_argument("--apply", action="store_true", help="真正写库(缺省只预演)")
    sp.set_defaults(fn=cmd_remerge)

    sp = sub.add_parser("context", help="打印读侧将注入 LLM 的记忆块")
    sp.add_argument("--user", default=cfg.MEMORY_USER_KEY)
    sp.add_argument("--purpose", default="reply", choices=["reply", "care", "debug"])
    sp.set_defaults(fn=cmd_context)
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    from gzhu_cup2.agent.common.console import force_utf8_stdout
    force_utf8_stdout()   # Windows GBK 控制台兜底
    raise SystemExit(main())
