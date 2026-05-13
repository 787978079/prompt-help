"""导入冲突解决对话框（Phase 14 T4.3）。

流程：
1. 用户选 ZIP → 先 inspect_zip 拿冲突清单
2. 弹本对话框列出 新增 / 完全相同 / 冲突 三类
3. 冲突项让用户选保留本地 / 覆盖 / 双留
4. 顶部全局策略下拉，可一键应用到全部冲突
5. 确认后调 import_zip 传 per_item_actions
"""

from __future__ import annotations

import difflib
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QButtonGroup, QComboBox, QDialog, QDialogButtonBox, QFrame, QHBoxLayout,
    QLabel, QPlainTextEdit, QPushButton, QRadioButton, QScrollArea,
    QSizePolicy, QSplitter, QVBoxLayout, QWidget,
)


# 策略：title → "skip" | "replace" | "both"
STRATEGY_LABELS = {
    "skip": "保留本地（跳过远程）",
    "replace": "覆盖本地",
    "both": "双留为副本",
}


class ConflictResolverDialog(QDialog):
    def __init__(self, cfg, archive_path: Path, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self.archive_path = archive_path
        self.inspect_result: dict = {}
        self.actions: dict[str, str] = {}  # title → strategy
        self.row_radios: dict[str, QButtonGroup] = {}
        self.selected_title: str = ""
        self.setWindowTitle(f"导入合并 · {archive_path.name}")
        self.resize(1000, 640)
        self._build()
        self._load()

    def _build(self) -> None:
        v = QVBoxLayout(self)
        v.setContentsMargins(20, 16, 20, 16)
        v.setSpacing(10)

        # 顶部摘要
        self.summary = QLabel("加载中…")
        self.summary.setStyleSheet("font-size: 13px; color: #0a0a0a;")
        self.summary.setWordWrap(True)
        v.addWidget(self.summary)

        # 全局策略行
        gs = QHBoxLayout()
        gs.addWidget(QLabel("批量应用到所有冲突："))
        self.global_combo = QComboBox()
        self.global_combo.addItem("（保持各项独立选择）", "")
        for key, lbl in STRATEGY_LABELS.items():
            self.global_combo.addItem(lbl, key)
        self.global_combo.currentIndexChanged.connect(self._on_global_changed)
        gs.addWidget(self.global_combo)
        gs.addStretch(1)
        v.addLayout(gs)

        # 左列表 + 右 diff
        split = QSplitter(Qt.Orientation.Horizontal)

        # 左：滚动列表
        list_host = QWidget()
        self.list_layout = QVBoxLayout(list_host)
        self.list_layout.setContentsMargins(0, 0, 0, 0)
        self.list_layout.setSpacing(6)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(list_host)
        split.addWidget(scroll)

        # 右：diff 预览
        right = QWidget()
        rv = QVBoxLayout(right)
        rv.setContentsMargins(0, 0, 0, 0)
        rv.setSpacing(6)
        self.diff_title = QLabel("（点左侧条目预览 diff）")
        self.diff_title.setStyleSheet("font-size: 12px; font-weight: 600; color: #0a0a0a;")
        rv.addWidget(self.diff_title)
        self.diff_view = QPlainTextEdit()
        self.diff_view.setReadOnly(True)
        self.diff_view.setStyleSheet(
            "QPlainTextEdit { background: #fafafa; border: 0; "
            "border-radius: 6px; padding: 8px; font-family: monospace; font-size: 11px; }"
        )
        rv.addWidget(self.diff_view, 1)
        split.addWidget(right)
        split.setSizes([460, 520])
        v.addWidget(split, 1)

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        btns.button(QDialogButtonBox.StandardButton.Ok).setText("开始导入")
        btns.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        v.addWidget(btns)

    def _load(self) -> None:
        from ...cli.share import inspect_zip
        try:
            self.inspect_result = inspect_zip(self.cfg, self.archive_path)
        except Exception as e:
            self.summary.setText(f"读取失败：{type(e).__name__}: {e}")
            return
        if self.inspect_result.get("error"):
            self.summary.setText(f"{self.inspect_result['error']}")
            return

        items = self.inspect_result.get("items") or []
        conflicts = self.inspect_result.get("conflicts") or []
        new_n = sum(1 for it in items if not it["local_exists"])
        same_n = sum(1 for it in items if it["identical"])
        diff_n = len(conflicts)

        self.summary.setText(
            f"包内共 **{len(items)}** 条提示词："
            f"新增 {new_n} 条（直接入库）  ·  完全相同 {same_n} 条（自动跳过）"
            f"  ·  冲突 **{diff_n}** 条（需要你选策略）"
        )
        self.summary.setTextFormat(Qt.TextFormat.MarkdownText)

        # 默认每条冲突 = skip
        for c in conflicts:
            self.actions[c["title"]] = "skip"
            self.list_layout.addWidget(self._make_row(c))
        if not conflicts:
            ok = QLabel("✓ 没有冲突——所有条目都是新增或完全相同。直接点「开始导入」即可。")
            ok.setStyleSheet("color: #16a34a; padding: 24px; font-size: 13px;")
            self.list_layout.addWidget(ok)
        self.list_layout.addStretch(1)

    def _make_row(self, item: dict) -> QFrame:
        f = QFrame()
        f.setStyleSheet(
            "QFrame { background: #fafafa; border: 0; border-radius: 8px; }"
            "QFrame:hover { border-color: #d4d4d4; }"
        )
        f.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        v = QVBoxLayout(f)
        v.setContentsMargins(12, 8, 12, 8)
        v.setSpacing(4)

        title_row = QHBoxLayout()
        title = QLabel(item["title"])
        title.setStyleSheet("font-size: 13px; font-weight: 500; color: #0a0a0a;")
        title.setWordWrap(True)
        title_row.addWidget(title, 1)
        diff_btn = QPushButton("看 diff")
        diff_btn.setProperty("class", "subtle")
        diff_btn.setStyleSheet("font-size: 11px; padding: 2px 8px;")
        diff_btn.clicked.connect(lambda _=False, it=item: self._show_diff(it))
        title_row.addWidget(diff_btn)
        v.addLayout(title_row)

        hash_lbl = QLabel(f"本地 {item['local_hash']}  ↔  远程 {item['remote_hash']}")
        hash_lbl.setStyleSheet("color: #737373; font-size: 10px; font-family: monospace;")
        v.addWidget(hash_lbl)

        # 3 个 radio
        radio_row = QHBoxLayout()
        radio_row.setSpacing(10)
        bg = QButtonGroup(f)
        for key, lbl in STRATEGY_LABELS.items():
            rb = QRadioButton(lbl)
            rb.setStyleSheet("font-size: 11px;")
            rb.setProperty("strategy_key", key)
            if key == "skip":
                rb.setChecked(True)
            rb.toggled.connect(
                lambda checked, t=item["title"], k=key:
                    self._on_radio(t, k) if checked else None
            )
            bg.addButton(rb)
            radio_row.addWidget(rb)
        radio_row.addStretch(1)
        self.row_radios[item["title"]] = bg
        v.addLayout(radio_row)
        return f

    def _on_radio(self, title: str, strategy: str) -> None:
        self.actions[title] = strategy

    def _on_global_changed(self, _idx: int) -> None:
        target = self.global_combo.currentData() or ""
        if not target:
            return
        # 同步所有 row 的 radio 选中
        for title, bg in self.row_radios.items():
            for rb in bg.buttons():
                if rb.property("strategy_key") == target:
                    rb.setChecked(True)
                    break
            self.actions[title] = target

    def _show_diff(self, item: dict) -> None:
        self.diff_title.setText(f"diff · {item['title']}")
        local = (item["local_body"] or "").splitlines()
        remote = (item["remote_body"] or "").splitlines()
        diff = difflib.unified_diff(
            local, remote,
            fromfile="本地", tofile="远程", lineterm="", n=3,
        )
        self.diff_view.setPlainText("\n".join(diff))
