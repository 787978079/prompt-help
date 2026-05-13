"""新建 / 编辑提示词的对话框。"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout,
    QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPlainTextEdit, QPushButton,
    QVBoxLayout, QWidget,
)

from ...core import indexer, optimizer, storage
from ...core.config import Config


class PromptEditorDialog(QDialog):
    """新建或编辑一条提示词。"""

    def __init__(
        self,
        cfg: Config,
        prompt: Optional[storage.Prompt] = None,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.cfg = cfg
        self.prompt = prompt
        self.setWindowTitle("编辑提示词" if prompt else "新建提示词")
        self.resize(720, 600)
        self._build()
        if prompt:
            self._load(prompt)

    def _build(self) -> None:
        v = QVBoxLayout(self)
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        # Phase 9：名称
        self.title = QLineEdit()
        self.title.setPlaceholderText("短标识，5-20 字（如「跑 Playwright 验证」）")
        form.addRow("名称", self.title)

        # Phase 9：描述 + LLM 生成按钮
        desc_row = QHBoxLayout()
        desc_row.setSpacing(6)
        self.description = QLineEdit()
        self.description.setPlaceholderText("一句话说明这条做什么用、解决什么问题；可让 LLM 生成")
        desc_row.addWidget(self.description, 1)
        from .. import icons as _icons
        from PySide6.QtCore import QSize as _QSize
        self.btn_gen_desc = QPushButton(" LLM 生成")
        self.btn_gen_desc.setProperty("class", "subtle")
        self.btn_gen_desc.setIcon(_icons.icon("generalize"))
        self.btn_gen_desc.setIconSize(_QSize(13, 13))
        self.btn_gen_desc.setToolTip("基于「正文」内容，让 LLM 帮你写一句话描述")
        self.btn_gen_desc.clicked.connect(self._on_gen_description)
        desc_row.addWidget(self.btn_gen_desc)
        desc_host = QWidget()
        desc_host.setLayout(desc_row)
        form.addRow("描述", desc_host)

        self.scope = QComboBox()
        self.scope.addItems(["global（跨项目通用）", "project（项目专属）", "trap（踩坑提醒）"])
        self.scope.currentIndexChanged.connect(self._on_scope_change)
        form.addRow("类型", self.scope)

        # Phase 9：参考来源（项目名 / 网页 URL / 文件路径）
        self.source_ref = QLineEdit()
        self.source_ref.setPlaceholderText("项目名 / 网页 URL / 文件路径（任选一种）")
        form.addRow("参考来源", self.source_ref)

        self.project = QLineEdit()
        self.project.setPlaceholderText("项目名（slug，如 minpei）")
        self.project_label = QLabel("项目名")
        self.project_row = self.project
        form.addRow(self.project_label, self.project)

        self.triggers = QLineEdit()
        self.triggers.setPlaceholderText("逗号分隔的关键词；含有这些词时自动召回（仅 trap）")
        self.triggers_label = QLabel("trap 触发词")
        form.addRow(self.triggers_label, self.triggers)

        self.tags = QLineEdit()
        self.tags.setPlaceholderText("逗号分隔，如 playwright,ui")
        form.addRow("tags", self.tags)

        self.stack = QLineEdit()
        self.stack.setPlaceholderText("适用技术栈，逗号分隔，如 nextjs,react")
        form.addRow("stack", self.stack)

        self.body = QPlainTextEdit()
        self.body.setPlaceholderText("提示词正文。Markdown 格式。")
        self.body.setMinimumHeight(280)
        form.addRow("正文", self.body)

        self.polish = QCheckBox("保存时调 LLM 优化（需有 API key）")
        self.polish.setChecked(False)
        form.addRow("", self.polish)

        v.addLayout(form)

        # 底部按钮
        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        btns.button(QDialogButtonBox.StandardButton.Save).setText("保存")
        btns.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        btns.accepted.connect(self._on_save)
        btns.rejected.connect(self.reject)
        v.addWidget(btns)

        self._on_scope_change()

    def _on_scope_change(self) -> None:
        scope = self._scope_value()
        is_project = scope == "project"
        is_trap = scope == "trap"
        self.project_label.setVisible(is_project)
        self.project_row.setVisible(is_project)
        self.triggers_label.setVisible(is_trap)
        self.triggers.setVisible(is_trap)

    def _scope_value(self) -> str:
        return ["global", "project", "trap"][self.scope.currentIndex()]

    def _load(self, p: storage.Prompt) -> None:
        self.title.setText(p.title)
        self.description.setText(p.description or "")
        self.source_ref.setText(p.source_ref or "")
        idx = {"global": 0, "project": 1, "trap": 2}.get(p.scope, 0)
        self.scope.setCurrentIndex(idx)
        self.project.setText(p.project or "")
        self.triggers.setText(", ".join(p.triggers))
        self.tags.setText(", ".join(p.tags))
        self.stack.setText(", ".join(p.stack))
        self.body.setPlainText(p.body)
        self._on_scope_change()

    def _on_gen_description(self) -> None:
        """LLM 基于正文生成一句话描述。"""
        body = self.body.toPlainText().strip()
        if not body:
            QMessageBox.information(self, "缺正文", "先填正文，LLM 才能生成描述。")
            return
        from PySide6.QtCore import Qt as _Qt
        from PySide6.QtGui import QCursor
        from PySide6.QtWidgets import QApplication
        QApplication.setOverrideCursor(QCursor(_Qt.CursorShape.WaitCursor))
        try:
            r = optimizer.summarize(self.cfg, body) if hasattr(optimizer, "summarize") \
                else _fallback_summarize(self.cfg, body)
        finally:
            QApplication.restoreOverrideCursor()
        if r and r.success and r.optimized:
            self.description.setText(r.optimized.strip()[:200])
        else:
            err = (r.error if r else "生成失败")
            QMessageBox.warning(self, "LLM 失败", f"生成描述失败：{err}\n检查 LLM 后端配置。")

    def _on_save(self) -> None:
        title = self.title.text().strip()
        body = self.body.toPlainText().strip()
        if not title or not body:
            QMessageBox.warning(self, "缺字段", "标题和正文都必填。")
            return

        scope = self._scope_value()
        if scope == "project" and not self.project.text().strip():
            QMessageBox.warning(self, "缺项目名", "scope=project 时必须填项目名（短 slug）。")
            return

        # polish（同步阻塞，加 wait cursor）
        if self.polish.isChecked():
            from PySide6.QtCore import Qt as _Qt
            from PySide6.QtGui import QCursor
            from PySide6.QtWidgets import QApplication
            QApplication.setOverrideCursor(QCursor(_Qt.CursorShape.WaitCursor))
            try:
                r = optimizer.optimize(self.cfg, body)
                if r.success:
                    body = r.optimized
                else:
                    QMessageBox.information(
                        self, "polish 跳过",
                        f"LLM 优化未生效（{r.error}）；按原文保存。",
                    )
            finally:
                QApplication.restoreOverrideCursor()

        desc = self.description.text().strip()
        ref = self.source_ref.text().strip()

        # 装配 Prompt
        if self.prompt:
            p = self.prompt
            p.title = title
            p.body = body
            p.scope = scope
            p.project = self.project.text().strip() or None
            p.tags = _split_csv(self.tags.text())
            p.stack = _split_csv(self.stack.text())
            p.triggers = _split_csv(self.triggers.text())
            p.description = desc
            p.source_ref = ref
        else:
            p = storage.Prompt.new(
                title=title, body=body, scope=scope,
                project=self.project.text().strip() or None,
                tags=_split_csv(self.tags.text()),
                stack=_split_csv(self.stack.text()),
                triggers=_split_csv(self.triggers.text()),
                origin="manual",
            )
            p.description = desc
            p.source_ref = ref

        file_path = storage.save(self.cfg, p)
        conn = indexer.open_db(self.cfg)
        indexer.upsert(conn, p, file_path)
        conn.close()
        self.accept()


def _split_csv(s: str) -> list[str]:
    return [x.strip() for x in s.replace("，", ",").split(",") if x.strip()]


def _fallback_summarize(cfg, body: str):
    """如果 optimizer 没有 summarize 函数，用 polish 的 system prompt 改写一下当 fallback。"""
    from ...core import optimizer as _opt
    from ...core.optimizer import OptimizeResult
    sys_prompt = (
        "你是一名提示词工程师。基于下面的提示词正文，给出一句话描述（中文，30 字以内），"
        "说明这条提示词做什么用、适用场景。只输出这一句话，不要其他内容。"
    )
    try:
        # 直接复用 _run（内部已有 cc_cli / api 路由）
        result = _opt._run(cfg, body, system_prompt=sys_prompt, mode="auto")
        return result
    except Exception as e:
        return OptimizeResult(
            original=body, optimized="", diff_text="",
            success=False, error=str(e),
        )
