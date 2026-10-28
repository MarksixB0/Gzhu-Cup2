# -*- coding: utf-8 -*-
"""控制台输出编码兜底(Windows 专用,零依赖、幂等)。

把 stdout/stderr 重设为 UTF-8(errors="replace"),避免 Windows 默认 GBK 控制台
在打印中文与符号时抛 UnicodeEncodeError。

不修改文件写入编码,不改动非 Windows 平台,也不依赖 PYTHONIOENCODING。
在 CLI 入口调用一次即可,重复调用无副作用。
"""
import sys


def force_utf8_stdout() -> bool:
    """把 stdout/stderr 的编码重设为 UTF-8。返回是否真的做了改动。

    仅在 Windows 生效;其他平台直接返回 False(不碰它们的终端设置)。
    """
    if sys.platform != "win32":
        return False
    changed = False
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            # line_buffering 保持原样;errors="replace" 保证永不因编码而崩
            reconfigure(encoding="utf-8", errors="replace")
            changed = True
        except (ValueError, OSError):
            # 某些被重定向的流不支持 reconfigure —— 不影响主流程
            continue
    return changed
