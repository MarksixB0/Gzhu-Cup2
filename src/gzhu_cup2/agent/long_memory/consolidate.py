# -*- coding: utf-8 -*-
"""固化阶段纯逻辑:把待固化对话整理成结构化记忆(由后台记忆图调用)。

- extract_memory:单次 LLM 结构化抽取(向后兼容入口);
- extract_memory_voted:自一致投票 —— 同一段对话采样 N 次(默认 cfg
  MEMORY_CONSOLIDATE_VOTE_SAMPLES),只把"多次一致"的条目作为强证据合并,
  不一致内容自动丢弃(宁可少记,不把单次抽样的抖动当事实);
- merge_extractions:纯函数合并(零 LLM、零 IO),可离线单测。
"""
import logging
import re

from gzhu_cup2.agent.config import config as cfg
from gzhu_cup2.agent.config import runtime_connection
from gzhu_cup2.agent.core.models.LLM_base import get_llm, structured_llm
from gzhu_cup2.agent.core.schemas import CrisisFacets, MemoryExtraction
from gzhu_cup2.agent.long_memory import dates as _dates
from gzhu_cup2.agent.prompts.long_memory_prompts import build_memory_extraction_prompt
from gzhu_cup2.agent.prompts.prompts import now_text

logger = logging.getLogger(__name__)


class ConsolidationFailed(RuntimeError):
    """固化抽取的全部采样都失败。

    必须是异常而不是"空对象":worker 的守卫只判断 ``extraction is None``,
    返回空 MemoryExtraction 会被当成成功 —— 于是落一条空 digest 的 episode、
    pending 被推进、用户原话静默丢失,退避机制也被清空。
    抛异常后 worker 会记退避并保留 pending 重试:宁可晚一点固化,也不能丢记忆。
    """


# ---- 回忆型提问:不把它当成"新的事实断言" ----
# 用户确认「我之前说过什么」时,句中的相对时间会被按**今天**重新锚定,
# 同一件事因此被写成第二个矛盾日期。这类话不提供新信息,抽取前直接中和掉。
_RECALL_MARKERS = (
    "什么来着", "啥来着", "你还记得", "记得吗", "记不记得", "我说过什么",
    "我之前说", "我上次说", "我是不是说过", "提醒我一下", "我说的是",
)
# 带这些词说明可能是更正/补充新信息,不能当"纯回忆"处理
_NEW_INFO_MARKERS = ("改到", "改成", "推迟", "延后", "提前", "调整到", "换成",
                     "不是", "其实", "确切")
_RE_HAS_DIGIT = re.compile(r"\d")


def _is_recall_question(text: str) -> bool:
    """是否是"在确认已记过的事"的纯回忆提问(不提供新事实)。

    刻意保守:带数字(可能是更正日期)、带更正词、或句子过长(可能夹带新信息)
    一律不判为回忆 —— 宁可多抽一条,不可把用户真的更正过的信息丢掉。
    """
    t = str(text or "").strip()
    if not t or len(t) > 40:
        return False
    if any(m in t for m in _NEW_INFO_MARKERS):
        return False
    if _RE_HAS_DIGIT.search(t):
        return False
    return any(m in t for m in _RECALL_MARKERS)


def format_pending_text(pending: dict) -> str:
    """把 pending 段的轮次流水格式化成可交给 LLM 的文本。

    回忆型提问(见 _is_recall_question)会被替换成中性说明:用户那一侧没有新信息,
    助手那一侧只是复述旧内容 —— 两侧都不该再被抽成事实/摘要,否则相对时间会被
    按今天重新锚定,造出与首次断言矛盾的第二个日期。
    """
    lines = []
    for t in (pending or {}).get("turns") or []:
        user_text = str(t.get("user") or "").strip()
        ai_text = str(t.get("ai") or "").strip()
        recall = _is_recall_question(user_text)
        if user_text:
            if recall:
                lines.append("用户：（在确认之前提过的事，没有提供新信息）")
            else:
                lines.append(f"用户：{user_text[:600]}")
        if ai_text:
            if recall:
                lines.append("助手：（复述了此前记录过的内容）")
            else:
                lines.append(f"助手：{ai_text[:600]}")
    return "\n".join(lines)


def _preferred_llm_params(pending: dict) -> dict:
    """取该段对话实际使用的模型(优先最后一轮有记录的),使固化与聊天同模型/同额度。

    单点模式(cfg.MODEL_SINGLE_SOURCE=True)下返回空 —— 一律走 config 默认。

    关闭单点时,历史记录里的模型需与当前生效模型一致才采用:切换服务商后旧模型名
    在新端点通常不存在,沿用会稳定触发 4xx,使该段固化永久卡在失败重试上。
    """
    if cfg.MODEL_SINGLE_SOURCE:
        return {}
    model, temperature = None, None
    for t in (pending or {}).get("turns") or []:
        meta = t.get("meta") or {}
        if meta.get("model"):
            model = meta.get("model")
        if meta.get("temperature") is not None:
            temperature = meta.get("temperature")
    if model and model != runtime_connection.resolve_model():
        model = None
    return {"model_name": model, "temperature": temperature}


