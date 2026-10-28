# -*- coding: utf-8 -*-
"""一次性整理已落库的事实:纠正分类、合并日期冲突、合并详略两版。

为什么需要这个脚本
------------------
写入路径上已有守卫(见 store._safety_category / store._is_date_variant /
store._is_wording_extension / consolidate._existing_facts_brief),
但只对之后新固化的事实生效 —— **已经写进库的脏数据不会自愈**。
本脚本按同一套确定性判据回头整理一遍,可反复运行。

它做三件事
----------
1. 安全类纠正:文本命中自伤语汇但 category≠health / protected=False 的,改过来;
2. 日期冲突:同一件事被锚定成两个日期的,合并为一条。默认保留 **first_seen 最早**
   的那条(即用户第一次说出口的断言);若你已确认哪个日期才对,用
   ``--keep-date 2026-10-21`` 指定,脚本会以那条为准;
3. 详略两版:短的是长的严格前缀时合成一条,保留**信息更全**的那条。

只报告、不动作的部分:换了说法的同义改写(如「被组员署了自己名」/「被同学抢署名」)
字符串判据无法安全识别,脚本只在末尾列为"建议人工确认",不擅自合并 ——
误合并是不可逆的事实丢失,宁可留给抽取端以后自然收敛。

用法::

    # 先看(默认 dry-run,只读)
    .venv\\Scripts\\python.exe src/gzhu_cup2/agent/tools/dedupe_facts.py

    # 确认无误后写库(会先备份 sqlite 文件)
    .venv\\Scripts\\python.exe src/gzhu_cup2/agent/tools/dedupe_facts.py --apply

    # 已确认考试是 10-21,以它为基准
    ... dedupe_facts.py --apply --keep-date 2026-10-21

    # 指定用户 / 换数据目录(演示、测试)
    ... dedupe_facts.py --user u-demo
    LONG_MEMORY_DIR=/tmp/mem ... dedupe_facts.py
"""
import argparse
import os
import shutil
import sys
import time

# 默认后端(sqlite)在 main() 里声明,见 _ensure_backend —— 不在模块级改环境。

from gzhu_cup2.agent.config import config as cfg            # noqa: E402
from gzhu_cup2.agent.long_memory import similarity as msim  # noqa: E402
from gzhu_cup2.agent.long_memory import store as mstore     # noqa: E402


def _ensure_backend() -> None:
    """只在**脚本入口**声明默认后端,不在 import 时改环境。

    ⚠ 后端选择只能放在入口:``os.environ.setdefault(..., "sqlite")`` 若写在
    模块级,任何 import 它的测试都会把后端定成 sqlite 并**越过 conftest 的
    memory 隔离**,后续模块的 Store 单例与夹具对不上,整片用例在 teardown 报
    ERROR(单跑却全绿)。放到 main() 里就没有这个副作用。
    """
    os.environ.setdefault("LONG_MEMORY_BACKEND", "sqlite")

# 语义重复的"报告线":单字 Jaccard。只用于提示人工看一眼,绝不用于自动合并。
_REPORT_JACCARD = 0.35


# ============================== 读取 ==============================
def _load(user: str) -> list:
    """取未归档事实 → [(key, value)],按 (first_seen, key) 排序保证确定性。"""
    rows = []
    for it in mstore.list_items(mstore.ns_facts(user), limit=2000):
        v = dict(it.value or {})
        if v.get("archived"):
            continue
        if not str(v.get("text") or "").strip():
            continue
        rows.append((it.key, v))
    rows.sort(key=lambda r: (float(r[1].get("first_seen", 0) or 0), r[0]))
    return rows


# ============================== 规划 ==============================
def _plan_safety(rows: list) -> list:
    """安全类纠正计划: [(key, 旧 value, 新 value)]。"""
    plan = []
    for key, v in rows:
        cat, safety = mstore._safety_category(v.get("text"), v.get("category"))
        if not safety:
            continue
        if v.get("category") == "health" and v.get("protected") is True:
            continue
        nv = dict(v)
        nv["category"] = "health"
        nv["protected"] = True
        plan.append((key, v, nv))
    return plan


def _group(vals: list, predicate) -> list:
    """把互相满足 predicate 的行贪婪分组 → [[idx...]]。

    predicate(a, b) 必须近似对称(日期变体、前缀关系都满足),贪婪即可;
    一次分配保证每行只进一个组,避免链式传递把三件不同的事串成一串。
    """
    groups, used = [], set()
    for i in range(len(vals)):
        if i in used:
            continue
        grp = [i]
        used.add(i)
        for j in range(i + 1, len(vals)):
            if j in used:
                continue
            if predicate(vals[i], vals[j]):
                grp.append(j)
                used.add(j)
        if len(grp) > 1:
            groups.append(grp)
    return groups


