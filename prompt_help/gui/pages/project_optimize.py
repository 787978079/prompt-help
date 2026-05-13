"""项目优化页：粘贴提示词 + 选项目 → LLM 基于项目上下文重写。

UI 布局（垂直）：
  ┌─ 页标题 + 说明
  ├─ 原始提示词输入区（QPlainTextEdit，多行）
  ├─ 项目选择行（已登记下拉 + 浏览按钮 + 当前路径显示）
  ├─ 「优化」按钮 + 状态文字
  └─ 优化结果区（QPlainTextEdit + 复制 / 保存到我的库 / 重试 按钮）
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from PySide6.QtCore import QSize, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QComboBox, QDialog, QFileDialog, QFrame, QHBoxLayout, QLabel, QListWidget,
    QListWidgetItem, QMessageBox, QPlainTextEdit, QProgressBar, QPushButton,
    QScrollArea, QSizePolicy, QSplitter, QVBoxLayout, QWidget,
)

from .. import icons as _icons
from ...core import indexer, storage
from ...core.config import Config
from ...core.optimizer import OptimizeResult
from ...core.project_optimize import (
    extract_project_summary, optimize_for_project,
)


_HISTORY_MAX = 20
_HISTORY_FILE = "project_optimize_history.json"


def _history_path(cfg: Config) -> Path:
    return cfg.vault_path / _HISTORY_FILE


def _load_history(cfg: Config) -> list[dict]:
    p = _history_path(cfg)
    if not p.is_file():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (OSError, json.JSONDecodeError):
        return []


def _append_history(cfg: Config, entry: dict) -> None:
    items = _load_history(cfg)
    items.insert(0, entry)
    items = items[:_HISTORY_MAX]
    try:
        _history_path(cfg).write_text(
            json.dumps(items, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except OSError:
        pass


class _OptimizeWorker(QThread):
    """后台跑 LLM 优化——避免阻塞 UI。"""

    finished = Signal(object, float)  # OptimizeResult, elapsed_seconds
    failed = Signal(str, float)

    def __init__(self, cfg: Config, prompt: str, project_path: Path):
        super().__init__()
        self.cfg = cfg
        self.prompt = prompt
        self.project_path = project_path

    def run(self) -> None:
        import time as _t
        t0 = _t.monotonic()
        try:
            result = optimize_for_project(self.cfg, self.prompt, self.project_path)
            elapsed = _t.monotonic() - t0
            self.finished.emit(result, elapsed)
        except Exception as e:
            elapsed = _t.monotonic() - t0
            self.failed.emit(f"{type(e).__name__}: {e}", elapsed)


class _TitleWorker(QThread):
    """后台调 LLM 生成 ≤10 字标题——避免保存按钮卡 UI。"""

    finished = Signal(str, float)  # title, elapsed
    failed = Signal(str, float)

    def __init__(self, cfg: Config, body: str, fallback: str, kind: str):
        super().__init__()
        self.cfg = cfg
        self.body = body
        self.fallback = fallback
        self.kind = kind

    def run(self) -> None:
        import time as _t
        t0 = _t.monotonic()
        try:
            from ...core import optimizer
            t = optimizer.safe_generate_title(
                self.cfg, self.body, fallback=self.fallback, kind=self.kind,
            )
            elapsed = _t.monotonic() - t0
            self.finished.emit(t or self.fallback, elapsed)
        except Exception as e:
            elapsed = _t.monotonic() - t0
            self.failed.emit(f"{type(e).__name__}: {e}", elapsed)


class ProjectOptimizePage(QWidget):
    def __init__(self, cfg: Config):
        super().__init__()
        self.cfg = cfg
        self._worker: _OptimizeWorker | None = None
        self._build()
        self.on_show()

    def _build(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        host = QWidget()
        scroll.setWidget(host)
        outer.addWidget(scroll)

        v = QVBoxLayout(host)
        v.setContentsMargins(16, 16, 16, 16)
        v.setSpacing(12)

        title = QLabel("项目优化")
        title.setObjectName("pageTitle")
        v.addWidget(title)
        hint = QLabel(
            "粘贴一条通用提示词 + 选一个项目，LLM 会读项目的 CLAUDE.md / package.json / "
            "README 等核心元信息，把提示词改写成针对该项目栈和约定的高质量版本。"
        )
        hint.setObjectName("pageHint")
        hint.setWordWrap(True)
        v.addWidget(hint)

        # ---- 1. 原始提示词输入 ----
        lbl_in = QLabel("原始提示词")
        lbl_in.setStyleSheet("font-size: 13px; font-weight: 600; color: #0a0a0a;")
        v.addWidget(lbl_in)

        self.input_edit = QPlainTextEdit()
        self.input_edit.setPlaceholderText(
            "粘贴你想优化的提示词，例如：\n"
            "  - 帮我加一个用户头像组件\n"
            "  - 跑完整测试套件，确保没有回归\n"
            "  - 修一下登录接口的 bug"
        )
        self.input_edit.setMinimumHeight(140)
        self.input_edit.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        v.addWidget(self.input_edit)

        # ---- 2. 项目选择 ----
        proj_row = QHBoxLayout()
        proj_row.setSpacing(8)
        lbl_proj = QLabel("目标项目")
        lbl_proj.setStyleSheet("font-size: 13px; font-weight: 600; color: #0a0a0a;")
        proj_row.addWidget(lbl_proj)

        self.project_combo = QComboBox()
        self.project_combo.setMinimumWidth(280)
        self.project_combo.currentIndexChanged.connect(self._on_combo_changed)
        proj_row.addWidget(self.project_combo, 1)

        self.btn_browse = QPushButton(" 浏览其他...")
        self.btn_browse.setProperty("class", "subtle")
        self.btn_browse.setIcon(_icons.icon("folder"))
        self.btn_browse.setIconSize(QSize(14, 14))
        self.btn_browse.setToolTip("选一个未登记的项目目录临时使用（不登记到 PH）")
        self.btn_browse.clicked.connect(self._on_browse)
        proj_row.addWidget(self.btn_browse)
        v.addLayout(proj_row)

        self.path_lbl = QLabel("（请选项目）")
        self.path_lbl.setStyleSheet("color: #525252; font-size: 11px; padding-left: 80px;")
        self.path_lbl.setWordWrap(True)
        v.addWidget(self.path_lbl)

        # ---- 3. 优化按钮 + 状态 ----
        act_row = QHBoxLayout()
        self.btn_optimize = QPushButton(" 优化（Ctrl+Enter）")
        self.btn_optimize.setProperty("class", "primary")
        self.btn_optimize.setIcon(_icons.icon_white("generalize"))
        self.btn_optimize.setIconSize(QSize(14, 14))
        self.btn_optimize.setToolTip("快捷键：Ctrl+Enter")
        self.btn_optimize.clicked.connect(self._on_optimize)
        act_row.addWidget(self.btn_optimize)

        self.status_lbl = QLabel("")
        self.status_lbl.setStyleSheet("color: #737373; font-size: 12px;")
        act_row.addWidget(self.status_lbl, 1)

        # 失败后兜底：切后端引导（隐藏，失败时再显示）
        self.btn_switch_backend = QPushButton(" 切到其他后端")
        self.btn_switch_backend.setProperty("class", "subtle")
        self.btn_switch_backend.setIcon(_icons.icon("settings"))
        self.btn_switch_backend.setIconSize(QSize(14, 14))
        self.btn_switch_backend.setToolTip("跳到「设置 → LLM 配置」切换 Claude Code CLI / Codex CLI / DeepSeek API")
        self.btn_switch_backend.clicked.connect(self._on_jump_settings)
        self.btn_switch_backend.setVisible(False)
        act_row.addWidget(self.btn_switch_backend)

        # 历史按钮
        self.btn_history = QPushButton(" 历史")
        self.btn_history.setProperty("class", "subtle")
        self.btn_history.setIcon(_icons.icon("history"))
        self.btn_history.setIconSize(QSize(14, 14))
        self.btn_history.setToolTip(f"查看最近 {_HISTORY_MAX} 次项目优化记录")
        self.btn_history.clicked.connect(self._on_show_history)
        act_row.addWidget(self.btn_history)

        v.addLayout(act_row)

        # 进度条（determinate，基于历史 ETA 推进；超 ETA 后卡 95% 等真完成）
        self.progress = QProgressBar()
        self.progress.setRange(0, 1000)  # 千分制，更平滑
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(6)
        self.progress.setVisible(False)
        v.addWidget(self.progress)

        # 200ms tick：刷新 progress + ETA 文字
        self._tick_ms = 200
        self._eta_seconds = 0.0
        self._elapsed_ms = 0
        self._busy_kind = ""
        self._busy_backend = ""
        self._tick_timer = QTimer(self)
        self._tick_timer.setInterval(self._tick_ms)
        self._tick_timer.timeout.connect(self._on_tick)

        # Ctrl+Enter 触发优化（input_edit 焦点内有效）
        sc = QShortcut(QKeySequence("Ctrl+Return"), self.input_edit)
        sc.activated.connect(self._on_optimize)
        sc2 = QShortcut(QKeySequence("Ctrl+Enter"), self.input_edit)
        sc2.activated.connect(self._on_optimize)

        # ---- 4. 输出区 ----
        lbl_out = QLabel("优化结果")
        lbl_out.setStyleSheet("font-size: 13px; font-weight: 600; color: #0a0a0a;")
        v.addWidget(lbl_out)

        self.output_edit = QPlainTextEdit()
        self.output_edit.setReadOnly(True)
        self.output_edit.setPlaceholderText("点上方「优化」按钮后，LLM 改写结果会在这里出现。")
        self.output_edit.setMinimumHeight(200)
        self.output_edit.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        v.addWidget(self.output_edit, 1)

        out_actions = QHBoxLayout()
        self.btn_copy = QPushButton(" 复制结果")
        self.btn_copy.setProperty("class", "subtle")
        self.btn_copy.setIcon(_icons.icon("copy"))
        self.btn_copy.setIconSize(QSize(14, 14))
        self.btn_copy.clicked.connect(self._on_copy)
        self.btn_copy.setEnabled(False)
        out_actions.addWidget(self.btn_copy)

        self.btn_save = QPushButton(" 保存到我的库")
        self.btn_save.setProperty("class", "subtle")
        self.btn_save.setIcon(_icons.icon("new_item"))
        self.btn_save.setIconSize(QSize(14, 14))
        self.btn_save.clicked.connect(self._on_save)
        self.btn_save.setEnabled(False)
        out_actions.addWidget(self.btn_save)

        self.btn_retry = QPushButton(" 重试")
        self.btn_retry.setProperty("class", "subtle")
        self.btn_retry.setIcon(_icons.icon("refresh"))
        self.btn_retry.setIconSize(QSize(14, 14))
        self.btn_retry.clicked.connect(self._on_optimize)
        self.btn_retry.setEnabled(False)
        out_actions.addWidget(self.btn_retry)
        out_actions.addStretch(1)
        v.addLayout(out_actions)

        # 内部状态
        self._current_project_path: Path | None = None

    # ------------------------------------------------------------------
    # 项目选择
    # ------------------------------------------------------------------

    def on_show(self) -> None:
        self._reload_projects()

    def _reload_projects(self) -> None:
        """从 indexer 拉已登记项目填入下拉。"""
        prev = self.project_combo.currentData()
        self.project_combo.blockSignals(True)
        self.project_combo.clear()
        try:
            conn = indexer.open_db(self.cfg)
            try:
                rows = indexer.list_projects(conn)
            finally:
                conn.close()
        except Exception:
            rows = []

        if not rows:
            self.project_combo.addItem("（还没登记项目 — 点右边「浏览其他」选目录）", None)
        else:
            self.project_combo.addItem(f"（选一个已登记项目 · 共 {len(rows)} 个）", None)
            for row in rows:
                name = row["name"]
                cwd = row["cwd_path"] or ""
                self.project_combo.addItem(f"{name}  ·  {cwd}", cwd)
        self.project_combo.blockSignals(False)

        # 恢复之前选中
        if prev:
            idx = self.project_combo.findData(prev)
            if idx >= 0:
                self.project_combo.setCurrentIndex(idx)
                return
        self._on_combo_changed(0)

    def _on_combo_changed(self, _idx: int) -> None:
        data = self.project_combo.currentData()
        if data:
            self._current_project_path = Path(data)
            self.path_lbl.setText(f"路径：{data}")
        else:
            self._current_project_path = None
            self.path_lbl.setText("（请从下拉选已登记项目，或点「浏览其他」选任意目录）")

    def _on_browse(self) -> None:
        path = QFileDialog.getExistingDirectory(
            self, "选项目根目录",
            str(Path.home()),
        )
        if not path:
            return
        self._current_project_path = Path(path)
        # 插入到 combo 并选中（标记"临时"）
        label = f"（临时） {Path(path).name}  ·  {path}"
        idx = self.project_combo.findData(path)
        if idx < 0:
            self.project_combo.blockSignals(True)
            self.project_combo.addItem(label, path)
            self.project_combo.setCurrentIndex(self.project_combo.count() - 1)
            self.project_combo.blockSignals(False)
        else:
            self.project_combo.setCurrentIndex(idx)
        self.path_lbl.setText(f"路径：{path}（临时使用，未登记）")

    # ------------------------------------------------------------------
    # 优化执行
    # ------------------------------------------------------------------

    def _on_optimize(self) -> None:
        prompt = self.input_edit.toPlainText().strip()
        if not prompt:
            QMessageBox.information(self, "提示", "请先在上方粘贴原始提示词。")
            return
        if not self._current_project_path or not self._current_project_path.is_dir():
            QMessageBox.warning(self, "提示", "请选一个有效的项目目录。")
            return

        # 检查项目摘要是否有内容（项目可能是空目录）
        summary = extract_project_summary(self._current_project_path)
        if not summary.sections:
            ans = QMessageBox.question(
                self, "项目信息不足",
                f"目录 {self._current_project_path} 里没找到 CLAUDE.md / package.json / pyproject.toml / "
                "README.md 等任何元信息文件。优化效果会很有限。\n\n继续吗？",
            )
            if ans != QMessageBox.StandardButton.Yes:
                return

        self.btn_optimize.setEnabled(False)
        self.btn_retry.setEnabled(False)
        self.btn_copy.setEnabled(False)
        self.btn_save.setEnabled(False)
        self.output_edit.clear()
        self._start_busy(
            f"正在分析「{summary.project_name}」并调 LLM 优化中（摘要 {summary.total_chars} 字符）",
            kind="project_optimize",
        )

        self._worker = _OptimizeWorker(self.cfg, prompt, self._current_project_path)
        self._worker.finished.connect(self._on_optimize_done)
        self._worker.failed.connect(self._on_optimize_failed)
        self._worker.start()

    # ------------------------------------------------------------------
    # 进度反馈
    # ------------------------------------------------------------------

    def _start_busy(self, msg: str, kind: str) -> None:
        """显示进度条 + 启动计时。

        kind: "project_optimize" / "generate_title" 等，用于查历史 ETA
        """
        from ...core import llm_timings, optimizer as _opt
        # 当前会用哪个后端（estimate 取相应的历史均值）
        self._busy_backend = _opt._decide_backend(self.cfg, "auto")
        self._busy_kind = kind
        self._busy_msg = msg
        self._eta_seconds = llm_timings.estimate(self.cfg, self._busy_backend, kind)
        self._elapsed_ms = 0

        self.status_lbl.setStyleSheet("color: #525252; font-size: 12px;")
        self.status_lbl.setText(
            f"{msg}  预计 ~{self._eta_seconds:.0f}s（后端 {self._busy_backend}）"
        )
        self.progress.setValue(0)
        self.progress.setVisible(True)
        self.btn_switch_backend.setVisible(False)
        if not self._tick_timer.isActive():
            self._tick_timer.start()

    def _stop_busy(self) -> None:
        self._tick_timer.stop()
        # 完成时跳到 100%（短暂可见）
        self.progress.setValue(1000)
        self.progress.setVisible(False)

    def _on_tick(self) -> None:
        self._elapsed_ms += self._tick_ms
        elapsed_s = self._elapsed_ms / 1000.0
        if self._eta_seconds <= 0:
            ratio = 0.5
        else:
            ratio = elapsed_s / self._eta_seconds
        # 在 ETA 内线性推进；超 ETA 后渐进卡向 95%（不要跳 100% 假装完成）
        if ratio < 0.85:
            pct = int(ratio * 850)  # 0 ~ 850（千分制 = 0-85%）
        else:
            # 0.85 ~ ∞ 映射到 850 ~ 950，渐进
            overshoot = ratio - 0.85
            pct = 850 + int(min(100, overshoot * 200))
        pct = min(pct, 950)
        self.progress.setValue(pct)

        # 文字 ETA
        remaining = max(0.0, self._eta_seconds - elapsed_s)
        if elapsed_s < self._eta_seconds * 0.95:
            eta_text = f"剩约 {remaining:.0f}s"
        elif elapsed_s < self._eta_seconds * 1.5:
            eta_text = "即将完成…"
        elif elapsed_s < self._eta_seconds * 3:
            eta_text = "比预期慢，再等等"
        else:
            eta_text = "已超 3× 预期，可考虑切到「设置」用别的后端"
        self.status_lbl.setText(
            f"{self._busy_msg}  已耗时 {elapsed_s:.0f}s / 预计 ~{self._eta_seconds:.0f}s · {eta_text}"
        )

    def _on_optimize_done(self, result: OptimizeResult, elapsed: float) -> None:
        self._stop_busy()
        self.btn_optimize.setEnabled(True)
        self.btn_retry.setEnabled(True)
        # 记录耗时供下次 ETA 估算
        from ...core import llm_timings
        if result.backend:
            llm_timings.record(self.cfg, result.backend, "project_optimize", elapsed)

        if not result.success or not result.optimized:
            err = result.error or "返回空"
            self.status_lbl.setText(
                f"✗ 失败（后端 {result.backend}，耗时 {elapsed:.0f}s）：{err}  →"
            )
            self.status_lbl.setStyleSheet("color: #b91c1c; font-size: 12px;")
            self.btn_switch_backend.setVisible(True)
            return
        self.btn_switch_backend.setVisible(False)
        self.output_edit.setPlainText(result.optimized)
        self.btn_copy.setEnabled(True)
        self.btn_save.setEnabled(True)
        self.status_lbl.setStyleSheet("color: #16a34a; font-size: 12px;")
        self.status_lbl.setText(
            f"✓ 完成（后端 {result.backend}，耗时 {elapsed:.0f}s）"
        )
        # 记历史
        if self._current_project_path:
            _append_history(self.cfg, {
                "ts": int(time.time()),
                "project_name": self._current_project_path.name,
                "project_path": str(self._current_project_path),
                "input": self.input_edit.toPlainText().strip(),
                "output": result.optimized,
                "backend": result.backend,
            })

    def _on_optimize_failed(self, err: str, elapsed: float) -> None:
        self._stop_busy()
        self.btn_optimize.setEnabled(True)
        self.btn_retry.setEnabled(True)
        self.status_lbl.setStyleSheet("color: #b91c1c; font-size: 12px;")
        self.status_lbl.setText(f"✗ 异常（耗时 {elapsed:.0f}s）：{err}  →")
        self.btn_switch_backend.setVisible(True)

    def _on_jump_settings(self) -> None:
        """跳到「设置 → LLM 配置」让用户切换后端。"""
        win = self.window()
        if hasattr(win, "_on_open_settings"):
            win._on_open_settings()
        self.btn_switch_backend.setVisible(False)

    def _on_show_history(self) -> None:
        items = _load_history(self.cfg)
        dlg = _HistoryDialog(self, items)
        if dlg.exec() == QDialog.DialogCode.Accepted and dlg.selected_index >= 0:
            entry = items[dlg.selected_index]
            self.input_edit.setPlainText(entry.get("input", ""))
            self.output_edit.setPlainText(entry.get("output", ""))
            self.btn_copy.setEnabled(bool(entry.get("output")))
            self.btn_save.setEnabled(bool(entry.get("output")))
            self.status_lbl.setText(
                f"已载入历史：{entry.get('project_name', '?')}（后端 {entry.get('backend', '?')}）"
            )
            self.status_lbl.setStyleSheet("color: #0a0a0a; font-size: 12px;")

    # ------------------------------------------------------------------
    # 结果操作
    # ------------------------------------------------------------------

    def _on_copy(self) -> None:
        from PySide6.QtWidgets import QApplication
        text = self.output_edit.toPlainText()
        if not text.strip():
            return
        QApplication.clipboard().setText(text)
        self.status_lbl.setText("已复制到剪贴板")
        self.status_lbl.setStyleSheet("color: #16a34a; font-size: 12px;")

    def _on_save(self) -> None:
        text = self.output_edit.toPlainText().strip()
        if not text:
            return
        first_line = text.splitlines()[0] if text else "项目优化"
        fallback_title = first_line.strip()[:10] or "未命名"
        # 异步调 LLM 生成标题；UI 不卡，进度条按 ETA 推进
        self.btn_save.setEnabled(False)
        self._start_busy("生成标题中（LLM ≤10 字）", kind="generate_title")
        self._title_worker = _TitleWorker(
            self.cfg, text, fallback_title, kind="project",
        )
        self._title_worker.finished.connect(
            lambda t, el: self._on_title_ready(t, text, el)
        )
        self._title_worker.failed.connect(
            lambda e, el: self._on_title_ready(fallback_title, text, el)
        )
        self._title_worker.start()

    def _on_title_ready(self, title: str, text: str, elapsed: float) -> None:
        """LLM 生成完标题后真正开 editor 对话框。"""
        self._stop_busy()
        # 记录历史
        from ...core import llm_timings
        llm_timings.record(self.cfg, self._busy_backend, "generate_title", elapsed)
        self.btn_save.setEnabled(True)
        self.status_lbl.setStyleSheet("color: #525252; font-size: 12px;")
        self.status_lbl.setText(f"标题已生成（耗时 {elapsed:.0f}s）：{title}")

        project_name = (
            self._current_project_path.name
            if self._current_project_path else None
        )
        ref = (
            str(self._current_project_path)
            if self._current_project_path else (project_name or "")
        )
        from ..widgets.prompt_editor import PromptEditorDialog
        prefilled = storage.Prompt.new(
            title=title,
            body=text,
            scope="project" if project_name else "global",
            project=project_name,
            tags=["项目优化"],
            origin="manual",
            source_ref=ref,
            description=f"基于「{project_name or '?'}」项目上下文优化",
        )
        dlg = PromptEditorDialog(self.cfg, prompt=prefilled, parent=self)
        if dlg.exec() == dlg.DialogCode.Accepted:
            self.status_lbl.setText("已保存到我的库")
            self.status_lbl.setStyleSheet("color: #16a34a; font-size: 12px;")
            win = self.window()
            if hasattr(win, "_refresh_status"):
                win._refresh_status()


class _HistoryDialog(QDialog):
    """列出最近 N 次项目优化，选一条可载入回主页。"""

    def __init__(self, parent: QWidget, items: list[dict]):
        super().__init__(parent)
        self.setWindowTitle("项目优化历史")
        self.resize(720, 480)
        self.selected_index: int = -1

        v = QVBoxLayout(self)
        v.setContentsMargins(16, 16, 16, 12)
        v.setSpacing(8)

        title = QLabel(f"最近 {len(items)} 次项目优化")
        title.setStyleSheet("font-size: 14px; font-weight: 600; color: #0a0a0a;")
        v.addWidget(title)

        if not items:
            empty = QLabel("还没有历史记录。每次点「优化」成功后会自动保存最多 20 条。")
            empty.setStyleSheet("color: #737373; font-size: 12px; padding: 24px 0;")
            v.addWidget(empty)
            btn_close = QPushButton("关闭")
            btn_close.clicked.connect(self.reject)
            v.addWidget(btn_close, alignment=Qt.AlignmentFlag.AlignRight)
            return

        split = QSplitter(Qt.Orientation.Horizontal)
        v.addWidget(split, 1)

        self.list_widget = QListWidget()
        self.list_widget.setMinimumWidth(260)
        for i, entry in enumerate(items):
            ts = entry.get("ts", 0)
            time_str = time.strftime("%Y-%m-%d %H:%M", time.localtime(ts)) if ts else "?"
            proj = entry.get("project_name", "?")
            backend = entry.get("backend", "?")
            preview = (entry.get("input", "") or "").splitlines()[0][:40] if entry.get("input") else ""
            li = QListWidgetItem(f"{time_str}  ·  {proj}  ·  {backend}\n  {preview}")
            self.list_widget.addItem(li)
        self.list_widget.currentRowChanged.connect(self._on_row_changed)
        split.addWidget(self.list_widget)

        # 右侧详情
        self.detail = QPlainTextEdit()
        self.detail.setReadOnly(True)
        split.addWidget(self.detail)
        split.setStretchFactor(1, 1)

        self._items = items
        self.list_widget.setCurrentRow(0)

        btn_row = QHBoxLayout()
        btn_load = QPushButton("载入到主页")
        btn_load.setDefault(True)
        btn_load.setProperty("class", "primary")
        btn_load.clicked.connect(self._on_load)
        btn_close = QPushButton("关闭")
        btn_close.clicked.connect(self.reject)
        btn_row.addStretch(1)
        btn_row.addWidget(btn_close)
        btn_row.addWidget(btn_load)
        v.addLayout(btn_row)

    def _on_row_changed(self, row: int) -> None:
        if 0 <= row < len(self._items):
            e = self._items[row]
            text = (
                f"项目：{e.get('project_name', '?')}\n"
                f"路径：{e.get('project_path', '?')}\n"
                f"后端：{e.get('backend', '?')}\n"
                f"\n— 原始 —\n{e.get('input', '')}\n"
                f"\n— 优化后 —\n{e.get('output', '')}"
            )
            self.detail.setPlainText(text)

    def _on_load(self) -> None:
        self.selected_index = self.list_widget.currentRow()
        self.accept()
