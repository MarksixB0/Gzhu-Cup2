# -*- coding: utf-8 -*-
"""桌宠域(pet)+ petlife 主动消息离线测试(不触网)。

覆盖:pet 运行时开关与最近生成日 / 每日叙事懒生成幂等与 **LLM 失败不产文本** /
petlife 文案安全 / policy petlife 分支 / runtime 记录 petlife 时间。
(角色卡的结构校验与回落语义见 test_character_card.py;旧的 pet.cards 已裁撤。)
"""
import time

from gzhu_cup2.agent.pet import runtime as pruntime
from gzhu_cup2.agent.pet import narrative as pnarrative
from gzhu_cup2.agent.care.policy import CareSnapshot, decide

USER = "u-pet"
HOUR = 3600.0


# pet 运行时状态隔离夹具(pet_state_file)已统一收到 tests/conftest.py。


# ============================ pet 运行时 ============================
class TestPetRuntime:
    def test_enabled_default_and_toggle(self, pet_state_file):
        assert pruntime.daily_enabled(USER) is True
        pruntime.set_daily_enabled(USER, False)
        assert pruntime.daily_enabled(USER) is False

    def test_mark_generated_idempotent(self, pet_state_file):
        d1 = pruntime.mark_generated(USER, "2026-01-01")
        d2 = pruntime.mark_generated(USER, "2026-01-01")
        assert d1 == d2 == "2026-01-01"

    def test_approach_prefs_default_and_clamp(self, pet_state_file):
        """靠近偏好:默认开/10 分钟;间隔夹取 1~120;关闭后保持。"""
        prefs = pruntime.approach_prefs(USER)
        assert prefs == {"approach_enabled": True, "approach_interval_min": 10}
        pruntime.set_approach(USER, interval_min=999)
        assert pruntime.approach_prefs(USER)["approach_interval_min"] == 120
        pruntime.set_approach(USER, interval_min=0)
        assert pruntime.approach_prefs(USER)["approach_interval_min"] == 1
        pruntime.set_approach(USER, enabled=False)
        got = pruntime.approach_prefs(USER)
        assert got["approach_enabled"] is False and got["approach_interval_min"] == 1
        # mode_status 一并携带(桌面端 15s 轮询的同一入口)
        status = pruntime.mode_status(USER)
        assert status["approach_enabled"] is False


# ============================ 每日叙事 ============================
class TestDailyNarrative:
    def test_ensure_once_per_day_and_idempotent(self, pet_state_file, monkeypatch):
        monkeypatch.setattr(pnarrative, "_llm_generate",
                            lambda refs: "今天我晒了太阳，尾巴尖都暖了。")
        first = pnarrative.ensure_today(USER, now=time.time())
        assert first is not None and first["source"] == "llm"
        assert "太阳" in first["text"]
        # 同一天再次 ensure → 幂等返回同一叙事,不重复生成/不覆盖
        again = pnarrative.ensure_today(USER, now=time.time())
        assert again["text"] == first["text"]
        items = pnarrative.list_recent(USER, days=7)
        assert len(items) == 1

    def test_llm_failure_produces_no_text_and_does_not_retry(self, pet_state_file, monkeypatch):
        """LLM 失败**不再回落确定性模板**(用户裁定 2026-09,见 PET_POLICY.md 第四节)。

        该日叙事记为 unavailable:不产生任何文本,但同样落库 + 标记当日已尝试,
        避免关怀调度线程每 60s 重试一次 LLM。
        """
        calls = []

        def _boom(refs):
            calls.append(refs)
            raise RuntimeError("llm unavailable")

        monkeypatch.setattr(pnarrative, "_llm_generate", _boom)
        item = pnarrative.ensure_today(USER, now=time.time())
        assert item is not None
        assert item["source"] == "unavailable"
        assert item["text"] is None
        # 当日再次 ensure 必须幂等,不得再打一次 LLM
        again = pnarrative.ensure_today(USER, now=time.time())
        assert again["source"] == "unavailable"
        assert len(calls) == 1

    def test_empty_llm_output_also_unavailable(self, pet_state_file, monkeypatch):
        monkeypatch.setattr(pnarrative, "_llm_generate", lambda refs: "   ")
        item = pnarrative.ensure_today(USER, now=time.time())
        assert item["source"] == "unavailable" and item["text"] is None

    def test_disabled_user_returns_none(self, pet_state_file):
        pruntime.set_daily_enabled(USER, False)
        assert pnarrative.ensure_today(USER) is None


