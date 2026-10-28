# -*- coding: utf-8 -*-
"""用户自述资料(称呼 / 喜欢 / 讨厌)与首启引导的契约。

守四件事:

  1. 三项可读可写,且**写入走统一设置面**(``PATCH /api/settings``) ——
     不新开写接口,才不会出现"设置页改了、另一处还留一份"的双源真相;
  2. 非法值(超长、未知动作)明确报错,**不静默截断**(用户会以为自己填的记住了);
  3. 首启引导:``needed`` → ``seen`` 计入次数(最多 3 次);``skip`` 之后**永不重复询问**;
  4. 这些内容只用于称呼与提示词,不进任何判定通道(见 ``core/user_profile.py`` 的红线)。
"""
import types

import pytest

from gzhu_cup2.agent.core import user_profile as uprofile


@pytest.fixture
def client(monkeypatch):
    from fastapi.testclient import TestClient
    import gzhu_cup2.entrypoints.api as server_mod
    from gzhu_cup2.agent.config import config as cfg
    from gzhu_cup2.agent.web import engine, sessions

    # 关掉夜间自动休眠:与本模块无关,但不关会让档位解析随墙上时间变化
    # (与 test_settings_api.py 同一处理)。
    monkeypatch.setattr(cfg, "PET_NIGHT_SLEEP_ENABLED", False)
    sessions.reset()
    engine.reset()
    return TestClient(server_mod.app)


def _prompt_text() -> str:
    """跑一遍生成节点的提示词构造,拿到 system + human 的合并文本。"""
    from gzhu_cup2.agent.prompts import prompts as fprompts

    state = {
        "messages": [],
        "process_input": "在吗",
        "emotional_analysis": types.SimpleNamespace(emotion="neutral", description=""),
        "risk_level": "low",
        "memory_context": None,
        "crisis_handled": False,
    }
    return "\n".join(str(m.content) for m in fprompts.build_generate_response_prompt(state))


# ============================ 设置面 ============================
class TestSettingsSurface:
    def test_snapshot_has_empty_profile(self, client):
        assert client.get("/api/settings").json()["profile"] == {
            "display_name": "", "likes": "", "dislikes": "",
        }

    def test_patch_writes_all_three(self, client):
        res = client.patch("/api/settings", json={
            "profile.display_name": "小雨",
            "profile.likes": "猫、下雨天",
            "profile.dislikes": "被催",
        })
        assert res.status_code == 200
        assert res.json()["profile"]["display_name"] == "小雨"
        # 同一个事实的另一个读入口(/api/user/profile)必须一致
        assert client.get("/api/user/profile").json()["display_name"] == "小雨"

    def test_name_is_normalized(self, client):
        client.patch("/api/settings", json={"profile.display_name": "  小\n雨  "})
        assert uprofile.display_name() == "小 雨"

    def test_overlong_name_rejected_and_not_written(self, client):
        res = client.patch("/api/settings", json={"profile.display_name": "名" * 21})
        assert res.status_code == 400
        assert "20" in res.json()["detail"]
        assert uprofile.display_name() == "", "被拒绝的值不得落库"

    def test_overlong_list_rejected(self, client):
        assert client.patch("/api/settings", json={"profile.likes": "字" * 301}).status_code == 400

    def test_empty_value_clears_field(self, client):
        client.patch("/api/settings", json={"profile.likes": "猫"})
        client.patch("/api/settings", json={"profile.likes": ""})
        assert uprofile.get_field("likes") == ""

    def test_unknown_profile_key_rejected(self, client):
        res = client.patch("/api/settings", json={"profile.nickname": "x"})
        assert res.status_code == 400
        assert "profile.nickname" in res.json()["detail"]


