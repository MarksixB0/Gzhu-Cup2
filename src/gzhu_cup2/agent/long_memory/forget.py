# -*- coding: utf-8 -*-
"""人脑式遗忘(纯函数,可离线单测)。

分层策略:
  1. 功能遗忘:检索/排序权重 = importance × (1+ln提及次数) × 新鲜度指数衰减;
  2. 定期归档:后台任务把「低于分阈值且超龄」的条目标记 archived(软删,可恢复);
  3. 物理清理:默认关闭(cfg.MEMORY_FORGET_PURGE_ENABLED=False),需显式开启;
  4. 受保护集:危机/照护类别永不自动归档或删除;
  5. 免衰减(pinned):importance ≥ 阈值的条目永不自动归档,与受保护集分开。
"""
import math
import time

from gzhu_cup2.agent.config import config as cfg
from gzhu_cup2.agent.long_memory import store as mstore


def _age_days(value: dict, now: float) -> float:
    last_seen = float(value.get("last_seen") or value.get("first_seen") or now)
    return max(0.0, (now - last_seen) / 86400.0)


def memory_score(value: dict | None, now: float | None = None) -> float:
    """记忆条目在检索/排序中的权重(数值越大越该被想起)。

    = importance × (1+ln 提及次数) × 新鲜度衰减 × 弱证据折扣

    弱证据折扣:``weak=True`` 的事实乘以 cfg.MEMORY_VOTE_WEAK_SCORE_FACTOR(默认 0.6),
    缺 ``weak`` 字段的旧数据不降权;因子置 1.0 即完全回退。
    """
    v = value or {}
    now = now if now is not None else time.time()
    importance = float(v.get("importance") or 0.0)
    mentions = max(1, int(v.get("mentions") or 1))
    base = importance * (1.0 + math.log1p(mentions))
    decay = math.exp(-_age_days(v, now) / cfg.MEMORY_FORGET_HALF_LIFE_DAYS)
    weak_factor = 1.0
    if v.get("weak"):
        weak_factor = float(getattr(cfg, "MEMORY_VOTE_WEAK_SCORE_FACTOR", 1.0) or 1.0)
    return base * decay * weak_factor


def is_protected(value: dict | None) -> bool:
    """受保护集:危机 / 照护类。

    被两个消费者共用:``forgetting_decision``(该不该忘)与
    ``retrieve.can_initiate``(能否主动提起)。新增"免于遗忘"的类别请用 ``is_pinned``。
    """
    v = value or {}
    return bool(v.get("protected")) or v.get("category") in cfg.MEMORY_PROTECTED_CATEGORIES


def is_pinned(value: dict | None) -> bool:
    """免衰减(pinned):永不自动归档,但不影响"可否被主动提起"。

    判据(任一成立):
      · 类别 ∈ cfg.MEMORY_FORGET_PINNED_CATEGORIES(默认 risk/care);
      · importance ≥ cfg.MEMORY_FORGET_PINNED_IMPORTANCE(默认 0.7)。
    """
    v = value or {}
    if v.get("category") in (getattr(cfg, "MEMORY_FORGET_PINNED_CATEGORIES", ()) or ()):
        return True
    try:
        return float(v.get("importance") or 0.0) >= float(
            getattr(cfg, "MEMORY_FORGET_PINNED_IMPORTANCE", 1.1))
    except (TypeError, ValueError):
        return False


def forgetting_decision(value: dict | None, now: float | None = None) -> str:
    """单条记忆的处置:keep(保留/维持归档) / archive(软归档) / purge(物理删除)。

    purge 仅在 cfg.MEMORY_FORGET_PURGE_ENABLED 为真且超过归档观察期时返回;
    受保护集与 pinned 条目恒为 keep。
    """
    v = dict(value or {})
    now = now if now is not None else time.time()
    if is_protected(v) or is_pinned(v):
        return "keep"
    if v.get("archived"):
        if not getattr(cfg, "MEMORY_FORGET_PURGE_ENABLED", False):
            return "keep"          # 物理删除默认关闭:归档件长期保留、可被重新提及解除
        arch_at = float(v.get("archived_at") or now)
        if (now - arch_at) / 86400.0 >= cfg.MEMORY_ARCHIVE_GRACE_DAYS:
            return "purge"
        return "keep"
    if _age_days(v, now) < cfg.MEMORY_FORGET_MIN_AGE_DAYS:
        return "keep"
    if memory_score(v, now) < cfg.MEMORY_IMPORTANCE_FLOOR:
        return "archive"
    return "keep"


def apply_user_forgetting(user: str, now: float | None = None) -> dict:
    """对该用户全部事实/摘要执行遗忘策略;返回统计。"""
    now = now if now is not None else time.time()
    stats = {"checked": 0, "archived": 0, "purged": 0, "kept": 0}
    for ns in (mstore.ns_facts(user), mstore.ns_episodes(user)):
        for item in mstore.list_items(ns):
            stats["checked"] += 1
            decision = forgetting_decision(item.value, now)
            if decision == "archive":
                val = dict(item.value)
                val["archived"] = True
                val["archived_at"] = now
                mstore.put_item(ns, item.key, val)
                stats["archived"] += 1
            elif decision == "purge":
                mstore.delete_item(ns, item.key)
                stats["purged"] += 1
            else:
                stats["kept"] += 1
    # 时序层:过期日桶清理(非保护桶超保留期即删;风险日桶长期保留)
    try:
        from gzhu_cup2.agent.long_memory import ticks as _ticks
        t = _ticks.prune_expired(user, now=now)
        stats["tick_checked"] = t.get("checked", 0)
        stats["tick_pruned"] = t.get("pruned", 0)
    except Exception:  # noqa: BLE001 - 清理非关键路径,失败不阻断其余遗忘
        pass
    return stats


def apply_forgetting_all(users=None, now: float | None = None) -> dict:
    """全用户遗忘维护(后台 worker 周期性调用)。"""
    now = now if now is not None else time.time()
    users = list(users) if users is not None else mstore.list_memory_users()
    total = {"checked": 0, "archived": 0, "purged": 0, "kept": 0}
    for u in users:
        part = apply_user_forgetting(u, now)
        for k in total:
            total[k] += part[k]
    return total