# ============================ petlife 文案(提示词注入) ============================
class TestPetlifeContent:
    def test_petlife_prompt_injects_narrative(self):
        # petlife 叙事文本现在作为上下文注入 prompts/care_prompts 的提示词
        from gzhu_cup2.agent.prompts.care_prompts import build_care_llm_prompt
        msgs = build_care_llm_prompt("petlife", context={"topic": "晒了一下午太阳"})
        human = msgs[1].content
        assert "晒了一下午太阳" in human

    def test_petlife_prompt_no_internal_tokens(self):
        from gzhu_cup2.agent.prompts.care_prompts import build_care_llm_prompt
        forbidden = ("PHQ", "GAD", "C-SSRS", "量表", "分数", "等级", "风险", "检测", "热线")
        for narrative in ("晒了一下午太阳", "今天我路过面包店"):
            msgs = build_care_llm_prompt("petlife", context={"topic": narrative})
            human = msgs[1].content
            for token in forbidden:
                assert token not in human, f"petlife 提示词泄漏内部标记({narrative}): {human}"

    def test_warm_prompt_includes_concrete_present_matter(self):
        """warm 意图必须给出**当下具体的**事,而不是"我观察到你的变化"。

        这是一条边界回归锁:warm 是最容易滑成"描述用户"的一类主动消息
        (它由守护档位 + 低落收尾共同触发),所以这里同时钉住两件事 ——
        ① 具体事确实被注入(不空洞);② 提示词明令禁止"观察结论"式说法。
        """
        from gzhu_cup2.agent.prompts.care_prompts import build_care_llm_prompt
        msgs = build_care_llm_prompt("warm", context={"mood_down": True, "quieter": True})
        human = msgs[1].content
        # ① 具体、当下的事
        assert "没什么精神" in human
        assert "话也短一些" in human
        assert "【Sayori想说的具体事】" in human or "想说的具体事" in human
        # ② 明令禁止把关心说成对用户的观察
        assert "我注意到你最近" in human      # 作为反例出现在禁令里
        assert "严禁" in human

    def test_warm_observations_are_not_trajectory_claims(self):
        """默认观察短语不得写成"统计用户长期轨迹"的话(那会带来被监控感)。"""
        from gzhu_cup2.agent.pet.character import prompts as pprompts
        obs = pprompts.DEFAULTS["observations"]
        for text in obs.values():
            for bad in ("次数", "越来越少", "最近一直", "这段时间", "我注意到"):
                assert bad not in text, f"观察短语像在统计用户:{text!r}"


# ============================ policy:petlife 分支 ============================
def snap(**over):
    base = dict(
        enabled=True, now=time.time(), quiet_start_hour=0, quiet_end_hour=0,
        max_per_day=2, cooldown_hours=4,
        recall_min_gap_hours=24, checkin_gap_hours=48,
        soothe_min_gap_hours=1,
        probe_min_gap_hours=6, probe_max_gap_hours=72,
        last_proactive_ts=None, date_count=0, last_user_ts=None,
        initiable_topics=[], recent_distress=False, probe_candidates=[],
        petlife_text=None, petlife_min_gap_hours=36, last_petlife_ts=None,
    )
    base.update(over)
    return CareSnapshot(**base)


class TestDecidePetlife:
    def test_petlife_when_never_sent_and_text_present(self):
        d = decide(snap(petlife_text="今天我晒了太阳。", last_user_ts=time.time() - 1 * HOUR))
        assert d.due is True and d.kind == "petlife"

    def test_petlife_respects_min_gap(self):
        now = time.time()
        d = decide(snap(petlife_text="今天我晒了太阳。",
                        last_user_ts=now - 1 * HOUR,
                        last_petlife_ts=now - 2 * HOUR))  # 距上一条不足 36h
        assert d.due is False or d.kind != "petlife"

    def test_petlife_after_gap_ok(self):
        now = time.time()
        d = decide(snap(petlife_text="今天我晒了太阳。",
                        last_user_ts=now - 1 * HOUR,
                        last_petlife_ts=now - 40 * HOUR))
        assert d.due is True and d.kind == "petlife"

    def test_no_text_no_petlife(self):
        d = decide(snap(last_user_ts=time.time() - 1 * HOUR))
        assert d.due is False or d.kind != "petlife"


# ============================ runtime:petlife 时间记录 ============================
class TestRuntimePetlife:
    def test_mark_sent_petlife_records_ts(self, care_state_file):
        from gzhu_cup2.agent.care import runtime as cruntime
        cruntime.mark_sent(USER, "petlife", "今天我晒了太阳。\n你呢？")
        c = cruntime.counters(USER)
        assert c["last_petlife_ts"] is not None
