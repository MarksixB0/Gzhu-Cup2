# -*- coding: utf-8 -*-
"""``common/json_state.py`` 的行为测试。

这个模块是 care.runtime 与 pet.runtime 的公共基座(此前那套样板被逐行复制了两份),
所以它的语义必须被钉死 —— 尤其是"读出来的 dict 会补齐缺失顶层键"这条,
它等价于原先各域的 ``data.setdefault(...)`` 写法,一旦行为漂移会静默影响两个域。
"""
import json
import os

import pytest

from gzhu_cup2.agent.common.json_state import JsonStateFile

_TMP_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_state_tmp")


@pytest.fixture
def tmpdir():
    os.makedirs(_TMP_DIR, exist_ok=True)
    yield _TMP_DIR


def _mk(tmpdir, name="state.json", empty=None):
    path = os.path.join(tmpdir, name)
    if os.path.exists(path):
        os.remove(path)
    return JsonStateFile(path, empty or (lambda: {"users": {}})), path


def _write(path, payload):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)


def _read(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


# ============================ 路径 ============================
class TestPath:
    def test_default_path_used_initially(self, tmpdir):
        state, path = _mk(tmpdir)
        assert state.path == path
        assert state.is_default_path() is True

    def test_set_path_redirects(self, tmpdir):
        state, _ = _mk(tmpdir)
        other = os.path.join(tmpdir, "other.json")
        state.set_path(other)
        assert state.path == other
        assert state.is_default_path() is False

    def test_empty_string_restores_default(self, tmpdir):
        state, path = _mk(tmpdir)
        state.set_path(os.path.join(tmpdir, "other.json"))
        state.set_path("")
        assert state.path == path
        assert state.is_default_path() is True

    def test_none_restores_default(self, tmpdir):
        state, path = _mk(tmpdir)
        state.set_path(None)
        assert state.path == path


# ============================ 读写 ============================
class TestReadWrite:
    def test_missing_file_yields_empty_state(self, tmpdir):
        state, _ = _mk(tmpdir)
        assert state.read() == {"users": {}}

    def test_reset_writes_empty_state(self, tmpdir):
        state, path = _mk(tmpdir, empty=lambda: {"users": {}, "pending": []})
        state.reset()
        assert _read(path) == {"users": {}, "pending": []}

    def test_locked_persists_on_clean_exit(self, tmpdir):
        state, path = _mk(tmpdir)
        with state.locked() as data:
            data["users"]["u1"] = {"enabled": False}
        assert _read(path)["users"]["u1"] == {"enabled": False}

    def test_locked_does_not_persist_on_exception(self, tmpdir):
        """异常退出不落盘 —— 避免把写了一半的状态提交。"""
        state, path = _mk(tmpdir)
        state.reset()
        with pytest.raises(RuntimeError):
            with state.locked() as data:
                data["users"]["boom"] = {"x": 1}
                raise RuntimeError("boom")
        assert "boom" not in _read(path)["users"]

    def test_view_does_not_persist(self, tmpdir):
        state, path = _mk(tmpdir)
        state.reset()
        with state.view() as data:
            data["users"]["ghost"] = {"x": 1}
        assert "ghost" not in _read(path)["users"]

    def test_atomic_write_leaves_no_tmp(self, tmpdir):
        state, path = _mk(tmpdir)
        state.reset()
        assert not os.path.exists(path + ".tmp")

    def test_roundtrip_preserves_unicode(self, tmpdir):
        state, path = _mk(tmpdir)
        with state.locked() as data:
            data["users"]["年糕"] = {"note": "晒太阳"}
        with open(path, "r", encoding="utf-8") as f:
            assert "年糕" in f.read()


# ============================ 与 setdefault 等价的补齐语义 ============================
class TestTopLevelNormalization:
    """读出来的 dict 会用 empty_factory() 补齐缺失的顶层键。

    这正是原先各域 ``data.setdefault("users", {})`` 的行为 —— 两个域都依赖它。
    """

    def test_missing_keys_are_filled(self, tmpdir):
        state, path = _mk(tmpdir, empty=lambda: {"users": {}, "pending": []})
        _write(path, {"users": {"u1": {"enabled": True}}})
        got = state.read()
        assert got["users"] == {"u1": {"enabled": True}}
        assert got["pending"] == []

    def test_existing_keys_win_over_defaults(self, tmpdir):
        state, path = _mk(tmpdir, empty=lambda: {"users": {}, "pending": []})
        _write(path, {"users": {"u1": {}}, "pending": [{"id": "m1"}]})
        assert state.read()["pending"] == [{"id": "m1"}]

    def test_extra_keys_are_preserved(self, tmpdir):
        """未知顶层键不得被丢弃(向前兼容:新版本写的数据旧版本也要能读)。"""
        state, path = _mk(tmpdir)
        _write(path, {"users": {}, "future_field": 42})
        assert state.read()["future_field"] == 42


# ============================ 坏数据 ============================
class TestBadData:
    def test_corrupt_json_falls_back_to_empty(self, tmpdir):
        state, path = _mk(tmpdir)
        with open(path, "w", encoding="utf-8") as f:
            f.write("{ not json")
        assert state.read() == {"users": {}}

    def test_non_dict_json_falls_back_to_empty(self, tmpdir):
        state, path = _mk(tmpdir)
        _write(path, [1, 2, 3])
        assert state.read() == {"users": {}}

    def test_recovery_after_corruption(self, tmpdir):
        """坏文件不该让状态永久不可用:下一次写入应恢复正常。"""
        state, path = _mk(tmpdir)
        with open(path, "w", encoding="utf-8") as f:
            f.write("garbage")
        with state.locked() as data:
            data["users"]["u1"] = {"ok": True}
        assert _read(path)["users"]["u1"] == {"ok": True}


# ============================ 默认路径钩子 ============================
class TestDefaultPathHook:
    def test_hook_runs_for_default_path(self, tmpdir):
        calls = []
        path = os.path.join(tmpdir, "hooked.json")
        state = JsonStateFile(path, lambda: {"users": {}}, on_default_path=lambda: calls.append(1))
        state.reset()
        assert calls, "使用默认路径时必须触发钩子(用于确保统一记忆目录存在)"

    def test_hook_skipped_for_custom_path(self, tmpdir):
        """重定向到自定义路径时不得触发 —— 否则测试会去创建真实记忆目录。"""
        calls = []
        path = os.path.join(tmpdir, "hooked.json")
        state = JsonStateFile(path, lambda: {"users": {}}, on_default_path=lambda: calls.append(1))
        state.set_path(os.path.join(tmpdir, "redirected.json"))
        state.reset()
        assert calls == []

    def test_no_hook_is_allowed(self, tmpdir):
        state, _ = _mk(tmpdir, empty=None)
        state.reset()  # on_default_path=None 不应报错
        assert state.read() == {"users": {}}
