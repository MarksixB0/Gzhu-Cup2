# -*- coding: utf-8 -*-
"""统一记忆目录 + 记忆调试 CLI 的测试。

重点回归一条真实踩过的坑:
    用 LONG_MEMORY_DIR 指向测试目录时,绝不允许把默认位置的历史记忆"搬"过去
    —— 否则只是切换一下数据集,就把用户的真实数据库劫持走了。
"""
import json
import os

import pytest

from gzhu_cup2.agent.config import config as cfg
from gzhu_cup2.agent.long_memory import paths, store as mstore

USER = "u-devcli"


# 临时目录夹具 ``workdir`` 由 tests/conftest.py 统一提供(此前本文件自建了一份)。


@pytest.fixture
def cli():
    from gzhu_cup2.agent.tools import memory_dev
    return memory_dev


@pytest.fixture
def isolated_paths(monkeypatch, workdir):
    """把新/旧记忆路径整体指向临时目录,便于验证迁移行为。"""
    old, new = os.path.join(workdir, "old"), os.path.join(workdir, "new")
    os.makedirs(old, exist_ok=True)
    monkeypatch.delenv("LONG_MEMORY_DIR", raising=False)
    monkeypatch.setattr(cfg, "MEMORY_DIR", new)
    monkeypatch.setattr(cfg, "LONG_MEMORY_DB_PATH", os.path.join(new, "memory.sqlite3"))
    monkeypatch.setattr(cfg, "PET_STATE_PATH", os.path.join(new, "pet_runtime.json"))
    monkeypatch.setattr(cfg, "CARE_STATE_PATH", os.path.join(new, "care_runtime.json"))
    monkeypatch.setattr(cfg, "LEGACY_LONG_MEMORY_DB_PATH", os.path.join(old, "memory.sqlite3"))
    monkeypatch.setattr(cfg, "LEGACY_PET_STATE_PATH", os.path.join(old, "pet_runtime.json"))
    monkeypatch.setattr(cfg, "LEGACY_CARE_STATE_PATH", os.path.join(old, "care_runtime.json"))
    # 复数候选(顺序真相源)也必须隔离:否则默认值指向仓库根的 app/Agent/ 真实历史路径,
    # 测试会把开发机上尚未迁移的真实数据搬走。
    monkeypatch.setattr(cfg, "LEGACY_LONG_MEMORY_DB_PATHS", (os.path.join(old, "memory.sqlite3"),))
    monkeypatch.setattr(cfg, "LEGACY_PET_STATE_PATHS", (os.path.join(old, "pet_runtime.json"),))
    monkeypatch.setattr(cfg, "LEGACY_CARE_STATE_PATHS", (os.path.join(old, "care_runtime.json"),))
    paths.reset_migration_flag()
    return old, new


