# -*- coding: utf-8 -*-
"""对话基调(mode)选择:根据"当轮情绪 + 风险档"给话术一个确定性基调。

纯函数、零 IO、零 LLM(与 care.policy 同风格,便于单测)。
基调只用于控制语气,绝不作为对用户可见的标签出现,也不参与任何风险判定。
"""
from typing import Optional

_NEG = frozenset({"sad", "anxious", "angry", "frustrated", "fearful"})
_POS = frozenset({"happy", "hopeful"})


def dialogue_mode(emotion: Optional[str], risk_level: Optional[str],
                  care_intensity: Optional[str] = None) -> str:
    """返回对话基调:serious / gentle / normal / playful。

    规则(确定性,宁轻勿重):
      - crisis → serious(正常不会走到这里;危机有独立前台分支)
      - high / 负向情绪 → gentle(轻一点、慢一点、多倾听)
      - medium + 负向 → gentle;medium 其他 → normal
      - low + 正向情绪 → playful(可带一点点轻松)
      - 其余 → normal

    care_intensity(纵向守护分寸档)是可选的**单向升档**输入:只要它不是 steady,
    基调就不再退回 playful;它只能让基调更克制,不会更活泼。不传时视为不施加该限制。
    """
    emo = str(emotion or "").strip() or None
    risk = str(risk_level or "").strip().lower()
    if risk == "crisis":
        return "serious"
    if risk == "high":
        return "gentle"
    neg = emo in _NEG
    pos = emo in _POS
    if risk == "medium" and not neg:
        base = "normal"
    elif risk == "low" and pos and not neg:
        base = "playful"
    elif neg:
        # 负向情绪一律 gentle,与 risk 档无关。
        base = "gentle"
    else:
        base = "normal"
    # 分寸档的单向收紧:watch 及以上不允许 playful
    if base == "playful" and care_level(care_intensity) != "steady":
        return "gentle"
    return base


# ---- 纵向守护档位 → 对话分寸档(纯映射,零 IO) ----
# 档位不写进提示词,只映射成场合词。
_TIER_TO_LEVEL = {"elevated": "caring", "watch": "gentle"}


def care_level(guard_tier: Optional[str]) -> str:
    """把守护档位翻译成「分寸档」:steady / gentle / caring。

    只返回场合词,不返回档位本身;未登记的档位回落 steady。
    """
    return _TIER_TO_LEVEL.get(str(guard_tier or "").strip().lower(), "steady")


def care_hint(level: Optional[str], prompts: Optional[dict] = None) -> str:
    """把分寸档翻译成一句给模型的**场合**提示(不含任何档位/评估字样)。

    文案来自**角色卡可覆盖的提示词集**(键 ``care_voice``),卡里没写才用代码默认 ——
    与 tone_hint 同一条"写活"路径,保证换卡时说话方式跟着角色走。

    ⚠ 返回的是**原始文案,含 {{char}}/{{user}} 宏**,调用方必须渲染后再进提示词
      (见 pet.character.prompts.render_text)。与 tone_hint 的不同之处在于:
      分寸文案以角色的第一人称说话,不渲染宏就会在提示词里留下占位符而"出戏"。
    """
    lv = str(level or "").strip().lower() or "steady"
    if prompts is None:
        from gzhu_cup2.agent.pet.character import prompts as pprompts
        prompts = pprompts.active_prompts()
    return str((prompts.get("care_voice") or {}).get(lv, "") or "")



def tone_hint(mode: str, prompts: Optional[dict] = None) -> str:
    """把基调翻译成给生成模型的一句语气提示(不含内部标记词)。

    文案来自**角色卡可覆盖的提示词集**(pet/character/prompts.py),卡里没写才用
    代码默认。调用方若已持有解析好的提示词集,传进来可避免重复加载角色卡。
    """
    if prompts is None:
        from gzhu_cup2.agent.pet.character import prompts as pprompts
        prompts = pprompts.active_prompts()
    return str((prompts.get("tone") or {}).get(mode, "") or "")


# ---- 镜像确认(对话内自然纠错)的确定性判别 ----
_AFFIRM_SHORT = frozenset({"嗯", "对", "是", "是啊", "是的", "对啊", "真的", "确实",
                           "没错", "就是", "嗯嗯", "对呀", "是呀"})
_DENY_SHORT = frozenset({"不是", "没有", "没有啊", "还好", "还好啦", "没那么",
                         "不至于", "没有吧", "算了", "没啥"})
_DENY_PREFIX = ("不是", "没有", "没那么", "不至于", "还好", "没啥", "不太")
_AFFIRM_PREFIX = ("对", "是", "嗯", "确实", "真的")


def classify_affect_reply(text: Optional[str]) -> Optional[str]:
    """判断一句话是否是"对上一轮自己负面表达的确认/否认"(只判短回应)。

    返回 "confirm" / "deny" / None。仅用于镜像确认统计,不作其他用途;
    宁可漏判,不可误判(长句、含解释的内容一律返回 None)。
    """
    t = " ".join(str(text or "").split()).strip("。！!?？~～., ")
    if not t:
        return None
    if t in _AFFIRM_SHORT:
        return "confirm"
    if t in _DENY_SHORT:
        return "deny"
    for w in _DENY_PREFIX:
        if t.startswith(w) and len(t) <= 10:
            return "deny"
    for w in _AFFIRM_PREFIX:
        if t.startswith(w) and len(t) <= 8:
            return "confirm"
    return None
