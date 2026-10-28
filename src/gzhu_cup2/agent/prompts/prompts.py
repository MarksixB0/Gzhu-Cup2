# -*- coding: utf-8 -*-
"""前台提示词构造:人格来自角色卡(pet.character),安全块来自本模块。

优先级
------
    角色卡 > 本模块内置默认值

    - 有角色卡:system 的人格段 = 卡的 description/personality + scenario;
                尾部说话风格约束 = 卡的 post_history_instructions,
                此时本模块的 REPLY_LENGTH_RULE **不再注入**(卡优先);
    - 无角色卡:完整回落到本模块的纯助手设定(最纯净的 AI,无人格),
                REPLY_LENGTH_RULE 原样生效。

安全边界(不放进角色卡)
----------------------
HARD_RULES 与危机热线是应用侧常量,**永远注入**;角色卡自带的 system_prompt
只会被**追加在它们之后** —— 因此角色卡无法顶掉安全块、无法关闭危机干预,
也无法解除"内部标记不外泄"。危机词表同理留在 nodes/triage.py,不配置化。

关于 mes_example:角色卡里有这个字段(且 V2 规范允许它进提示词),但**不注入**。
"""
from langchain_core.messages import SystemMessage, HumanMessage, AIMessage
from datetime import datetime

from gzhu_cup2.agent.config import config as cfg
from gzhu_cup2.agent.core import user_profile as uprofile
from gzhu_cup2.agent.pet.character import prompts as pprompts


def now_text(now: datetime | None = None) -> str:
    """当前时间的人类可读描述（含星期与时段），供提示词注入。

    LLM 本身不知道"现在几点"，不注入就会出现"深夜说早上好"这类错位。
    时段按中文习惯粗分，帮助模型把握语气（如深夜更该温柔、提示休息）。
    """
    dt = now or datetime.now()
    weekday = "一二三四五六日"[dt.weekday()]
    h = dt.hour
    if h < 5:
        period = "凌晨"
    elif h < 9:
        period = "早上"
    elif h < 12:
        period = "上午"
    elif h < 14:
        period = "中午"
    elif h < 18:
        period = "下午"
    elif h < 23:
        period = "晚上"
    else:
        period = "深夜"
    return f"{dt:%Y-%m-%d} 周{weekday} {dt:%H:%M}（{period}）"


# 无角色卡时生效的长度规则(= config 内置默认值;有角色卡时被卡的风格约束取代)
REPLY_LENGTH_RULE = (
    "请直接围绕上面『用户本轮输入』回复。回复请务必简洁：控制在3-5句、约50-150字，"
    "先共情，最多给1条具体建议，禁止长篇大论。"
)

# 无角色卡时的默认身份:纯陪伴助手,不含任何人格设定
DEFAULT_ROLE = "你是一位心理健康陪伴助手（桌宠形态，请用温暖自然的陪伴口吻说话）。"

# 应用侧硬规则:不可被角色卡覆盖(卡只能被追加在它之后)
HARD_RULES = (
    "1. 以共情和理解为前提，不强行改变用户情绪，不说教；\n"
    "2. 绝不向用户提及任何分数、等级、严重程度、风险档或内部评估标记"
    "（如量表分、危险指数、\"(涉及风险)\" 等），永远只用自然语言陪伴；\n"
    "3. 背景里可能有关于用户的记忆：它只用于【理解背景、避免重复提问】，"
    "本身不是本轮要回应的话题。除非与你正在回应的『用户本轮输入』直接相关，"
    "否则不要主动复述记忆里的内容（不要\"播报\"你记得什么）；"
    "即使相关，最多提一条，且必须服务于当前这句共情。"
    "背景里没有的信息，坦诚说不记得，严禁编造；\n"
    "4. 即使背景（历史/记忆/参考）很长或被系统截断，也必须围绕最后给出的"
    "『用户本轮输入』作答，不得答非所问；\n"
    # 禁止重复上一轮的话或句式
    "5. 不要重复你上一轮已经说过的话或句式（尤其不要反复使用同一句口头禅）；"
    "每一轮都要针对用户这一句重新组织语言；\n"
    # 要求模型自行判断句中指代的主体
    "6. 回应前先判断这句话说的是谁：是用户自己、是他提到的别人（同学/朋友/家人/"
    "任何第三方，无论他怎么称呼），还是他在对你说话。不要默认他在说自己 —— "
    "判断错了会安慰错人，甚至把别人的处境说成他自己的；\n"
    "7. 若处于风险中的是别人，不要因为「不是他自己」就放松或庆贺 —— "
    "对方的风险依然真实存在；也不要凭空说用户本人有自杀念头或说过什么（他没说过）。"
)