# ============================ 目录与迁移 ============================
class TestMemoryDir:
    def test_memory_dir_follows_config(self, monkeypatch, workdir):
        monkeypatch.setattr(cfg, "MEMORY_DIR", workdir)
        assert paths.memory_dir() == workdir

    def test_ensure_creates_dir(self, isolated_paths):
        _, new = isolated_paths
        assert not os.path.isdir(new)
        got = paths.ensure_memory_dir()
        assert got == new and os.path.isdir(new)

    def test_migrate_moves_legacy_files(self, isolated_paths):
        old, new = isolated_paths
        os.makedirs(old, exist_ok=True)
        legacy_db = os.path.join(old, "memory.sqlite3")
        with open(legacy_db, "wb") as f:
            f.write(b"legacy-db-bytes")

        actions = paths.migrate_legacy()

        assert actions.get("memory.sqlite3") == "migrated"
        assert os.path.exists(os.path.join(new, "memory.sqlite3"))
        assert not os.path.exists(legacy_db)

    def test_migrate_keeps_existing_target(self, isolated_paths):
        old, new = isolated_paths
        os.makedirs(old, exist_ok=True)
        os.makedirs(new, exist_ok=True)
        with open(os.path.join(old, "memory.sqlite3"), "wb") as f:
            f.write(b"old")
        with open(os.path.join(new, "memory.sqlite3"), "wb") as f:
            f.write(b"new")

        actions = paths.migrate_legacy()

        assert actions.get("memory.sqlite3") == "skip:target_exists"
        assert open(os.path.join(new, "memory.sqlite3"), "rb").read() == b"new"
        assert open(os.path.join(old, "memory.sqlite3"), "rb").read() == b"old"

    def test_migrate_skipped_under_env_override(self, monkeypatch, workdir):
        """安全红线:显式重定向时绝不搬运历史数据。"""
        monkeypatch.setenv("LONG_MEMORY_DIR", workdir)
        paths.reset_migration_flag()
        assert paths.migrate_legacy() == {"_skipped": "explicit LONG_MEMORY_DIR"}

    # ---- 多候选旧路径(重构把后端从 app/Agent/ 搬到 src/gzhu_cup2/agent/ 后新增) ----
    def test_migrate_falls_back_to_older_layout(self, isolated_paths, monkeypatch, workdir):
        """多候选:首选落点不存在时回退到更早布局,sqlite 旁挂文件一并带走。"""
        _, new = isolated_paths
        newest = os.path.join(workdir, "legacy-newest", "memory.sqlite3")
        older = os.path.join(workdir, "legacy-older", "memory.sqlite3")
        os.makedirs(os.path.dirname(older), exist_ok=True)
        for p, payload in ((older, b"older-db"), (older + "-wal", b"wal"), (older + "-shm", b"shm")):
            with open(p, "wb") as f:
                f.write(payload)
        monkeypatch.setattr(cfg, "LEGACY_LONG_MEMORY_DB_PATH", newest)
        monkeypatch.setattr(cfg, "LEGACY_LONG_MEMORY_DB_PATHS", (newest, older))

        actions = paths.migrate_legacy()

        assert actions.get("memory.sqlite3") == "migrated"
        target = os.path.join(new, "memory.sqlite3")
        assert open(target, "rb").read() == b"older-db"
        assert open(target + "-wal", "rb").read() == b"wal"
        assert open(target + "-shm", "rb").read() == b"shm"
        assert not os.path.exists(older)

    def test_migrate_prefers_newest_candidate(self, isolated_paths, monkeypatch, workdir):
        """多候选:新旧落点都存在时搬新的那份,旧候选原样不动。"""
        _, new = isolated_paths
        newest = os.path.join(workdir, "legacy-newest", "memory.sqlite3")
        older = os.path.join(workdir, "legacy-older", "memory.sqlite3")
        for p, payload in ((newest, b"newest-db"), (older, b"older-db")):
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "wb") as f:
                f.write(payload)
        monkeypatch.setattr(cfg, "LEGACY_LONG_MEMORY_DB_PATH", newest)
        monkeypatch.setattr(cfg, "LEGACY_LONG_MEMORY_DB_PATHS", (newest, older))

        actions = paths.migrate_legacy()

        assert actions.get("memory.sqlite3") == "migrated"
        assert open(os.path.join(new, "memory.sqlite3"), "rb").read() == b"newest-db"
        assert not os.path.exists(newest)
        assert os.path.exists(older)

    def test_migrate_second_run_is_noop(self, isolated_paths, monkeypatch, workdir):
        """幂等:第一次搬完后,第二次不再产生任何动作。"""
        _, new = isolated_paths
        older = os.path.join(workdir, "legacy-older", "memory.sqlite3")
        os.makedirs(os.path.dirname(older), exist_ok=True)
        with open(older, "wb") as f:
            f.write(b"older-db")
        monkeypatch.setattr(cfg, "LEGACY_LONG_MEMORY_DB_PATH", older)
        monkeypatch.setattr(cfg, "LEGACY_LONG_MEMORY_DB_PATHS", (older,))

        assert paths.migrate_legacy().get("memory.sqlite3") == "migrated"
        assert paths.migrate_legacy() == {}
        assert open(os.path.join(new, "memory.sqlite3"), "rb").read() == b"older-db"

    def test_move_tolerates_source_stolen_by_concurrent_process(self, monkeypatch, workdir):
        """并发启动不得让迁移崩掉启动路径。

        复现:Electron 与 CLI 同时启动,两个进程都通过了 `os.path.exists(dst)` 检查,
        随后其中一个先把源文件移走 —— 另一个的 `os.replace` 会抛 FileNotFoundError。
        该异常此前沿 `ensure_memory_dir → 记忆目录初始化 → 启动路径` 冒泡,
        让一个"什么都不用做"的启动直接失败。现在应视作"别人已经搬过了"返回 False。
        """
        src = os.path.join(workdir, "legacy.json")
        dst = os.path.join(workdir, "sub", "legacy.json")
        with open(src, "w", encoding="utf-8") as f:
            f.write("{}")

        def boom(*_a, **_k):
            raise FileNotFoundError(2, "No such file or directory")

        monkeypatch.setattr(paths.os, "replace", boom)
        monkeypatch.setattr(paths.shutil, "copy2", boom)

        assert paths._move(src, dst) is False   # 不得抛异常

    def test_move_still_raises_on_real_errors(self, monkeypatch, workdir):
        """真正的错误(如权限)必须继续抛 —— 不能为了容忍竞态而吞掉所有 OSError。"""
        src = os.path.join(workdir, "legacy.json")
        dst = os.path.join(workdir, "sub", "legacy.json")
        with open(src, "w", encoding="utf-8") as f:
            f.write("{}")

        def denied(*_a, **_k):
            raise PermissionError(13, "Permission denied")

        monkeypatch.setattr(paths.os, "replace", denied)
        monkeypatch.setattr(paths.shutil, "copy2", denied)

        with pytest.raises(PermissionError):
            paths._move(src, dst)

    def test_describe_reports_files(self, isolated_paths):
        _, new = isolated_paths
        paths.ensure_memory_dir()
        info = paths.describe()
        assert info["dir"] == new
        assert set(info["files"]) == {"memory.sqlite3", "pet_runtime.json", "care_runtime.json"}


