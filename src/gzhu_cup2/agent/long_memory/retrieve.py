# -*- coding: utf-8 -*-
"""读取侧:把用户的长期记忆整理成给生成节点的纯文本上下文(分区、明确来源)。

结构(自前往后,重要性递减、越靠后越"丢了也安全"):
  【用户档案】稳定事实(长驻)
  【近期状态】近 N 天情绪起伏摘要(来自时序层日桶,自然语言,不含内部数字)
  【过往对话回顾】已固化会话摘要
  【最近对话(尚未整理)】其它会话未固化的兜底

同时提供"可主动提及"白名单派生函数(can_initiate / list_initiable),
供未来的主动关怀使用:宁可少说不可说错 —— 只有低压类别、近期出现、
非受保护、重要度达标的稳定事实才有资格被系统主动提起。
"""
import logging
import time
from datetime import date, datetime

from gzhu_cup2.agent.config import config as cfg
from gzhu_cup2.agent.long_memory import store as mstore
from gzhu_cup2.agent.long_memory import dates as _dates
from gzhu_cup2.agent.long_memory import forget
from gzhu_cup2.agent.long_memory import intent as intent_mod
from gzhu_cup2.agent.long_memory import lanes
from gzhu_cup2.agent.long_memory import profile as mprofile
from gzhu_cup2.agent.long_memory import relevance
from gzhu_cup2.agent.long_memory import similarity as _similarity

logger = logging.getLogger(__name__)


def _fts_candidates(user: str, query: str | None,
                    q_intent: dict | None = None) -> tuple[set[tuple[str, str]], set[str]]:
    """读取 FTS 候选键/文本；索引不可用或没有命中时返回空集，保留旧逻辑兜底。

    ``q_intent`` 的主题会展开成 ``TOPIC_TERMS`` 词表一起检索:问"考研"时
    "答辩/论文/老师"这些同主题事实也能进候选,恢复旧的间接召回通道。
    """
    if not query:
        return set(), set()
    try:
        from gzhu_cup2.agent.long_memory import fts
        terms: list[str] = []
        for topic in (q_intent or {}).get("topics") or []:
            terms.extend(intent_mod.TOPIC_TERMS.get(str(topic)) or ())
        hits = fts.search(user, query,
                          limit=max(48, int(cfg.MEMORY_RETRIEVE_MAX_FACTS) * 2),
                          extra_terms=list(dict.fromkeys(terms)))
        keys = {(str(h.get("kind") or ""), str(h.get("key") or "")) for h in hits
                if h.get("kind") and h.get("key")}
        texts = {mstore.norm_text(h.get("text") or "") for h in hits if h.get("text")}
        return keys, texts
    except Exception:
        return set(), set()


def _fmt(ts: float) -> str:
    try:
        return datetime.fromtimestamp(float(ts)).strftime("%m-%d")
    except (ValueError, OSError, TypeError):
        return "?"


def _relative_day_hint(d: date, today: date) -> str:
    """把日期换算成相对「今天」的自然语言提示(今天/明天/后天/N 天后/已过去)。"""
    delta = (d - today).days
    if delta == 0:
        return "今天"
    if delta == 1:
        return "明天"
    if delta == 2:
        return "后天"
    if delta == -1:
        return "昨天"
    if delta > 2:
        return f"{delta} 天后"
    return "已过去"


def _day_hint_suffix(ts, today: date) -> str:
    """时间戳 → 「，今天 / ，2 天前」后缀(换算失败返回空串,不影响主渲染)。"""
    try:
        return "，" + _relative_day_hint(datetime.fromtimestamp(float(ts)).date(), today)
    except (ValueError, OSError, TypeError):
        return ""


def _snippet(text: str, limit: int = 80) -> str:
    t = " ".join(str(text or "").split())
    return t[:limit] + ("…" if len(t) > limit else "")


