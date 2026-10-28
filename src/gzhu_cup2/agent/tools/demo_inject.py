# -*- coding: utf-8 -*-
"""把演示数据集注入本地长期记忆目录(会**覆盖**当前记忆,先自动备份)。

用途:让运行中的应用(记忆洞察视图 / 桌宠对话)直接看到 14 天演示轨迹与
合成记忆,而无需改环境变量或重启到另一个数据目录。

⚠ 这是**破坏性操作**:会清空目标目录下该 user 的 ticks / facts / episodes /
  risk。脚本会先把整个 memory.sqlite3 备份为 memory.sqlite3.bak-<时间戳>,
  然后才写入。备份文件不会被自动清理。

用法:
    # 注入到默认记忆目录(src/gzhu_cup2/agent/memory_data),用户 local_user
    .venv\\Scripts\\python.exe src/gzhu_cup2/agent/tools/demo_inject.py

    # 指定目标目录 / 用户
    .venv\\Scripts\\python.exe src/gzhu_cup2/agent/tools/demo_inject.py --dir src/gzhu_cup2/agent/memory_data --user local_user

    # 不备份(危险,仅在目标本来就是演示库时使用)
    .venv\\Scripts\\python.exe src/gzhu_cup2/agent/tools/demo_inject.py --no-backup
"""
import argparse
import os
import shutil
import sys
from datetime import datetime

# 仓库根:本文件在 <repo>/src/gzhu_cup2/agent/tools/,从 __file__ 上溯 5 层
_repo_root = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))


def main() -> int:
    p = argparse.ArgumentParser(description="把演示数据集注入本地长期记忆目录(先备份)")
    p.add_argument("--dir", default=os.path.join("src", "gzhu_cup2", "agent", "memory_data"),
                   help="目标记忆目录(默认即应用实际使用的目录)")
    p.add_argument("--user", default="local_user",
                   help="目标用户键(应用默认 MEMORY_USER_KEY=local_user)")
    p.add_argument("--no-backup", action="store_true", help="跳过备份(危险)")
    args = p.parse_args()

    target = os.path.abspath(os.path.join(_repo_root, args.dir))
    db = os.path.join(target, "memory.sqlite3")

    print("目标目录 : %s" % target)
    print("目标用户 : %s" % args.user)
    print()

    # ---------- 1. 备份 ----------
    if os.path.exists(db):
        if args.no_backup:
            print("[backup] 已跳过(--no-backup);原库将被覆盖")
        else:
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            bak = "%s.bak-%s" % (db, stamp)
            shutil.copy2(db, bak)
            print("[backup] 已备份 -> %s (%d bytes)"
                  % (os.path.basename(bak), os.path.getsize(bak)))
            for suffix in ("-wal", "-shm"):
                side = db + suffix
                if os.path.exists(side):
                    shutil.copy2(side, bak + suffix)
                    print("[backup] 已备份 %s" % os.path.basename(side))
    else:
        print("[backup] 目标库不存在(首次注入),无需备份")
    print()

    # ---------- 2. 注入(在导入 config 前设好目录)----------
    os.environ["LONG_MEMORY_DIR"] = target
    os.environ.setdefault("LONG_MEMORY_BACKEND", "sqlite")

    from gzhu_cup2.agent.algo import skeleton as guard_mod                 # noqa: E402
    from gzhu_cup2.agent.config import config as cfg                      # noqa: E402
    from gzhu_cup2.agent.long_memory import store as mstore                # noqa: E402
    from gzhu_cup2.agent.tools import demo_seed as ds                      # noqa: E402

    if os.path.abspath(cfg.MEMORY_DIR) != target:
        print("*** 目录解析不一致:cfg.MEMORY_DIR=%s" % cfg.MEMORY_DIR, file=sys.stderr)
        return 2

    per_day = ds.seed(args.user, reset=True, quiet=True)
    stats = ds.seed_memory(args.user, reset=True, quiet=True)

    # ---------- 3. 校验 ----------
    from gzhu_cup2.agent.long_memory import ticks as mticks                # noqa: E402
    buckets = mticks.list_day_buckets(args.user, days=None)
    tiers = []
    for day, _b in buckets:
        anchor = datetime.strptime(day, "%Y-%m-%d").replace(hour=23, minute=59)
        tiers.append(guard_mod.tier_of(args.user, now=anchor.timestamp()))

    print("注入完成:")
    print("  日桶(ticks)   : %d 天 / %d 轮" % (len(buckets), sum(d["count"] for d in per_day)))
    print("  稳定事实      : %d 条" % stats["facts"])
    print("  会话片段      : %d 条" % stats["episodes"])
    print("  危机留痕      : %d 条" % stats["risk_events"])
    print("  存储后端      : %s" % mstore.store_backend_name())
    print()
    print("  逐日档位(当时判定):")
    for (day, _b), tier in zip(buckets, tiers):
        print("    %s  %s" % (day, tier))
    final = guard_mod.tier_of(args.user, now=ds.reference_now())
    print()
    print("  判定基准时刻 = 数据最后一天 23:59 → 当前档位: %s" % final)

    from gzhu_cup2.agent.long_memory import retrieve as mretrieve          # noqa: E402
    ctx = mretrieve.build_memory_context(args.user, purpose="reply")
    print("  读侧记忆块    : %d 字符" % len(ctx))
    print()
    print("提示:重启应用(Electron 会重新拉起后端)后,打开右侧「记忆」即可看到上述数据。")
    print("     如需还原:把 memory.sqlite3.bak-<时间戳> 改回 memory.sqlite3 即可。")
    return 0


if __name__ == "__main__":
    from gzhu_cup2.agent.common.console import force_utf8_stdout
    force_utf8_stdout()   # Windows GBK 控制台兜底
    raise SystemExit(main())
