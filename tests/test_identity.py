# -*- coding: utf-8 -*-
"""用户身份解析层测试(``web/sessions.resolve_user`` / ``server._cors_settings``)。

**为什么需要这个文件**
----------------------
"用户键"此前在 web 层被硬编码为 ``cfg.MEMORY_USER_KEY``(共 9 处),既无法切换
也无法按请求区分;而它会被拼进长期记忆的 SQLite 命名空间,是**数据隔离的边界**。
本文件把这条边界钉死:

  1. 合法键原样通过,非法键**回落**且不抛异常(坏请求头不该让对话 500);
  2. ``X-User-Id`` 真的能分流记忆(state.user_id 被设置),不再有硬编码;
  3. CORS 绝不出现"通配源 + 凭据"这个非法且危险的组合。
"""
import pytest

from gzhu_cup2.agent.config import config as cfg
from gzhu_cup2.agent.web import sessions


class TestUserKeyValidation:
    def test_valid_keys_pass(self):
        for key in ("local_user", "stud-01", "a.b_c", "A" * 64, "u1"):
            assert sessions.is_valid_user_key(key) is True, key

    def test_invalid_keys_rejected(self):
        """非法键必须被拒:它们会污染记忆命名空间或造成路径歧义。"""
        for key in ("", "a/b", "a\\b", "a b", "a\nb", "张三", "A" * 65,
                    "a" * 63 + "!", "a;b", "a'b", None, 123, b"bytes"):
            assert sessions.is_valid_user_key(key) is False, repr(key)

    def test_length_boundary(self):
        assert sessions.is_valid_user_key("a" * 64) is True
        assert sessions.is_valid_user_key("a" * 65) is False


class TestResolveUser:
    def test_default_when_nothing_given(self):
        assert sessions.resolve_user() == sessions.resolve_user(None)

    def test_explicit_valid_key_wins(self):
        assert sessions.resolve_user("stud-01") == "stud-01"

    def test_illegal_input_falls_back_without_raising(self):
        """非法输入一律回落默认用户 —— 绝不抛异常。

        这是刻意的取舍:一个坏请求头不应让整条对话链路 500。
        """
        fallback = sessions.resolve_user()
        for dirty in ("a/b", "", "   ", "a" * 200, "张三", None, "a b"):
            got = sessions.resolve_user(dirty)
            assert got == fallback, (dirty, got)
            assert sessions.is_valid_user_key(got), "回落值本身也必须合法"

    def test_env_default_is_used_when_no_header(self, monkeypatch):
        """config 的默认用户键(可由环境变量覆盖)是唯一兜底来源。"""
        monkeypatch.setattr(cfg, "MEMORY_USER_KEY", "class-2-student")
        assert sessions.resolve_user() == "class-2-student"

    def test_illegal_config_default_falls_back_to_hard_default(self, monkeypatch):
        """连配置都被写脏时,仍要回落到内置默认值,而不是把脏键写进记忆库。"""
        monkeypatch.setattr(cfg, "MEMORY_USER_KEY", "bad/key")
        got = sessions.resolve_user()
        assert got == cfg.MEMORY_USER_KEY_DEFAULT
        assert sessions.is_valid_user_key(got)


class TestDifferentUsersAreIsolated:
    def test_two_users_do_not_share_memory_namespace(self, monkeypatch):
        """不同用户键必须落在不同的记忆命名空间 —— 这是隔离的实际含义。"""
        from gzhu_cup2.agent.long_memory import store as mstore

        monkeypatch.setenv("LONG_MEMORY_BACKEND", "memory")
        mstore.reset_store()
        mstore.capture_turn("user-a", "t1", "我很难过", "我在听")
        assert mstore.get_pending("user-a") is not None
        assert mstore.get_pending("user-b") is None
        mstore.reset_store()


class TestRunTurnUsesResolvedUser:
    def test_run_turn_passes_user_into_graph_state(self, monkeypatch):
        """engine.run_turn 必须把 resolved user 写进 state.user_id。

        回归:此前它直接写 ``cfg.MEMORY_USER_KEY``,"按请求区分用户"从入口就被
        抹平了 —— 这也是 web 层 9 处硬编码里最关键的一处。
        """
        from gzhu_cup2.agent.web import engine

        captured = {}

        class FakeGraph:
            def invoke(self, payload, config=None):
                captured.update(payload)
                from langchain_core.messages import AIMessage
                return {"messages": [AIMessage(content="好")]}

        monkeypatch.setattr(engine, "graph", lambda: FakeGraph())
        engine.run_turn("thread-1", "你好", user="stud-42")

        assert captured.get("user_id") == "stud-42"

    def test_run_turn_defaults_to_resolved_user(self, monkeypatch):
        from gzhu_cup2.agent.web import engine

        captured = {}

        class FakeGraph:
            def invoke(self, payload, config=None):
                captured.update(payload)
                from langchain_core.messages import AIMessage
                return {"messages": [AIMessage(content="好")]}

        monkeypatch.setattr(engine, "graph", lambda: FakeGraph())
        engine.run_turn("thread-1", "你好")
        assert captured.get("user_id") == sessions.resolve_user()