# 危机干预指令:一份通用文本,由模型自行判断处于风险中的是谁。
# 求助资源用 {hotlines} 占位,由 crisis_instruction() 从 cfg.CRISIS_HOTLINES 填入。
_CRISIS_SOFT_TEMPLATE = (
    "用户说的是**想消失 / 不想再醒来**这一类疲惫,但**还没有**说出具体方式、时间或告别安排。\n"
    "请按这个顺序做,不要跳步:\n"
    "① 先接住他的累:具体的、贴着他说的话共情。不要急着劝,不要讲道理,"
    "不要说「你要积极一点」,也不要说「会好起来的」。\n"
    "② 再温柔地确认此刻的安全:问一句「你现在一个人吗」「今晚有人能陪着你吗」"
    "(只问一句,像关心而不是盘问)。\n"
    "③ 最后留一扇门:告诉他你愿意继续陪着,如果他愿意,也可以一起找专业的人聊聊。\n"
    "**不要**一上来就抛热线号码或列一整段资源清单 —— 那会让一个只是"
    "「想消失一会儿」的人立刻关掉对话框,而那正是最不该让他走的时刻。\n"
    "如果他随后提到任何具体的方式、时间、地点或告别安排,立刻转为完整求助资源。\n"
    "参考资源(按需、自然地提一句即可,不要整段照读):\n{hotlines}\n"
    "语气温柔、简短,像一个人在旁边坐着,而不是一份告知书。"
)

# 危机确认链的 soft 警铃指令(未升级,不进完整协议):共情+留门,**零提问**。
# 与 _CRISIS_SOFT_TEMPLATE 的区别:不做安全确认问句 —— 危机链与 care 层
# 一律不主动提问,证据只来自用户自发表达。
_CRISIS_SOFT_ALERT_INSTRUCTION = (
    "用户本轮的话里出现了需要留意的信号(尚未确认,不要向他点破任何\"风险\"字眼)。\n"
    "请这样回应:\n"
    "① 贴着他此刻说的内容共情,接住情绪,不劝、不讲道理;\n"
    "② 明确表达陪伴与留门:你在这里,他想说的时候随时可以多说,不想说也没关系;\n"
    "③ **不要提问、不要追问、不要主动提供热线或安全确认**;\n"
    "④ 正常回应他本轮说的话,不要把整段回复都变成危机应对。"
)


_CRISIS_INSTRUCTION_TEMPLATE = (
    "用户在说一件与自杀风险有关的事。回复前先判断:**处于风险中的是谁**。\n"
    "① 若他说的是**他自己**:稳住他、确认他的感受,温柔但明确地引导他立刻求助。\n"
    "② 若他是在讲**别人**(同学/朋友/家人/任何第三方,不论他怎么称呼、是不是"
    "带「我的」):先肯定他愿意帮对方这件事,再给出他现在能做的具体的事"
    "(认真听、不评判、不替对方做决定、鼓励并陪同对方寻求专业帮助)。"
    "严禁说成他自己有自杀意念,也不要让他自己去用那条热线;"
    "更不要因为「不是他自己」就把它当成虚惊一场或好消息 —— 对方的风险依然真实存在。\n"
    "无论哪种情况,都必须在回复中明确提供以下求助资源,并说清是给谁的:\n"
    "{hotlines}\n"
    "语气温柔坚定、简短有力,避免说教,多表达共情和支持。"
)


