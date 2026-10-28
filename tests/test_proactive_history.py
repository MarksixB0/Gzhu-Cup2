# -*- coding: utf-8 -*-
"""主动消息落进会话历史(不再只是一个 outbox 队列)。

对应 PET_POLICY.md 第四节:桌宠主动说的话**就是聊天记录的一部分** ——
outbox 只负责"读过没有"(未读角标),内容必须持久化在会话里,刷新/切会话都不丢。

这里钉死四个语义:
  1. 投递会写进会话历史的轮次,并带上 kind="proactive" / care_kind / care_msg_id;
  2. 没有会话时新建一条,标题取自**角色卡里的角色名**(不写死);
  3. 投递挂在"最近更新的会话"上;
  4. 空文本不投递;普通对话轮次的 kind 为 "chat"。
"""
import time

import pytest

from conftest import TEST_CARD_NAME


@pytest.fixture
def client(api_client):
    """复用 conftest 的 api_client:连假 LLM,并每用例清空会话记录与图单例。"""
    return api_client


def _msg(text="好久没见你啦", kind="checkin", mid="m1"):
    return {
        "id": mid, "user": "local_user", "kind": kind,
        "topic": None, "text": text, "created_ts": time.time(),
    }


class TestProactiveDelivery:
    def test_delivery_lands_in_thread_history(self, client):
        from gzhu_cup2.agent.web import sessions
        sessions.deliver_proactive(_msg())

        threads = client.get("/api/threads").json()
        assert len(threads) == 1
        turns = client.get(f"/api/threads/{threads[0]['id']}").json()["turns"]
        assert len(turns) == 1
        turn = turns[0]
        assert turn["kind"] == "proactive"
        assert turn["user"] == ""            # 桌宠自己开口,没有用户那一侧
        assert turn["ai"] == "好久没见你啦"
        assert turn["care_kind"] == "checkin"
        assert turn["care_msg_id"] == "m1"   # 前端据此标记已读

    def test_new_thread_title_comes_from_character_card(self, client, test_card):
        """新建会话的标题取角色卡里的角色名 —— 不写死在代码里。

        卡由 conftest 的 test_card 夹具现造(仓库里不再带任何预设人格)。
        """
        from gzhu_cup2.agent.web import sessions
        sessions.deliver_proactive(_msg())
        assert client.get("/api/threads").json()[0]["title"] == TEST_CARD_NAME

    def test_delivery_goes_into_latest_thread(self, client):
        from gzhu_cup2.agent.web import sessions
        a = client.post("/api/threads").json()["thread_id"]
        b = client.post("/api/threads").json()["thread_id"]
        # _now() 只有秒级精度,同一秒建的两条会话无法区分先后 → 显式抬高 B
        sessions.get(b).updated_at = "2099-01-01 00:00:00"

        sessions.deliver_proactive(_msg(text="在的呀"))

        assert client.get(f"/api/threads/{a}").json()["turns"] == []
        turns_b = client.get(f"/api/threads/{b}").json()["turns"]
        assert len(turns_b) == 1 and turns_b[0]["ai"] == "在的呀"

    def test_blank_text_is_not_delivered(self, client):
        from gzhu_cup2.agent.web import sessions
        sessions.deliver_proactive(_msg(text="   "))
        sessions.deliver_proactive({})
        assert client.get("/api/threads").json() == []

    def test_foreign_user_message_is_not_delivered(self, client):
        """别的用户(多用户 / 测试数据)的主动消息不得落进当前用户的会话。

        背景:后台调度按"所有有记忆的用户"逐个评估,而会话是单用户的全局对话流 ——
        实测 8 个 eval_* 用户各发一条 petlife,会把当前用户的会话刷屏。
        """
        from gzhu_cup2.agent.web import sessions
        sessions.deliver_proactive({**_msg(), "user": "eval_stable"})
        assert client.get("/api/threads").json() == []

    def test_delivery_bumps_thread_to_top(self, client):
        """投递后该会话应成为最近会话(前端角标靠这个跳转)。"""
        from gzhu_cup2.agent.web import sessions
        a = client.post("/api/threads").json()["thread_id"]
        sessions.get(a).updated_at = "2000-01-01 00:00:00"
        sessions.deliver_proactive(_msg())
        assert client.get("/api/threads").json()[0]["id"] == a


class TestChatTurnKind:
    def test_chat_turn_carries_kind_chat(self, client):
        """普通对话轮次显式带 kind="chat",前端据此区分两侧渲染。"""
        r = client.post("/api/chat", json={"message": "你好"})
        assert r.status_code == 200
        turns = client.get(f"/api/threads/{r.json()['thread_id']}").json()["turns"]
        assert turns[0]["kind"] == "chat"
        assert turns[0]["user"] == "你好"

    def test_proactive_and_chat_coexist_in_one_thread(self, client):
        from gzhu_cup2.agent.web import sessions
        r = client.post("/api/chat", json={"message": "在吗"})
        tid = r.json()["thread_id"]
        sessions.deliver_proactive(_msg(text="我在的"))

        turns = client.get(f"/api/threads/{tid}").json()["turns"]
        assert [t["kind"] for t in turns] == ["chat", "proactive"]


