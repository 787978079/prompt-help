"""占位符填表对话框（Phase 11 B1）。

当用户复制带占位符的通用模板时，先让 ta 填值再带入剪贴板。
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QFormLayout, QLabel, QLineEdit,
    QPlainTextEdit, QVBoxLayout, QWidget,
)

from ...core.placeholders import fill


class FillPlaceholdersDialog(QDialog):
    """显示 [占位符] 列表 + 输入框 + 实时预览。"""

    def __init__(
        self,
        title: str,
        body: str,
        placeholders: list[str],
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.title_text = title
        self.body = body
        self.placeholders = placeholders
        self.inputs: dict[str, QLineEdit] = {}
        self.setWindowTitle(f"填占位符 · {title}")
        self.resize(680, 560)
        self._build()
        self._update_preview()

    def _build(self) -> None:
        v = QVBoxLayout(self)
        v.setContentsMargins(20, 16, 20, 16)
        v.setSpacing(10)

        hint = QLabel(
            f"这条通用模板里有 **{len(self.placeholders)} 个占位符**。"
            "下面填好值，确认后会用替换后的文本复制到剪贴板。\n"
            "留空的占位符会原样保留（你可以之后手动改）。"
        )
        hint.setTextFormat(Qt.TextFormat.MarkdownText)
        hint.setStyleSheet("color: #525252; font-size: 12px; line-height: 1.55;")
        hint.setWordWrap(True)
        v.addWidget(hint)

        # 表单
        form_host = QWidget()
        form = QFormLayout(form_host)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        for name in self.placeholders:
            line = QLineEdit()
            line.setPlaceholderText(f"在这里填 {name}")
            line.textChanged.connect(self._update_preview)
            self.inputs[name] = line
            form.addRow(name, line)
        v.addWidget(form_host)

        # 预览
        prev_label = QLabel("替换预览（实时）")
        prev_label.setStyleSheet("color: #0a0a0a; font-size: 12px; font-weight: 600; padding-top: 6px;")
        v.addWidget(prev_label)
        self.preview = QPlainTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setStyleSheet(
            "QPlainTextEdit { background: #fafafa; border: 0; "
            "border-radius: 6px; padding: 8px; font-family: monospace; font-size: 12px; }"
        )
        v.addWidget(self.preview, 1)

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        btns.button(QDialogButtonBox.StandardButton.Ok).setText("复制到剪贴板")
        btns.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        v.addWidget(btns)

    def _values(self) -> dict[str, str]:
        return {name: w.text() for name, w in self.inputs.items() if w.text()}

    def _update_preview(self) -> None:
        self.preview.setPlainText(fill(self.body, self._values()))

    def filled_text(self) -> str:
        return fill(self.body, self._values())
