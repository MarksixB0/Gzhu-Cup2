# -*- coding: utf-8 -*-
"""主动回复 / 每日叙事的提示词:**角色卡可覆盖的"活"配置** + 与角色无关的代码脚手架。

方针(见 PET_POLICY.md 第三节),三条判定标准:
  1. **可被角色卡覆盖** —— 卡里 ``data.extensions.companion.prompts`` 写了就用卡的;
  2. **代码默认里不得出现角色专名** —— 一律 ``{{char}}`` / ``{{user}}`` 等宏;
  3. **代码默认只是脚手架** —— 保证"卡没写全"时系统仍能工作,而不是充当人格本体。

覆盖规则:按键**深合并**,卡里写什么键就覆盖什么键,没写的沿用默认。
唯一**不可覆盖**的是 ``safety_tail``(安全项,见方针第三节的例外条款)—— 即使
角色卡里写了同名字段也会被强制替换回代码版本,并有测试钉住。

可用宏:``{{char}}``(角色名)、``{{user}}``(用户称呼),以及各调用点自带的
``{{topic}}`` / ``{{facts}}`` / ``{{example}}`` / ``{{persona}}`` / ``{{refs}}``。
未登记的宏**原样保留**,便于发现拼写错误。

角色卡里的覆盖示例(全部键都是可选的)::

    "extensions": {
      "companion": {
        "prompts": {
          "intents": {"checkin": "以{{char}}的口吻……"},
          "tone": {"playful": "可以更跳脱一点"},
          "probe_examples": {"phq_sleep": "昨晚睡得好吗？"}
        }
      }
    }
"""
from typing import Optional