def _merge_values(members: list, base_i: int) -> dict:
    """以 members[base_i] 为基准合并:计数相加、重要度取 max、时间窗取并集。"""
    base = dict(members[base_i])
    mentions = 0
    importance = 0.0
    first_seen = []
    last_seen = []
    for v in members:
        mentions += int(v.get("mentions", 1) or 0)
        importance = max(importance, float(v.get("importance", 0.0) or 0.0))
        if v.get("first_seen"):
            first_seen.append(float(v["first_seen"]))
        if v.get("last_seen"):
            last_seen.append(float(v["last_seen"]))
    base["mentions"] = mentions
    base["importance"] = importance
    base["first_seen"] = min(first_seen) if first_seen else base.get("first_seen")
    base["last_seen"] = max(last_seen) if last_seen else base.get("last_seen")
    base["archived"] = False
    base["archived_at"] = None
    return base


def _plan_date_variants(rows: list, keep_date: str | None) -> tuple:
    """日期冲突合并计划 → (计划列表, 提示信息列表)。

    计划项: ``{"keep_key", "values": [...], "drop": [...], "note": str}``
    """
    plan, notes = [], []
    groups = _group([v for _, v in rows],
                    lambda a, b: mstore._is_date_variant(str(a.get("text") or ""),
                                                       str(b.get("text") or "")))
    # _group 的下标是相对 rows 的,重新映射回 (key, value)
    for grp in groups:
        members = [rows[i] for i in grp]
        vals = [v for _, v in members]
        dates = [str(v.get("event_date") or "") for v in vals]
        base_i = 0                     # 默认:first_seen 最早(rows 已按此排序)
        note = "保留 first_seen 最早的一条"
        if keep_date:
            hit = [i for i, d in enumerate(dates) if d == keep_date]
            if hit:
                base_i = hit[0]
                note = f"按 --keep-date {keep_date} 保留"
            else:
                notes.append(
                    f"--keep-date {keep_date} 未出现在本组({dates}) → 仍按首次断言处理")
        plan.append({
            "keep_key": members[base_i][0],
            "values": vals,
            "base_i": base_i,
            "drop": [k for i, (k, _) in enumerate(members) if i != base_i],
            "drop_texts": [str(v.get("text") or "") for i, (_, v) in enumerate(members)
                           if i != base_i],
            "dates": dates,
            "note": note,
        })
    return plan, notes


def _plan_extensions(rows: list, taken: set) -> list:
    """详略两版合并计划(跳过已被日期冲突计划占用的行)。"""
    pool = [(k, v) for k, v in rows if k not in taken]
    plan = []
    for grp in _group([v for _, v in pool],
                      lambda a, b: mstore._is_wording_extension(str(a.get("text") or ""),
                                                             str(b.get("text") or ""))):
        members = [pool[i] for i in grp]
        vals = [v for _, v in members]
        # 基准 = 文本最长的那条(信息最全)
        base_i = max(range(len(vals)), key=lambda i: len(str(vals[i].get("text") or "")))
        plan.append({
            "keep_key": members[base_i][0],
            "values": vals,
            "base_i": base_i,
            "drop": [k for i, (k, _) in enumerate(members) if i != base_i],
            "drop_texts": [str(v.get("text") or "") for i, (_, v) in enumerate(members)
                           if i != base_i],
            "note": "保留最长(信息最全)的一条",
        })
    return plan


def _report_semantic_dups(rows: list, handled: set) -> list:
    """换了说法的疑似重复 → 只报告,不合并。"""
    out = []
    pool = [(k, v) for k, v in rows if k not in handled]
    for i in range(len(pool)):
        for j in range(i + 1, len(pool)):
            a = str(pool[i][1].get("text") or "")
            b = str(pool[j][1].get("text") or "")
            if msim.jaccard(a, b, 1) >= _REPORT_JACCARD:
                out.append((pool[i][0], a, pool[j][0], b))
    return out


# ============================== 输出与执行 ==============================
def _short(text: str, n: int = 34) -> str:
    t = str(text or "").replace("\n", " ")
    return t if len(t) <= n else t[: n - 1] + "…"


def _safety_note(old: dict) -> str:
    """描述这条安全类事实具体要改什么(可能只差 protected)。"""
    parts = []
    if old.get("category") != "health":
        parts.append(f"category: {old.get('category')} → health")
    if old.get("protected") is not True:
        parts.append("protected: False → True")
    return ";".join(parts) or "无需改动"


