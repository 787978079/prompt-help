"""统一的 subprocess 包装：Windows 下隐藏控制台窗口。

PyInstaller `console=False` 的 .exe 在调子进程时，如果没传
`creationflags=CREATE_NO_WINDOW`，Windows 会闪一个黑色 cmd 窗口，
并且每次 spawn 都比正常慢 200-800ms。所有库内 subprocess 必须走这里，
不要直接 `subprocess.run()`。
"""

from __future__ import annotations

import subprocess
import sys
from typing import Any

# Windows CreateProcess flag。POSIX 上为 0（无影响）。
CREATE_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0

# 同时给 Popen 用：兼容 STARTUPINFO 隐藏窗口（双保险，处理某些 shell=True 场景）
if sys.platform == "win32":
    _STARTUPINFO = subprocess.STARTUPINFO()
    _STARTUPINFO.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    _STARTUPINFO.wShowWindow = 0  # SW_HIDE
else:
    _STARTUPINFO = None


def _inject_silence(kwargs: dict[str, Any]) -> dict[str, Any]:
    if sys.platform != "win32":
        return kwargs
    # 不覆盖用户显式传的 creationflags
    flags = kwargs.get("creationflags", 0)
    kwargs["creationflags"] = flags | CREATE_NO_WINDOW
    kwargs.setdefault("startupinfo", _STARTUPINFO)
    return kwargs


def run(*args, **kwargs):
    """subprocess.run 替代品。强制隐藏 Windows 控制台。"""
    return subprocess.run(*args, **_inject_silence(kwargs))


def popen(*args, **kwargs):
    """subprocess.Popen 替代品。强制隐藏 Windows 控制台。"""
    return subprocess.Popen(*args, **_inject_silence(kwargs))
