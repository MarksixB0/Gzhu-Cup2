# -*- coding: utf-8 -*-
"""记忆数据目录:统一存放位置 + 旧数据自动迁移(纯路径逻辑,零 Store 依赖)。

设计约定:
  - 全部「记忆类」数据落在一个目录(默认 src/gzhu_cup2/agent/memory_data,见 cfg.MEMORY_DIR):
        memory.sqlite3     长期记忆 KV(facts/episodes/ticks/risk_events/pending + pet.daily)
        pet_runtime.json   桌宠运行时状态(每日叙事开关 / 最近生成日)
        care_runtime.json  关怀运行时状态(冷却 / outbox)
    开发期增删记忆只需操作这一个目录:整目录删除 = 记忆清零,换目录 = 换一份数据集;
  - 环境变量 LONG_MEMORY_DIR 整体重定向(多数据集切换 / 测试隔离);
  - 首次使用把旧路径(每个目标可有多个历史落点,新 → 旧)上的历史文件搬进新目录:
    仅在「源存在且目标不存在」时搬,幂等、可重入、跨盘失败自动退化为复制;
  - 静态素材(桌宠角色卡 pet/cards)属于代码资产,不纳入本目录。
"""
import errno
import logging
import os
import shutil
import threading

from gzhu_cup2.agent.config import config as cfg

logger = logging.getLogger(__name__)

# (新路径常量名, 旧路径单数常量名, 旧路径候选列表常量名) —— 旧常量只用于搬家,见 config.py。
# 候选 = 复数常量(顺序真相源,新 → 旧)+ 单数常量(若不在其中则追加)。
# 这样单数常量仍可被单独覆盖(既有测试如此),复数常量则提供更早布局的回退落点。
_LEGACY_PAIRS = (
    ("LONG_MEMORY_DB_PATH", "LEGACY_LONG_MEMORY_DB_PATH", "LEGACY_LONG_MEMORY_DB_PATHS"),
    ("PET_STATE_PATH", "LEGACY_PET_STATE_PATH", "LEGACY_PET_STATE_PATHS"),
    ("CARE_STATE_PATH", "LEGACY_CARE_STATE_PATH", "LEGACY_CARE_STATE_PATHS"),
)

# SQLite 可能残留的旁挂文件,搬家时一并带走
_SQLITE_SIDECARS = ("-wal", "-shm")

_LOCK = threading.RLock()
_MIGRATED = False


def memory_dir() -> str:
    """当前记忆数据目录(不创建)。"""
    return str(cfg.MEMORY_DIR)


def ensure_memory_dir() -> str:
    """确保目录存在并完成一次性旧数据迁移;返回目录路径。

    进程内只真正执行一次迁移检查(_MIGRATED 标志),后续调用只做 makedirs。
    """
    global _MIGRATED
    path = memory_dir()
    os.makedirs(path, exist_ok=True)
    if not _MIGRATED:
        with _LOCK:
            if not _MIGRATED:
                migrate_legacy()
                _MIGRATED = True
    return path


def _is_missing(e: OSError) -> bool:
    """该 OSError 是否表示"源已经不在了"(并发迁移时会这样)。"""
    return getattr(e, "errno", None) == errno.ENOENT


def _move(src: str, dst: str, with_sidecars: bool = False) -> bool:
    """把 src 移到 dst(目标已存在则跳过)。返回是否真的搬了。

    ⚠ 竞态:`_move` 的调用方(migrate_legacy)先检查过 dst 不存在,但**检查到移动
      之间**另一个进程(Electron 与 CLI 同时启动)可能已经把 src 搬走。此时
      os.replace 抛 FileNotFoundError,并沿 ensure_memory_dir → 记忆目录初始化
      → 启动路径冒泡,让一个"什么都不用做"的启动直接崩掉。故按 ENOENT 视作
      "别人已经搬过了",返回 False,而不是抛。
    """
    if not os.path.exists(src) or os.path.exists(dst):
        return False
    os.makedirs(os.path.dirname(dst) or ".", exist_ok=True)
    try:
        os.replace(src, dst)          # 同盘:原子移动
    except OSError as e:
        if _is_missing(e):
            return False              # 源已被并发进程搬走 → 视作已完成
        try:
            shutil.copy2(src, dst)    # 跨盘:复制后删源
            os.remove(src)
        except OSError as e2:
            if _is_missing(e2):
                return False
            raise
    if with_sidecars:
        for suffix in _SQLITE_SIDECARS:
            if os.path.exists(src + suffix):
                try:
                    os.replace(src + suffix, dst + suffix)
                except OSError as e:
                    if not _is_missing(e):
                        logger.debug("旁挂文件迁移失败 %s: %s", src + suffix, e)
    return True