def _print_plan(title: str, plan: list) -> None:
    if not plan:
        print(f"[{title}] 无需处理")
        return
    print(f"[{title}] {len(plan)} 组")
    for i, p in enumerate(plan, 1):
        base = p["values"][p["base_i"]]
        dates = p.get("dates") or []
        print(f"  {i}. 保留 {p['keep_key']}  {_short(base.get('text'))}")
        if dates:
            print(f"     日期: {dates} → {base.get('event_date')}  ({p['note']})")
            if len(set(d for d in dates if d)) > 1:
                print("     注意:两个日期不一致,请确认哪个才对(可用 --keep-date 指定)")
        else:
            print(f"     {p['note']}")
        for k, t in zip(p["drop"], p.get("drop_texts") or [""] * len(p["drop"])):
            print(f"     - 合并并删除 {k}  {_short(t)}")


def _apply_plan(user: str, plan: list) -> int:
    """执行一组合并计划,返回改动行数。"""
    n = 0
    ns = mstore.ns_facts(user)
    for p in plan:
        merged = _merge_values(p["values"], p["base_i"])
        # 日期冲突组:日期以基准那条为准(不因 sort 顺序漂移)
        base = p["values"][p["base_i"]]
        merged["event_date"] = base.get("event_date")
        mstore.put_item(ns, p["keep_key"], merged)
        for k in p["drop"]:
            mstore.delete_item(ns, k)
            n += 1
    return n


def _backup() -> str | None:
    """写库前备份 sqlite 文件(旁挂 -wal/-shm 一并带)。

    只在**真正走 sqlite 后端**时备份 —— 内存后端(InMemoryStore,测试/演示常用)
    没有落盘文件,此时若照 cfg.LONG_MEMORY_DB_PATH 去拷,会备份一份和本次操作
    毫无关系的库,给人"已经备份好了"的错觉。
    """
    if "sqlite" not in mstore.store_backend_name().lower():
        return None
    path = str(getattr(cfg, "LONG_MEMORY_DB_PATH", "") or "")
    if not path or not os.path.exists(path):
        return None
    stamp = time.strftime("%Y%m%d-%H%M%S")
    dst = f"{path}.bak-{stamp}"
    shutil.copy2(path, dst)
    for ext in ("-wal", "-shm"):
        if os.path.exists(path + ext):
            shutil.copy2(path + ext, dst + ext)
    return dst


def main(argv: list | None = None) -> int:
    _ensure_backend()
    ap = argparse.ArgumentParser(description="整理已落库事实(分类/日期冲突/详略两版)")
    ap.add_argument("--user", default=cfg.MEMORY_USER_KEY, help="用户键(默认 config 的当前用户)")
    ap.add_argument("--keep-date", default=None,
                    help="日期冲突时以该 ISO 日期为准(如 2026-10-21);缺省保留首次断言")
    ap.add_argument("--apply", action="store_true", help="真正写库(缺省只 dry-run)")
    args = ap.parse_args(argv)

    rows = _load(args.user)
    print(f"用户 {args.user}:未归档事实 {len(rows)} 条 @ {mstore.store_backend_name()}")

    safety = _plan_safety(rows)
    dplan, dnotes = _plan_date_variants(rows, args.keep_date)
    taken = {k for p in dplan for k in [p["keep_key"], *p["drop"]]}
    eplan = _plan_extensions(rows, taken)
    taken |= {k for p in eplan for k in [p["keep_key"], *p["drop"]]}
    semantic = _report_semantic_dups(rows, taken)

    for note in dnotes:
        print(f"⚠ {note}")
    print()
    _print_plan("安全类分类纠正", [{"keep_key": k, "values": [nv], "base_i": 0,
                                    "drop": [], "note": _safety_note(v)}
                                   for k, v, nv in safety])
    _print_plan("日期冲突合并", dplan)
    _print_plan("详略两版合并", eplan)

    if semantic:
        print(f"\n[建议人工确认] {len(semantic)} 对疑似同义改写(不自动合并)")
        for ka, a, kb, b in semantic:
            print(f"  - {_short(a)}  |  {_short(b)}")

    changed = sum(len(p["drop"]) for p in dplan) + sum(len(p["drop"]) for p in eplan) + len(safety)
    if not changed:
        print("\n没有需要改动的内容。")
        return 0
    if not args.apply:
        print(f"\n[dry-run] 将要改动 {changed} 处;确认无误后加 --apply 真正写库。")
        return 0

    bak = _backup()
    print(f"\n已备份:{bak}" if bak else "\n⚠ 未找到 sqlite 文件,跳过备份(请自行确认后端)")
    for key, _old, nv in safety:
        mstore.put_item(mstore.ns_facts(args.user), key, nv)
    dropped = _apply_plan(args.user, dplan) + _apply_plan(args.user, eplan)
    print(f"完成:纠正 {len(safety)} 条分类,合并删除 {dropped} 条重复。")
    print("提示:重启桌宠后记忆界面才会重新读取。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
