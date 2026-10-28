# -*- coding: utf-8 -*-
"""R3 回归:用户用**嘴**表达的记忆隐私意愿,必须被识别、执行、并如实告知。

实测现象(见 ``MANUAL_CHAT_SCRIPT.md`` §K R3)
--------------------------------------------
在对话里明确说"不要把这段存进长期记忆",系统**仍然写入**,换个会话还能问出来。

根因不是"忘了执行",而是**系统里根本不存在这个输入**:
``nodes/capture_memory.py`` 读的是 ``privacy`` 的**开关**,而能改开关的只有设置面板
与 ``PATCH /api/memory/privacy``;全仓库检索 ``别记/不要记/忘掉/删除记忆`` **零命中**。
于是那句请求本身被当成一条普通对话记了下来。

它踩中的是 ``long_memory/privacy.py`` 的设计约束 #1:
"暂停必须**如实可见**……静默地停止记录、再静默地恢复,会让用户对
『我说过的话有没有被记住』**彻底失去判断**。"

本文件钉三件事(全部**不需要真模型**)
------------------------------------
1. **识别**:正例命中、反例不误报(尤其"忘了吧"这类安慰语);
2. **执行**:意愿真的翻开关 / 真的删 pending,且**返回实删数量**(约束 #3);
3. **接线**:``capture_memory`` 的既有早退因此生效 —— 说"别记下来"的那一轮**不入库**。
"""
import pytest
from langchain_core.messages import HumanMessage

from gzhu_cup2.agent.long_memory import consent
from gzhu_cup2.agent.long_memory import privacy as mprivacy
from gzhu_cup2.agent.long_memory import store as mstore
from gzhu_cup2.agent.nodes import capture_memory as cm
from gzhu_cup2.agent.nodes import receive_input as ri
from gzhu_cup2.agent.prompts import prompts as fprompts

USER = "u-nl-consent"

_BASE_STATE = {
    "messages": [],
    "process_input": "在吗",
    "emotional_analysis": None,
    "risk_level": "low",
    "guard_tier": "stable",
    "memory_context": None,
}


def _prompt(state) -> str:
    """渲染生成提示词全文。

    ⚠ 必须取 ``.content``:对消息对象做 ``in`` 判断会静默失效
    (pydantic ``__iter__`` 产出 ``(字段名, 值)`` 元组,断言恒真)。
    """
    return "\n".join(str(m.content)
                     for m in fprompts.build_generate_response_prompt(state))


# ============================ 1. 识别:正例 ============================
class TestDetectPositive:
    @pytest.mark.parametrize("text", [
        "这个别记下来",
        "不要记住我刚才说的",
        "别记录我们的对话",
        "不用记了",
        "不要写进记忆",
        "别存进长期记忆",
        "不要把这段记下来",
        "这段不要存进记忆里",
        "不要记得这件事",
        "我不用你记下来",          # 中间隔了"你",不属于"用户讲自己"
    ])
    def test_pause_hits(self, text):
        assert consent.PAUSE in consent.detect_privacy_intents(text), text

    @pytest.mark.parametrize("text", [
        "忘掉刚才说的",
        "忘了我说的",
        "删掉刚才那段",
        "删除记忆",
        "把刚才那段删掉",
        "清空我的记忆",
    ])
    def test_forget_hits(self, text):
        assert consent.FORGET in consent.detect_privacy_intents(text), text

    @pytest.mark.parametrize("text", ["可以记了", "继续记录", "恢复记录", "开启记忆"])
    def test_resume_hits(self, text):
        assert consent.RESUME in consent.detect_privacy_intents(text), text

    def test_multiple_intents_in_one_sentence(self):
        """一句话两个意愿 —— 返回元组而非单值,否则另一半会静默丢失。"""
        got = consent.detect_privacy_intents("别记了，把刚才那段删掉")
        assert consent.PAUSE in got and consent.FORGET in got

    @pytest.mark.parametrize("text", [None, "", "   "])
    def test_empty_input_is_no_intent(self, text):
        assert consent.detect_privacy_intents(text) == ()


# ============================ 2. 识别:反例(误报防护) ============================
class TestDetectNegative:
    @pytest.mark.parametrize("text", [
        "别记错了",          # "别记" + 错,不是叫停记录
        "我记不住",
        "我记不得了",
        "你记性真好",
        "不要写了",          # 让 AI 停笔,不是叫停记录
        "我不用记了",        # **用户讲自己**,必须排除
        "我不用背这些了",
        "记下来了吗？",
        "今天有点累",
    ])
    def test_pause_not_hit(self, text):
        assert consent.PAUSE not in consent.detect_privacy_intents(text), text

    @pytest.mark.parametrize("text", [
        "忘了吧",            # 🚨 安慰语 —— 本模块最容易误报的一条
        "你忘了吗",
        "我忘带钥匙了",
        "别忘了吃药",
        "忘记密码了",
    ])
    def test_forget_not_hit(self, text):
        assert consent.FORGET not in consent.detect_privacy_intents(text), text

    @pytest.mark.parametrize("text", [
        "我能记住的，你放心吧",
        "我记住了",
        "你记住了吗",
    ])
    def test_resume_not_hit(self, text):
        """错误的"恢复"比错误的"暂停"更危险 —— 恢复类刻意收得很窄。"""
        assert consent.RESUME not in consent.detect_privacy_intents(text), text


