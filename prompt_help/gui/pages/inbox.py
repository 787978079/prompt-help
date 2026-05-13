"""Inbox 页：mining 候选卡片化审阅。"""

from __future__ import annotations

import json
from pathlib import Path

import yaml
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QInputDialog, QLabel, QMessageBox, QPushButton,
    QScrollArea, QSizePolicy, QVBoxLayout, QWidget,
)

from ...cli.inbox import InboxItem
from ...core import indexer, storage
from ...core.config import Config


class InboxPage(QWidget):
    def __init__(self, cfg: Config):
        super().__init__()
        self.cfg = cfg
        self._build()
        self.refresh()

    def _build(self) -> None:
        v = QVBoxLayout(self)
        v.setContentsMargins(16, 16, 16, 16)
        v.setSpacing(8)

        title = QLabel("Inbox 待审")
        title.setObjectName("pageTitle")
        v.addWidget(title)
        hint = QLabel(
            "Stop / PreCompact hook 自动挖掘的候选提示词，逐条决定保存或丢弃。"
            "confidence < 0.4 的建议直接 dismiss。"
        )
        hint.setObjectName("pageHint")
        hint.setWordWrap(True)
        v.addWidget(hint)

        # 顶部刷新按钮
        bar = QHBoxLayout()
        bar.addStretch(1)
        self.btn_refresh = QPushButton("🔄  刷新")
        self.btn_refresh.setProperty("class", "subtle")
        self.btn_refresh.clicked.connect(self.refresh)
        self.btn_clear = QPushButton("🗑  清空 7 天前的")
        self.btn_clear.setProperty("class", "subtle")
        self.btn_clear.clicked.connect(self._on_clear_old)
        bar.addWidget(self.btn_clear)
        bar.addWidget(self.btn_refresh)
        v.addLayout(bar)

        # 滚动卡片区
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.cards_host = QWidget()
        self.cards_layout = QVBoxLayout(self.cards_host)
        self.cards_layout.setContentsMargins(0, 0, 0, 0)
        self.cards_layout.setSpacing(8)
        self.scroll.setWidget(self.cards_host)
        v.addWidget(self.scroll, 1)

    def on_show(self) -> None:
        self.refresh()

    def refresh(self) -> None:
        # 清空旧卡
        while self.cards_layout.count():
            item = self.cards_layout.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()

        items: list[InboxItem] = []
        if self.cfg.inbox_dir.is_dir():
            for p in sorted(self.cfg.inbox_dir.glob("*.md")):
                try:
                    items.append(InboxItem.load(p))
                except Exception:
                    continue
            items.sort(key=lambda x: (-x.confidence, x.created))

        if not items:
            empty = QLabel("✓ Inbox 为空，没有待审候选。\n（hook 启用后，CC 会话里识别到值得保存的提示词会自动留在这里。）")
            empty.setStyleSheet("color: #6b7280; padding: 32px; font-size: 14px;")
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            empty.setWordWrap(True)
            self.cards_layout.addWidget(empty)
            self.cards_layout.addStretch(1)
            return

        for it in items:
            self.cards_layout.addWidget(InboxCard(self.cfg, it, parent_page=self))
        self.cards_layout.addStretch(1)

    def _on_clear_old(self) -> None:
        import datetime as dt
        if not self.cfg.inbox_dir.is_dir():
            return
        cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=7)
        deleted = 0
        for p in self.cfg.inbox_dir.glob("*.md"):
            try:
                t = dt.datetime.strptime(p.stem.split("-")[0], "%Y%m%dT%H%M%S")
                t = t.replace(tzinfo=dt.timezone.utc)
                if t < cutoff:
                    p.unlink()
                    deleted += 1
            except Exception:
                continue
        QMessageBox.information(self, "已清理", f"删了 {deleted} 条 7 天前的候选。")
        self.refresh()


class InboxCard(QFrame):
    """单条候选卡片。"""

    def __init__(self, cfg: Config, item: InboxItem, parent_page: InboxPage):
        super().__init__()
        self.cfg = cfg
        self.item = item
        self.parent_page = parent_page

        # P22：StyledPanel 会让 Qt 自己绘制一条 frame，不被 QSS border:0 覆盖。
        # 改 NoFrame + transparent + hover 反馈，与其他卡片一致。
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setStyleSheet("""
            InboxCard {
                background: transparent;
                border: 0;
                border-radius: 8px;
            }
            InboxCard:hover {
                background-color: #fafafa;
            }
        """)
        self._build()

    def _build(self) -> None:
        v = QVBoxLayout(self)
        v.setContentsMargins(16, 12, 16, 12)
        v.setSpacing(8)

        head = QHBoxLayout()
        conf = self.item.confidence
        color = "#16a34a" if conf >= 0.6 else ("#ca8a04" if conf >= 0.4 else "#9ca3af")
        badge = QLabel(f"conf {conf:.2f}")
        badge.setStyleSheet(f"color: {color}; font-weight: 600; font-size: 12px;")
        head.addWidget(badge)

        origin = QLabel(f"· {self.item.origin}")
        origin.setStyleSheet("color: #6b7280; font-size: 12px;")
        head.addWidget(origin)
        head.addWidget(QLabel(f"· {self.item.path.name}"))

        head.addStretch(1)

        self.btn_approve = QPushButton("✓  保存")
        self.btn_approve.setProperty("class", "primary")
        self.btn_approve.clicked.connect(self._on_approve)
        self.btn_dismiss = QPushButton("✗  丢弃")
        self.btn_dismiss.setProperty("class", "danger")
        self.btn_dismiss.clicked.connect(self._on_dismiss)
        head.addWidget(self.btn_approve)
        head.addWidget(self.btn_dismiss)
        v.addLayout(head)

        body_text = self.item.body.strip()
        body_preview = (body_text if len(body_text) <= 600 else body_text[:600] + "…")
        body = QLabel(body_preview)
        body.setWordWrap(True)
        body.setStyleSheet("color: #1f2937; font-size: 13px; line-height: 1.5;")
        body.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        v.addWidget(body)

    def _on_approve(self) -> None:
        from ..widgets.prompt_editor import PromptEditorDialog

        # 用 prompt editor 让用户填元数据，body 预填候选内容
        prefilled = storage.Prompt.new(
            title=self.item.suggested_title or self.item.body.split("\n")[0][:30],
            body=self.item.body,
            scope="global",
            origin="mining",
        )
        # 但 prompt 实际还没存盘，editor 保存时会 storage.save
        dlg = PromptEditorDialog(self.cfg, prompt=prefilled, parent=self)
        if dlg.exec() == dlg.DialogCode.Accepted:
            try:
                self.item.path.unlink()
            except Exception:
                pass
            self.parent_page.refresh()
            self.parent_page.window()._refresh_status()

    def _on_dismiss(self) -> None:
        ans = QMessageBox.question(self, "确认丢弃", "确定丢弃这条候选吗？")
        if ans != QMessageBox.StandardButton.Yes:
            return
        try:
            self.item.path.unlink()
        except Exception:
            pass
        self.parent_page.refresh()
        self.parent_page.window()._refresh_status()