# ============================== 可主动提及白名单 ==============================
def can_initiate(value: dict | None, now: float | None = None) -> bool:
    """该条稳定事实是否"可被系统主动提起"。

    规则(全部满足才 True,且全部为确定性规则,不依赖额外 LLM 判断):
      - 是事实类、未归档;
      - 非受保护(risk/care 永不);
      - 类别 ∈ MEMORY_INITIATE_CATEGORIES(默认仅 preference/life 低压话题);
      - 距最近一次提及不超过 MEMORY_INITIATE_MAX_AGE_DAYS 天(近况才可提);
      - importance ≥ MEMORY_INITIATE_MIN_IMPORTANCE。
    """
    v = dict(value or {})
    now = now if now is not None else time.time()
    if v.get("type") not in (None, "fact"):
        return False
    if v.get("archived") or forget.is_protected(v):
        return False
    if str(v.get("category") or "other") not in cfg.MEMORY_INITIATE_CATEGORIES:
        return False
    last_seen = float(v.get("last_seen") or v.get("first_seen") or 0.0)
    if last_seen <= 0 or (now - last_seen) > float(cfg.MEMORY_INITIATE_MAX_AGE_DAYS) * 86400.0:
        return False
    if float(v.get("importance") or 0.0) < float(cfg.MEMORY_INITIATE_MIN_IMPORTANCE):
        return False
    return True


def list_initiable(user: str, limit: int = 8, now: float | None = None) -> list:
    """按记忆分排序返回该用户可主动提及的事实文本(未来主动关怀素材)。"""
    if not user:
        return []
    now = now if now is not None else time.time()
    out = []
    for it in mstore.list_items(mstore.ns_facts(user)):
        v = it.value or {}
        if not can_initiate(v, now):
            continue
        out.append((forget.memory_score(v, now), str(v.get("text") or "")))
    out.sort(key=lambda x: x[0], reverse=True)
    return [text for _, text in out[: max(0, int(limit))]]


# ============================== 对话上下文 ==============================
# ---- 行渲染:选取(算字符)与输出(拼文本)共用这三个函数 ----
def _absolutize_legacy(text: str, v: dict) -> str:
    """老数据没有 ``event_date``(相对时间绝对化上线前落的事实)→ **读侧**兜底换算一次。

    锚点取 ``first_seen``(这条事实第一次被说出口的时刻)——相对时间词是**首次**
    陈述时的视角,``last_seen`` 会被每次复述刷新,拿它当锚点会让「下周三」被
    反复重锚到越来越远的日期。缺失时回落 ``last_seen``。
    文本里没有相对时间词时是 no-op,故对新数据与无关文本完全无副作用。
    作用:存量库里裸着「明天/下周三」的老事实,不会在几天后被读成"现在说的明天"。
    """
    raw = str(text or "")
    if not raw:
        return raw
    ts = v.get("first_seen") or v.get("last_seen")
    if not ts:
        return raw
    return _dates.absolutize(raw, _anchor_dt({"ts_start": ts}, ts))