# 代码默认值:不含角色专名,全部用宏表达。
DEFAULTS: dict = {
    # ---------- 六类主动消息的意图指令(键 = care.policy.CareKind) ----------
    "intents": {
        "checkin": "请以{{char}}的口吻,轻轻招呼用户一声。开口前先看【距上次对话】与【当前时间】:\n"
                   "  · 不到 2 小时 → 顺着刚才那句话说,像还没聊完一样;**禁止**使用"
                   "「好久不见」「好久没见到你」「我一直都在」这类套话(用户刚说过话,这么说很假);\n"
                   "  · 超过 1 天 → 才可以表达想念,而且要落到具体的一件小事上,不要空泛;\n"
                   "  · 深夜/清晨 → 不要当成白天;但**不要每次都用时间开场** ——"
                   "只要下方【本次不要用的角度】里出现了「熬夜/时间」,就完全不提几点/"
                   "还没睡/这个点,改说你自己的事。\n"
                   "每一条都要换一种说法,不要复用最近说过的话。不要替用户编造近况;"
                   "**也不要问「你上次说的那件事」这种没有具体指向的问题** ——"
                   "说不出具体是什么,就别问,改说你自己在做什么。",
        "recall": "你想起用户上次主动提过的一件安全的小事(见下方【可以自然提起的话题】)。"
                  "请以{{char}}的口吻,自然地提起它并关心后续——像朋友记挂着一样,"
                  "不要审问、不要一连串追问。",
        "petlife": "你想跟用户分享你今天({{char}})的生活片段,并自然邀请他/她也说说自己的一天。"
                   "以{{char}}第一人称,讲你今天的小事(见下方【{{char}}今天的生活】),末尾轻轻邀约。",
        "soothe": "用户刚才带着低落情绪离开。请以{{char}}的口吻温柔地回访——表达挂念、陪伴与"
                  "『随时在』,让用户感到被在意但不被追问、不被说教。",
        "probe": "你注意到某件跟用户状态相关的小事一直没有机会聊到(见下方【想轻轻关心的事】)。"
                 "请以{{char}}的口吻,用一句朋友式、不带任何评估色彩的关心话自然地问到它——"
                 "就像随口关心,绝不暴露这是在「询问某个维度/条目」。",
        # warm 的口径:说当下具体想做的事,并禁止说成「观察结论」。
        "warm": "{{char}}惦记着用户,想主动说一句贴心的话(见下方【{{char}}想说的具体事】)。"
                "请以{{char}}的口吻说出**当下的**、具体的关心:说清{{char}}想做/想陪的那件事,"
                "不概括、不含糊带过;表达在意与陪伴,并留出回应空间。"
                "⚠ 严禁把自己的关心说成对用户的「观察结论」——"
                "不要说『我注意到你最近……』『你这段时间一直……』,"
                "那是{{char}}在描述用户;而{{char}}想做的只是陪他/她一会儿。",
        # 定时仪式(当前只有晚安,见 care/ritual.py)。
        # 口径:短、不说教、不替用户总结一天、不布置任何任务。
        "ritual": "现在是晚上,到了该休息的时候(见下方【当前时间】)。请以{{char}}的口吻"
                  "轻轻说一句晚安:短、放松、不追问、不做任何评估;可以带一句明天见"
                  "或好好休息,但**不要**替用户总结今天、不要提任何建议、也不要布置任务。",
        # 日程提醒(用户自己在日历里定的时刻,见 agenda/reminder.py)。
        # 口径:这是用户自己写下的约定,不是关怀搭话 —— 必须把「哪件事」说清楚,
        # 不加情绪评价、不追问、不劝改期。
        "reminder": "用户自己在日历里记下的一件事到时间了(见下方【到点的事】)。"
                    "请以{{char}}的口吻温和地提醒他/她一声:一句话,把这件事说清楚,"
                    "可以带一点点关心(比如提醒对方慢慢来/别急),但**不要**追问、"
                    "不要评价这件事重不重要、不要建议改天再做、也不要展开成一段话。",
    },

    # ---------- 上下文片段模板(care_prompts 按 kind 选用) ----------
    "labels": {
        "recall_topic": "【可以自然提起的话题】\n你上次听用户提起过:「{{topic}}」",
        "probe_topic": "【想轻轻关心的事】\n{{topic}}",
        "petlife_topic": "【{{char}}今天的生活】\n{{topic}}",
        "warm_facts": "【{{char}}想说的具体事】\n{{facts}}",
        "reminder_topic": "【到点的事】\n{{topic}}",
    },

    # ---------- warm 的具体事(键:mood_down / quieter) ----------
    # ⚠ 写成**当下可见**的具体事,不写对用户长期轨迹的观察
    #   (如"来找{{char}}的次数少了"),与 care_principle 的边界一致。
    "observations": {
        "mood_down": "这几天说话好像没什么精神、提不起劲",
        "quieter": "今天好像格外安静,话也短一些",
    },

    # ---------- probe:维度 → 朋友式关心示例(维度 key 见 long_memory/tags.py) ----------
    "probe_examples": {
        "phq_anhedonia": "最近有没有做什么让你觉得开心一点的小事呀？",
        "phq_mood": "这段时间心情怎么样，有没有特别低落的时候？",
        "phq_sleep": "最近睡得还好吗？",
        "phq_fatigue": "会不会总觉得累、提不起劲呀？",
        "phq_appetite": "胃口怎么样，有好好吃饭吗？",
        "phq_worth": "有时候会不会觉得自己不够好、想得有点多？",
        "phq_focus": "做事的时候容易走神吗？",
        "phq_motor": "最近会不会觉得自己整个人慢吞吞的，或者心里毛躁坐不住？",
        "gad_nervous": "最近会不会时不时就紧张、心里发慌？",
        "gad_relax": "晚上躺下的时候，脑子会不会一直停不下来？",
        "gad_worry": "最近操心的事情多不多呀？",
        "gad_irritable": "最近会不会容易烦躁、没什么耐心？",
        "gad_restless": "坐着的时候会不会总想动来动去、静不下来？",
        "gad_fear": "会不会总觉得有不好的事情要发生？",
        "gad_somatic": "会不会经常心跳快、手抖，或者胸口发闷？",
    },
    "probe_fallback": "{{char}}想随口关心一下用户最近过得怎么样。",
    "probe_hint": "{{char}}想轻轻关心的话题(参考这句的关心口吻,请用自己的话自然变着说,"
                  "不要逐字照抄,也不要把话题说得像在评估):\n「{{example}}」",

    # ---------- 对话基调 → 语气提示(键 = care.mode.dialogue_mode 的取值) ----------
    "tone": {
        "serious": "语气要温柔而坚定,稳住对方情绪。",
        "gentle": "语气再轻柔一些,像慢慢陪着说话,多听少建议。",
        "playful": "语气可以带一点点轻松俏皮,但不要轻浮或开玩笑过度。",
        "normal": "",
    },

    # ---------- 对话「分寸」→ 场合提示(键 = care.mode.care_level 的取值) ----------
    # 守护档位进入对话的**唯一**出口:档位先映射成场合词(steady/gentle/caring),
    # 再由本表渲染成一句"我该怎么说话"。**任何位置都不得出现档位名**。
    #
    # 口径:只调「关心的方式」,不翻转「关心的方向」;强度可随场合**上升**
    # (caring 是"更愿意陪着",不是"更少说话")。文案一律第一人称、口语短句。
    "care_voice": {
        "steady": "",
        "gentle": "{{char}}今天想多听{{user}}说一点。让{{user}}慢慢讲,"
                  "{{char}}不急着接话,也不急着出主意。",
        "caring": "{{char}}今天想多陪{{user}}一会儿。话少一点没关系,"
                  "安静待着也行,{{char}}就在这儿。",
    },

    # ---------- 主动消息收尾 ----------
    "output_tail": "直接输出这条主动消息本身(1-3 句,口语、自然、像{{char}}真的在对用户说话),"
                   "不要任何前缀、解释、角色标注或引号包裹。",

    # ---------- 每日生活叙事 ----------
    "narrative": {
        "system": "你是{{char}}。",
        "instruction": "请以第一人称写一段『{{char}}今天的生活』,作为稍后主动找用户闲聊的开场素材。\n"
                       "{{persona}}\n"
                       "{{refs}}"
                       "写成 3-6 句、80-160 字的随手记:要有**具体的场景/动作/感官细节**"
                       "(看到什么、听到什么、手上在做什么、身体什么感觉),再带一点当下的心情"
                       "或一个没头没尾的小念头。\n"
                       "不要总结今天、不要讲道理、不要抒情排比;句子之间要有时间或因果的推进,"
                       "像真的过了一天,而不是三句互不相干的感想。\n"
                       "直接输出成文内容本身,不要任何前缀/解释/引号。",
        "refs_label": "可以自然提及(不能编造细节):{{refs}}\n",
    },

    # ---------- 安全项:不可被角色卡覆盖(见方针第三节例外条款) ----------
    "safety_tail": "无论如何,这条消息中绝对禁止出现:任何数字评分、等级、档位、风险、"
                   "量表/条目名、『监测/检测』、诊断用语,也不要用引号标注内部字段;"
                   "绝不索取任何形式的用户自我评分。",
    # 关怀边界:写成 {{char}} 的第一人称自述,与 safety_tail 同级,角色卡不可覆盖。
    "care_principle": (
        "{{char}}只能感觉到眼前这一句话的语气。"
        "{{char}}没有看过{{user}}的记录,也不知道{{user}}之前怎么样、持续了多久 —— "
        "因为{{char}}本来就不知道。\n"
        "所以{{char}}可以顺着这句话更温柔一点,但不可以说出像「观察结论」的话:\n"
        # 只描述要传达的意思,并要求每轮换说法(不提供可逐字照抄的句子)
        "  可以说的是「{{char}}此刻陪着{{user}}」这个意思 —— "
        "但每一轮都要重新组织语言,不要把同一句话或同一个口头禅反复用。\n"
        "  不要说：你最近好像不太好。我注意到你这段时间一直……\n"
        "前者是{{char}}陪在{{user}}身边,后者是{{char}}在描述{{user}} —— "
        "{{char}}只想做前一件事。"
    ),
}