# ============================ CLI ============================
class TestMemoryDevCli:
    def test_dir_command(self, cli, capsys, isolated_paths):
        assert cli.main(["dir"]) == 0
        out = capsys.readouterr().out
        assert "记忆目录" in out

    def test_add_list_del_fact_roundtrip(self, cli, capsys):
        assert cli.main(["add-fact", "--user", USER, "--text", "用户养了一只叫年糕的橘猫",
                         "--category", "life", "--importance", "0.8"]) == 0
        capsys.readouterr()

        assert cli.main(["list", "--user", USER, "--ns", "facts"]) == 0
        out = capsys.readouterr().out
        assert "年糕" in out and "[life]" in out

        assert cli.main(["del", "--user", USER, "--ns", "facts",
                         "--text", "用户养了一只叫年糕的橘猫"]) == 0
        capsys.readouterr()
        assert cli.main(["list", "--user", USER, "--ns", "facts"]) == 0
        assert "年糕" not in capsys.readouterr().out

    def test_same_fact_twice_merges(self, cli, capsys):
        for _ in range(2):
            cli.main(["add-fact", "--user", USER, "--text", "用户喜欢下雨天"])
        capsys.readouterr()
        cli.main(["list", "--user", USER, "--ns", "facts"])
        out = capsys.readouterr().out
        assert "mentions=2" in out

    def test_add_risk_uses_protected_namespace(self, cli, capsys):
        assert cli.main(["add-risk", "--user", USER, "--facet", "ideation",
                         "--quote", "有时候觉得活着没意思"]) == 0
        capsys.readouterr()
        cli.main(["list", "--user", USER, "--ns", "risk"])
        out = capsys.readouterr().out
        assert "ideation" in out and "有时候觉得活着没意思" in out

    def test_clear_only_touches_named_user(self, cli, capsys):
        cli.main(["add-fact", "--user", USER, "--text", "待清理"])
        cli.main(["add-fact", "--user", "u-other", "--text", "别动我"])
        capsys.readouterr()

        assert cli.main(["clear", "--user", USER]) == 0
        capsys.readouterr()

        cli.main(["list", "--user", "u-other", "--ns", "facts"])
        assert "别动我" in capsys.readouterr().out

    def test_seed_from_file_is_idempotent(self, cli, capsys, workdir):
        seed = {
            "user": USER,
            "facts": [{"text": "用户在准备答辩", "category": "work", "importance": 0.9}],
            "episodes": [{"digest": "聊了答辩压力", "topics": ["school"]}],
            "risk": [{"facet": "plan", "quote": "已经想好时间了"}],
            "ticks": [{"day": "2026-09-01", "count": 3, "neg_sum": 1.2, "neg_n": 3,
                       "emotions": {"sad": 3}}],
        }
        path = os.path.join(workdir, "seed.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(seed, f, ensure_ascii=False)

        cli.main(["seed", "--file", path])
        cli.main(["seed", "--file", path])   # 再跑一次
        capsys.readouterr()

        cli.main(["list", "--user", USER, "--ns", "all"])
        out = capsys.readouterr().out
        assert "用户在准备答辩" in out
        assert "聊了答辩压力" in out
        # 幂等:重复 seed 不产生第二条 episode
        assert out.count("聊了答辩压力") == 1

    def test_episode_key_is_deterministic(self, cli):
        assert cli._episode_key(USER, "x") == cli._episode_key(USER, "x")
        assert cli._episode_key(USER, "x") != cli._episode_key(USER, "y")

    def test_context_renders_injected_block(self, cli, capsys):
        cli.main(["add-fact", "--user", USER, "--text", "用户叫小林", "--category", "life"])
        capsys.readouterr()
        assert cli.main(["context", "--user", USER]) == 0
        out = capsys.readouterr().out
        assert "用户档案" in out and "用户叫小林" in out

    def test_dump_roundtrip(self, cli, capsys, workdir):
        cli.main(["add-fact", "--user", USER, "--text", "导出我"])
        capsys.readouterr()
        out_file = os.path.join(workdir, "dump.json")
        assert cli.main(["dump", "--user", USER, "--out", out_file]) == 0
        with open(out_file, encoding="utf-8") as f:
            data = json.load(f)
        assert data["user"] == USER
        assert any("导出我" in (r.get("text") or "") for r in data["facts"])

    def test_pending_namespace_lists_segment(self, cli, capsys):
        mstore.capture_turn(USER, "t1", "你好", "你好呀", meta={"risk_level": "low"})
        assert cli.main(["list", "--user", USER, "--ns", "pending"]) == 0
        assert "turns=1" in capsys.readouterr().out