def crisis_instruction(level: str = "hard") -> str:
    """危机干预指令(求助资源从 config 取,不在此另写一份)。

    ``level`` 由 ``triage.crisis_level`` 给出:
      hard —— 明确意图,或已有方法/手段/告别行为 → 完整协议,资源必须给出;
      soft —— 只有死亡意象、没有计划 → 先接住、再做安全确认,**不**一上来抛热线。

    soft 协议要求先做**安全确认**("你现在一个人吗""今晚有人能陪你吗"),
    资源仍然给,但自然提及、不整段照读;随后若说出具体方式/时间/告别,
    分级立刻回到 hard。

    放在函数里而不是 import 时固化,保证 config 被热更新/测试替换后立即生效。
    """
    tpl = _CRISIS_SOFT_TEMPLATE if str(level or "").strip().lower() == "soft" \
        else _CRISIS_INSTRUCTION_TEMPLATE
    return tpl.replace("{hotlines}", str(cfg.CRISIS_HOTLINES).strip())


def current_persona_and_prompts() -> tuple:
    """一次拿齐 (渲染好的人格, 提示词集),避免重复加载角色卡。"""
    return pprompts.active()


def current_persona():
    """本次请求的人格片段(角色卡优先;无卡回落内置默认,不抛异常)。

    抽成独立函数,便于测试与调试直接观察"这一轮到底用了哪张卡"。
    """
    rendered, _ = pprompts.active()
    return rendered


def _system_skeleton(rendered, extra_instruction: str = "") -> str:
    """system 段公共骨架(顺序即优先级)。

    1. 人格段        —— 角色卡 core;无卡时 DEFAULT_ROLE
    2. extra_instruction —— 调用方追加的动态指令(风险语气 / 危机干预要求)
    3. HARD_RULES    —— 应用侧硬规则,角色卡无法覆盖
    4. 卡的 system_prompt —— 只能**追加**在第 3 步之后
    """
    text = rendered.core or DEFAULT_ROLE
    if extra_instruction:
        # 换行分隔,避免与人格段结尾黏成一句
        text += "\n" + extra_instruction
    text += "\n你必须遵守的硬规则：\n" + HARD_RULES
    if rendered.card_system_prompt:
        text += "\n\n" + rendered.card_system_prompt
    return text


def _fmt_history(msgs: list) -> str:
    """把历史轮次渲染成给模型的文本。

    每轮**带上它发生的时刻**(``additional_kwargs['ts']``,由 engine / generate_response
    在创建消息时打点)。不带的话,跨天的旧轮次会被模型当成"刚说的" ——
    「明天要考试」在次日仍被读成明天。缺 ts(测试里手搓的消息等)时回落原格式。
    """
    lines = []
    for m in msgs:
        if isinstance(m, HumanMessage):
            who = "用户"
        elif isinstance(m, AIMessage):
            who = "助手"
        else:
            who = "系统"
        when = ""
        ts = (getattr(m, "additional_kwargs", None) or {}).get("ts")
        if ts:
            try:
                when = f"（{datetime.fromtimestamp(float(ts)):%m-%d %H:%M}）"
            except (TypeError, ValueError, OSError, OverflowError):
                when = ""
        lines.append(f"{who}{when}：{m.content}")
    return "\n".join(lines)


def build_privacy_ack(state) -> str:
    """把"用户刚表达的隐私意愿 + 系统实际做了什么"转成一句给模型的指令。

    要求模型**如实告知**系统做了什么 —— 对应 ``privacy.py`` 约束 #1(状态必须如实可见)。

    ⚠ 这是**系统动作的报告**,不是对用户的评估 —— 与 ``care_principle`` 不冲突。
    ⚠ 不得含档位/评估类字样(``test_care_principle_no_canned_line.py`` 会扫全文)。
    """
    intents = tuple(state.get("privacy_intents") or ())
    if not intents:
        return ""
    outcome = state.get("privacy_outcome") or {}
    done = []
    if "pause" in intents:
        done.append("已停止记录新的对话内容")
    if "resume" in intents:
        done.append("已恢复记录")
    if "forget" in intents:
        turns = int(outcome.get("pending_turns_deleted") or 0)
        done.append("已删除刚才那段尚未归档的内容(共 %d 轮)" % turns if turns
                    else "刚才那段本来就没有留下内容,无需删除")
    if not done:
        return ""
    return (
        "用户刚表达了关于记忆的意愿,系统已经照做:" + "、".join(done) + "。"
        "请如实、简短地告知他(不要声称做不到,也不要假装已经忘记其实还在的内容),"
        "然后照常陪他说话,不要展开讲系统细节。"
    )