class TestProactiveContextSync:
    """主动消息必须同时进入"模型看的图状态",否则对话会前言不搭后语。

    实测(2026-10-27 13:32):主动消息说「等会儿念给你听呀」,用户回「好呀,讲讲吧」,
    模型的回复却是「那随便聊～ 今天中午吃了什么呀」—— 因为它只从 sessions 里看得到
    用户那句话,看不到自己刚承诺过什么。
    """

    def test_manual_message_is_inline_when_user_is_active(self, client):
        """用户正在聊天 → 不摆「主动消息 · 问候」卡片,改用轻量标注接着说。

        但 ``kind`` 仍是 proactive:写成 chat 的语义是"用户说一句 + AI 回一句",
        这里根本没有用户那一句,前端按 chat 渲染会多出一个 user="" 的空气泡 ——
        用户看到的是对话流里凭空一个空格,而且"是它先开口"的标记也没了。
        """
        from gzhu_cup2.agent.web import sessions
        sessions.deliver_proactive({**_msg(text="来了来了"), "inline": True})

        turns = client.get(f"/api/threads/{sessions.pet_thread().id}").json()["turns"]
        assert turns[0]["kind"] == "proactive"     # 仍是"它先开口",只是不摆卡片
        assert turns[0]["inline"] is True          # 前端据此用轻量标注
        assert turns[0]["user"] == ""              # 没有用户那一侧 → 不渲染空气泡
        assert turns[0]["care_kind"] == "checkin"  # 内部来源仍可追溯

    def test_legacy_inline_turn_is_repaired(self):
        """旧数据(误标成 chat 的主动消息)被修正,否则空格会永远留在库里。

        2026-10-27 之前的版本把 inline 主动消息写成 ``kind="chat", user=""``,
        前端据此渲染出一个空的用户气泡。判定要同时满足三条,不能误伤真实聊天。
        """
        from gzhu_cup2.agent.web import sessions
        rec = sessions.ThreadRecord("t-legacy", "旧对话")
        rec.turns = [
            {"ts": "2026-10-27 13:52:27", "user": "", "ai": "硅基生命也会饿的",
             "kind": "chat", "care_kind": "checkin"},
            {"ts": "2026-10-27 13:53:09", "user": "你当你是那条大肥鱼吗",
             "ai": "喂喂喂", "kind": "chat"},
            {"ts": "2026-10-27 13:54:00", "user": "", "ai": "在的呀",
             "kind": "proactive", "care_kind": "checkin"},
        ]

        assert sessions._repair_legacy_inline_proactive_locked(rec) is True
        assert rec.turns[0]["kind"] == "proactive"
        assert rec.turns[0]["inline"] is True
        # 真实聊天轮次不能被误伤(有用户输入)
        assert rec.turns[1]["kind"] == "chat" and "inline" not in rec.turns[1]
        # 本来就是 proactive 的不用补 inline
        assert "inline" not in rec.turns[2]
        # 幂等:第二次没有改动
        assert sessions._repair_legacy_inline_proactive_locked(rec) is False

    def test_delivery_pushes_line_into_graph(self, client, monkeypatch):
        """投递要把这句话补进图状态,模型下一轮才知道自己说过。"""
        from gzhu_cup2.agent.web import engine, sessions
        seen = {}
        monkeypatch.setattr(
            engine, "push_assistant_line",
            lambda tid, text, ts=None: seen.update(thread=tid, text=text) or True)

        sessions.deliver_proactive(_msg(text="等会儿念给你听呀"))

        assert seen["text"] == "等会儿念给你听呀"
        assert seen["thread"] == sessions.pet_thread().id

    def test_push_is_noop_without_graph(self, monkeypatch):
        """图还没建时不该为了补上下文去构建整张图。"""
        from gzhu_cup2.agent.web import engine
        monkeypatch.setattr(engine, "_graph", None)
        assert engine.push_assistant_line("t1", "在的呀") is False

    def test_graph_keeps_proactive_line(self, client):
        """真的进了图状态:下一轮模型能在历史里看到这句。"""
        from gzhu_cup2.agent.web import engine
        r = client.post("/api/chat", json={"message": "在吗"})
        tid = r.json()["thread_id"]

        assert engine.push_assistant_line(tid, "等会儿念给你听呀", 12345.0) is True
        st = engine.graph().get_state({"configurable": {"thread_id": tid}})
        assert "等会儿念给你听呀" in [m.content for m in st.values["messages"]]


class TestEvaluateEndpointDelivers:
    def test_manual_evaluate_also_writes_history(self, client, monkeypatch):
        """手动触发评估(联调用)与调度线程行为一致:同样落进会话历史。"""
        from gzhu_cup2.agent.care import coordinator as ccoord
        monkeypatch.setattr(
            ccoord, "evaluate",
            lambda u, petlife_text=None: _msg(text="我在的", mid="m9"),
        )
        r = client.post("/api/care/evaluate")
        assert r.status_code == 200 and len(r.json()["messages"]) == 1

        threads = client.get("/api/threads").json()
        turns = client.get(f"/api/threads/{threads[0]['id']}").json()["turns"]
        assert turns[-1]["care_msg_id"] == "m9"

    def test_no_message_means_no_thread_created(self, client, monkeypatch):
        from gzhu_cup2.agent.care import coordinator as ccoord
        monkeypatch.setattr(ccoord, "evaluate", lambda u, petlife_text=None: None)
        assert client.post("/api/care/evaluate").json()["messages"] == []
        assert client.get("/api/threads").json() == []