class TestChatApiUserHeader:
    """端到端:``X-User-Id`` 真的把长期记忆分到不同用户。"""

    def test_header_partitions_memory(self, api_client):
        from gzhu_cup2.agent.long_memory import store as mstore

        r = api_client.post("/api/chat", json={"message": "我今天很难过"},
                            headers={"X-User-Id": "stud-42"})
        assert r.status_code == 200, r.text
        assert mstore.get_pending("stud-42") is not None, "记忆未落到 stud-42"
        assert mstore.get_pending("local_user") is None, "不应落到默认用户"

    def test_without_header_uses_default_user(self, api_client):
        from gzhu_cup2.agent.long_memory import store as mstore

        r = api_client.post("/api/chat", json={"message": "普通的一轮"})
        assert r.status_code == 200, r.text
        assert mstore.get_pending(sessions.resolve_user()) is not None

    def test_illegal_header_falls_back_and_still_answers(self, api_client):
        """非法用户键不得让请求失败 —— 回落默认用户并正常回复。"""
        from gzhu_cup2.agent.long_memory import store as mstore

        r = api_client.post("/api/chat", json={"message": "我还是我"},
                            headers={"X-User-Id": "bad/key"})
        assert r.status_code == 200, r.text
        assert mstore.get_pending("bad/key") is None, "脏键不得进入记忆库"
        assert mstore.get_pending(sessions.resolve_user()) is not None


class TestCorsSettings:
    def test_wildcard_never_combined_with_credentials(self, monkeypatch):
        """通配源 + 凭据是非法且危险的组合:必须强制关掉凭据。

        危险性:Starlette 在 credentials=True 时会把通配源回显为请求方 Origin,
        等于"任意网站都能带着凭据读取接口" —— 而本项目有返回记忆原文的接口。
        """
        import gzhu_cup2.entrypoints.api as server

        monkeypatch.setattr(cfg, "CORS_ALLOW_ORIGINS", ("*",))
        monkeypatch.setattr(cfg, "CORS_ALLOW_CREDENTIALS", True)
        origins, credentials = server._cors_settings()
        assert origins == ["*"]
        assert credentials is False, "通配源下凭据必须为 False"

    def test_explicit_origins_keep_credentials_flag(self, monkeypatch):
        import gzhu_cup2.entrypoints.api as server

        monkeypatch.setattr(cfg, "CORS_ALLOW_ORIGINS", ("http://localhost:5173",))
        monkeypatch.setattr(cfg, "CORS_ALLOW_CREDENTIALS", True)
        origins, credentials = server._cors_settings()
        assert origins == ["http://localhost:5173"]
        assert credentials is True

    def test_default_config_has_no_wildcard(self):
        """默认配置不得是通配源 —— 这是"开箱即安全"的底线。"""
        assert "*" not in (cfg.CORS_ALLOW_ORIGINS or ())
        assert cfg.CORS_ALLOW_CREDENTIALS is False

    def test_desktop_file_origin_allowed(self):
        """必须放行字面量 ``"null"`` —— 否则桌宠前端直接连不上后端。

        回归:桌面端主窗口用 Electron ``loadFile()`` 载入前端产物,即 **file://**;
        浏览器把 file:// 页面的 Origin 序列化成**字符串 "null"**。把 CORS 从
        `["*"]` 收紧成显式白名单时,若漏掉它,桌宠就会静默失去后端能力
        —— 而"收紧安全配置"很容易顺手把这行删掉,故钉在这里。
        """
        assert "null" in (cfg.CORS_ALLOW_ORIGINS or ()), \
            "缺少 Origin: null 会打断 Electron(file://)与后端的通信"
        # 本地 dev server 也必须在白名单内(README 记录的 5173 / 5180)
        for o in ("http://localhost:5173", "http://127.0.0.1:5173",
                  "http://localhost:5180", "http://127.0.0.1:5180"):
            assert o in cfg.CORS_ALLOW_ORIGINS, o

    def test_third_party_origin_not_allowed(self):
        """收紧的意义:第三方站点不得被放行(否则可跨域读记忆原文接口)。"""
        assert "http://evil.example.com" not in (cfg.CORS_ALLOW_ORIGINS or ())