def _existing_facts_brief(user: str | None) -> list:
    """给抽取提示用的『已记下的事』清单(未归档、按热度取前 N、每条截断)。

    为什么只给纯文本:抽取器要的是"原句长什么样",好让它**沿用原措辞**;
    带上类别/日期/次数这类元信息反而会让它开始复述我们的内部字段。

    读取失败只降级为"无清单"(记 warning 后返回空),绝不让清单读取把整段固化
    拖挂 —— 那会走到 ConsolidationFailed 的退避路径,pending 迟迟不落库。
    """
    if not bool(getattr(cfg, "MEMORY_PROMPT_EXISTING_FACTS", True)):
        return []
    u = str(user or "").strip()
    if not u:
        return []
    max_n = int(getattr(cfg, "MEMORY_PROMPT_EXISTING_FACTS_MAX", 24) or 0)
    if max_n <= 0:
        return []
    max_chars = int(getattr(cfg, "MEMORY_PROMPT_EXISTING_FACT_CHARS", 48) or 48)
    # 局部导入:store 在模块尾部才被 import norm_text(见本文件下方),
    # 这里用局部导入避免依赖模块级符号的加载顺序。
    from gzhu_cup2.agent.long_memory import store as _store

    rows = []
    try:
        for it in _store.list_items(_store.ns_facts(u), limit=max_n * 4):
            v = it.value or {}
            if v.get("archived"):
                continue
            t = str(v.get("text") or "").strip()
            if not t:
                continue
            heat = (int(v.get("mentions", 0) or 0),
                    float(v.get("importance", 0.0) or 0.0))
            rows.append((heat, it.key, t))
    except Exception:  # noqa: BLE001 - 清单是增强,不是必需
        logger.warning("读取已有事实清单失败,按无清单继续固化", exc_info=True)
        return []
    # 热度高的先给:模型更可能复述它最近反复提到的事;同热度按 key 保证确定性
    rows.sort(key=lambda r: (r[0], r[1]), reverse=True)
    out, seen = [], set()
    for _, _, t in rows:
        n = _norm(t)
        if not n or n in seen:
            continue
        seen.add(n)
        out.append(t[:max_chars])
        if len(out) >= max_n:
            break
    return out


def extract_memory(pending: dict, llm=None,
                   user: str | None = None) -> MemoryExtraction:
    """调用 LLM 做结构化总结与事实抽取(无 LLM 结果时给出空安全值)。

    注入锚定日期:提取器只有看到「这段对话发生在哪一天」,才知道事实里的
    「明天/下周」是相对哪一天;同时提示词要求它**原样保留时间词**,绝对日期的
    换算统一由 Python 侧的 dates.resolve_event_date 在落库时完成(确定性)。

    注入已有事实清单(见 _existing_facts_brief):不给清单,模型每段只能"新起
    一条",同一件事的详略两版必然并存;给了清单并引导"沿用原句再补细节",
    落库侧的前缀判据(store._is_wording_extension)才能把它们收成一条。

    温度固定为零(cfg.MEMORY_CONSOLIDATE_TEMPERATURE):结构化抽取不是创作任务,
    旧实现会让温度落到 MODEL_TEMPERATURE=0.7(单点模式下 `_preferred_llm_params`
    返回空),同一段对话两次抽取的措辞必然漂移。若 pending 里带过旧温度,这里**强制覆盖**。
    """
    text = format_pending_text(pending)
    params = dict(_preferred_llm_params(pending))
    params.pop("temperature", None)          # 旧 pending 里残留的温度不再生效
    llm = llm or get_llm(max_tokens=int(getattr(cfg, "MEMORY_CONSOLIDATE_MAX_TOKENS", 900)),
                         temperature=float(getattr(cfg, "MEMORY_CONSOLIDATE_TEMPERATURE", 0.0)),
                         **params)
    anchor_dt = _dates.anchor_datetime(pending)
    messages = build_memory_extraction_prompt(
        text, anchor_date_text=now_text(anchor_dt),
        existing_facts=_existing_facts_brief(user))
    result: MemoryExtraction = structured_llm(llm, MemoryExtraction).invoke(messages)
    if result is None:
        # 模型未产出工具调用:交给 worker 退避重试,不写空固化(否则该段记忆静默丢失)
        raise RuntimeError("记忆固化未取得结构化结果:模型未按 schema 返回")
    return result


# ============================ 多采样合并(并集 + 支持度) ============================
# 规范化复用 store.norm_text(全仓唯一),它同时是事实键与支持度字典的键。
from gzhu_cup2.agent.long_memory.store import norm_text as _norm  # noqa: E402


