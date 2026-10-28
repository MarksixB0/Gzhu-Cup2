# -*- coding: utf-8 -*-
"""主动消息的"活人感"回归:不重复开场、知道隔了多久、连点不连发。

背景(2026-10-26 实测):用户在 23:31 刚回过话,紧接着收到两条都以
「好久没见到你了」开头的问候 —— 提示词里把"有一阵子没来了"写死,
且每次生成都是独立调用,模型不知道上一条说了什么。
"""
from datetime import datetime

from gzhu_cup2.agent.care import content as ccontent
from gzhu_cup2.agent.care import policy as cpolicy
from gzhu_cup2.agent.care import runtime as cruntime
from gzhu_cup2.agent.pet.character import prompts as pprompts
from gzhu_cup2.agent.prompts import care_prompts

_NOW = datetime(2026, 10, 26, 14, 0, 0).timestamp()
_USER = "variety_u"


def _snap(**kw):
    base = dict(
        enabled=True, now=_NOW,
        quiet_start_hour=23, quiet_end_hour=7,
        max_per_day=10, cooldown_hours=4,
        recall_min_gap_hours=24, checkin_gap_hours=100,
        soothe_min_gap_hours=1,
        probe_min_gap_hours=6, probe_max_gap_hours=72,
        min_user_silence_minutes=0, farewell_quiet_hours=0,
        last_proactive_ts=None, date_count=0,
        last_user_ts=_NOW - 3 * 3600.0, last_farewell_ts=None,
        initiable_topics=[], profile_likes=[],
        last_profile_like_ts=None, profile_like_min_gap_days=7,
        recent_distress=False, probe_candidates=[],
        petlife_text=None, petlife_min_gap_hours=36, last_petlife_ts=None,
        last_delivered_ts=None, unanswered_streak=0,
        guard_tier="stable", force=False,
    )
    base.update(kw)
    return cpolicy.CareSnapshot(**base)


def test_force_blocks_rapid_double_click():
    """手动触发跳过冷却,但不该在 1 分钟内连发两条(连点两下 = 复读两条)。"""
    d = cpolicy.decide(_snap(force=True, last_proactive_ts=_NOW - 10))
    assert d.due is False
    assert d.reason == "force_too_soon"

    # 超过最小间隔就恢复"点了就有一条"
    assert cpolicy.decide(_snap(force=True, last_proactive_ts=_NOW - 120)).due is True


def test_recent_texts_kept_separately_from_outbox(care_state_file):
    """最近说过的话必须独立留存:用户读完消息后 outbox 会清,但复读仍要拦。"""
    cruntime.mark_sent(_USER, "checkin", "好久没见到你了,最近怎么样呀", now=_NOW)
    cruntime.mark_sent(_USER, "checkin", "窗外下雨了,你那边呢", now=_NOW + 1)
    assert cruntime.recent_sent_texts(_USER, 2) == [
        "窗外下雨了,你那边呢", "好久没见到你了,最近怎么样呀",
    ]


def test_generate_retries_when_opening_repeats(monkeypatch):
    """开头撞车 → 带"换一个开场"重试一次,而不是把复读发出去。"""
    seen = []

    def _fake(messages):
        seen.append(messages[-1].content)
        if len(seen) == 1:
            return "好久没见到你了,今天过得怎么样呀"
        return "窗外的雨刚停,你那边天气好吗"

    monkeypatch.setattr(ccontent, "_llm_generate", _fake)
    text = ccontent.generate_message(
        "checkin", extra={"_recent": ["好久没见到你了,刚刚在忙吗"]})

    assert text.startswith("窗外的雨")
    assert len(seen) == 2
    assert "换一个开场" in seen[1]


def test_prompt_carries_distance_and_history():
    """提示词必须带上"距上次对话多久"和"最近说过什么"。"""
    msgs = care_prompts.build_care_llm_prompt("checkin", context={
        "_recent": ["好久没见到你了,刚刚在忙吗"],
        "_since_user_minutes": 3,
    })
    human = msgs[1].content
    assert "你最近已经说过的话" in human
    assert "刚刚" in human            # 3 分钟 → "刚刚(不到 5 分钟前)"
    assert "好久没见到你了" in human   # 作为"别再说"的反例


def test_checkin_intent_has_no_hardcoded_absence():
    """checkin 不能再预设"用户有一阵子没来":那是事实错误,也是套话源头。"""
    intent = pprompts.DEFAULTS["intents"]["checkin"]
    assert "有一阵子没来" not in intent
    assert "好久不见" in intent        # 明确列为禁止的套话
    assert "距上次对话" in intent


