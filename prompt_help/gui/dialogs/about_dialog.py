"""关于 Prompt Help（Phase 18 T2）。

显示：logo / 版本 / 定位 / 依赖 / 文件路径 / 链接。
"""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import Qt, QSize
from PySide6.QtGui import QDesktopServices, QFont, QPixmap
from PySide6.QtCore import QUrl
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QFrame, QHBoxLayout, QLabel,
    QPlainTextEdit, QPushButton, QVBoxLayout, QWidget,
)


APP_VERSION = "1.0.0"
APP_TAGLINE = "跨项目沉淀提示词 · 系统记忆 · 项目踩坑点"
APP_DESC = (
    "PH 是 vibecoder 的私有第二大脑。它跨项目沉淀你写过的提示词、"
    "项目踩过的坑、系统记忆，再用 LLM 自动通用化成可分享的模板。"
)


def _logo_path() -> Path | None:
    """优先用 256 PNG（PyInstaller 模式下从 _MEIPASS 内取）。"""
    candidates = []
    # 开发模式
    candidates.append(Path(__file__).resolve().parents[3] / "assets" / "icon-256.png")
    # PyInstaller 解压目录
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidates.append(Path(meipass) / "assets" / "icon-256.png")
    for c in candidates:
        if c.is_file():
            return c
    return None


class AboutDialog(QDialog):
    def __init__(self, cfg, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self.setWindowTitle("关于 Prompt Help")
        self.resize(560, 600)
        self._build()

    def _build(self) -> None:
        v = QVBoxLayout(self)
        v.setContentsMargins(28, 24, 28, 20)
        v.setSpacing(14)

        # 顶部：logo + 产品名 + 版本
        head = QHBoxLayout()
        head.setSpacing(16)
        logo_path = _logo_path()
        if logo_path:
            pix = QPixmap(str(logo_path)).scaled(
                72, 72, Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            logo_lbl = QLabel()
            logo_lbl.setPixmap(pix)
            head.addWidget(logo_lbl)

        head_text = QVBoxLayout()
        head_text.setSpacing(2)
        name = QLabel("Prompt Help")
        f = QFont(); f.setPointSize(20); f.setWeight(QFont.Weight.Bold)
        name.setFont(f)
        name.setStyleSheet("color: #0a0a0a;")
        head_text.addWidget(name)
        ver = QLabel(f"v{APP_VERSION}")
        ver.setStyleSheet("color: #737373; font-size: 12px;")
        head_text.addWidget(ver)
        tag = QLabel(APP_TAGLINE)
        tag.setStyleSheet("color: #525252; font-size: 13px;")
        tag.setWordWrap(True)
        head_text.addWidget(tag)
        head.addLayout(head_text, 1)
        v.addLayout(head)

        # 分隔线
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet("background: #ececec; max-height: 1px;")
        v.addWidget(sep)

        # 简介
        desc = QLabel(APP_DESC)
        desc.setStyleSheet("color: #525252; font-size: 13px; line-height: 1.6;")
        desc.setWordWrap(True)
        v.addWidget(desc)

        # 系统信息（vault 路径 / log 路径 / Python 版本）
        info_text = (
            f"<b>数据路径</b>：{self.cfg.vault_path}<br>"
            f"<b>日志路径</b>：{self.cfg.logs_dir}<br>"
            f"<b>Python</b>：{sys.version.split()[0]}<br>"
            f"<b>主要依赖</b>：PySide6 · qtawesome · openai · GitPython · PyYAML"
        )
        info = QLabel(info_text)
        info.setStyleSheet(
            "QLabel { background: #fafafa; border: 0;"
            "border-radius: 8px; padding: 12px 14px; font-size: 12px; color: #0a0a0a;"
            "line-height: 1.8; }"
        )
        info.setTextFormat(Qt.TextFormat.RichText)
        info.setWordWrap(True)
        info.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        v.addWidget(info)

        # 链接行
        link_row = QHBoxLayout()
        link_row.setSpacing(10)
        for label, url in [
            ("打开数据目录", f"file:///{self.cfg.vault_path.as_posix()}"),
            ("打开日志目录", f"file:///{self.cfg.logs_dir.as_posix()}"),
            ("查看 README", f"file:///{(Path(__file__).resolve().parents[3] / 'README.md').as_posix()}"),
        ]:
            btn = QPushButton(label)
            btn.setProperty("class", "subtle")
            btn.setStyleSheet("font-size: 11px; padding: 4px 12px;")
            btn.clicked.connect(lambda _=False, u=url: QDesktopServices.openUrl(QUrl(u)))
            link_row.addWidget(btn)
        link_row.addStretch(1)
        v.addLayout(link_row)

        # 协议 + 版权
        legal = QLabel(
            f"© 2026 · MIT License · 本软件不会上传任何 prompt 内容到云端。<br>"
            f"翻译 / 通用化等 LLM 调用通过你自己配置的 API key（默认 DeepSeek）或本地 Claude Code CLI。"
        )
        legal.setStyleSheet("color: #a3a3a3; font-size: 11px; line-height: 1.6;")
        legal.setTextFormat(Qt.TextFormat.RichText)
        legal.setWordWrap(True)
        v.addWidget(legal)

        # 关闭按钮
        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        btns.button(QDialogButtonBox.StandardButton.Close).setText("关闭")
        btns.rejected.connect(self.reject)
        v.addWidget(btns)
