"""产品发现页（Phase 7 重写）：LLM 主导对话窗口。

去掉 7 阶段固定表单，改为：
- 顶部：三维度进度条（What/Why/How 0-10）+ 轮数
- 中部：消息气泡列表（用户/AI 交替）
- 底部：输入框 + 发送按钮 + 「我觉得够了，生成 brief」按钮
- 用户气泡支持编辑（点编辑后，该条之后的 AI 反问失效，触发重生成）

LLM 调用走后台 QThread，UI 实时显示「AI 正在思考…」loading。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QInputDialog, QLabel, QMessageBox,
    QPlainTextEdit, QProgressBar, QPushButton, QScrollArea, QSizePolicy,
    QStackedWidget, QVBoxLayout, QWidget,
)

from ...core import pm_dialog
from ...core.config import Config
from ...cli import pm_mode as pm_cli  # 复用 _dialog_dir / _slugify / _load_session / _save_session
from .. import icons as _icons


class PmModePage(QWidget):
    def __init__(self, cfg: Config):
        super().__init__()
        self.cfg = cfg
        self.session: Optional[pm_dialog.PMSession] = None
        self._tx_thread: Optional[QThread] = None
        self._build()
        self._show_session_list()

    # ------------------------------------------------------------------
    # UI build
    # ------------------------------------------------------------------

    def _build(self) -> None:
        v = QVBoxLayout(self)
        v.setContentsMargins(40, 28, 40, 24)
        v.setSpacing(10)

        title = QLabel("产品发现")
        title.setObjectName("pageTitle")
        v.addWidget(title)

        hint = QLabel(
            "**LLM 主导的苏格拉底式访谈**——AI 根据你的回答动态生成下一题，"
            "在 What / Why / How 三个维度都到 7/10 时自动生成 brief 四件套。"
        )
        hint.setObjectName("pageHint")
        hint.setTextFormat(Qt.TextFormat.MarkdownText)
        hint.setWordWrap(True)
        v.addWidget(hint)

        self.stack = QStackedWidget()
        v.addWidget(self.stack, 1)

        self.list_page = self._build_list_page()
        self.chat_page = self._build_chat_page()
        self.stack.addWidget(self.list_page)
        self.stack.addWidget(self.chat_page)

    def _build_list_page(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(12)

        bar = QHBoxLayout()
        self.btn_new = QPushButton(" 新对话")
        self.btn_new.setProperty("class", "primary")
        self.btn_new.setIcon(_icons.icon_white("new_item"))
        from PySide6.QtCore import QSize as _QSize
        self.btn_new.setIconSize(_QSize(14, 14))
        self.btn_new.clicked.connect(self._on_new_session)
        bar.addWidget(self.btn_new)
        bar.addStretch(1)
        v.addLayout(bar)

        self.list_scroll = QScrollArea()
        self.list_scroll.setWidgetResizable(True)
        self.list_host = QWidget()
        self.list_layout = QVBoxLayout(self.list_host)
        self.list_layout.setContentsMargins(0, 0, 0, 0)
        self.list_layout.setSpacing(8)
        self.list_scroll.setWidget(self.list_host)
        v.addWidget(self.list_scroll, 1)
        return w

    def _build_chat_page(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(10)

        # 顶部 bar：返回 + 标题 + 评分
        top = QHBoxLayout()
        self.btn_back = QPushButton("返回列表")
        self.btn_back.setProperty("class", "subtle")
        self.btn_back.clicked.connect(self._show_session_list)
        top.addWidget(self.btn_back)

        self.lbl_session_title = QLabel("")
        self.lbl_session_title.setStyleSheet("font-size: 14px; font-weight: 600;")
        top.addWidget(self.lbl_session_title, 1)

        # P12-N3：导出对话按钮
        self.btn_export = QPushButton("导出对话")
        self.btn_export.setProperty("class", "subtle")
        self.btn_export.setToolTip("把当前对话历史导出为 markdown 文件")
        self.btn_export.clicked.connect(self._on_export)
        top.addWidget(self.btn_export)
        v.addLayout(top)

        # 三维度进度条
        scores_row = QHBoxLayout()
        scores_row.setSpacing(16)
        self.bar_what = self._make_dim_bar("What", "#0a0a0a")
        self.bar_why = self._make_dim_bar("Why", "#0a0a0a")
        self.bar_how = self._make_dim_bar("How", "#0a0a0a")
        scores_row.addLayout(self.bar_what["layout"])
        scores_row.addLayout(self.bar_why["layout"])
        scores_row.addLayout(self.bar_how["layout"])
        self.lbl_turn = QLabel("第 0 轮")
        self.lbl_turn.setStyleSheet("color: #737373; font-size: 12px;")
        scores_row.addWidget(self.lbl_turn)
        v.addLayout(scores_row)

        # 消息气泡区
        self.msg_scroll = QScrollArea()
        self.msg_scroll.setWidgetResizable(True)
        self.msg_host = QWidget()
        self.msg_layout = QVBoxLayout(self.msg_host)
        self.msg_layout.setContentsMargins(8, 8, 8, 8)
        self.msg_layout.setSpacing(10)
        self.msg_scroll.setWidget(self.msg_host)
        v.addWidget(self.msg_scroll, 1)

        # 输入区
        input_row = QHBoxLayout()
        self.input_edit = QPlainTextEdit()
        self.input_edit.setPlaceholderText("回答 AI 的反问，或写「我觉得够了」让 AI 收尾…")
        self.input_edit.setFixedHeight(80)
        input_row.addWidget(self.input_edit, 1)

        col = QVBoxLayout()
        self.btn_send = QPushButton("发送 ↵")
        self.btn_send.setProperty("class", "primary")
        self.btn_send.clicked.connect(self._on_send)
        col.addWidget(self.btn_send)
        # A3：永久醒目可见的 escape hatch
        self.btn_finish = QPushButton("我觉得够了，生成 brief")
        self.btn_finish.setProperty("class", "primary")
        self.btn_finish.setStyleSheet(
            "QPushButton { background: #525252; color: white; border: 0; border-radius: 8px;"
            "padding: 6px 12px; font-size: 12px; }"
            "QPushButton:hover { background: #262626; }"
        )
        self.btn_finish.setToolTip("不想继续对话也可以——只要轮数 >= 3 就能生成 brief 4 件套")
        self.btn_finish.clicked.connect(self._on_finish)
        col.addWidget(self.btn_finish)
        input_row.addLayout(col)

        v.addLayout(input_row)

        self.lbl_status = QLabel("")
        self.lbl_status.setStyleSheet("color: #737373; font-size: 12px;")
        v.addWidget(self.lbl_status)
        return w

    def _make_dim_bar(self, label: str, color: str) -> dict:
        layout = QVBoxLayout()
        layout.setSpacing(2)
        lbl = QLabel(f"{label} 0/10")
        lbl.setStyleSheet("color: #525252; font-size: 11px;")
        layout.addWidget(lbl)
        bar = QProgressBar()
        bar.setRange(0, 10)
        bar.setValue(0)
        bar.setTextVisible(False)
        bar.setFixedHeight(6)
        bar.setStyleSheet(
            "QProgressBar { background: #f5f5f5; border: 0; border-radius: 3px; }"
            f"QProgressBar::chunk {{ background: {color}; border-radius: 3px; }}"
        )
        layout.addWidget(bar)
        return {"layout": layout, "label": lbl, "bar": bar, "name": label}

    # ------------------------------------------------------------------
    # 会话列表
    # ------------------------------------------------------------------

    def on_show(self) -> None:
        if self.stack.currentWidget() is self.list_page:
            self._refresh_list()

    def _show_session_list(self) -> None:
        self.stack.setCurrentWidget(self.list_page)
        self._refresh_list()

    def _refresh_list(self) -> None:
        while self.list_layout.count():
            it = self.list_layout.takeAt(0)
            if it.widget():
                it.widget().deleteLater()

        d = pm_cli._dialog_dir(self.cfg)
        files = sorted(d.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        if not files:
            empty = QLabel(
                "还没有对话。点上方「新对话」开始一次产品发现访谈。\n\n"
                "AI 会用苏格拉底式反问帮你想清楚要建什么、为什么建、技术死穴在哪。"
            )
            empty.setStyleSheet(
                "color: #737373; padding: 40px; font-size: 13px;"
                "background: #fafafa; border-radius: 10px; line-height: 1.7;"
            )
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            empty.setWordWrap(True)
            self.list_layout.addWidget(empty)
            self.list_layout.addStretch(1)
            return

        for f in files:
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
            except Exception:
                continue
            self.list_layout.addWidget(self._make_session_card(f.stem, data))
        self.list_layout.addStretch(1)

    def _make_session_card(self, slug: str, data: dict) -> QFrame:
        # P22：去掉 #fafafa 背景——浅灰卡片在白底页上视觉上像被"框住"。
        # 改 transparent + hover 浅灰，与 PromptCard 保持一致。
        f = QFrame()
        f.setFrameShape(QFrame.Shape.NoFrame)
        f.setStyleSheet(
            "QFrame { background: transparent; border: 0; border-radius: 10px; }"
            "QFrame:hover { background: #fafafa; }"
        )
        h = QHBoxLayout(f)
        h.setContentsMargins(16, 12, 16, 12)
        h.setSpacing(12)

        v = QVBoxLayout()
        v.setSpacing(4)
        title = QLabel(data.get("idea", slug)[:80])
        title.setStyleSheet("font-size: 14px; font-weight: 600; color: #0a0a0a;")
        title.setWordWrap(True)
        v.addWidget(title)

        scores = data.get("scores", {})
        turn = data.get("turn_count", 0)
        ready = data.get("ready", False)
        meta = (
            f"第 {turn} 轮 · "
            f"What:{scores.get('what',0)} / Why:{scores.get('why',0)} / How:{scores.get('how',0)}"
            f" · {'✅ 可生成 brief' if ready else '进行中'}"
        )
        m = QLabel(meta)
        m.setStyleSheet("color: #737373; font-size: 11px;")
        v.addWidget(m)
        h.addLayout(v, 1)

        btn_open = QPushButton("打开")
        btn_open.setProperty("class", "subtle")
        btn_open.clicked.connect(lambda _=False, s=slug: self._open_session(s))
        h.addWidget(btn_open)

        btn_del = QPushButton("删除")
        btn_del.setProperty("class", "subtle")
        btn_del.clicked.connect(lambda _=False, s=slug: self._delete_session(s))
        h.addWidget(btn_del)
        return f

    def _delete_session(self, slug: str) -> None:
        r = QMessageBox.question(
            self, "确认删除", f"删除对话 {slug}？（已生成的 brief 不会被删）",
        )
        if r != QMessageBox.StandardButton.Yes:
            return
        p = pm_cli._dialog_dir(self.cfg) / f"{slug}.json"
        if p.is_file():
            p.unlink()
        self._refresh_list()

    # ------------------------------------------------------------------
    # 新对话 / 打开
    # ------------------------------------------------------------------

    def _on_new_session(self) -> None:
        idea, ok = QInputDialog.getText(
            self, "新对话",
            "用一句话描述你的产品想法（例如「做个截止日期提醒 app，帮自由职业工程师追踪客户项目」）：",
        )
        if not ok or not idea.strip():
            return
        idea = idea.strip()
        slug = pm_cli._slugify(idea)
        # 重复 slug 加数字后缀
        d = pm_cli._dialog_dir(self.cfg)
        base = slug
        i = 1
        while (d / f"{slug}.json").is_file():
            i += 1
            slug = f"{base}-{i}"

        session = pm_dialog.PMSession(
            slug=slug, idea=idea, cwd=str(Path.cwd()),
        )
        session.append_user(idea)
        session.turn_count = 1
        pm_cli._save_session(self.cfg, session)
        self.session = session
        self._enter_chat()
        # 让 AI 自动出第一题
        self._ask_llm()

    def _open_session(self, slug: str) -> None:
        self.session = pm_cli._load_session(self.cfg, slug)
        self._enter_chat()

    def _enter_chat(self) -> None:
        self.stack.setCurrentWidget(self.chat_page)
        self._render_session()

    # ------------------------------------------------------------------
    # 渲染
    # ------------------------------------------------------------------

    def _render_session(self) -> None:
        if not self.session:
            return
        self.lbl_session_title.setText(f"对话：{self.session.idea[:60]}")
        self._update_scores(self.session.scores, self.session.turn_count)

        while self.msg_layout.count():
            it = self.msg_layout.takeAt(0)
            if it.widget():
                it.widget().deleteLater()

        for i, m in enumerate(self.session.history):
            self.msg_layout.addWidget(self._make_bubble(i, m["role"], m["content"]))
        self.msg_layout.addStretch(1)

        # 滚动到底
        sb = self.msg_scroll.verticalScrollBar()
        sb.setValue(sb.maximum())

        # 准备态：ready=True 时禁用输入区，提示生成 brief
        if self.session.ready:
            self.lbl_status.setText("三维度都已饱和。点「我觉得够了，生成 brief」可生成 4 件套。")
            self.input_edit.setPlaceholderText("（信息已足够，可直接点右侧生成 brief）")
        else:
            self.lbl_status.setText("")
            self.input_edit.setPlaceholderText("回答 AI 的反问…")

    def _update_scores(self, scores: dict, turn: int) -> None:
        for key, dim in [("what", self.bar_what), ("why", self.bar_why), ("how", self.bar_how)]:
            v = int(scores.get(key, 0))
            dim["bar"].setValue(v)
            dim["label"].setText(f"{dim['name']} {v}/10")
            # 维度 ≥7 高亮
            color = "#16a34a" if v >= 7 else "#0a0a0a"
            dim["bar"].setStyleSheet(
                "QProgressBar { background: #f5f5f5; border: 0; border-radius: 3px; }"
                f"QProgressBar::chunk {{ background: {color}; border-radius: 3px; }}"
            )
        self.lbl_turn.setText(f"第 {turn} 轮")

    def _make_bubble(self, index: int, role: str, content: str) -> QFrame:
        f = QFrame()
        is_user = role == "user"
        bg = "#0a0a0a" if is_user else "#fafafa"
        fg = "#fafafa" if is_user else "#0a0a0a"
        align = "right" if is_user else "left"
        f.setStyleSheet(
            f"QFrame {{ background: {bg}; border-radius: 12px; }}"
        )
        v = QVBoxLayout(f)
        v.setContentsMargins(14, 10, 14, 10)
        v.setSpacing(4)

        head = QHBoxLayout()
        label = QLabel("你" if is_user else "AI")
        label.setStyleSheet(f"color: {fg}; font-size: 11px; opacity: 0.7;")
        head.addWidget(label)
        head.addStretch(1)
        if is_user:
            btn_edit = QPushButton()
            from PySide6.QtCore import QSize as _QSize
            btn_edit.setIcon(_icons.icon_white("edit"))
            btn_edit.setIconSize(_QSize(12, 12))
            btn_edit.setFixedSize(24, 20)
            btn_edit.setStyleSheet(
                "QPushButton { background: transparent; border: 0; }"
            )
            btn_edit.setToolTip("编辑此条（之后的 AI 反问会重新生成）")
            btn_edit.clicked.connect(lambda _=False, idx=index: self._edit_user_message(idx))
            head.addWidget(btn_edit)
        v.addLayout(head)

        body = QLabel(content)
        body.setStyleSheet(f"color: {fg}; font-size: 13px; line-height: 1.5;")
        body.setWordWrap(True)
        body.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        v.addWidget(body)

        # 用 size policy 控制气泡最大宽度
        f.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        f.setMaximumWidth(700)

        wrap = QHBoxLayout()
        if is_user:
            wrap.addStretch(1)
            wrap.addWidget(f)
        else:
            wrap.addWidget(f)
            wrap.addStretch(1)
        host = QWidget()
        host.setLayout(wrap)
        return host

    # ------------------------------------------------------------------
    # 用户操作
    # ------------------------------------------------------------------

    def _on_send(self) -> None:
        if not self.session:
            return
        text = self.input_edit.toPlainText().strip()
        if not text:
            return
        self.input_edit.clear()
        self.session.append_user(text)
        self.session.turn_count = sum(
            1 for m in self.session.history if m["role"] == "user"
        )
        pm_cli._save_session(self.cfg, self.session)
        self._render_session()
        self._ask_llm()

    def _edit_user_message(self, index: int) -> None:
        if not self.session:
            return
        old = self.session.history[index]["content"]
        new_text, ok = QInputDialog.getMultiLineText(
            self, "编辑回答", "改完后，这条之后的 AI 反问会重新生成：", old,
        )
        if not ok or not new_text.strip():
            return
        try:
            self.session.edit_user_at(index, new_text.strip())
        except (IndexError, ValueError) as e:
            QMessageBox.warning(self, "编辑失败", str(e))
            return
        pm_cli._save_session(self.cfg, self.session)
        self._render_session()
        self._ask_llm()

    def _ask_llm(self) -> None:
        if not self.session:
            return
        self.btn_send.setEnabled(False)
        self.btn_finish.setEnabled(False)
        self.lbl_status.setText("💭 AI 正在思考下一题…（CC CLI 冷启动约 15-20s，超过 60s 会自动恢复按钮）")
        self._tx_thread = _NextQuestionThread(self.cfg, self.session)
        self._tx_thread.done.connect(self._on_llm_done)
        self._tx_thread.start()
        # P16-T4：看门狗——防止 thread 崩溃 / 卡住按钮永久锁死
        from PySide6.QtCore import QTimer
        self._llm_watchdog = QTimer(self)
        self._llm_watchdog.setSingleShot(True)
        self._llm_watchdog.timeout.connect(self._on_llm_timeout)
        self._llm_watchdog.start(60000)  # 60s

    def _on_llm_timeout(self) -> None:
        """看门狗超时——恢复按钮，让用户能重试 / 强制收尾。"""
        if not self.btn_send.isEnabled():
            self.btn_send.setEnabled(True)
            self.btn_finish.setEnabled(True)
            self.lbl_status.setText(
                "⚠ AI 60 秒没响应。按钮已恢复——可重新发送、或点「我觉得够了」收尾。"
            )

    def _on_llm_done(self, result: dict) -> None:
        # 收到回调，取消看门狗
        if hasattr(self, "_llm_watchdog") and self._llm_watchdog is not None:
            try:
                self._llm_watchdog.stop()
            except Exception:
                pass
        self.btn_send.setEnabled(True)
        self.btn_finish.setEnabled(True)
        if not self.session:
            return
        if result.get("error"):
            err = str(result.get("error") or "")
            # 友好分类常见错误
            if "API key" in err or "api_key_env" in err or "未设置" in err:
                hint = "❌ LLM 后端不可用——你还没配 API key。\n\n打开「设置 → LLM polish 配置」填 DEEPSEEK_API_KEY，或确保 Claude Code CLI 在 PATH 里。"
            elif "claude CLI" in err or "退出码" in err:
                hint = "❌ Claude Code CLI 调用失败。\n\n检查：（1）`claude` 命令在 PATH 里能跑（终端试 `claude --version`）；（2）已登录。"
            elif "超时" in err.lower() or "timeout" in err.lower():
                hint = "⚠ LLM 响应超时。CC CLI 冷启动慢（首次约 15-20s）。\n\n再点一下「发送」重试，或在设置里调高 cc_cli_timeout_seconds。"
            else:
                hint = f"⚠ {err}\n\n按「我觉得够了」直接收尾用现有信息生成 brief，或检查 LLM 后端。"
            self.lbl_status.setText(f"出错：{err}")
            QMessageBox.warning(self, "LLM 失败", hint)
            return
        scores = result.get("scores") or self.session.scores
        self.session.scores = scores
        if result["ready"]:
            self.session.ready = True
        elif result.get("next_question"):
            self.session.append_assistant(result["next_question"])
        pm_cli._save_session(self.cfg, self.session)
        self._render_session()

    def _on_export(self) -> None:
        """P12-N3：导出对话历史为 markdown。"""
        if not self.session:
            QMessageBox.information(self, "无对话", "还没开始对话。")
            return
        from PySide6.QtWidgets import QFileDialog
        default_name = f"pm-dialog-{self.session.slug}.md"
        path, _ = QFileDialog.getSaveFileName(
            self, "保存对话", default_name, "Markdown (*.md)",
        )
        if not path:
            return
        lines = [
            f"# 产品发现对话：{self.session.idea}",
            "",
            f"- **slug**: `{self.session.slug}`",
            f"- **创建**: {self.session.created[:19]}",
            f"- **更新**: {self.session.updated[:19]}",
            f"- **轮数**: {self.session.turn_count}",
            f"- **三维度评分**: What {self.session.scores.get('what', 0)} / "
            f"Why {self.session.scores.get('why', 0)} / How {self.session.scores.get('how', 0)}",
            f"- **ready**: {self.session.ready}",
            "",
            "## 对话历史",
            "",
        ]
        for i, m in enumerate(self.session.history):
            role = "**你**" if m["role"] == "user" else "**AI**"
            lines.append(f"### #{i} · {role}")
            lines.append("")
            lines.append(m["content"])
            lines.append("")
        try:
            from pathlib import Path
            Path(path).write_text("\n".join(lines), encoding="utf-8")
            QMessageBox.information(self, "已导出", f"对话已写入：\n{path}")
        except Exception as e:
            QMessageBox.warning(self, "导出失败", f"{type(e).__name__}: {e}")

    def _on_finish(self) -> None:
        if not self.session:
            return
        if self.session.turn_count < 3:
            QMessageBox.information(
                self, "对话太短",
                f"才跑了 {self.session.turn_count} 轮，建议至少 3 轮再生成 brief。继续回答 AI 的反问。",
            )
            return
        self.btn_send.setEnabled(False)
        self.btn_finish.setEnabled(False)
        self.lbl_status.setText("💭 LLM 正在生成 4 件套（brief / stories / risks / decisions）… 超时 90s 自动恢复")
        self._bundle_thread = _GenerateBundleThread(self.cfg, self.session)
        self._bundle_thread.done.connect(self._on_bundle_done)
        self._bundle_thread.start()
        # P16-T4 看门狗（4 件套生成更慢，给 90s）
        from PySide6.QtCore import QTimer
        self._bundle_watchdog = QTimer(self)
        self._bundle_watchdog.setSingleShot(True)
        self._bundle_watchdog.timeout.connect(self._on_bundle_timeout)
        self._bundle_watchdog.start(90000)

    def _on_bundle_timeout(self) -> None:
        if not self.btn_send.isEnabled():
            self.btn_send.setEnabled(True)
            self.btn_finish.setEnabled(True)
            self.lbl_status.setText("生成 brief 超时 90 秒。按钮已恢复——可重试或继续对话。")

    def _on_bundle_done(self, bundle: dict) -> None:
        if hasattr(self, "_bundle_watchdog") and self._bundle_watchdog is not None:
            try:
                self._bundle_watchdog.stop()
            except Exception:
                pass
        self.btn_send.setEnabled(True)
        self.btn_finish.setEnabled(True)
        if not self.session:
            return
        if bundle.get("error"):
            self.lbl_status.setText(f"生成失败：{bundle['error']}")
            QMessageBox.warning(self, "生成失败", bundle["error"])
            return

        date = self.session.created[:10]
        out_dir = self.cfg.briefs_dir / f"{date}-{self.session.slug}"
        out_dir.mkdir(parents=True, exist_ok=True)
        for key, fname in [
            ("brief", "PRODUCT_BRIEF.md"),
            ("user_stories", "USER_STORIES.md"),
            ("risks", "RISKS.md"),
            ("decisions", "DECISIONS.md"),
        ]:
            (out_dir / fname).write_text(bundle.get(key, "") or "", encoding="utf-8")

        # 写 cwd 项目根（仅 PRODUCT_BRIEF）
        cwd_path = Path(self.session.cwd) if self.session.cwd else None
        if cwd_path and cwd_path.is_dir():
            (cwd_path / "PRODUCT_BRIEF.md").write_text(bundle["brief"], encoding="utf-8")

        QMessageBox.information(
            self, "完成",
            f"4 件套已生成到：\n\n{out_dir}\n\n"
            f"PRODUCT_BRIEF.md 同步写入项目根：{cwd_path or '（未设置）'}",
        )
        self.lbl_status.setText(f"已生成到 {out_dir}")


# ----------------------------------------------------------------------------
# 后台线程
# ----------------------------------------------------------------------------

class _NextQuestionThread(QThread):
    done = Signal(dict)

    def __init__(self, cfg: Config, session: pm_dialog.PMSession):
        super().__init__()
        self.cfg = cfg
        self.session = session

    def run(self) -> None:
        try:
            r = pm_dialog.next_question(self.cfg, self.session)
        except Exception as e:
            r = {"scores": self.session.scores, "next_question": "",
                 "ready": False, "error": f"{type(e).__name__}: {e}"}
        self.done.emit(r)


class _GenerateBundleThread(QThread):
    done = Signal(dict)

    def __init__(self, cfg: Config, session: pm_dialog.PMSession):
        super().__init__()
        self.cfg = cfg
        self.session = session

    def run(self) -> None:
        try:
            b = pm_dialog.generate_brief_bundle(self.cfg, self.session)
        except Exception as e:
            b = {"error": f"{type(e).__name__}: {e}"}
        self.done.emit(b)
