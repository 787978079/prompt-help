"""通用模板生成对话框（Phase 8 T3 核心 UI）。

流程：
1. 弹对话框，显示原文
2. 启后台 QThread 调 optimizer.generalize（CC CLI 优先、API fallback）
3. 完成后并排显示「原文 / 通用模板版」+ diff 高亮
4. 用户三选：
   - 「保留原版」（默认，不动）
   - 「用通用模板替换」（更新原条目 body + is_template=True）
   - 「双版本都存」（新建一条 is_template=True, optimized_from=<原 id>）
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QMessageBox, QPlainTextEdit,
    QProgressBar, QPushButton, QSplitter, QVBoxLayout, QWidget,
)

from ...core import indexer, optimizer, storage
from ...core.config import Config


class _GeneralizeThread(QThread):
    done = Signal(object)  # OptimizeResult

    def __init__(self, cfg: Config, original: str):
        super().__init__()
        self.cfg = cfg
        self.original = original

    def run(self) -> None:
        try:
            r = optimizer.generalize(self.cfg, self.original)
        except Exception as e:
            from dataclasses import dataclass
            @dataclass
            class _Fail:
                original: str
                optimized: str
                success: bool
                error: str
                backend: str = ""
                diff_text: str = ""
            r = _Fail(original=self.original, optimized=self.original,
                      success=False, error=f"{type(e).__name__}: {e}")
        self.done.emit(r)


class GeneralizeDialog(QDialog):
    """Diff 对话框：左原文 / 右通用模板版 / 底部三选项。"""

    def __init__(self, cfg: Config, row: dict, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self.row = row
        self.original_body = row.get("body") or ""
        self.optimized: Optional[str] = None
        self.action: str = "cancel"
        self._build()
        self._start_llm()

    def _build(self) -> None:
        self.setWindowTitle("⚡ 生成通用模板")
        self.resize(960, 600)

        v = QVBoxLayout(self)
        v.setContentsMargins(20, 18, 20, 16)
        v.setSpacing(10)

        title = QLabel(f"📝 「{self.row.get('title', '')[:60]}」 → 通用模板")
        title.setStyleSheet("font-size: 15px; font-weight: 600; color: #0a0a0a;")
        v.addWidget(title)

        hint = QLabel(
            "LLM 会把项目名 / 文件路径 / 版本号 / UUID / task ID / 具体环境变量 → 抽象成 `[占位符]`，"
            "保留指令结构和约束清单。生成完后你可以选择保留原版、替换、或双版本都存。"
        )
        hint.setStyleSheet("color: #525252; font-size: 12px; line-height: 1.5;")
        hint.setWordWrap(True)
        v.addWidget(hint)

        # 进度条
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)  # busy
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(4)
        v.addWidget(self.progress)
        self.status = QLabel("💭 LLM 生成中…（CC CLI 冷启动约 15-20s）")
        self.status.setStyleSheet("color: #737373; font-size: 12px;")
        v.addWidget(self.status)

        # 左右并排：原文 / 通用模板
        split = QSplitter(Qt.Orientation.Horizontal)

        left_box = QWidget()
        lv = QVBoxLayout(left_box)
        lv.setContentsMargins(0, 0, 0, 0)
        lv.setSpacing(4)
        lv.addWidget(self._col_header("📄 原文（项目化）"))
        self.edit_original = QPlainTextEdit(self.original_body)
        self.edit_original.setReadOnly(True)
        self.edit_original.setStyleSheet(
            "QPlainTextEdit { background: #fafafa; border: 0; "
            "border-radius: 6px; padding: 8px; font-family: monospace; font-size: 12px; }"
        )
        lv.addWidget(self.edit_original)
        split.addWidget(left_box)

        right_box = QWidget()
        rv = QVBoxLayout(right_box)
        rv.setContentsMargins(0, 0, 0, 0)
        rv.setSpacing(4)
        rv.addWidget(self._col_header("🎯 通用模板（已抽象）"))
        self.edit_generalized = QPlainTextEdit("（等待 LLM 生成…）")
        self.edit_generalized.setStyleSheet(
            "QPlainTextEdit { background: #fffdf6; border: 0; "
            "border-radius: 6px; padding: 8px; font-family: monospace; font-size: 12px; }"
        )
        rv.addWidget(self.edit_generalized)
        split.addWidget(right_box)

        split.setSizes([480, 480])
        v.addWidget(split, 1)

        # 底部操作按钮
        actions = QHBoxLayout()
        actions.addStretch(1)

        self.btn_keep = QPushButton("保留原版（不动）")
        self.btn_keep.setStyleSheet(
            "QPushButton { background: transparent; color: #737373; border: 0; "
            "padding: 8px 14px; font-size: 12px; }"
        )
        self.btn_keep.clicked.connect(self._on_keep)
        actions.addWidget(self.btn_keep)

        self.btn_replace = QPushButton("用通用模板替换原文")
        self.btn_replace.setStyleSheet(
            "QPushButton { background: #fafafa; color: #0a0a0a; border: 1px solid #d4d4d4; "
            "border-radius: 6px; padding: 8px 14px; font-size: 12px; }"
            "QPushButton:hover:enabled { background: #f0f0f0; }"
        )
        self.btn_replace.setEnabled(False)
        self.btn_replace.clicked.connect(self._on_replace)
        actions.addWidget(self.btn_replace)

        self.btn_both = QPushButton(" 双版本都存（推荐）")
        from .. import icons as _icons
        from PySide6.QtCore import QSize as _QSize
        self.btn_both.setIcon(_icons.icon_white("generalize"))
        self.btn_both.setIconSize(_QSize(14, 14))
        self.btn_both.setStyleSheet(
            "QPushButton { background: #0a0a0a; color: white; border: 0; "
            "border-radius: 6px; padding: 8px 16px; font-size: 12px; font-weight: 600; }"
            "QPushButton:hover:enabled { background: #262626; }"
            "QPushButton:disabled { background: #a3a3a3; }"
        )
        self.btn_both.setEnabled(False)
        self.btn_both.clicked.connect(self._on_both)
        actions.addWidget(self.btn_both)

        v.addLayout(actions)

    def _col_header(self, text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setStyleSheet("color: #0a0a0a; font-size: 12px; font-weight: 600; padding: 4px 0;")
        return lbl

    # ------------------------------------------------------------------

    def _start_llm(self) -> None:
        self._thread = _GeneralizeThread(self.cfg, self.original_body)
        self._thread.done.connect(self._on_llm_done)
        self._thread.start()

    def _on_llm_done(self, result) -> None:
        self.progress.setRange(0, 1)
        self.progress.setValue(1)
        if not getattr(result, "success", False):
            err = getattr(result, "error", "未知错误")
            self.status.setText(f"生成失败：{err}")
            # P16-T4 修：失败时让 btn_keep 醒目，告诉用户出口
            self.btn_keep.setStyleSheet(
                "QPushButton { background: #0a0a0a; color: white; border: 0; "
                "border-radius: 6px; padding: 8px 16px; font-size: 12px; font-weight: 600; }"
                "QPushButton:hover { background: #262626; }"
            )
            self.btn_keep.setText("关闭（不改原条目）")
            QMessageBox.warning(
                self, "通用化失败",
                f"LLM 调用失败：\n\n{err}\n\n"
                "请检查后端：设置 → API key 或确认 CC CLI 可达。\n"
                "点底部「关闭」按钮退出本对话框。",
            )
            return
        self.optimized = (result.optimized or "").strip()
        if not self.optimized or self.optimized == self.original_body:
            # A2 修：无需改动时仍允许操作——给用户"标记为通用模板"的出路
            self.status.setText(
                "ℹ️ LLM 判断本身已经够通用——可直接「标记为通用模板」无需重写，或「保留原版」关闭。"
            )
            self.edit_generalized.setPlainText(self.optimized or self.original_body)
            # 复用 btn_both 做"原样标记为模板"——内部不写副本，只改 is_template
            self.btn_both.setEnabled(True)
            self.btn_both.setText(" 标记为通用模板")
            return
        self.edit_generalized.setPlainText(self.optimized)
        self.status.setText(f"✓ 已生成（后端：{getattr(result, 'backend', '?')}）")
        self.btn_replace.setEnabled(True)
        self.btn_both.setEnabled(True)

    # ------------------------------------------------------------------

    def _on_keep(self) -> None:
        self.action = "cancel"
        self.reject()

    def _on_replace(self) -> None:
        """用通用模板替换原文：同条目 body 改写 + is_template=True。"""
        if not self.optimized:
            return
        # A7：destructive 操作加二次确认
        ans = QMessageBox.warning(
            self,
            "确认替换原文",
            "替换后原条目的「项目化原文」会被通用模板覆盖，"
            "**这个动作不可撤销**（除非从 git 历史里手动恢复）。\n\n"
            "如果想保留原版，请改选「双版本都存（推荐）」。\n\n"
            "确定替换吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if ans != QMessageBox.StandardButton.Yes:
            return
        try:
            # 读原文件，改 body + is_template，再 save
            from pathlib import Path
            path = Path(self.row["file_path"])
            p = storage.load(path)
            p.body = self.optimized
            p.is_template = True
            p.tags = list(set(p.tags + ["通用模板"]))
            storage.save(self.cfg, p, commit_msg=f"generalize replace: {p.title[:30]}")
            conn = indexer.open_db(self.cfg)
            indexer.upsert(conn, p, path)
            conn.close()
            self.action = "replace"
            QMessageBox.information(
                self, "完成",
                "原条目已升级为通用模板版本，可在「🎯 通用模板」tab 看到。",
            )
            self.accept()
        except Exception as e:
            QMessageBox.warning(self, "替换失败", f"{type(e).__name__}: {e}")

    def _on_both(self) -> None:
        """双版本都存：原条目保留，新建一条 is_template=True，optimized_from 关联原 id。

        v0.2 修复：旧实现遇到 optimized == original（LLM 判断无需改写）时
        **短路升级原条目**——结果用户点了"双版本"却只看到一条。现在统一行为：
        无论 LLM 是否改写，永远生成第二份模板版副本；原条目完全不动。
        """
        if not self.optimized:
            return

        # 保留原版 tags + categories + 元数据，方便用户在「通用模板」tab 一眼看出来源
        raw_tags = [t.strip() for t in (self.row.get("tags_csv") or "").split(",") if t.strip()]
        if "通用模板" not in raw_tags:
            raw_tags.append("通用模板")
        # 内容相同的特殊场景：加 tag 标记给用户知道为啥两条 body 一样
        body_same = self.optimized == self.original_body
        if body_same and "原文已通用" not in raw_tags:
            raw_tags.append("原文已通用")

        raw_categories = [
            c.strip() for c in (self.row.get("categories_csv") or "").split(",")
            if c.strip()
        ]
        # 强制让"参考来源"可追溯：优先用原条目 source_ref，没有就用 project，
        # 最终兜底原条目 title——保证模板版永远能溯源回原版
        src_ref = (
            self.row.get("source_ref")
            or self.row.get("project")
            or f"原条目: {self.row.get('title', '')[:30]}"
        )

        # LLM 同步生成 ≤10 字精炼标题，失败兜底原标题截前 10 字（不加后缀）
        orig_title = (self.row.get("title", "") or "untitled").strip()
        # 兜底也要剥 LLM 可能加的旧后缀
        for s in ("（通用模板）", "(通用模板)", "【通用提示词】", "【项目优化】"):
            orig_title = orig_title.replace(s, "")
        fallback_title = orig_title.strip()[:10] or "未命名"
        new_title = optimizer.safe_generate_title(
            self.cfg, self.optimized,
            fallback=fallback_title,
            kind="template",
        )
        try:
            new = storage.Prompt.new(
                title=new_title,
                body=self.optimized,
                scope="global",
                tags=raw_tags,
                origin="generalized",
                source_ref=src_ref,
                description=self.row.get("description") or "",
                optimized_from=self.row["id"],
                is_template=True,
                categories=raw_categories,
            )
            path = storage.save(
                self.cfg, new, commit_msg=f"generalize both: {new.title[:30]}",
            )
            conn = indexer.open_db(self.cfg)
            indexer.upsert(conn, new, path)
            conn.close()
            self.action = "both"
            tail = (
                "（LLM 判断原文已经够通用——副本内容相同但标 is_template=True，"
                "方便你在「通用模板」tab 看到）"
                if body_same else ""
            )
            QMessageBox.information(
                self, "完成",
                f"已新增通用模板条目「{new.title[:40]}」。\n"
                f"原条目保持不变，参考来源指向：{src_ref[:60]}\n\n{tail}\n"
                f"切到「🎯 通用模板」tab 查看新增的模板版。",
            )
            self.accept()
        except Exception as e:
            QMessageBox.warning(self, "保存失败", f"{type(e).__name__}: {e}")
