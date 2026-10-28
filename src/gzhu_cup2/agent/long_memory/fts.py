# -*- coding: utf-8 -*-
"""SQLite FTS5 本地记忆候选检索。

这里只负责「找候选」，不负责隐私、风险或最终上下文配额；最终裁剪仍由
``long_memory.retrieve`` 完成。索引是可删除、可重建的派生数据。
"""
from __future__ import annotations

import logging
import os
import re
import sqlite3
import threading
import time

from gzhu_cup2.agent.config import config as cfg

logger = logging.getLogger(__name__)
_LOCK = threading.RLock()
_READY: set[str] = set()


def _enabled() -> bool:
    # 测试/显式内存后端不写持久索引，避免把测试数据混入开发目录；
    # 生产默认 sqlite 后端才启用 FTS5。主检索仍会自动走原有规则兜底。
    return os.getenv("LONG_MEMORY_BACKEND", "sqlite").strip().lower() != "memory"


def _path() -> str:
    return str(getattr(cfg, "MEMORY_FTS_DB_PATH", "") or
               os.path.join(cfg.MEMORY_DIR, "memory_fts.sqlite3"))


def _connect() -> sqlite3.Connection:
    path = _path()
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    conn = sqlite3.connect(path, timeout=5.0)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS memory_fts_meta ("
        "user_id TEXT NOT NULL, kind TEXT NOT NULL, item_key TEXT NOT NULL, "
        "text TEXT NOT NULL, archived INTEGER NOT NULL DEFAULT 0, "
        "protected INTEGER NOT NULL DEFAULT 0, updated REAL NOT NULL, "
        "PRIMARY KEY(user_id, kind, item_key))")
    # trigram 对中文连续文字有效；若运行时 SQLite 不支持，则降级到普通 FTS。
    try:
        conn.execute("CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts USING fts5("
                     "user_id UNINDEXED, kind UNINDEXED, item_key UNINDEXED, "
                     "text, archived UNINDEXED, protected UNINDEXED, updated UNINDEXED, "
                     "tokenize='trigram')")
    except sqlite3.OperationalError:
        conn.execute("CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts USING fts5("
                     "user_id UNINDEXED, kind UNINDEXED, item_key UNINDEXED, "
                     "text, archived UNINDEXED, protected UNINDEXED, updated UNINDEXED, "
                     "tokenize='unicode61')")
    conn.commit()
    return conn


def _text(kind: str, value: dict | None) -> str:
    value = value or {}
    if kind == "facts":
        return str(value.get("text") or "").strip()
    if kind == "episodes":
        return str(value.get("digest") or "").strip()
    if kind == "pending":
        return " ".join(str(t.get("user") or "").strip()
                        for t in (value.get("turns") or [])).strip()
    return ""


def _kind(namespace: tuple) -> str | None:
    if len(namespace) == 3 and namespace[0] == "memory":
        return str(namespace[2]) if namespace[2] in {"facts", "episodes", "pending"} else None
    return None


def upsert(namespace: tuple, key: str, value: dict | None) -> None:
    """同步一条派生索引；失败只记录日志，不阻塞主聊天/记忆落库。"""
    if not _enabled():
        return
    kind = _kind(namespace)
    user = str(namespace[1]) if kind else ""
    text = _text(kind or "", value)
    if not kind or not user or not key:
        return
    with _LOCK:
        conn = None
        try:
            conn = _connect()
            conn.execute("DELETE FROM memory_fts WHERE user_id=? AND kind=? AND item_key=?",
                         (user, kind, str(key)))
            conn.execute("DELETE FROM memory_fts_meta WHERE user_id=? AND kind=? AND item_key=?",
                         (user, kind, str(key)))
            if text:
                archived = int(bool((value or {}).get("archived")))
                protected = int(bool((value or {}).get("protected")))
                updated = float((value or {}).get("last_seen") or time.time())
                conn.execute("INSERT INTO memory_fts_meta VALUES (?,?,?,?,?,?,?)",
                             (user, kind, str(key), text, archived, protected, updated))
                conn.execute("INSERT INTO memory_fts VALUES (?,?,?,?,?,?,?)",
                             (user, kind, str(key), text, archived, protected, updated))
            conn.commit()
        except Exception as exc:  # pragma: no cover - defensive boundary
            logger.warning("记忆 FTS 索引更新失败 user=%s kind=%s: %s", user, kind, exc)
        finally:
            if conn is not None:
                conn.close()


def remove(namespace: tuple, key: str) -> None:
    if not _enabled():
        return
    kind = _kind(namespace)
    user = str(namespace[1]) if kind else ""
    if not kind or not user or not key:
        return
    with _LOCK:
        conn = None
        try:
            conn = _connect()
            conn.execute("DELETE FROM memory_fts WHERE user_id=? AND kind=? AND item_key=?",
                         (user, kind, str(key)))
            conn.execute("DELETE FROM memory_fts_meta WHERE user_id=? AND kind=? AND item_key=?",
                         (user, kind, str(key)))
            conn.commit()
        except Exception as exc:  # pragma: no cover
            logger.warning("记忆 FTS 索引删除失败 user=%s kind=%s: %s", user, kind, exc)
        finally:
            if conn is not None:
                conn.close()


