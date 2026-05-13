"""单独截 settings 和 help。"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from prompt_help.core.config import load_config
from prompt_help.gui.main_window import MainWindow
from prompt_help.gui.theme import apply as apply_theme

OUT = ROOT / "screenshots"


def main() -> int:
    app = QApplication(sys.argv)
    apply_theme(app)
    cfg = load_config()
    win = MainWindow(cfg)
    win.resize(1280, 820)
    win.show()
    QApplication.processEvents()

    def grab(name):
        pix = win.grab()
        pix.save(str(OUT / f"{name}.png"), "PNG")
        print(f"saved {name}")

    def go_help():
        win.btn_help.setChecked(True)
        win._switch_to_key("help")
        QApplication.processEvents()
        QTimer.singleShot(700, lambda: (grab("06_help"), app.quit()))

    def go_settings():
        win._on_open_settings()
        QApplication.processEvents()
        QTimer.singleShot(700, lambda: (grab("05_settings"), go_help()))

    QTimer.singleShot(800, go_settings)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
