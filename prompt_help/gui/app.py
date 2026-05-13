"""GUI 入口：创建 QApplication，决定是否首次启动向导，再开主窗口。"""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QIcon
from PySide6.QtWidgets import QApplication, QMessageBox

from ..core.config import load_config, load_dotenv_if_present, save_config
from . import theme


def _first_run(cfg) -> bool:
    """vault 没初始化 OR onboarding 未完成。"""
    if not cfg.vault_path.is_dir():
        return True
    if not cfg.config_file.is_file():
        return True
    if not (cfg.vault_path / ".onboarding_done").is_file():
        return True
    return False


def main() -> int:
    load_dotenv_if_present()

    # Win11 上 Qt 默认会用 Fusion，原生外观需要 windowsvista 或 windows11
    QApplication.setStyle("Fusion")
    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName("Prompt Help")
    app.setOrganizationName("prompt-help")
    app.setApplicationDisplayName("Prompt Help")

    # 字号给老花眼/新手都友好
    f = app.font()
    if f.pointSize() < 10:
        f.setPointSize(10)
    app.setFont(f)

    theme.apply(app)

    cfg = load_config()

    # 首次启动 → 弹一屏 setup
    if _first_run(cfg):
        from .onboarding.wizard import OnboardingWizard
        setup = OnboardingWizard(cfg)
        if setup.exec() != setup.DialogCode.Accepted:
            # 用户点了"稍后再说"——也允许进主页，下次启动会再弹
            return 0
        cfg = load_config()

    # 主窗口
    from .main_window import MainWindow
    win = MainWindow(cfg)
    win.show()

    # Phase 7：自研 spotlight 引导特效（替换原静态 5 步 tour）
    # 由 MainWindow.start_global_tour() 启动；首次进来自动跑
    from PySide6.QtCore import QTimer
    QTimer.singleShot(300, win.maybe_start_global_tour)

    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