def _query(q: str) -> str:
    """生成不执行 SQL 的 FTS 查询；中文用连续三字片段，英文保留整词。"""
    q = " ".join(str(q or "").split()).strip()
    if not q:
        return ""
    chunks: list[str] = []
    for part in re.findall(r"[\u4e00-\u9fff]+|[A-Za-z0-9_]+", q):
        if re.fullmatch(r"[\u4e00-\u9fff]+", part):
            if len(part) < 3:
                chunks.append(part)
            else:
                chunks.extend(part[i:i + 3] for i in range(len(part) - 2))
        else:
            chunks.append(part)
    chunks = list(dict.fromkeys(x for x in chunks if x))
    return " OR ".join('"' + x.replace('"', '""') + '"' for x in chunks)


def search(user: str, query: str, limit: int = 48,
           extra_terms: list[str] | None = None) -> list[dict]:
    """返回候选 ``{kind,key,text,rank}``；无结果/不可用时返回空列表。

    extra_terms: 主题词表扩展(如问"考研"时把 school 主题的"答辩/论文"一起召回)。
    3 字以上的词并入 MATCH；1~2 字词无法被 trigram MATCH,用一次 LIKE 扫描补回。
    """
    if not _enabled():
        return []
    user = str(user or "").strip()
    q = str(query or "").strip()
    if not user or not q:
        return []
    limit = max(1, int(limit))
    extra = list(dict.fromkeys(
        str(t).strip() for t in (extra_terms or ()) if str(t).strip()))
    with _LOCK:
        conn = None
        try:
            # 兼容 FTS5 上线前已经存在的旧记忆：每个用户首次搜索时惰性补建一次。
            marker = _path() + "::" + user
            if marker not in _READY:
                _READY.add(marker)
                try:
                    rebuild_user(user)
                except Exception as exc:  # 索引补建失败仍可走旧检索
                    logger.warning("记忆 FTS 首次补建失败 user=%s: %s", user, exc)
            conn = _connect()
            match = _query(" ".join([q] + extra))
            rows = []
            if match:
                rows = conn.execute(
                    "SELECT kind,item_key,text,bm25(memory_fts) AS rank "
                    "FROM memory_fts WHERE user_id=? AND memory_fts MATCH ? "
                    "ORDER BY rank LIMIT ?", (user, match, limit)).fetchall()
            seen = {(r[0], r[1]) for r in rows}
            # 1~2 字中文或不支持 trigram 的 SQLite 用 LIKE 兜底。
            if len(q) < 3 or not rows:
                like = "%" + q.replace("%", "").replace("_", "") + "%"
                for r in conn.execute(
                        "SELECT kind,item_key,text,0.0 FROM memory_fts "
                        "WHERE user_id=? AND text LIKE ? ORDER BY updated DESC LIMIT ?",
                        (user, like, limit)).fetchall():
                    if (r[0], r[1]) not in seen:
                        rows.append(r)
                        seen.add((r[0], r[1]))
            # 主题词里的 1~2 字词(答辩/论文/老师…):trigram 不能 MATCH,用一次
            # 多条件 LIKE 扫描补回,避免"问考研召回答辩"的旧通道被 FTS 砍掉。
            short = [t for t in extra if len(t) < 3 and t not in q]
            if short:
                clause = " OR ".join(["text LIKE ?"] * len(short))
                params = [user]
                params += ["%" + t.replace("%", "").replace("_", "") + "%" for t in short]
                params.append(limit)
                for r in conn.execute(
                        "SELECT kind,item_key,text,0.0 FROM memory_fts "
                        f"WHERE user_id=? AND ({clause}) ORDER BY updated DESC LIMIT ?",
                        params).fetchall():
                    if (r[0], r[1]) not in seen:
                        rows.append(r)
                        seen.add((r[0], r[1]))
            return [{"kind": r[0], "key": r[1], "text": r[2], "rank": float(r[3] or 0)}
                    for r in rows[:limit]]
        except Exception as exc:  # pragma: no cover
            logger.warning("记忆 FTS 搜索失败 user=%s: %s", user, exc)
            return []
        finally:
            if conn is not None:
                conn.close()


def rebuild_user(user: str) -> int:
    """从主 Store 重建单个用户索引；用于升级和索引丢失后的恢复。"""
    from gzhu_cup2.agent.long_memory import store as mstore
    count = 0
    for kind, ns in (("facts", mstore.ns_facts(user)),
                     ("episodes", mstore.ns_episodes(user)),
                     ("pending", mstore.ns_pending(user))):
        for item in mstore.list_items(ns):
            upsert(ns, item.key, item.value or {})
            count += 1
    return count


def reset() -> None:
    """测试/维护用：删除派生索引，主记忆不受影响。

    ⚠ 必须一并清空 ``_READY``：它是"该用户的索引已补建过"的**进程内**标记。
    只删文件不清标记的话，后续 ``search`` 会认为索引仍就绪、不再 rebuild ——
    索引从此恒为空，检索静默退化成"永远没有 FTS 命中"。
    """
    with _LOCK:
        _READY.clear()
        path = _path()
        try:
            if os.path.exists(path):
                os.remove(path)
        except OSError as exc:
            logger.warning("无法删除 FTS 索引 %s: %s", path, exc)