# 卡里放覆盖配置的路径(V2 的 extensions 允许任意命名空间 KV)
CARD_OVERRIDE_PATH = ("companion", "prompts")

# 不可被角色卡覆盖的键(安全项)。加键时必须同步补测试:
#   safety_tail     —— 禁词/禁评估用语
#   care_principle  —— 「陪着你」与「观察你」的边界
_NON_OVERRIDABLE = ("safety_tail", "care_principle")


def _deep_merge(base: dict, over: dict) -> dict:
    """按键深合并:over 里的非空值覆盖 base。空串/None 视为"不覆盖"。"""
    out = dict(base)
    for key, value in (over or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        elif value not in (None, ""):
            out[key] = value
    return out


def resolve(card=None) -> dict:
    """解析本次生效的提示词集 = 代码默认 ⊕ 角色卡覆盖。

    ``safety_tail`` 与 ``care_principle`` 永远取代码版本 —— 角色卡无法关闭这两条
    安全约束。其余的 ``tone`` / ``care_voice`` / ``intents`` 等**都可以被卡覆盖**,
    以保证说话方式始终跟着角色走(不跳戏),但"陪着你而不是观察你"这条边界不可关。
    """
    over = {}
    data = getattr(card, "data", None)
    if data is not None:
        raw = data.ext(*CARD_OVERRIDE_PATH, default={})
        if isinstance(raw, dict):
            over = raw
    out = _deep_merge(DEFAULTS, over)
    for locked in _NON_OVERRIDABLE:
        out[locked] = DEFAULTS[locked]
    return out


def active() -> tuple:
    """主动回复/叙事链路的统一入口:一次拿齐 (渲染好的人格, 提示词集)。

    返回 ``(RenderedPersona, prompts_dict)``;无角色卡时人格为回落形态
    (core 为空),调用方需自行决定无人格时怎么办。
    """
    from gzhu_cup2.agent.pet.character import loader, renderer

    resolved = loader.resolve_active()
    return renderer.render_persona(resolved), resolve(resolved.card)


def active_prompts() -> dict:
    """只取当前启用卡解析出的提示词集(不渲染人格)。

    给"只需要意图/语气文案"的调用点用,避免重复加载卡。
    """
    from gzhu_cup2.agent.pet.character import loader

    return resolve(loader.resolve_active().card)


def render_text(text: str, rendered, **extra) -> str:
    """按已渲染的人格替换宏(便捷封装)。"""
    from gzhu_cup2.agent.pet.character import renderer

    return renderer.substitute_macros(
        text, char_name=rendered.char_name, user_name=rendered.user_name, **extra)


def describe_overrides(card=None) -> dict:
    """列出角色卡实际覆盖了哪些键(供调试/接口展示"哪些提示词被改过")。"""
    data = getattr(card, "data", None)
    if data is None:
        return {}
    raw = data.ext(*CARD_OVERRIDE_PATH, default={})
    return raw if isinstance(raw, dict) else {}


def has_override(card, *path: str) -> bool:
    """判断卡是否覆盖了某个键,例如 has_override(card, "intents", "checkin")。"""
    cur: Optional[object] = describe_overrides(card)
    for key in path:
        if not isinstance(cur, dict) or key not in cur:
            return False
        cur = cur[key]
    return cur not in (None, "")