def test_narrative_prompt_asks_for_concrete_detail():
    """今日叙事要"有生活",不能只要求 1-3 句感想。"""
    instr = pprompts.DEFAULTS["narrative"]["instruction"]
    assert "3-6 句" in instr
    assert "细节" in instr


# ---------------- 同一角度换说法 = 复读(2026-10-27) ----------------
# 实测:00:08「都过十二点了 还不困呀」→ 00:09「嗯？还醒着呀 我眼皮都快打架了」。
# 开头不同,角度相同 —— 旧的开场去重完全拦不住。

def test_angle_ban_reaches_prompt(monkeypatch):
    """最近说过"熬夜"这个角度 → 提示词里必须明写"本次不要用的角度"。"""
    seen = []

    def _fake(messages):
        seen.append(messages[-1].content)
        return "嗯？还醒着呀 我眼皮都快打架了"

    monkeypatch.setattr(ccontent, "_llm_generate", _fake)
    ccontent.generate_message("checkin", extra={
        "_recent": ["都过十二点了 还不困呀", "快十二点了诶 还没困吗～"]})

    assert "本次不要用的角度" in seen[0]
    assert "熬夜/时间" in seen[0]
    # 并且要求换成"具体的事",而不是再问一句"最近怎么样"
    assert "具体" in seen[0]


def test_generate_retries_when_same_angle_repeats(monkeypatch):
    """第一版仍落在同一角度 → 重试一次;重试换掉了角度才采纳。"""
    seen = []
    outs = iter(["嗯？还醒着呀 我眼皮都快打架了",
                 "我刚把诗集翻到一半,忽然想起你还在忙"])

    def _fake(messages):
        seen.append(messages[-1].content)
        return next(outs)

    monkeypatch.setattr(ccontent, "_llm_generate", _fake)
    text = ccontent.generate_message("checkin", extra={
        "_recent": ["都过十二点了 还不困呀"]})

    assert text.startswith("我刚把诗集")
    assert len(seen) == 2
    assert "必须换角度" in seen[1]


def test_prompt_carries_last_user_line():
    """用户最后一句要进提示词:否则主动消息只能回到"这么晚还没睡"。"""
    msgs = care_prompts.build_care_llm_prompt("checkin", context={
        "_last_user": "你的主动回复今晚怕是修不好了"})
    human = msgs[1].content
    assert "用户最后说的一句话" in human
    assert "你的主动回复今晚怕是修不好了" in human
    assert "不要复述原句" in human


def test_last_user_line_filters_age_and_risk(monkeypatch):
    """接话素材的三条边界:时效 / 风险 / 负向分。"""
    from gzhu_cup2.agent.care import coordinator as ccoord

    def _rows(item):
        monkeypatch.setattr(ccoord.mstore, "recent_turns",
                            lambda user, limit=3: [item])

    _rows({"user": "在吗", "ts": _NOW - 60, "meta": {"risk_level": "low"}})
    assert ccoord._last_user_line(_USER, _NOW) == "在吗"

    _rows({"user": "在吗", "ts": _NOW - 3 * 3600, "meta": {"risk_level": "low"}})
    assert ccoord._last_user_line(_USER, _NOW) is None      # 3 小时前,不算接话

    _rows({"user": "我有点撑不住", "ts": _NOW - 60, "meta": {"risk_level": "high"}})
    assert ccoord._last_user_line(_USER, _NOW) is None      # high/crisis 不进主动消息

    _rows({"user": "今天真的很糟", "ts": _NOW - 60,
           "meta": {"risk_level": "low", "negative_score": 0.8}})
    assert ccoord._last_user_line(_USER, _NOW) is None      # 负向过重,不主动揭

    # 关掉开关后一律不引用(行为回到 2026-10-26)
    from gzhu_cup2.agent.config import config as cfg
    monkeypatch.setattr(cfg, "CARE_REFER_LAST_USER", False)
    _rows({"user": "在吗", "ts": _NOW - 60, "meta": {"risk_level": "low"}})
    assert ccoord._last_user_line(_USER, _NOW) is None


