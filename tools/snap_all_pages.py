"""启动 GUI，逐页面截图保存到 screenshots/。

排查线框 / emoji 残留 / 滚动问题时用。每页给 600ms 让懒加载和 layout settle。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# 确保能 import 本仓 prompt_help
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import QApplication

# 截图时彻底禁用所有 subprocess 调用——vault 的 git status 里有 GBK 解码失败的字符，
# Python subprocess 的 readerthread 用系统编码 (GBK on Windows) 解 git stdout，
# 抛 UnicodeDecodeError 卡在线程里，会让 QTimer/QApplication.exec 进死循环。
import prompt_help.core.proc as _proc
class _FakeResult:
    returncode = 0
    stdout = ""
    stderr = ""
_proc.run = lambda *a, **kw: _FakeResult()
_proc.popen = lambda *a, **kw: _FakeResult()

from prompt_help.core.config import load_config
from prompt_help.gui.main_window import MainWindow
from prompt_help.gui.theme import apply as apply_theme

MainWindow._git_sync_status = lambda self: "本地仓"

OUT = ROOT / "screenshots"
OUT.mkdir(exist_ok=True)


def snap(win: MainWindow, key: str, name: str) -> None:
    """切到 key 页面，过 600ms 截图。"""
    # 找对应 nav button
    btn_map = {
        "home": win.btn_home,
        "library": win.btn_library,
        "public": win.btn_public,
        "pm": win.btn_pm,
        "project_optimize": win.btn_project_optimize,
        "stats": win.btn_stats,
        "help": win.btn_help,
    }
    if key == "settings":
        win._on_open_settings()
    elif key in btn_map:
        btn_map[key].setChecked(True)
        win._switch_to_key(key)

    QApplication.processEvents()


def capture(win: MainWindow, name: str) -> None:
    pix = win.grab()
    target = OUT / f"{name}.png"
    pix.save(str(target), "PNG")
    print(f"saved {target}")


def main() -> int:
    app = QApplication(sys.argv)
    apply_theme(app)
    cfg = load_config()
    win = MainWindow(cfg)
    win.resize(1280, 820)
    win.show()
    QApplication.processEvents()

    pages = ["home", "library", "public", "pm", "project_optimize", "stats", "settings", "help"]
    delays = [600] * len(pages)

    # 让窗口先 settle
    QTimer.singleShot(800, lambda: _run_sequence(win, app, pages, delays))
    return app.exec()


def _run_sequence(win: MainWindow, app: QApplication, pages, delays):
    """串行：snap → 等 delay → capture → 下一页。"""
    def step(i):
        if i >= len(pages):
            print("done all pages")
            app.quit()
            return
        key = pages[i]
        print(f"switching to {key} ...")
        snap(win, key, key)
        # 等 layout + 异步 thread settle 再截
        QTimer.singleShot(delays[i], lambda: (capture(win, f"{i:02d}_{key}"), step(i + 1)))
    step(0)


if __name__ == "__main__":
    sys.exit(main())