def _event_hint(event_date: str, text: str, today: date) -> str:
    """事件相对「今天」的提示;整周/整月表达落在区间内时提示「本周内/本月内」。

    区间首日是 event_date,但「下个月要搬家」这类事实整段都还没过完,按首日
    判会提前说成「已过去」——那会让桌宠把还在进行的事当成过去的事。
    """
    start_iso, end_iso, unit = _dates.event_span(event_date, text)
    try:
        start = datetime.strptime(start_iso, "%Y-%m-%d").date()
        end = datetime.strptime(end_iso, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return ""
    if unit != "day" and start <= today <= end:
        return "本周内" if unit == "week" else "本月内"
    return _relative_day_hint(start, today)


def _fact_line(v: dict, today: date, show_category: bool) -> str:
    cat = str(v.get("category") or "other")
    event_date = v.get("event_date")
    text = str(v.get("text") or "")
    if not event_date:
        # 老数据没有 event_date → 读侧兜底绝对化(no-op 于无相对词的文本)
        text = _absolutize_legacy(text, v)
    line = f"- {text}"
    # 事件日期:事实带 event_date 时渲染,并追加"相对今天"的提示。
    # 老数据没有该键 → 渲染与之前一致。
    if event_date:
        line += f"（事件日期：{event_date}）"
        hint = _event_hint(str(event_date), text, today)
        if hint:
            line += f"（{hint}）"
    if show_category and cat != "other":
        line += f"[{cat}]"
    return line


def _anchor_dt(v: dict, fallback_ts: float) -> datetime:
    """摘要读侧绝对化的锚点:优先段首 ``ts_start``(与写侧 ``dates.anchor_datetime`` 同口径)。

    相对时间词是【用户说那句话时】的视角,段首时刻最接近它;缺失/非法时逐级回落。
    """
    for raw in (v.get("ts_start"), fallback_ts):
        try:
            return datetime.fromtimestamp(float(raw))
        except (TypeError, ValueError, OSError, OverflowError):
            continue
    return datetime.now()


def _episode_line(v: dict, today: date, now: float) -> str:
    mark = "（涉及风险，请谨慎处理）" if (v.get("protected") or v.get("category") == "risk") else ""
    ts = v.get("ts_end") or v.get("last_seen") or now
    # 摘要里的相对时间词在读侧再绝对化一次(锚点取段首 ts_start,幂等)。
    digest = _dates.absolutize(str(v.get("digest") or ""), _anchor_dt(v, ts))
    return f"- （{_fmt(ts)}{_day_hint_suffix(ts, today)}）{digest}{mark}"


def _archived_line(v: dict, today: date, now: float) -> str:
    ts = v.get("last_seen") or v.get("first_seen") or now
    # 归档事实多为绝对化上线前的老数据 → 同样读侧兜底一次,免得裸相对词被读成现在
    return f"- （{_fmt(ts)}）{_absolutize_legacy(v.get('text'), v)}"


def _item_text(v) -> str:
    """一条记忆的**可比对文本**:事实用 text,会话摘要用 digest。"""
    v = v or {}
    return str(v.get("text") or v.get("digest") or "")


def select_context(user: str, purpose: str = "reply",
                   now: float | None = None, query: str | None = None) -> dict:
    """按字符预算在「事实 / 会话回顾 / 冷记忆」之间分配名额 —— 全仓唯一实现。

    排序键:`(命中档, base×(1+w×rel), base)`(前三者降序),
    `base = forget.memory_score`(弱证据折扣已内含)。

    query:用户本轮输入,用于按问题取用。为空 ⇒ 相关性恒为 0 ⇒ 排序与静态快照一致。

    返回 ``{"facts","episodes","archived","fact_texts","chars","budget"}`` 外加排障字段:
      ``query`` / ``intent`` / ``lanes``(各道条数与入选数)/ ``explain``(逐条 base、rel、
      命中档、是否入选、落选原因);``fact_texts`` 是选中事实的规范化文本集合;
      ``fact_keys`` / ``episode_keys`` 是选中条目的 store 键(检索留痕用);
      ``fts`` 是 FTS 初筛观测 ``{engaged, applied, n_hits, narrowed, fallback, candidates}``:
      engaged=FTS 是否命中, applied=是否真的进入初筛(小库不筛时为 False),
      narrowed=是否真的减少了候选, fallback=是否因命中集为空退回了全集,
      candidates=各命名空间初筛前/后条数(raw/eligible/after_fts/kept)。
    """
    now = now if now is not None else time.time()
    today = datetime.fromtimestamp(now).date()
    q = str(query or "").strip() or None
    q_intent = intent_mod.detect(q) if q else {}
    fts_keys, fts_texts = _fts_candidates(user, q, q_intent)
    rel_min = float(getattr(cfg, "MEMORY_RETRIEVE_RELEVANCE_MIN", 0.0) or 0.0)
    rel_step = float(getattr(cfg, "MEMORY_RETRIEVE_RELEVANCE_STEP", 0.25) or 0.25)
    read_dedup = bool(getattr(cfg, "MEMORY_FACT_READ_DEDUP", True))
    # 候选不超过该值时完全不筛:小库直接走旧排序,FTS 只在规模上来后才介入。
    fts_min = int(getattr(cfg, "MEMORY_FTS_MIN_CANDIDATES", 50) or 0)

    if purpose == "debug":
        # 排障视图:不设业务预算(安全阀只为防爆),由调用方保留 [类别] 等内部标记
        budget = None
        reserve = 0
        cap_f = int(cfg.MEMORY_RETRIEVE_MAX_FACTS) * 5
        cap_e = int(cfg.MEMORY_RETRIEVE_MAX_EPISODES) * 5
        archived_max = 0
    elif purpose == "care":
        budget = float(cfg.MEMORY_RETRIEVE_CARE_MAX_CHARS)
        reserve = min(budget / 3.0, float(cfg.MEMORY_RETRIEVE_EPISODE_RESERVE_CHARS))
        cap_f, cap_e = int(cfg.MEMORY_RETRIEVE_MAX_FACTS), 2
        archived_max = 0
    else:  # reply
        budget = float(cfg.MEMORY_RETRIEVE_MAX_CHARS)
        reserve = float(cfg.MEMORY_RETRIEVE_EPISODE_RESERVE_CHARS)
        cap_f = int(cfg.MEMORY_RETRIEVE_MAX_FACTS)
        cap_e = int(cfg.MEMORY_RETRIEVE_MAX_EPISODES)
        archived_max = int(getattr(cfg, "MEMORY_RETRIEVE_ARCHIVED_MAX", 0) or 0)

    # 分道配额与读侧去冗只对 **reply** 生效:care 是"情绪道"(主动关怀正需要状态内容),
    # debug 是排障视图(不做业务裁剪)。
    state_cap = (int(getattr(cfg, "MEMORY_RETRIEVE_STATE_MAX", 0) or 0)
                 if purpose == "reply" else None)
    dedup_on = read_dedup and purpose != "debug"

    def _sort_key(r):
        """命中档内先按相关性分档,再按 memory_score。"""
        return (r["on_topic"], r["rel_bucket"], r["base"])

    def _order_facts(rows):
        """把「按问题取用」实现为限量插队,只对事实层生效。

        最多前 cfg.MEMORY_RETRIEVE_RELEVANCE_PROMOTE_MAX 条命中事实排到最前,
        其余按 memory_score 排序;置 0 等价于纯静态序。
        """
        cap = int(getattr(cfg, "MEMORY_RETRIEVE_RELEVANCE_PROMOTE_MAX", 0) or 0)
        if not q or cap <= 0:
            return sorted(rows, key=lambda r: r["base"], reverse=True)
        promoted = [r for r in rows if r["on_topic"]][:cap]   # rows 已按 _sort_key 排好
        keep = {id(r) for r in promoted}
        rest = sorted((r for r in rows if id(r) not in keep),
                      key=lambda r: r["base"], reverse=True)
        return promoted + rest

    def _rows(items):
        out = []
        for v in items:
            text = _item_text(v)
            base = forget.memory_score(v, now)
            rel = relevance.score(q, text, intent=q_intent, value=v) if q else 0.0
            bucket = round(rel / rel_step) * rel_step if rel_step > 0 else rel
            out.append({"v": v, "text": text, "base": base, "rel": rel,
                        "rel_bucket": round(bucket, 4),
                        "on_topic": bool(q) and rel >= rel_min})
        out.sort(key=_sort_key, reverse=True)
        return out

    def _load(ns, show_archived: bool, kind: str | None = None,
              stats: dict | None = None):
        raw_items = mstore.list_items(ns)
        raw_n = len(raw_items)
        pairs = [(it.key, it.value) for it in raw_items]
        # 门槛与 top-N 都应基于"可评分候选":先排除空值(以及 reply 下的归档条目)。
        eligible = [(k, v) for k, v in pairs
                    if v and (show_archived or not (v or {}).get("archived"))]
        eligible_n = len(eligible)
        # 小库不筛:FTS 的收益在规模,不在小库。候选不超过门槛时直接走旧的相关性
        # 排序,避免"一条命中把同主题近义事实全部挡掉"(见 docs 里的复现)。
        # debug 是排障视图,也不筛 —— 否则"看全量"会变成"看被 FTS 裁过的全量"。
        filtered = bool(q and fts_keys and purpose != "debug" and eligible_n > fts_min)
        fallback = False
        if filtered:
            # 先在窗口内匹配(保留旧的 text 兜底),再按 key 直取窗口外的命中:
            # list_items 默认只回最新 1000 条,FTS 命中若更老,不直取就永远进不来。
            hits = [(k, v) for k, v in eligible
                    if (kind and (kind, str(k)) in fts_keys)
                    or mstore.norm_text(_item_text(v)) in fts_texts]
            hit_keys = {k for k, _ in hits}
            for hit_kind, hit_key in fts_keys:
                if kind and str(hit_kind) != str(kind):
                    continue
                if str(hit_key) in hit_keys:
                    continue
                val = mstore.get_item(ns, str(hit_key))
                if not val or (not show_archived and (val or {}).get("archived")):
                    continue
                hits.append((str(hit_key), val))
                hit_keys.add(str(hit_key))
            # 窗口外的命中数:留痕用它解释"kept 为什么可能大于 eligible"。
            eligible_keys = {k for k, _ in eligible}
            beyond_window = sum(1 for k, _ in hits if k not in eligible_keys)
            if hits:
                # P0:命中集 ∪ memory_score top-N。FTS 只负责"别忘了字面命中",
                # top-N 负责"别把高重要度/近况事实筛没了";排序仍交给下面的 _rows。
                top_n = max(1, int(cfg.MEMORY_RETRIEVE_MAX_FACTS))
                ranked = sorted(eligible,
                                key=lambda kv: forget.memory_score(kv[1], now),
                                reverse=True)[:top_n]
                pairs = hits + [(k, v) for k, v in ranked if k not in hit_keys]
            else:
                # 命中集为空才回退全集:兼容旧库/索引未重建,也覆盖"唯一命中被归档"。
                pairs = eligible
                fallback = bool(eligible_n)
                beyond_window = 0
        else:
            pairs = eligible
            beyond_window = 0
        after_fts_n = len(pairs)
        if stats is not None:
            stats.update({
                "raw": raw_n,
                "eligible": eligible_n,
                "after_fts": after_fts_n,
                "kept": len(pairs),
                "beyond_window": beyond_window,
                "filtered": filtered,
                "fallback": fallback,
            })
        # 键随行携带(选中条目的 store 键,检索留痕用);_rows 原样保留 value 引用,
        # 故可用 id() 把键映射回行上。
        key_by_id = {id(v): k for k, v in pairs}
        rows = _rows([v for _, v in pairs])
        for r in rows:
            r["item_key"] = key_by_id.get(id(r["v"]), "")
        return rows

    cand_stats: dict = {}
    live_facts = _order_facts(_load(
        mstore.ns_facts(user), show_archived=False, kind="facts",
        stats=cand_stats.setdefault("facts", {})))
    live_eps = _load(
        mstore.ns_episodes(user), show_archived=False, kind="episodes",
        stats=cand_stats.setdefault("episodes", {}))
    # FTS 初筛观测:必须放在两次 _load 之后 —— 它记录的是"候选被收窄了多少",
    # 而不是"索引里命中了多少"。n_hits 只说明索引有命中,不等于候选真的变少。
    fts_stats = {
        "engaged": bool(q and fts_keys),
        # applied=False 有两种可能:没有命中,或候选未超过门槛(小库不筛)。
        # 单看 engaged 会误以为"FTS 介入了但没效果",所以单独记一个 applied。
        "applied": any(bool(s.get("filtered")) for s in cand_stats.values()),
        "n_hits": len(fts_keys),
        "narrowed": any(s.get("filtered") and s.get("after_fts", 0) < s.get("eligible", 0)
                        for s in cand_stats.values()),
        "fallback": any(bool(s.get("fallback")) for s in cand_stats.values()),
        "candidates": cand_stats,
    }
    # 片段层先按天合并(仅业务用途):同一天只保留分数最高的那一段。
    # debug 视图不做这一步,排障时保留全部条目。
    if purpose != "debug":
        _seen_days = set()
        _eps_by_day = []
        for r in live_eps:
            v = r["v"]
            ts = v.get("ts_end") or v.get("last_seen") or now
            try:
                day = datetime.fromtimestamp(float(ts)).strftime("%Y-%m-%d")
            except (ValueError, OSError, TypeError):
                day = ""
            if day and day in _seen_days:
                continue
            if day:
                _seen_days.add(day)
            _eps_by_day.append(r)
        live_eps = _eps_by_day

    dropped: dict = {}

    def _note_drop(r, reason):
        if len(dropped) < 400:
            dropped[mstore.norm_text(r["text"])] = reason

    def _fill(rows, render, cap, room, allow=None):
        """按排序键从高到低填充,直到超出 room 或达到 cap。

          · 超预算时 `continue` 而不是 `break` —— 一条超长事实不该把后面的短事实全挡掉;
          · 支持 `allow` 谓词(分道配额 / 同关键词上限):被挡下的条目只记原因、继续看后面的。
        """
        picked, used = [], 0
        for r in rows:
            if len(picked) >= cap:
                _note_drop(r, "cap")
                continue
            if allow is not None:
                reason = allow(r, picked)
                if reason:
                    _note_drop(r, reason)
                    continue
            line = render(r["v"])
            if picked and budget is not None and used + len(line) + 1 > room:
                _note_drop(r, "budget")
                continue
            picked.append(r)
            used += len(line) + 1          # +1 计入换行
        return picked, used

    def _fact_allow(r, picked):
        """事实层的两道护栏(返回 None = 允许;返回字符串 = 落选原因)。

        1. 状态道上限:状态类事实最多 state_cap 条,风险内容按内容豁免;
        2. 读侧近义去冗:与已入选条目近义的事实不再入选(复用 similarity 的实现)。
        """
        lane = lanes.classify(r["v"])
        if state_cap is not None and lane == lanes.LANE_STATE and not lanes.is_risk_content(r["v"]):
            if sum(1 for x in picked
                   if lanes.classify(x["v"]) == lanes.LANE_STATE
                   and not lanes.is_risk_content(x["v"])) >= state_cap:
                return "state_lane_cap"
        if dedup_on:
            for x in picked:
                if _similarity.polarity_conflict(x["text"], r["text"]):
                    continue
                if _similarity.is_similar(x["text"], r["text"]):
                    return "duplicate_in_context"
        return None

    # 1) 事实层优先;为会话回顾与冷记忆各预留一段字符预算。
    arch_rows = []
    if archived_max > 0 and budget is not None:
        arch_rows = [r for r in _load(mstore.ns_facts(user), show_archived=True, kind="facts")
                     if r["v"].get("archived")]
        arch_rows += [r for r in _load(mstore.ns_episodes(user), show_archived=True, kind="episodes")
                      if r["v"].get("archived")]
        arch_rows.sort(key=_sort_key, reverse=True)
    arch_reserve = float(getattr(cfg, "MEMORY_RETRIEVE_ARCHIVED_RESERVE_CHARS", 0.0)) \
        if arch_rows else 0.0
    fact_room = (budget - reserve - arch_reserve) if budget is not None else float("inf")
    facts_rows, used_f = _fill(live_facts, lambda v: _fact_line(v, today, purpose == "debug"),
                               cap_f, fact_room, allow=_fact_allow)
    # 2) 会话回顾用剩余预算(含预留)
    ep_room = (budget - used_f) if budget is not None else float("inf")
    episodes_rows, used_e = _fill(live_eps, lambda v: _episode_line(v, today, now), cap_e, ep_room)
    # 3) 冷记忆兜底:归档件默认不进提示词(只归档、不删除),仅在还有余量时带 1 条,
    #    并单独分区标注"较早提及" —— 让"归档"退化为"提得少"而不是"彻底想不起来"。
    archived_rows = []
    if arch_rows and budget is not None:
        room = budget - used_f - used_e
        if room > 20:
            archived_rows, _ = _fill(arch_rows, lambda v: _archived_line(v, today, now),
                                     archived_max, room)

    facts = [r["v"] for r in facts_rows]
    episodes = [r["v"] for r in episodes_rows]
    archived = [r["v"] for r in archived_rows]

    sel_chars = 0
    sel_chars += sum(len(_fact_line(v, today, purpose == "debug")) + 1 for v in facts)
    sel_chars += sum(len(_episode_line(v, today, now)) + 1 for v in episodes)
    sel_chars += sum(len(_archived_line(v, today, now)) + 1 for v in archived)

    sel_texts = {mstore.norm_text(v.get("text")) for v in facts}
    explain = []
    for r in live_facts[:500]:
        key = mstore.norm_text(r["text"])
        explain.append({
            "text": r["text"], "lane": lanes.classify(r["v"]),
            "lane_zh": lanes.describe(r["v"]),
            "base": round(r["base"], 4),
            "rel": round(r["rel"], 4),
            "on_topic": bool(r["on_topic"]),
            "selected": key in sel_texts,
            "reason": None if key in sel_texts else dropped.get(key, "not_reached"),
            "risk": lanes.is_risk_content(r["v"]),
        })
    return {
        "facts": facts, "episodes": episodes, "archived": archived,
        "fact_texts": sel_texts,
        "chars": sel_chars, "budget": budget,
        "fact_keys": [str(r.get("item_key") or "") for r in facts_rows],
        "episode_keys": [str(r.get("item_key") or "") for r in episodes_rows],
        "fts": fts_stats,
        # ---- 排障/洞察:读侧到底按什么取的(不进提示词) ----
        "query": q or "",
        "intent": q_intent,
        "state_cap": state_cap,
        "read_dedup": dedup_on,
        "lanes": {
            "all": lanes.counts([r["v"] for r in live_facts]),
            "selected": lanes.counts(facts),
        },
        "explain": explain,
    }


def build_memory_context(user: str, exclude_thread=None, purpose: str = "reply",
                         query: str | None = None) -> str:
    """读取该用户已固化的档案事实 + 近期状态 + 会话回顾,分区块输出。

    exclude_thread:当前会话 id。尚未固化的待整理段若来自其它会话,
    附上简短兜底(防止"刚换新会话就问起上一段"时完全没线索)。

    query:用户**本轮那句话**。传了它,读侧才会"按问题取用"(见 select_context);
    不传则与加入该参数之前完全一致(静态排序)。

    近期状态给两行:近 3 天(recent_mood_summary)与近三周(long_horizon_summary)。

    purpose 分层(同一底层记忆可被不同读侧重用,按需裁剪预算):
      - "reply":生成回复用(默认)—— 预算取 cfg.MEMORY_RETRIEVE_MAX_CHARS(字符闸门);
      - "care":主动关怀/桌宠素材用(预算更紧、不带未整理兜底);
      - "debug":排障用(不做业务预算裁剪,并保留 [类别] 等内部标记)。
    """
    if not user:
        return ""
    now = time.time()
    today = datetime.fromtimestamp(now).date()
    include_pending = (purpose != "care")
    show_category = (purpose == "debug")

    sel = select_context(user, purpose=purpose, now=now, query=query)
    facts, episodes, archived = sel["facts"], sel["episodes"], sel["archived"]

    lines: list[str] = []

    if facts:
        lines.append("【用户档案】")
        lines.extend(_fact_line(v, today, show_category) for v in facts)

    # 近期状态:来自时序层日桶。两行 = 近 3 天 + 近三周。
    recent = mprofile.recent_mood_summary(user)
    long_recent = mprofile.long_horizon_summary(user)
    if recent or long_recent:
        lines.append("【近期状态】")
        if recent:
            lines.append("- " + recent)
        if long_recent:
            lines.append("- " + long_recent)

    if episodes:
        lines.append("【过往对话回顾】")
        lines.extend(_episode_line(v, today, now) for v in episodes)

    if archived:
        lines.append("【更早的记忆(较早提及，可能已过时)】")
        lines.extend(_archived_line(v, today, now) for v in archived)

    # 兜底:其它会话里尚未固化的最近对话(仅 reply/debug 用途;worker 未及固化时的最近窗口)
    if include_pending:
        pending = mstore.get_pending(user)
        if pending:
            others = [t for t in (pending.get("turns") or []) if t.get("thread") != exclude_thread]
            if others:
                lines.append("【最近对话(尚未整理)】")
                for t in others[-2:]:
                    # 带上该轮发生时刻:这条是待固化窗口的原文,未经时间词绝对化。
                    ts = t.get("ts")
                    when = f"（{_fmt(ts)}{_day_hint_suffix(ts, today)}）" if ts else ""
                    lines.append(f"- 用户{when}: {_snippet(t.get('user'))}")

    # 检索留痕:把这次组装的决策(选中/落选/FTS 观测)按次落盘,排障与审计用。
    # 只记会进提示词的两种用途;失败绝不影响回复(trace.record 内部再兜一层)。
    if purpose in ("reply", "care") and bool(getattr(cfg, "MEMORY_TRACE_ENABLED", True)):
        try:
            from gzhu_cup2.agent.long_memory import trace as _trace_mod
            _trace_mod.record(user, purpose, sel, now=now)
        except Exception:  # noqa: BLE001 - 留痕是增强,不是链路的一部分
            logger.warning("检索留痕调用失败 user=%s purpose=%s", user, purpose,
                           exc_info=True)

    return "\n".join(lines)