def _legacy_candidates(old_attr: str, old_list_attr: str) -> list:
    """合并某目标的旧路径候选(新 → 旧),去重且保持顺序。

    复数常量是顺序真相源;单数常量只在其不在候选中时追加 —— 保证「只覆盖单数
    常量」的既有用法仍然生效,同时不会把复数里的最新候选挤到后面。
    """
    raw = [str(p) for p in (getattr(cfg, old_list_attr, ()) or ()) if p]
    primary = str(getattr(cfg, old_attr, "") or "")
    if primary:
        raw.append(primary)
    seen: set = set()
    out: list = []
    for p in raw:
        key = os.path.abspath(p)
        if key not in seen:
            seen.add(key)
            out.append(p)
    return out


def migrate_legacy() -> dict:
    """把旧路径的记忆数据搬进 MEMORY_DIR。返回 {文件名: 动作} 供排障。

    仅当 MEMORY_DIR 是默认目录时才搬运;用 LONG_MEMORY_DIR 重定向时一律不搬。

    每个目标可有多个历史落点(见 config.py 的 LEGACY_*_PATHS,新 → 旧):
    按序取第一个存在的源;目标已存在则整体跳过(幂等、可重入)。
    """
    if os.getenv("LONG_MEMORY_DIR"):
        logger.debug("LONG_MEMORY_DIR 已显式设置,跳过历史数据迁移")
        return {"_skipped": "explicit LONG_MEMORY_DIR"}
    actions: dict = {}
    for new_attr, old_attr, old_list_attr in _LEGACY_PAIRS:
        new_path = str(getattr(cfg, new_attr, "") or "")
        if not new_path:
            continue
        name = os.path.basename(new_path)
        candidates = [
            p for p in _legacy_candidates(old_attr, old_list_attr)
            if os.path.abspath(p) != os.path.abspath(new_path)
        ]
        if not candidates:
            continue
        if os.path.exists(new_path):
            if any(os.path.exists(p) for p in candidates):
                actions[name] = "skip:target_exists"
            continue
        for old_path in candidates:
            if not os.path.exists(old_path):
                continue
            try:
                is_db = new_path.endswith(".sqlite3")
                moved = _move(old_path, new_path, is_db)
                actions[name] = "migrated" if moved else "skip"
                if moved:
                    logger.info("记忆数据已迁移: %s -> %s", old_path, new_path)
            except OSError as e:  # noqa: BLE001 - 迁移失败不阻断启动,沿用新空目录
                actions[name] = "failed:%s" % e
                logger.warning("记忆数据迁移失败 %s -> %s: %s", old_path, new_path, e)
            break
    return actions


def reset_migration_flag() -> None:
    """测试用:清除「已迁移」标志,使下次 ensure_memory_dir() 重新评估。"""
    global _MIGRATED
    with _LOCK:
        _MIGRATED = False


def describe() -> dict:
    """记忆目录与各文件状态(供 CLI / 排障展示)。"""
    path = memory_dir()
    files = {}
    for name in ("memory.sqlite3", "pet_runtime.json", "care_runtime.json"):
        p = os.path.join(path, name)
        files[name] = os.path.getsize(p) if os.path.exists(p) else None
    return {"dir": path, "files": files}