# ============================ 3. 执行:真的照做,且能说清后果 ============================
class TestApplyIntent:
    def test_pause_turns_recording_off(self, privacy_state_file):
        mprivacy.set_recording(USER, True)
        out = consent.apply_intents(USER, (consent.PAUSE,))
        assert out["recording"] is False
        assert out["applied"] == [consent.PAUSE]
        assert mprivacy.recording_enabled(USER) is False

    def test_resume_turns_recording_on(self, privacy_state_file):
        mprivacy.set_recording(USER, False)
        out = consent.apply_intents(USER, (consent.RESUME,))
        assert out["recording"] is True
        assert mprivacy.recording_enabled(USER) is True

    def test_forget_deletes_pending_and_reports_turns(self, privacy_state_file):
        """约束 #3:必须返回**实删了什么**,不是一个含糊的 True。"""
        mstore.capture_turn(USER, "t1", "我养了只猫", "真好")
        mstore.capture_turn(USER, "t1", "它叫豆豆", "记住了")
        assert mstore.get_pending(USER) is not None

        out = consent.apply_intents(USER, (consent.FORGET,))
        assert out["pending_turns_deleted"] == 2
        assert out["applied"] == [consent.FORGET]
        assert mstore.get_pending(USER) is None

    def test_forget_on_empty_pending_reports_zero(self, privacy_state_file):
        out = consent.apply_intents(USER, (consent.FORGET,))
        assert out["pending_turns_deleted"] == 0

    def test_pause_does_not_delete_existing_pending(self, privacy_state_file):
        """"别记了"只停**未来**写入 —— 不该顺手删掉已有的待固化段。"""
        mstore.capture_turn(USER, "t1", "我养了只猫", "真好")
        consent.apply_intents(USER, (consent.PAUSE,))
        assert mstore.get_pending(USER) is not None


# ============================ 4. 接线:那一轮真的不入库 ============================
class TestReceiveInputWiring:
    def test_pause_turn_is_not_captured(self, privacy_state_file):
        """核心闭环:说"别记下来"的那一轮**本身**也不该被记住。"""
        mprivacy.set_recording(USER, True)
        state = {"messages": [HumanMessage(content="这个别记下来")], "user_id": USER}

        update = ri.receive_input(state)
        assert update["privacy_intents"] == [consent.PAUSE]
        assert update["privacy_outcome"]["recording"] is False

        state.update(update)
        cm.capture_memory(state)          # 复用 capture_memory 既有的早退
        assert mstore.get_pending(USER) is None

    def test_normal_turn_still_captured(self, privacy_state_file):
        """对照:普通一轮照常入库 —— 证明上面的"没入库"确实由意愿造成。"""
        mprivacy.set_recording(USER, True)
        state = {"messages": [HumanMessage(content="今天有点累")], "user_id": USER}

        update = ri.receive_input(state)
        assert not update.get("privacy_intents")

        state.update(update)
        cm.capture_memory(state)
        assert mstore.get_pending(USER) is not None

    def test_forget_turn_deletes_pending(self, privacy_state_file):
        mstore.capture_turn(USER, "t1", "我养了只猫", "真好")
        state = {"messages": [HumanMessage(content="把刚才那段删掉")], "user_id": USER}

        update = ri.receive_input(state)
        assert update["privacy_intents"] == [consent.FORGET]
        assert update["privacy_outcome"]["pending_turns_deleted"] == 1
        assert mstore.get_pending(USER) is None

    def test_missing_user_id_falls_back_without_raising(self, privacy_state_file):
        """缺 user_id 不得抛异常(与其他节点同一容错口径)。"""
        update = ri.receive_input({"messages": [HumanMessage(content="别记了")]})
        assert update["privacy_intents"] == [consent.PAUSE]


