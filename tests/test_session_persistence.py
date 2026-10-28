# -*- coding: utf-8 -*-
"""会话历史持久化:重启(进程重建)后逐字对话仍在。

钉死四个语义:
  1. 写穿:get_or_create / commit_turn / deliver_proactive 把线程与轮次落盘;
  2. 重启恢复:丢弃内存态后从同一路径重载,线程、标题、两类轮次(kind /
     care_kind / care_msg_id)完整回来;
  3. delete 删除磁盘行;reset() 清空整库;
  4. LONG_MEMORY_BACKEND=memory 时不产生任何会话库文件。

隔离方式:conftest 的 autouse 夹具把 LONG_MEMORY_BACKEND=memory,本文件用
sessions.set_state_path() 显式重定向到**工作区内**的独立 SQLite(与 care/pet
runtime 的 state 文件隔离同一套写法),用毕还原到默认路径,不影响其他用例。

⚠ 刻意**不用 pytest 的 tmp_path**:本机 ``os.mkdir(p, 0o700)`` 建完即不可访问,
而 pytest 的 tmpdir 插件正是用 ``mode=0o700`` 建 basetemp(见仓库根 conftest.py 的说明),
于是 setup 阶段就 PermissionError。本文件曾是全套里唯一还在用 tmp_path 的。
"""
import os
import shutil
import sqlite3
import uuid

import pytest


# 临时目录夹具 ``workdir`` 由 tests/conftest.py 统一提供(此前本文件自建了一份,
# 与 test_memory_paths / test_care_llm_content 几乎逐字相同 —— 同一件事多份实现)。


@pytest.fixture
def disk_sessions(workdir):
    """把会话库重定向到工作区临时目录下的独立 SQLite(自建自清)。"""
    from gzhu_cup2.agent.web import sessions
    db = os.path.join(workdir, "sessions_test.sqlite3")
    sessions.set_state_path(db)
    sessions.reset()
    yield sessions, db
    sessions.set_state_path("")   # 回到 config 默认(空串);env 仍是 memory → 纯内存
    sessions.reset()


def _row_count(db: str, where: str = "", args: tuple = ()) -> int:
    """数一下磁盘上的行数。

    ⚠ 必须**显式 close**：``with sqlite3.connect(db)`` 的 ``__exit__`` 只做提交/回滚,
    **不关闭连接** —— 连接会一直挂着并锁住文件,Windows 上导致清理临时目录失败。
    (此前产物落在系统临时目录,泄漏被掩盖;改到工作区内后立刻现形。)
    """
    conn = sqlite3.connect(db)
    try:
        sql = "SELECT COUNT(*) FROM sessions" + where
        return int(conn.execute(sql, args).fetchone()[0])
    finally:
        conn.close()


class TestRestartRestoresSessions:
    def test_restart_restores_thread_title_and_both_turns(self, disk_sessions):
        sessions, db = disk_sessions

        tid = sessions.get_or_create(None)
        thread = sessions.get(tid)
        sessions.rename(tid, "睡前聊天")
        sessions.commit_turn(thread, "我睡不着", "我陪你", {"sentiment": "sad"})
        sessions.deliver_proactive({
            "id": "m7", "user": "local_user", "kind": "checkin",
            "topic": None, "text": "在吗", "created_ts": 1234567890.0,
        })

        # 模拟重启:丢弃内存态(换路径 = 关连接 + 清内存 + 重载),再从同一路径载回
        sessions.set_state_path(db)

        t2 = sessions.get(tid)
        assert t2 is not None
        assert t2.title == "睡前聊天"
        assert len(t2.turns) == 2

        chat = t2.turns[0]
        assert chat["kind"] == "chat"
        assert chat["user"] == "我睡不着"
        assert chat["ai"] == "我陪你"
        assert chat["analysis"] == {"sentiment": "sad"}

        proactive = t2.turns[1]
        assert proactive["kind"] == "proactive"
        assert proactive["user"] == ""
        assert proactive["ai"] == "在吗"
        assert proactive["care_kind"] == "checkin"
        assert proactive["care_msg_id"] == "m7"

        assert _row_count(db) == 1

    def test_delete_removes_row_from_disk(self, disk_sessions):
        sessions, db = disk_sessions
        tid = sessions.get_or_create(None)
        assert _row_count(db) == 1

        sessions.delete(tid)
        assert _row_count(db) == 0

        sessions.set_state_path(db)  # 重启
        assert sessions.get(tid) is None
        assert sessions.snapshot() == []

    def test_reset_empties_database(self, disk_sessions):
        sessions, db = disk_sessions
        sessions.get_or_create(None)
        sessions.get_or_create(None)
        assert _row_count(db) == 2

        sessions.reset()
        assert _row_count(db) == 0
        assert sessions.snapshot() == []

        sessions.set_state_path(db)  # 重启后依然为空
        assert sessions.snapshot() == []


class TestMemoryBackend:
    def test_memory_backend_writes_no_file(self, workdir, monkeypatch):
        """LONG_MEMORY_BACKEND=memory 时纯内存:不建库、不写文件。"""
        from gzhu_cup2.agent.config import config as cfg
        from gzhu_cup2.agent.web import sessions

        target = os.path.join(workdir, "never_created.sqlite3")
        monkeypatch.setenv("LONG_MEMORY_BACKEND", "memory")
        monkeypatch.setattr(cfg, "SESSIONS_DB_PATH", target)
        sessions.set_state_path("")   # 确保未被上一用例的重定向残留
        sessions.reset()

        tid = sessions.get_or_create(None)
        sessions.commit_turn(sessions.get(tid), "你好", "在的", {})
        sessions.deliver_proactive({
            "id": "m2", "kind": "checkin", "text": "记得喝水",
            "created_ts": 1234567890.0,
        })

        assert os.path.exists(target) is False
        assert sessions.snapshot() != []