# ============================ 首启引导 ============================
class TestOnboarding:
    def test_first_launch_needs_prompt(self):
        assert uprofile.onboarding_state()["needed"] is True

    def test_seen_counts_up_to_three(self):
        state = {}
        for i in range(3):
            state = uprofile.mark_onboarding("seen")
            assert state["seen"] == i + 1
        assert state["needed"] is False, "问满 3 次后不再主动询问"

    def test_skip_is_permanent(self, client):
        client.post("/api/user/profile/onboarding", json={"action": "skip"})
        state = uprofile.onboarding_state()
        assert state["dismissed"] is True
        assert state["needed"] is False
        # 旧客户端仍可能上报 seen:跳过之后不得回到 needed
        uprofile.mark_onboarding("seen")
        assert uprofile.onboarding_state()["needed"] is False

    def test_complete_stops_prompting(self):
        assert uprofile.mark_onboarding("complete")["needed"] is False

    def test_bad_action_rejected(self, client):
        assert client.post(
            "/api/user/profile/onboarding", json={"action": "later"}).status_code == 400

    def test_answering_does_not_finish_onboarding(self):
        """填了答案 ≠ 答完引导:三问要一题一题走完(前端按此推进)。"""
        uprofile.set_field("display_name", "小雨")
        assert uprofile.onboarding_state()["needed"] is True


# ============================ 被谁消费 ============================
class TestConsumption:
    def test_user_macro_prefers_profile(self):
        from gzhu_cup2.agent.pet.character import renderer

        uprofile.set_field("display_name", "小雨")
        assert renderer.resolve_user_name() == "小雨"
        # 显式 override 仍最高优先(调用点定向覆盖 / 测试用)
        assert renderer.resolve_user_name(override="借来的名字") == "借来的名字"

    def test_prompt_block_is_empty_when_unset(self):
        assert uprofile.prompt_block() == ""

    def test_prompt_block_lists_three_items(self):
        uprofile.set_field("display_name", "小雨")
        uprofile.set_field("likes", "猫")
        uprofile.set_field("dislikes", "被催")
        block = uprofile.prompt_block()
        assert "小雨" in block and "猫" in block and "被催" in block
        assert "不要主动复述" in block, "稳定信息不该被每轮念一遍"

    def test_prompt_gets_the_block_only_when_filled(self):
        assert "【关于用户" not in _prompt_text()
        uprofile.set_field("likes", "猫")
        assert "【关于用户" in _prompt_text()


# ============================ 红线:不进判定 ============================
class TestDecoupling:
    def test_profile_never_enters_ticks(self):
        """用户自述不是风险信号:写资料不得产生任何日桶记录。"""
        from gzhu_cup2.agent.long_memory import ticks as mticks

        uprofile.set_field("likes", "猫")
        uprofile.set_field("dislikes", "被催")
        assert mticks.list_day_buckets("local_user", days=365) == []


# ============================ 删除要删干净 ============================
class TestPrivacy:
    def test_wipe_clears_profile_content(self, client):
        client.patch("/api/settings", json={"profile.display_name": "小雨"})
        uprofile.set_field("likes", "猫")

        res = client.delete("/api/memory/all?confirm=1")
        assert res.status_code == 200
        assert "user_profile" in res.json()["removed"], "清空记忆必须连自述资料内容一起清"

        assert uprofile.display_name() == ""
        assert uprofile.get_field("likes") == ""

    def test_wipe_keeps_onboarding_so_skip_is_honoured(self, client):
        """清空记忆**不得**让「跳过就不再问」的承诺失效。

        事故同源:clear() 早先是 ``_STATE.reset()``,会把 dismiss 一起抹掉 ——
        用户点过跳过,清一次记忆又被问"我该怎么称呼你",等于没跳过。
        """
        uprofile.set_field("dislikes", "被催")
        uprofile.mark_onboarding("skip")

        res = client.delete("/api/memory/all?confirm=1")
        assert res.status_code == 200

        assert uprofile.get_field("dislikes") == "", "内容仍要删干净"
        state = uprofile.onboarding_state()
        assert state["dismissed"] is True
        assert state["needed"] is False, "跳过过的人不该因为清空记忆被重新问一遍"

    def test_wipe_keeps_completed_state(self, client):
        client.patch("/api/settings", json={"profile.display_name": "小雨"})
        uprofile.mark_onboarding("complete")

        client.delete("/api/memory/all?confirm=1")

        assert uprofile.display_name() == ""
        assert uprofile.onboarding_state()["needed"] is False, "答完过的人不该被重新问一遍"

    def test_wipe_keeps_prompt_count(self, client):
        """``seen`` 是防骚扰计数,不是用户填的内容 —— 清内容不许把它归零。"""
        uprofile.set_field("likes", "猫")
        uprofile.mark_onboarding("seen")
        uprofile.mark_onboarding("seen")

        client.delete("/api/memory/all?confirm=1")

        assert uprofile.onboarding_state()["seen"] == 2