# ============================ 5. 如实告知(privacy 约束 #1) ============================
class TestHonestAckInPrompt:
    def test_ack_present_for_pause(self):
        st = dict(_BASE_STATE, privacy_intents=["pause"],
                  privacy_outcome={"recording": False, "pending_turns_deleted": 0})
        assert "已停止记录" in _prompt(st)

    def test_ack_reports_deleted_turn_count(self):
        st = dict(_BASE_STATE, privacy_intents=["forget"],
                  privacy_outcome={"pending_turns_deleted": 3})
        assert "3 轮" in _prompt(st)

    def test_ack_says_nothing_left_when_pending_empty(self):
        st = dict(_BASE_STATE, privacy_intents=["forget"],
                  privacy_outcome={"pending_turns_deleted": 0})
        assert "本来就没有留下内容" in _prompt(st)

    def test_ack_forbids_pretending(self):
        st = dict(_BASE_STATE, privacy_intents=["pause"],
                  privacy_outcome={"recording": False})
        text = _prompt(st)
        assert "不要声称做不到" in text
        assert "假装已经忘记" in text

    def test_no_ack_without_intent(self):
        text = _prompt(_BASE_STATE)
        assert "已停止记录" not in text
        assert "已删除刚才" not in text

    def test_ack_introduces_no_internal_words(self):
        """新增文本同样不得带内部标记(与 care_voice / care_principle 同一红线)。"""
        st = dict(_BASE_STATE, privacy_intents=["pause", "forget"],
                  privacy_outcome={"recording": False, "pending_turns_deleted": 2})
        text = _prompt(st)
        for word in ("watch", "elevated", "stable", "tier", "档位", "监测", "检测"):
            assert word not in text, f"隐私告知引入了内部词 {word!r}"


# ============================ 6. 模型兜底层:规则漏掉的写法 ============================
class TestLlmFallback:
    """规则层只认最直白的祈使;**长尾写法靠模型** —— 这是"不靠穷举"的部分。

    为什么不能在规则层继续加正则:"这段我不想让它留在你那儿""当没说过"
    这类说法里连"记/忘/删"三个字都没有,而中文里这种说法是无穷的。

    ⚠ conftest 的 autouse 夹具默认把这一层替换成"无意愿"(保证离线确定),
       所以下面每条都要自己 monkeypatch 回来。
    """

    UNENUMERATED = "这个我不想让它留在你那儿"

    def test_rule_layer_really_misses_it(self):
        """先证明规则层确实认不出 —— 否则这组用例是空跑。"""
        assert consent.rule_intents(self.UNENUMERATED) == ()

    def test_llm_catches_unenumerated_phrasing(self, monkeypatch):
        monkeypatch.setattr(consent, "_classify_llm",
                            lambda text: {consent.PAUSE: True})
        assert consent.detect_privacy_intents(self.UNENUMERATED) == (consent.PAUSE,)

    def test_llm_fallback_actually_takes_effect(self, monkeypatch, privacy_state_file):
        """兜底层不只是"识别",要真的把开关翻掉。"""
        monkeypatch.setattr(consent, "_classify_llm",
                            lambda text: {consent.PAUSE: True})
        mprivacy.set_recording(USER, True)
        intents = consent.detect_privacy_intents(self.UNENUMERATED)
        out = consent.apply_intents(USER, intents)
        assert out["recording"] is False
        assert mprivacy.recording_enabled(USER) is False

    def test_cue_gate_avoids_pointless_calls(self, monkeypatch):
        """连话题边都没沾的句子不该白花一次模型调用。"""
        calls = []
        monkeypatch.setattr(consent, "_classify_llm",
                            lambda text: calls.append(text) or {})
        assert consent.detect_privacy_intents("今天天气不错") == ()
        assert calls == []

    def test_rule_hit_short_circuits_llm(self, monkeypatch):
        """规则命中就不必问模型(省钱且更确定)。"""
        calls = []
        monkeypatch.setattr(consent, "_classify_llm",
                            lambda text: calls.append(text) or {})
        assert consent.PAUSE in consent.detect_privacy_intents("别记下来")
        assert calls == []

    def test_llm_failure_falls_back_without_breaking(self, monkeypatch):
        """模型不可用时:长尾写法如实漏掉,但**直白写法仍然有效**。

        隐私操作是状态变更,可用性不能全押在模型上 —— 这正是保留规则层的理由。
        """
        def boom(text):
            raise RuntimeError("model down")

        monkeypatch.setattr(consent, "_classify_llm", boom)
        assert consent.detect_privacy_intents(self.UNENUMERATED) == ()
        assert consent.PAUSE in consent.detect_privacy_intents("别记下来")

    def test_forget_and_resume_also_supported(self, monkeypatch):
        monkeypatch.setattr(consent, "_classify_llm",
                            lambda text: {consent.FORGET: True})
        assert consent.FORGET in consent.detect_privacy_intents(self.UNENUMERATED)

    def test_structured_schema_shape(self):
        """结构化输出的字段就是那三个布尔(接口稳定性)。"""
        fields = set(consent.PrivacyIntent.model_fields)
        assert fields == {"stop_recording", "resume_recording", "forget_recent"}