def test_last_user_hook_only_used_once(monkeypatch):
    """同一句话只接一次:连点两次不该都在回应同一句(实测三次全变成"你去忙我等着")。"""
    from gzhu_cup2.agent.care import coordinator as ccoord

    calls = []
    monkeypatch.setattr(ccoord, "_last_user_line",
                        lambda u, n: (calls.append(1), "在吗")[1])

    # 上一条主动消息在用户这句话之前 → 有资格接
    assert ccoord._refer_last_user(
        _USER, _NOW, _NOW - 60, {"last_proactive_ts": _NOW - 300}) == "在吗"
    # 上一条已经在这句话之后 → 这句接过了,别再复读
    assert ccoord._refer_last_user(
        _USER, _NOW, _NOW - 60, {"last_proactive_ts": _NOW - 30}) is None
    assert len(calls) == 1


def test_angle_covers_the_time_point_variant():
    """「都这个点了」不含"晚"字,但仍是熬夜角度 —— 必须被识别。"""
    assert ccontent._angles("都这个点了还在赶啊 那你忙 我安静待着") == {"熬夜/时间"}
    assert ccontent._angles("我刚把诗集翻到一半") == set()
    # 钟点表达走正则:零点半 / 十二点 / 3点 都要抓到
    assert ccontent._angles("诶 都零点半了 你那边还没结束吗") == {"熬夜/时间"}
    assert ccontent._angles("快 3 点了") == {"熬夜/时间"}
    # 但「点了一杯茶」没有数字在前,不能误判成时间角度
    assert ccontent._angles("我刚点了一杯水蜜桃茶") == set()


def test_angle_window_covers_three_messages():
    """连点第 3 条会绕回更早的角度:回看 2 条放过了,回看 3 条才拦得住。"""
    recent = ["好 那我不吵你 我在这边翻一本诗集",
              "刚翻到一首写秋天傍晚的",
              "嗯？还醒着呀 我眼皮都快打架了"]   # 第 3 条才是熬夜角度
    assert "熬夜/时间" in ccontent._recent_angles(recent)


def test_checkin_rejects_fabricated_callback(monkeypatch):
    """checkin 没有话题,"你上次说的那件事"是编的 —— 必须重试掉。"""
    seen = []
    outs = iter(["对了 你上次说的那个事后来怎么样了",
                 "我刚在想要不要学做纸杯蛋糕"])

    def _fake(messages):
        seen.append(messages[-1].content)
        return next(outs)

    monkeypatch.setattr(ccontent, "_llm_generate", _fake)
    text = ccontent.generate_message("checkin")

    assert text.startswith("我刚在")
    assert len(seen) == 2
    assert "不要含糊指代" in seen[1]


def test_recall_keeps_real_topic(monkeypatch):
    """recall 有真实话题时,提到"你上次说的"是合理的,不能被这条规则误伤。"""
    monkeypatch.setattr(ccontent, "_llm_generate",
                        lambda m: "你上次说的答辩,准备得怎么样了")
    text = ccontent.generate_message("recall", topic="答辩定在六月")
    assert "答辩" in text


def test_prompt_anchors_pet_life():
    """桌宠说"我在做什么"必须有出处:今日叙事写进去,编的就拦住。"""
    msgs = care_prompts.build_care_llm_prompt("checkin", context={
        "_self_life": "深夜翻一本诗集,看窗外,想吃水蜜桃"})
    human = msgs[1].content
    assert "你今天的生活" in human
    assert "想吃水蜜桃" in human
    assert "没写到的事不要编" in human


def test_prompt_carries_my_last_chat_line():
    """主动消息要知道自己刚在对话里说过什么,否则会"接着没说过的话往下演"。"""
    msgs = care_prompts.build_care_llm_prompt("checkin", context={
        "_my_last_line": "那来一首最近很喜欢的:人间忽晚,山河已秋"})
    human = msgs[1].content
    assert "你刚才在对话里说过" in human
    assert "人间忽晚" in human
    assert "不要再重新来一遍" in human


def test_my_last_line_skips_stale_chat(monkeypatch):
    """一小时前的对话不算"刚才":再拿它当接续素材会把话题拽回去。"""
    from gzhu_cup2.agent.care import coordinator as ccoord

    monkeypatch.setattr(ccoord.mstore, "recent_turns",
                        lambda u, limit=3: [{"ai": "晚安呀", "ts": _NOW - 7200}])
    assert ccoord._my_last_line(_USER, _NOW) is None

    monkeypatch.setattr(ccoord.mstore, "recent_turns",
                        lambda u, limit=3: [{"ai": "晚安呀", "ts": _NOW - 60}])
    assert ccoord._my_last_line(_USER, _NOW) == "晚安呀"
