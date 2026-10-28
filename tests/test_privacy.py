# -*- coding: utf-8 -*-
"""记忆隐私控制:暂停记录开关,以及它与 capture_memory 的联动。

「暂停记录」的语义边界(PLAN_NEXT.md §6):
  - 只影响**写入**,不影响对话;
  - 默认**开启**记录 —— 关掉就没有记忆了,那不是更保守而是产品失效;
  - 暂停状态必须**如实可查**(前端常驻显示),所以这里也测 status() 的返回。
"""
import pytest


class TestRecordingSwitch:
    def test_default_is_on(self, privacy_state_file):
        from gzhu_cup2.agent.long_memory import privacy
        assert privacy.recording_enabled("u1") is True

    def test_toggle_persists(self, privacy_state_file):
        from gzhu_cup2.agent.long_memory import privacy
        privacy.set_recording("u1", False)
        assert privacy.recording_enabled("u1") is False
        assert privacy.status("u1")["recording"] is False
        privacy.set_recording("u1", True)
        assert privacy.recording_enabled("u1") is True

    def test_users_are_isolated(self, privacy_state_file):
        from gzhu_cup2.agent.long_memory import privacy
        privacy.set_recording("u1", False)
        assert privacy.recording_enabled("u2") is True

    def test_status_shape(self, privacy_state_file):
        """前端要靠它做常驻显示,字段不能少。"""
        from gzhu_cup2.agent.long_memory import privacy
        st = privacy.status("u1")
        assert st["user"] == "u1" and "recording" in st


class TestCaptureRespectsPause:
    """暂停时 capture_memory 必须**不写任何记忆** —— 它是记忆写入的唯一入口。"""

    @staticmethod
    def _state(user: str = "u1", text: str = "今天很累") -> dict:
        class _Msg:
            content = "我在听"

        return {"user_id": user, "process_input": text, "messages": [_Msg()]}

    def test_paused_writes_nothing(self, privacy_state_file, monkeypatch):
        from gzhu_cup2.agent.long_memory import privacy
        from gzhu_cup2.agent.nodes import capture_memory as cm
        calls = []
        monkeypatch.setattr(cm.mem_store, "capture_turn",
                            lambda *a, **k: calls.append(a))
        privacy.set_recording("u1", False)
        cm.capture_memory(self._state())
        assert calls == [], "暂停记录时不应发生任何写入"

    def test_recording_still_writes(self, privacy_state_file, monkeypatch):
        """对照组:没暂停时照旧写入(证明上面的差异确实来自开关)。"""
        from gzhu_cup2.agent.nodes import capture_memory as cm
        calls = []
        monkeypatch.setattr(cm.mem_store, "capture_turn",
                            lambda *a, **k: calls.append(a))
        cm.capture_memory(self._state())
        assert len(calls) == 1

    def test_pause_returns_clean_state(self, privacy_state_file):
        """暂停只影响记忆:capture 仍返回空 dict,对话链路照常往下走。"""
        from gzhu_cup2.agent.long_memory import privacy
        from gzhu_cup2.agent.nodes import capture_memory as cm
        privacy.set_recording("u1", False)
        assert cm.capture_memory(self._state()) == {}

    def test_pause_is_per_user(self, privacy_state_file, monkeypatch):
        from gzhu_cup2.agent.long_memory import privacy
        from gzhu_cup2.agent.nodes import capture_memory as cm
        calls = []
        monkeypatch.setattr(cm.mem_store, "capture_turn",
                            lambda *a, **k: calls.append(a))
        privacy.set_recording("u1", False)
        cm.capture_memory(self._state(user="u2"))   # 另一个用户不受影响
        assert len(calls) == 1