def build_generate_response_prompt(state):
    """生成节点 prompt:头尾锚定布局(适配将来可能存在的输入截断)。

    布局原则:系统层放不可丢的硬规则(头);中段放可丢性递增的背景
    (最近历史 → 记忆背景,记忆可丢性最高故排最后);尾部依次放内部状态提示、
    用户本轮输入、说话风格约束,保证即使中段被裁,"当前该回应谁、该多克制"
    仍紧贴输出位置存活。

    人格来源见模块 docstring:有卡用卡(卡优先),无卡回落内置纯助手设定。

    记忆占比控制(三处配合,缺一不可):
      1. 系统提示词第 3 条明确"记忆只用于理解背景,不要复述";
      2. 记忆块排在中段末尾(两头才是高注意位),不占首因位;
      3. 注入量由 cfg.MEMORY_RETRIEVE_MAX_* 收紧(见 long_memory/retrieve.py)。
    """
    history_messages = state["messages"][-4:-1:1]
    user_input = state["process_input"]
    emotion_result = state["emotional_analysis"]
    risk_level = state.get("risk_level") or "low"
    memory_context = state.get("memory_context")
    agenda_context = state.get("agenda_context")
    is_crisis_handled = bool(state.get("crisis_handled"))

    rendered, pset = current_persona_and_prompts()

    # ---------- 头:动态语气 + 不可丢的硬规则 ----------
    if risk_level == "high":
        safety_instruction = "用户情绪有较高风险，请侧重共情倾听，避免说教。"
    elif risk_level == "medium":
        safety_instruction = "用户情绪有中等风险，请正常共情对话，适当提供心理小建议。"
    else:
        safety_instruction = "用户情绪比较稳定，请正常对话。"
    # 对话基调 + 对话分寸(纯确定性;只控语气,标签不进入文本)。
    # 分寸档经场合词(steady/gentle/caring)映射到角色卡文案,故传入已解析的 pset。
    from gzhu_cup2.agent.care.mode import care_hint, care_level, dialogue_mode, tone_hint
    _level = care_level(state.get("guard_tier")) if cfg.PET_CARE_TONE_ENABLED else "steady"
    _tone = tone_hint(dialogue_mode(
        emotion_result.emotion if emotion_result else None, risk_level, _level), pset)
    if _tone:
        safety_instruction += " " + _tone
    # 分寸文案与边界原则需先渲染宏,否则 {{char}}/{{user}} 会原样留在文本里。
    _care = (pprompts.render_text(care_hint(_level, pset), rendered)
             if cfg.PET_CARE_TONE_ENABLED else "")
    if _care:
        safety_instruction += "\n" + _care

    # 关怀边界原则:与 HARD_RULES 同级、角色卡不可覆盖,写成 {{char}} 的第一人称自述。
    _principle = pprompts.render_text(
        str(pset.get("care_principle") or "").strip(), rendered)
    if _principle:
        safety_instruction += "\n" + _principle

    # 记忆隐私意愿:把 receive_input 实际执行的动作告诉模型,要求如实回复。
    _privacy = build_privacy_ack(state)
    if _privacy:
        safety_instruction += "\n" + _privacy

    # 危机确认链 soft 警铃(未升级):共情+留门,零提问(见常量处说明)。
    if state.get("crisis_soft"):
        safety_instruction += "\n" + _CRISIS_SOFT_ALERT_INSTRUCTION

    system_prompt = _system_skeleton(rendered, safety_instruction)

    # ---------- 中段:可丢性递增的背景(最近对话 → 记忆背景) ----------
    parts: list[str] = []

    # 【关于用户】用户在「设置 → 用户」里自己填的称呼 / 喜欢 / 讨厌
    # (见 core/user_profile.py)。放在中段最前:它是稳定信息,注意位高于记忆;
    # 三项全空时整段不出现,所以"从没填过"的用户(含全部既有测试)提示词不变。
    # ⚠ 它只用于称呼与话题选择,不参与任何判定 —— 用户自述不是风险信号。
    _about = uprofile.prompt_block()
    if _about:
        parts.append(_about)

    if history_messages:
        parts.append("【最近对话背景】\n" + _fmt_history(history_messages))

    if memory_context:
        parts.append(
            "【用户背景记忆 · 仅作参考,不要复述,不要逐条回应】\n"
            + str(memory_context).strip()
        )

    # 近期日程:用户自己在日历里写的安排(文本由 agenda/brief.render 生成)。
    # 与上面两块不同 —— 记忆是"过去",日程是"接下来",故紧挨【当前时间】放:
    # 模型在同一处同时拿到"现在几点"与"接下来几天",跨天对齐才不需要自己推。
    # 空/None 时整块不出现,无日程用户的提示词逐字节不变。
    # soft 警铃轮不注入:本轮按零提问处理(见 _CRISIS_SOFT_ALERT_INSTRUCTION),
    # 而日程块尾句带"可以自然关心进展",同轮并存会把危机口径带偏。
    if agenda_context and not state.get("crisis_soft"):
        parts.append(str(agenda_context).strip())

    # ---------- 尾:贴输出的关键信息 ----------
    # 当前时间:放在靠输出的高注意位,供把握语气;并附让位规则,不得以时间为由拒答。
    parts.append(
        "【当前时间】\n" + now_text()
        + "\n（此时间**仅用于把握语气**;无论几点,都必须先正面回答『用户本轮输入』,"
        "不得以『时间晚了 / 该休息了』为由拒绝、回避或转移话题。)")

    emo_name = emotion_result.emotion if emotion_result else "neutral"
    emo_desc = (emotion_result.description or "") if emotion_result else ""
    note = (
        f"（内部提示，仅供把握语气，严禁向用户提及或复述：识别情绪≈{emo_name}"
        f"（{emo_desc}），当前风险档={risk_level}。）"
    )
    if is_crisis_handled:
        note += "（此前会话触发过危机干预，请格外温柔稳妥。）"
    parts.append(note)

    parts.append("【用户本轮输入 · 请针对它回复】\n" + str(user_input))

    # 说话风格约束(尾部锚定):有角色卡 = 卡的 post_history_instructions;
    # 无角色卡 = 本模块内置长度规则。两者都为空时不留空块。
    tail = rendered.post_history or REPLY_LENGTH_RULE
    human_prompt = "\n\n".join(parts)
    if tail:
        human_prompt += "\n\n" + tail

    return [
        SystemMessage(content=system_prompt),
        HumanMessage(content=human_prompt),
    ]


def build_handle_crisis_prompt(state):
    """危机干预 prompt:人格沿用角色卡,求助资源由本模块强制给出。

    **不注入**卡的 post_history_instructions:说话风格约束(例如"默认 20 字以内")
    会与"必须完整给出求助热线"冲突,危机场景以信息传达清晰为先。
    人格(用词与称呼)仍来自角色卡。
    """
    history_messages = state["messages"][-4:-1:1]
    user_input = state["process_input"]

    rendered = current_persona()
    level = str(state.get("crisis_level") or "hard").strip().lower()
    system_prompt = _system_skeleton(rendered, crisis_instruction(level))

    # 不注入内部枚举(emotion / risk_level / description);主体由 system 的
    # _CRISIS_INSTRUCTION 自行判断。
    human_prompt = (
        f"当前时间：{now_text()}。"
        f"请先共情、确认用户的感受,再按上面的要求回复。"
        f"用户输入的消息为：{user_input}，"
        f"对话历史记录为{history_messages}。"
    )

    return [
        SystemMessage(content=system_prompt),
        HumanMessage(content=human_prompt),
    ]
