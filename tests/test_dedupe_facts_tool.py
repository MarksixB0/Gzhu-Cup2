# -*- coding: utf-8 -*-
"""已落库事实整理脚本(dedupe_facts)测试。

脚本处理的是**存量脏数据** —— 写入路径的新守卫(见 test_fact_merge /
test_existing_facts_prompt)不会回头改老行。这里用"绕过 merge_fact 直接 put_item"
的方式模拟老数据(老行的键是历史哈希、字段也可能不齐),钉死三件事:
  1. dry-run 绝不写库;
  2. apply 后分类被纠正、日期冲突与详略两版被合并(计数相加、详略取全);
  3. 换了说法的同义改写**不自动合并**,只在报告里列出。
"""
import pytest

from gzhu_cup2.agent.long_memory import store as mem_store
from gzhu_cup2.agent.tools import dedupe_facts as tool

USER = "u-dedupe"


def _seed(key: str, text: str, category: str, *, importance: float = 0.6,
          mentions: int = 1, event_date: str | None = None,
          first_seen: float = 1000.0, protected: bool = False,
          archived: bool = False) -> None:
    """按**老数据形态**直写:键与文本不对应(历史哈希),字段可能缺省。"""
    mem_store.put_item(mem_store.ns_facts(USER), key, {
        "type": "fact", "text": text, "category": category,
        "importance": importance, "mentions": mentions,
        "first_seen": first_seen, "last_seen": first_seen + 10,
        "event_date": event_date, "archived": archived,
        "protected": protected,
    })


def _facts() -> dict:
    return {it.key: dict(it.value or {})
            for it in mem_store.list_items(mem_store.ns_facts(USER), limit=500)}


@pytest.fixture()
def dirty():
    """典型脏库:安全类错分类 + 日期冲突 + 详略两版 + 一对同义改写。"""
    _seed("k1", "用户有一个室友叫阿杰", "relationship")
    _seed("k2", "用户的室友叫阿杰，天天打游戏很吵", "relationship")
    _seed("k3", "用户明确表示没有自伤意图", "school")
    _seed("k4", "用户在2026-10-21有个模拟考", "school",
          event_date="2026-10-21", first_seen=1000.0)
    _seed("k5", "用户在2026-10-28有模拟考", "school",
          event_date="2026-10-28", first_seen=2000.0)
    _seed("k6", "用户和室友阿杰一起住", "relationship")
    _seed("k7", "用户和室友阿杰一起住宿舍四人间经常吵架", "relationship")
    return USER


class TestDryRun:
    def test_dry_run_changes_nothing(self, dirty, capsys):
        before = _facts()
        assert tool.main(["--user", dirty]) == 0

        assert _facts() == before
        out = capsys.readouterr().out
        assert "dry-run" in out
        assert "日期冲突合并" in out

    def test_dry_run_reports_semantic_pairs_without_merging(self, dirty, capsys):
        tool.main(["--user", dirty])
        out = capsys.readouterr().out
        assert "建议人工确认" in out
        # 换了说法的同义改写必须原样保留两条(误合并是不可逆的事实丢失)
        assert "k1" in _facts() and "k2" in _facts()


class TestApply:
    def test_safety_category_corrected(self, dirty):
        tool.main(["--user", dirty, "--apply"])
        f = _facts()
        assert f["k3"]["category"] == "health"
        assert f["k3"]["protected"] is True

    def test_date_conflict_keeps_first_assertion_by_default(self, dirty):
        tool.main(["--user", dirty, "--apply"])
        f = _facts()
        assert "k4" in f and "k5" not in f
        assert f["k4"]["event_date"] == "2026-10-21"
        assert f["k4"]["mentions"] == 2          # 计数相加,不是丢弃

    def test_keep_date_overrides_base_choice(self, dirty):
        tool.main(["--user", dirty, "--keep-date", "2026-10-28", "--apply"])
        f = _facts()
        assert "k5" in f and "k4" not in f
        assert f["k5"]["event_date"] == "2026-10-28"

    def test_unmatched_keep_date_falls_back_to_first_assertion(self, dirty, capsys):
        tool.main(["--user", dirty, "--keep-date", "2026-11-30", "--apply"])
        out = capsys.readouterr().out
        assert "未出现在本组" in out
        assert _facts()["k4"]["event_date"] == "2026-10-21"

    def test_extension_merge_keeps_detailed_version(self, dirty):
        tool.main(["--user", dirty, "--apply"])
        f = _facts()
        assert "k7" in f and "k6" not in f
        assert f["k7"]["text"] == "用户和室友阿杰一起住宿舍四人间经常吵架"
        assert f["k7"]["mentions"] == 2

    def test_archived_rows_untouched(self, dirty):
        _seed("k9", "用户养了一只叫年糕的橘猫", "life", archived=True)
        tool.main(["--user", dirty, "--apply"])
        assert _facts()["k9"]["archived"] is True

    def test_idempotent_second_run(self, dirty, capsys):
        tool.main(["--user", dirty, "--apply"])
        capsys.readouterr()
        tool.main(["--user", dirty, "--apply"])
        out = capsys.readouterr().out
        assert "没有需要改动的内容" in out


class TestGrouping:
    def test_group_is_disjoint_greedy(self):
        """一次分配:一行只进一个组,避免链式传递把不同的事串成一串。"""
        vals = ["用户在2026-10-21有个模拟考",
                "用户在2026-10-28有模拟考",
                "用户在2026-10-21有个面试"]
        groups = tool._group(
            vals, lambda a, b: mem_store._is_date_variant(a, b))
        flat = [i for g in groups for i in g]
        assert len(flat) == len(set(flat))
        assert groups == [[0, 1]]