def merge_extractions(samples, support_out: dict | None = None) -> MemoryExtraction:
    """合并多份 MemoryExtraction:并集保留 + 支持度标注。

    facts / topics / triggers / coping / dimension_evidence / crisis 取并集。
    事实的支持度通过 ``support_out`` 出参带回,结构为
    ``{"facts": {规范化文本: 命中样本数}, "vote_samples": 成功采样数}``,
    由 ``store.commit_segment`` 落到事实行的 ``support`` / ``vote_samples`` / ``weak`` 上。
    弱证据不丢弃,只在检索时降权。

    ``support_out`` 为空时与"并集但不带支持度"一致;单样本恒等返回。
    纯函数:零 LLM、零 IO、可离线单测。
    """
    samples = [s for s in (samples or []) if s is not None]
    if not samples:
        return MemoryExtraction(digest="")
    first = samples[0]
    if len(samples) == 1:
        return first
    n = len(samples)

    # ---- facts:并集;支持度 = 命中样本数 ----
    def _facts_union():
        per_fact = {}
        order = []
        for ex in samples:
            seen_in_sample = set()
            for f in ex.facts or []:
                k = _norm(f.text)
                if not k or k in seen_in_sample:
                    continue          # 同一段里重复写同一条只算一次支持
                seen_in_sample.add(k)
                if k not in per_fact:
                    per_fact[k] = {"fact": f, "support": 0}
                    order.append(k)
                per_fact[k]["support"] += 1
        out = [per_fact[k]["fact"] for k in order]
        if support_out is not None:
            support_out["facts"] = {k: per_fact[k]["support"] for k in order}
            support_out["vote_samples"] = n
        return out

    # ---- 短列表(topics/triggers/coping):并集,条数由 tags.clamp_* 封顶 ----
    def _list_union(attr):
        out, seen = [], set()
        for ex in samples:
            for v in (getattr(ex, attr) or []):
                k = _norm(v)
                if k and k not in seen:
                    seen.add(k)
                    out.append(str(v))
        return out

    # ---- dimension_evidence:并集,同一维度 level 取最高,quote 取更高档那条 ----
    def _evidence_union():
        merged = {}
        for ex in samples:
            for key, item in (ex.dimension_evidence or {}).items():
                if not isinstance(item, dict):
                    continue
                try:
                    lv = int(item.get("level", 0) or 0)
                except (TypeError, ValueError):
                    lv = 0
                if lv <= 0:
                    continue
                cur = merged.get(key)
                if cur is None or lv > cur["level"]:
                    merged[key] = {"level": lv, "quote": str(item.get("quote") or "")}
        return merged

    # ---- crisis 分面:**并集**保留(任一采样命中即留,多条取最长) ----
    def _crisis_voted():
        """危机分面收集:任一采样命中即保留,同一分面取最长引文。

        引文长度 ≥ 2 字才保留(挡空串/单字噪声),不要求跨样本逐字一致。
        段级补充字段(denied/uncertain)同口径合并。
        """
        out = {}
        for facet in ("ideation", "intent", "plan", "behavior",
                      "denied_quote", "uncertain_quote"):
            best = ""
            for ex in samples:
                c = getattr(ex, "crisis", None)
                q = " ".join(str(getattr(c, facet, "") if c else "").split())
                if len(q) > len(best):
                    best = q
            if len(best) >= 2:
                out[facet] = best
        return CrisisFacets(**out) if out else None

    return MemoryExtraction(
        digest=str(getattr(first, "digest", "") or ""),
        facts=_facts_union(),
        importance=float(getattr(first, "importance", 0.5) or 0.5),
        risk_note=str(getattr(first, "risk_note", "") or ""),
        crisis=_crisis_voted(),
        topics=_list_union("topics"),
        dimension_evidence=_evidence_union(),
        triggers=_list_union("triggers"),
        coping=_list_union("coping"),
    )


def extract_memory_voted(pending: dict, llm=None, samples: int | None = None,
                         support_out: dict | None = None,
                         user: str | None = None) -> MemoryExtraction:
    """多采样抽取:采样 N 次(默认 config)、按**并集 + 支持度**合并。

    任一次采样失败只跳过该次;全部失败抛 ConsolidationFailed,
    由调用方保留 pending 并退避重试。

    support_out:非空时写入 ``{"facts": {规范化事实文本: 被几个采样抽到}, "vote_samples": 成功采样数}``,
    供落库层标注弱证据。单采样(samples==1)时**不合并、也不写支持度** ——
    那一路是"不做投票"的模式,事实一律视为强证据(与旧行为一致,便于对照)。

    user:**必须传**(调用方 memory_graph 手上有 user_id)。它只用于取"已记下的事"
    清单注入提示 —— 采样之间必须共用同一份清单,否则同一段对话的两次采样会
    按不同参照物措辞,投票反而更容易把真事实判成低支持度。
    """
    samples = max(1, int(samples if samples is not None
                         else cfg.MEMORY_CONSOLIDATE_VOTE_SAMPLES))
    if samples == 1:
        return extract_memory(pending, llm=llm, user=user)
    results = []
    for _ in range(samples):
        try:
            results.append(extract_memory(pending, llm=llm, user=user))
        except Exception as e:  # noqa: BLE001 - 单次失败不影响其余采样/固化
            logger.warning("记忆固化采样失败(第 %d/%d 次): %s",
                           len(results) + 1, samples, e)
    if not results:
        raise ConsolidationFailed(
            "记忆固化:全部 %d 次采样均失败,保留 pending 待重试" % samples)
    return merge_extractions(results, support_out=support_out)
