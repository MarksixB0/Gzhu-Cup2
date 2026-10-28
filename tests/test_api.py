# -*- coding: utf-8 -*-
"""server.py 提供的 HTTP API 功能测试(不触真实 LLM/RAG)。

依赖 conftest 里的 fake_llm:分析节点返回 neutral / cssrs0 → risk=low。
本文件 import server(较重:torch/agent),放同一处统一分摊。
"""
import json

import pytest

from conftest import FakeLLM

pytest.importorskip("fastapi")  # 环境没有 fastapi 时跳过,不误跑


@pytest.fixture
def c(api_client):
    """短别名。"""
    return api_client


class TestHealth:
    def test_health_ok(self, c):
        r = c.get("/api/health")
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "ok"


class TestChatNonStream:
    def test_chat_basic_returns_analysis(self, c):
        r = c.post("/api/chat", json={"message": "你好，我最近比较累"})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["thread_id"]
        assert isinstance(body["reply"], str) and body["reply"]
        a = body["analysis"]
        # analysis 只暴露对话基调(桌宠换表情用);内部量表/风险标签不进响应
        assert a["tone"] in ("serious", "gentle", "normal", "playful")

    def test_chat_accepts_model_temperature_override(self, c):
        r = c.post(
            "/api/chat",
            json={"message": "睡不着", "model": "gpt-4o-mini", "temperature": 0.2},
        )
        assert r.status_code == 200, r.text
        a = r.json()["analysis"]
        assert a["tone"] in ("serious", "gentle", "normal", "playful")

    def test_chat_reuses_same_thread(self, c):
        id1 = c.post("/api/chat", json={"message": "第一句"}).json()["thread_id"]
        assert c.post("/api/chat", json={"message": "第二句", "thread_id": id1}).status_code == 200
        detail = c.get(f"/api/threads/{id1}").json()
        assert len(detail["turns"]) == 2

    def test_chat_without_key_returns_503(self, c, monkeypatch):
        monkeypatch.delenv("LLM_API_KEY", raising=False)
        r = c.post("/api/chat", json={"message": "hi"})
        assert r.status_code == 503
        assert "API Key" in r.json()["detail"]


class TestThreadLifecycle:
    def test_create_list(self, c):
        created = c.post("/api/threads").json()
        assert "thread_id" in created
        lst = c.get("/api/threads").json()
        assert any(t["id"] == created["thread_id"] for t in lst)

    def test_get_nonexistent_404(self, c):
        assert c.get("/api/threads/does-not-exist").status_code == 404

    def test_rename(self, c):
        tid = c.post("/api/threads").json()["thread_id"]
        r = c.patch(f"/api/threads/{tid}", json={"title": "改名后的会话"})
        assert r.status_code == 200
        assert c.get(f"/api/threads/{tid}").json()["title"] == "改名后的会话"

    def test_delete(self, c):
        tid = c.post("/api/threads").json()["thread_id"]
        assert c.delete(f"/api/threads/{tid}").status_code == 200
        assert c.get(f"/api/threads/{tid}").status_code == 404

    def test_delete_all_requires_confirm(self, c):
        c.post("/api/threads")
        assert c.delete("/api/threads").status_code == 400

    def test_delete_all_clears_everything(self, c):
        t1 = c.post("/api/threads").json()["thread_id"]
        t2 = c.post("/api/threads").json()["thread_id"]
        r = c.delete("/api/threads", params={"confirm": 1})
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is True
        assert body["removed"] >= 2
        assert c.get("/api/threads").json() == []
        assert c.get(f"/api/threads/{t1}").status_code == 404
        assert c.get(f"/api/threads/{t2}").status_code == 404

    def test_first_message_becomes_title(self, c):
        tid = c.post("/api/chat", json={"message": "我晚饭吃得很好很开心"}).json()["thread_id"]
        title = c.get(f"/api/threads/{tid}").json()["title"]
        assert "晚饭" in title


def _parse_sse(buf: str):
    events = []
    for frame in buf.split("\n\n"):
        if not frame.strip():
            continue
        ev = None
        data_parts = []
        for ln in frame.splitlines():
            if ln.startswith("event:"):
                ev = ln[6:].strip()
            elif ln.startswith("data:"):
                data_parts.append(ln[5:].strip())
        if ev is None or not data_parts:
            continue
        events.append((ev, json.loads("\n".join(data_parts))))
    return events


class TestStreamSSE:
    def test_stream_shows_phases_and_result(self, c):
        with c.stream(
            "POST", "/api/chat/stream", json={"message": "压力很大", "model": "gpt-4o-mini"}
        ) as resp:
            assert resp.status_code == 200
            body = b"".join(resp.iter_bytes()).decode("utf-8")
        events = _parse_sse(body)
        phases = [
            d["phase"] for ev, d in events if ev == "phase" and "phase" in d
        ]
        assert phases, "应至少有一次 phase 心跳"
        assert any(p in ("prepare", "thinking") for p in phases), phases
        evtypes = [e for e, _ in events]
        assert "result" in evtypes
        result_payload = next(d for e, d in events if e == "result")
        assert result_payload["thread_id"]
        assert result_payload["reply"]
        # analysis 只暴露对话基调(桌宠换表情用);内部风险标签不外泄
        assert result_payload["analysis"].get("tone") in ("serious", "gentle", "normal", "playful")


class TestAnalysisContract:
    """复用具名 import 一下 web.engine.extract_analysis 做纯函数快照。"""

    def test_extract_analysis_null_guards(self):
        from gzhu_cup2.agent.web import engine
        # 缺 emotional/risk 也不应抛,且只含 tone 一个键(内部标记不外泄)
        a = engine.extract_analysis({"messages": [], "user_id": "x"})
        assert set(a.keys()) == {"tone"}
        assert a["tone"] in ("serious", "gentle", "normal", "playful")
