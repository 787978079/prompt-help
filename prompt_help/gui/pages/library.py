"""我的库：tabs 区分 全部 / 待审 / 通用 / 项目专属 / 踩坑提醒。

把 Inbox 合并进来：「待审」tab 显示候选卡片。
术语全部中文化：scope→类型，global→通用，project→项目专属，trap→踩坑提醒。
"""

from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QFileDialog, QFrame, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
    QMessageBox, QPushButton, QScrollArea, QSizePolicy, QSplitter,
    QTableWidget, QTableWidgetItem, QTabWidget, QTextBrowser, QVBoxLayout,
    QWidget,
)

from ...cli.inbox import InboxItem
from ...core import classify, indexer, storage
from ...core.config import Config


_SCOPE_LABEL = {"global": "通用", "project": "项目专属", "trap": "踩坑提醒"}


class LibraryPage(QWidget):
    def __init__(self, cfg: Config):
        super().__init__()
        self.cfg = cfg
        # P21：tab view 懒刷——只在 tab 第一次显示时跑 SQL
        # _dirty[key]=True 表示该 view 自上次 refresh 起数据可能变了
        self._dirty: dict[str, bool] = {}
        self._build()
        # 只刷当前可见 tab + 顶部 tab 计数（轻量 count 查询）
        self._refresh_tab_titles()
        self._refresh_current_tab()

    def _build(self) -> None:
        v = QVBoxLayout(self)
        v.setContentsMargins(40, 28, 40, 24)
        v.setSpacing(8)

        title = QLabel("我的库")
        title.setObjectName("pageTitle")
        v.addWidget(title)
        hint = QLabel(
            "PH 的库容纳三类内容：**提示词、系统记忆、项目踩坑点**。"
            "经 LLM 通用化的会标记到「通用模板」tab——这才是可跨项目分享的核心资产。"
        )
        hint.setObjectName("pageHint")
        hint.setWordWrap(True)
        hint.setTextFormat(Qt.TextFormat.MarkdownText)
        v.addWidget(hint)

        # 顶部工具条：搜索 + 主操作
        bar_host = QWidget()
        bar_host.setObjectName("tour_library_import_bar")
        bar = QHBoxLayout(bar_host)
        bar.setContentsMargins(0, 0, 0, 0)
        bar.setSpacing(8)
        self.search = QLineEdit()
        self.search.setPlaceholderText("搜索标题、标签、内容（输入即过滤）")
        self.search.textChanged.connect(self._on_search_changed)
        bar.addWidget(self.search, 1)

        from .. import icons as _icons
        from PySide6.QtCore import QSize as _QSize

        # P22：从单按钮改下拉菜单，支持多源（Claude Code / Codex / 全部）
        from PySide6.QtWidgets import QMenu
        self.btn_scan = QPushButton(" 从 AI 历史导入")
        self.btn_scan.setProperty("class", "subtle")
        self.btn_scan.setIcon(_icons.icon("scan_history"))
        self.btn_scan.setIconSize(_QSize(14, 14))
        self.btn_scan.setToolTip(
            "扫 Claude Code / Codex 等 AI 工具的历史会话，把你写过的好提示词挖出来入库"
        )
        scan_menu = QMenu(self)
        # 探测每个 adapter 是否在系统上，未检测到的灰化 + tooltip 指导
        from ...cli.adapters.claude_code import ClaudeCodeAdapter
        from ...cli.adapters.codex import CodexAdapter
        cc_detected = ClaudeCodeAdapter().detect()
        cx_detected = CodexAdapter().detect()

        cc_act = scan_menu.addAction(
            "Claude Code 历史（~/.claude/projects/）",
            lambda: self._on_scan_transcripts("claude_code"),
        )
        if not cc_detected:
            cc_act.setEnabled(False)
            cc_act.setText("Claude Code 历史（未检测到 ~/.claude/projects/）")

        cx_act = scan_menu.addAction(
            "Codex 历史（~/.codex/sessions/）",
            lambda: self._on_scan_transcripts("codex"),
        )
        if not cx_detected:
            cx_act.setEnabled(False)
            cx_act.setText("Codex 历史（未检测到，先 npm i -g @openai/codex && codex login）")

        all_act = scan_menu.addAction(
            "全部已检测到的 AI 工具",
            lambda: self._on_scan_transcripts("all"),
        )
        if not (cc_detected or cx_detected):
            all_act.setEnabled(False)
            all_act.setText("全部已检测到的 AI 工具（暂未检测到任何）")
        self.btn_scan.setMenu(scan_menu)
        bar.addWidget(self.btn_scan)

        self.btn_import_files = QPushButton(" 从文件导入")
        self.btn_import_files.setProperty("class", "subtle")
        self.btn_import_files.setIcon(_icons.icon("import_file"))
        self.btn_import_files.setIconSize(_QSize(14, 14))
        self.btn_import_files.setToolTip(
            "其他工具（Cursor / Cline / Aider / Continue / Codex 等）的会话导出文件，"
            "或朋友给的 .zip 分享包，都从这里导入"
        )
        self.btn_import_files.clicked.connect(self._on_import_files)
        bar.addWidget(self.btn_import_files)

        self.btn_import_help = QPushButton(" 支持哪些格式")
        self.btn_import_help.setProperty("class", "subtle")
        self.btn_import_help.setIcon(_icons.icon("info"))
        self.btn_import_help.setIconSize(_QSize(14, 14))
        self.btn_import_help.setCheckable(True)
        self.btn_import_help.toggled.connect(self._toggle_import_help)
        bar.addWidget(self.btn_import_help)

        self.btn_share = QPushButton(" 分享选中")
        self.btn_share.setProperty("class", "subtle")
        self.btn_share.setIcon(_icons.icon("link"))
        self.btn_share.setIconSize(_QSize(14, 14))
        self.btn_share.setToolTip("把当前 tab 选中的（Ctrl/Shift 多选）打包成 ZIP 分享给朋友")
        self.btn_share.clicked.connect(self._on_share_selected)
        bar.addWidget(self.btn_share)

        self.btn_new = QPushButton(" 新建")
        self.btn_new.setProperty("class", "primary")
        self.btn_new.setIcon(_icons.icon_white("new_item"))
        self.btn_new.setIconSize(_QSize(14, 14))
        self.btn_new.clicked.connect(self._on_new)
        bar.addWidget(self.btn_new)

        v.addWidget(bar_host)

        # Phase 9：内嵌「支持的格式」提示信号（默认折叠，点 ⓘ 按钮展开）
        self.import_help_panel = QLabel(
            "✅ <b>.md</b> Markdown — 按 ## / ### 标题拆为多条 prompt&nbsp;&nbsp;&nbsp;"
            "✅ <b>.json</b> OpenAI / Claude transcript / Aider / Continue 结构&nbsp;&nbsp;&nbsp;"
            "✅ <b>.jsonl</b> 每行一条 JSON（Codex 等）&nbsp;&nbsp;&nbsp;"
            "✅ <b>.txt</b> 整文件作为一条&nbsp;&nbsp;&nbsp;"
            "✅ <b>.zip</b> 朋友分享包<br>"
            "❌ .docx / .pdf 暂不支持（先用 Pandoc 转 .md）&nbsp;&nbsp;&nbsp;"
            "💡 拖拽多个文件也可以"
        )
        self.import_help_panel.setStyleSheet(
            "QLabel { background: #f5f7fa; border: 0; border-radius: 6px;"
            "padding: 10px 12px; color: #1f2937; font-size: 12px; line-height: 1.7; }"
        )
        self.import_help_panel.setTextFormat(Qt.TextFormat.RichText)
        self.import_help_panel.setWordWrap(True)
        self.import_help_panel.hide()
        v.addWidget(self.import_help_panel)

        # Phase 9：移除分类 chips（用户反馈"没侧重点"），保留排序下拉
        self.category_chips: dict[str, QPushButton] = {}  # 兼容旧代码
        sort_row = QHBoxLayout()
        sort_row.addStretch(1)
        sort_label = QLabel("排序：")
        sort_label.setStyleSheet("color: #737373; font-size: 12px; padding: 0 4px 0 0;")
        sort_row.addWidget(sort_label)
        from PySide6.QtWidgets import QComboBox
        self.sort_combo = QComboBox()
        self.sort_combo.setStyleSheet("font-size: 12px; padding: 2px 6px;")
        self.sort_combo.addItem("综合评分", "score")
        self.sort_combo.addItem("最近热门", "trending")
        self.sort_combo.addItem("使用次数", "used")
        self.sort_combo.addItem("成功信号", "success")
        self.sort_combo.addItem("最近使用", "last_used")
        self.sort_combo.addItem("新增时间", "created")
        self.sort_combo.currentIndexChanged.connect(self._on_sort_changed)
        sort_row.addWidget(self.sort_combo)
        v.addLayout(sort_row)

        # tabs
        self.tabs = QTabWidget()
        self.tabs.currentChanged.connect(self._on_tab_changed)
        v.addWidget(self.tabs, 1)

        # Phase 8：3 tab 收敛——通用模板（核心）+ 原始材料 + 待审
        # P21：去 emoji，QTabBar 用纯文字 + 选中底线（QSS 已定义）
        self.tab_filters = []
        for key, label, scope, is_tpl in [
            ("templates", "通用模板", None, True),
            ("raw", "原始材料", None, False),
        ]:
            list_view = PromptListView(
                self.cfg, scope_filter=scope, parent_page=self,
                is_template_filter=is_tpl,
            )
            self.tabs.addTab(list_view, label)
            self.tab_filters.append((key, list_view))
            self._dirty[key] = True

        # 待审 tab（卡片视图）
        self.inbox_view = InboxView(self.cfg, parent_page=self)
        self.tabs.addTab(self.inbox_view, "待审")
        self.tab_filters.append(("inbox", self.inbox_view))
        self._dirty["inbox"] = True

        # Phase 22.5 N2：QSettings 持久化 — 恢复上次的 tab / 排序 / 搜索关键词
        self._restore_persistent_state()

    # ------------------------------------------------------------------

    _last_show_ts: float = 0.0

    def on_show(self) -> None:
        # P20：15 秒节流——避免切 tab 来回 4 个 PromptListView 全都重 SQL
        import time
        now = time.time()
        if now - self._last_show_ts < 15:
            return
        self._last_show_ts = now
        self.refresh()

    def refresh(self) -> None:
        # P21：只刷当前 tab，其余打 dirty 旗，切到时再刷
        for key, _v in self.tab_filters:
            self._dirty[key] = True
        self._refresh_current_tab()
        self._refresh_tab_titles()

    def _refresh_current_tab(self) -> None:
        idx = self.tabs.currentIndex()
        if 0 <= idx < len(self.tab_filters):
            key, view = self.tab_filters[idx]
            view.refresh()
            self._dirty[key] = False

    def _refresh_tab_titles(self) -> None:
        try:
            conn = indexer.open_db(self.cfg)
            tpl = indexer.count_templates(conn)
            conn.close()
            inbox_n = (
                len(list(self.cfg.inbox_dir.glob("*.md")))
                if self.cfg.inbox_dir.is_dir() else 0
            )
        except Exception:
            tpl = {"templates": 0, "raw": 0, "total": 0}
            inbox_n = 0

        # Phase 8：3 tab 数量（P21 去 emoji）
        self.tabs.setTabText(0, f"通用模板 ({tpl['templates']})")
        self.tabs.setTabText(1, f"原始材料 ({tpl['raw']})")
        self.tabs.setTabText(2, f"待审 ({inbox_n})")

    def show_tab(self, key: str) -> None:
        """供 HomePage 跳转。兼容旧 key（global/project/traps → raw、all → templates）。"""
        alias = {
            "all": "templates", "global": "templates",
            "project": "raw", "traps": "raw",
        }
        target = alias.get(key, key)
        for i, (k, _v) in enumerate(self.tab_filters):
            if k == target:
                self.tabs.setCurrentIndex(i)
                return

    def selected_categories(self) -> list[str]:
        # Phase 9 移除分类 chips 后恒返回空
        return []

    def _on_chips_changed(self, _checked: bool) -> None:
        pass

    def _on_clear_chips(self) -> None:
        pass

    def _on_sort_changed(self, _idx: int) -> None:
        sort_by = self.sort_combo.currentData() or "score"
        for key, view in self.tab_filters:
            if isinstance(view, PromptListView):
                view.set_sort(sort_by)
                self._dirty[key] = True
        self._refresh_current_tab()
        self._save_persistent_state()

    def _on_share_selected(self) -> None:
        """P12-N1 + P14：批量分享选中的 prompts。支持全量 / 增量两种模式。"""
        cur = self.tabs.currentWidget()
        ids: list[str] = []
        if isinstance(cur, PromptListView):
            for row in cur.table.selectionModel().selectedRows():
                it = cur.table.item(row.row(), 0)
                pid = it.data(Qt.ItemDataRole.UserRole) if it else None
                if pid:
                    ids.append(pid)
        elif isinstance(cur, InboxView):
            QMessageBox.information(
                self, "「待审」不支持批量分享",
                "「待审」里的条目还没正式入库——\n"
                "请先「保留」或「保留并通用化」后，到「通用模板」/「原始材料」tab 选中再分享。",
            )
            return
        if not ids:
            ans = QMessageBox.question(
                self, "没选中",
                "你没勾选任何条目。要导出整个当前 tab 还是只导增量（上次 export 之后改动的）？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No | QMessageBox.StandardButton.Cancel,
            )
            if ans == QMessageBox.StandardButton.Cancel:
                return
            ids = None  # 让 export_zip 用 tab 范围
            use_delta = (ans == QMessageBox.StandardButton.No)
        else:
            use_delta = False
        from PySide6.QtWidgets import QFileDialog
        suffix = "delta" if use_delta else f"{len(ids) if ids else 'all'}-items"
        default_name = f"prompts-{suffix}.zip"
        path, _ = QFileDialog.getSaveFileName(
            self, "保存分享 ZIP", default_name, "ZIP 文件 (*.zip)",
        )
        if not path:
            return
        from ...cli.share import export_zip
        try:
            n = export_zip(self.cfg, Path(path), ids=ids, delta=use_delta)
        except Exception as e:
            QMessageBox.warning(self, "导出失败", f"{type(e).__name__}: {e}")
            return
        if n == 0:
            QMessageBox.information(
                self, "无变动",
                "增量模式：上次 export 之后没有改动。换用全量？",
            )
            return
        mode = "增量" if use_delta else "全量"
        QMessageBox.information(
            self, "已导出",
            f"{mode}导出 {n} 条到：\n{path}\n\n"
            "把这个 ZIP 发给朋友，他们拖进 PH 主窗口即可导入。\n"
            "导入时会先弹冲突解决界面——可选保留本地 / 覆盖 / 双留。",
        )

    def _on_search_changed(self, txt: str) -> None:
        if not hasattr(self, "_search_timer"):
            self._search_timer = QTimer(self)
            self._search_timer.setSingleShot(True)
            self._search_timer.timeout.connect(self._do_search)
        self._search_timer.start(250)
        # 第一次输入非空 → 标记 try_search 完成（onboarding 任务清单）
        if txt.strip() and not getattr(self, "_try_search_marked", False):
            self._try_search_marked = True
            try:
                from ..components.onboarding_checklist import load_state, save_state
                st = load_state(self.cfg)
                st.setdefault("manual", {})["try_search"] = True
                save_state(self.cfg, st)
            except Exception:
                pass  # 永不阻塞搜索

    def _do_search(self) -> None:
        q = self.search.text().strip()
        # 把查询同步到所有 view（搜索条件全局），但只刷当前可见 tab；
        # 其他 tab 切到时通过 dirty 重刷一次即可
        for key, view in self.tab_filters:
            if isinstance(view, PromptListView):
                view.set_query(q)
                self._dirty[key] = True
        self._refresh_current_tab()
        self._refresh_tab_titles()
        # 持久化搜索文本（debounce 后再写，避免每字符都触 QSettings 落盘）
        self._save_persistent_state()

    def _on_tab_changed(self, _i: int) -> None:
        idx = self.tabs.currentIndex()
        if 0 <= idx < len(self.tab_filters):
            key, view = self.tab_filters[idx]
            # 仅 dirty 时才跑 SQL，避免每次切换都全量重查
            if self._dirty.get(key, True):
                view.refresh()
                self._dirty[key] = False
        self._save_persistent_state()

    # ------------------------------------------------------------------
    # Phase 22.5 N2：状态持久化（QSettings）
    # ------------------------------------------------------------------

    _SETTINGS_GROUP = "library"

    def _settings(self):
        from PySide6.QtCore import QSettings
        return QSettings("PromptHelp", "PH")

    def _save_persistent_state(self) -> None:
        s = self._settings()
        s.beginGroup(self._SETTINGS_GROUP)
        try:
            s.setValue("tab_index", self.tabs.currentIndex())
            s.setValue("sort_by", self.sort_combo.currentData() or "score")
            s.setValue("search", self.search.text())
        finally:
            s.endGroup()

    def _restore_persistent_state(self) -> None:
        s = self._settings()
        s.beginGroup(self._SETTINGS_GROUP)
        try:
            tab_idx = s.value("tab_index", 0, type=int)
            sort_by = s.value("sort_by", "score", type=str)
            search = s.value("search", "", type=str)
        finally:
            s.endGroup()
        # 恢复排序（触发 _on_sort_changed → 再 save，但 save 后值不变）
        sort_idx = self.sort_combo.findData(sort_by)
        if sort_idx >= 0:
            self.sort_combo.setCurrentIndex(sort_idx)
        # 恢复搜索关键词（只填字段，让 textChanged → _do_search 自然触发）
        if search:
            self.search.setText(search)
        # 恢复 tab 放最后（其他状态先就位再切，避免切完又被覆盖）
        if 0 <= tab_idx < self.tabs.count():
            self.tabs.setCurrentIndex(tab_idx)

    def _on_new(self) -> None:
        from ..widgets.prompt_editor import PromptEditorDialog
        dlg = PromptEditorDialog(self.cfg, parent=self)
        if dlg.exec() == dlg.DialogCode.Accepted:
            self.refresh()
            self._notify_status_changed()

    def _toggle_import_help(self, checked: bool) -> None:
        """点 ⓘ 按钮内嵌展开/折叠支持格式提示。"""
        self.import_help_panel.setVisible(checked)

    def _show_import_help(self) -> None:
        """点 ? 图标显示「支持的文件格式说明」（Phase 7 T4）。"""
        from PySide6.QtWidgets import QMessageBox
        msg = QMessageBox(self)
        msg.setWindowTitle("支持的文件格式")
        msg.setTextFormat(Qt.TextFormat.MarkdownText)
        msg.setText(
            "**「从文件导入」支持以下格式**\n\n"
            "✅ **`.md`** Markdown — 按 `##` / `###` 标题拆为多条 prompt\n\n"
            "✅ **`.json`** 支持以下结构（自动识别）：\n"
            "- OpenAI ChatCompletion `messages` 数组：`[{role, content}, ...]`\n"
            "- Claude Code transcript：`[{role:\"user\", content:...}, ...]`\n"
            "- Aider `.aider.input.history`：每行 JSON\n"
            "- Continue.dev session 文件\n\n"
            "✅ **`.jsonl`** 每行一条 JSON（Codex / 自定义日志）\n\n"
            "✅ **`.txt`** 整文件作为一条 prompt\n\n"
            "✅ **`.zip`** 分享包（含 `manifest.json` + `prompts/*.md`）\n\n"
            "❌ `.docx` / `.pdf` 暂不支持（可先用 Pandoc 转 `.md`）\n\n"
            "💡 拖拽多个文件也可以，会批量解析。"
        )
        msg.exec()

    def _on_import_files(self) -> None:
        """支持 .md / .json / .jsonl / .txt（其他工具导出）+ .zip（朋友分享包）。"""
        files, _ = QFileDialog.getOpenFileNames(
            self, "选要导入的文件",
            str(Path.home()),
            "支持的格式 (*.md *.json *.jsonl *.txt *.zip);;所有文件 (*)",
        )
        if not files:
            return

        from PySide6.QtCore import Qt as _Qt
        from PySide6.QtGui import QCursor
        from PySide6.QtWidgets import QApplication

        QApplication.setOverrideCursor(QCursor(_Qt.CursorShape.WaitCursor))
        try:
            saved_total = 0
            zip_imported = 0
            for f in files:
                p = Path(f)
                if p.suffix.lower() == ".zip":
                    zip_imported += self._import_share_zip(p)
                else:
                    saved_total += self._import_single_file(p)
        finally:
            QApplication.restoreOverrideCursor()

        QMessageBox.information(
            self, "导入完成",
            f"从 {len(files)} 个文件导入：{saved_total} 条挖掘内容 + {zip_imported} 条来自朋友 ZIP",
        )
        self.refresh()
        self._notify_status_changed()

    def _import_single_file(self, path: Path) -> int:
        """走 ManualDropAdapter + quality 过滤。"""
        from ...cli import transcripts as ts
        from ...cli.adapters.manual_drop import ManualDropAdapter
        adapter = ManualDropAdapter([path])
        if not adapter.detect():
            return 0
        cands = ts._candidates_from_adapter(adapter, self.cfg.quality)
        return ts._import_candidates(self.cfg, cands, polish=False)

    def _import_share_zip(self, path: Path) -> int:
        """从朋友的 ZIP 分享包导入（P14：先弹冲突解决对话框）。"""
        from ...cli.share import import_zip, inspect_zip
        # 先 inspect 看冲突
        try:
            inspect = inspect_zip(self.cfg, path)
        except Exception as e:
            QMessageBox.warning(self, "ZIP 读取失败", f"{type(e).__name__}: {e}")
            return 0
        if inspect.get("error"):
            QMessageBox.warning(self, "格式不对", inspect["error"])
            return 0

        conflicts = inspect.get("conflicts") or []
        per_item_actions: dict[str, str] = {}
        # 有冲突才弹对话框；无冲突直接 import
        if conflicts:
            from ..dialogs.conflict_resolver_dialog import ConflictResolverDialog
            dlg = ConflictResolverDialog(self.cfg, path, parent=self)
            if dlg.exec() != dlg.DialogCode.Accepted:
                return 0
            per_item_actions = dlg.actions

        try:
            result = import_zip(
                self.cfg, path, per_item_actions=per_item_actions,
            )
        except Exception as e:
            QMessageBox.warning(self, "导入失败", f"{type(e).__name__}: {e}")
            return 0
        if result.get("error"):
            QMessageBox.warning(self, "格式不对", result["error"])
            return 0

        saved = result.get("saved", 0)
        replaced = result.get("replaced", 0)
        kept_both = result.get("kept_both", 0)
        skipped = result.get("skipped", 0)
        total = saved + replaced + kept_both
        win = self.window()
        if hasattr(win, "statusBar"):
            try:
                win.statusBar().showMessage(
                    f"✓ 从 {path.name}：新增 {saved} / 覆盖 {replaced} / 双留 {kept_both} / 跳过 {skipped}",
                    5000,
                )
            except Exception:
                pass
        return total

    def _on_scan_transcripts(self, source: str = "claude_code") -> None:
        """从 AI 工具历史会话挖真实提示词。P22：支持 claude_code / codex / all。"""
        from PySide6.QtCore import Qt as _Qt
        from PySide6.QtGui import QCursor
        from PySide6.QtWidgets import QApplication

        labels = {
            "claude_code": ("Claude Code 历史", "~/.claude/projects/"),
            "codex": ("Codex 历史", "~/.codex/sessions/"),
            "all": ("全部 AI 工具", "已检测到的所有适配器"),
        }
        label, where = labels.get(source, labels["claude_code"])

        ans = QMessageBox.question(
            self,
            f"从 {label} 导入",
            f"将扫描{label}（{where}），\n"
            "把你写过的、有结构的、值得复用的提示词挖出来入库。\n\n"
            "继续？",
        )
        if ans != QMessageBox.StandardButton.Yes:
            return

        QApplication.setOverrideCursor(QCursor(_Qt.CursorShape.WaitCursor))
        sessions = 0
        saved = 0
        try:
            if source in ("claude_code", "all"):
                s, n = self._scan_claude_code()
                sessions += s
                saved += n
            if source in ("codex", "all"):
                n = self._scan_via_adapter("codex")
                saved += n
        finally:
            QApplication.restoreOverrideCursor()

        msg = f"挖到 {saved} 条值得复用的提示词"
        if sessions:
            msg = f"扫了 {sessions} 个 CC 会话，{msg}"
        QMessageBox.information(self, "扫描完成", msg + "。")
        self.refresh()
        self._notify_status_changed()

    def _scan_claude_code(self) -> tuple[int, int]:
        """旧版精细 CC 扫描（按项目分组 + db 去重）。返回 (sessions_扫了, 入库条数)。"""
        from ...cli import transcripts as ts
        from collections import defaultdict

        cc_root = Path.home() / ".claude" / "projects"
        if not cc_root.is_dir():
            return 0, 0
        qc = self.cfg.quality
        by_project: dict[str, list[ts.Candidate]] = defaultdict(list)
        sessions = 0
        for proj_encoded, jsonl in ts._walk_sessions(cc_root):
            sessions += 1
            proj_name = ts._decode_project(proj_encoded)
            for c in ts._extract_candidates(jsonl, proj_name, qc):
                by_project[proj_name].append(c)

        final: dict[str, list[ts.Candidate]] = {}
        for proj, cands in by_project.items():
            deduped = ts._dedupe_within(
                cands,
                token_threshold=qc.inter_dedupe_token,
                seq_threshold=qc.inter_dedupe_seq_ratio,
            )
            final[proj] = ts._dedupe_against_db(deduped, self.cfg)

        saved = 0
        conn = indexer.open_db(self.cfg)
        try:
            for proj, cands in final.items():
                for c in cands:
                    title = ts._auto_title(c.body)
                    p = storage.Prompt.new(
                        title=title, body=c.body, scope="project", project=proj,
                        tags=[f"来自-{proj}", f"于-{c.source_date[:7]}"],
                        origin="imported",
                    )
                    file_path = storage.save(
                        self.cfg, p,
                        commit_msg=f"GUI 扫描: {proj}/{c.source_session[:8]}",
                    )
                    indexer.upsert(conn, p, file_path)
                    saved += 1
        finally:
            conn.close()
        return sessions, saved

    def _scan_via_adapter(self, adapter_name: str) -> int:
        """P22：通用 adapter 扫描路径，给 Codex / 未来其他工具用。"""
        from ...cli import transcripts as ts
        from ...cli.adapters import all_adapters
        from ...core import quality

        target = None
        for ad in all_adapters():
            if ad.name == adapter_name:
                target = ad
                break
        if target is None or not target.detect():
            return 0

        qc = self.cfg.quality
        # 把 adapter 的 RawMessage 转成 ts.Candidate 以复用既有去重/入库流水线
        cands: list[ts.Candidate] = []
        bodies_seen: set[str] = set()
        for raw in target.walk():
            if raw.role != "user":
                continue
            body = raw.text.strip()
            if not body:
                continue
            passed, _reason = quality.is_quality_prompt(body, qc)
            if not passed:
                continue
            key = body[:200]
            if key in bodies_seen:
                continue
            bodies_seen.add(key)
            cands.append(ts.Candidate(
                body=body,
                source_project=raw.source_project or adapter_name,
                source_session=raw.source_session,
                source_date=raw.source_date,
                source_jsonl=raw.source_path,
                line_index=raw.line_index,
            ))

        # 复用 transcripts.py 的库内去重
        deduped = ts._dedupe_within(
            cands,
            token_threshold=qc.inter_dedupe_token,
            seq_threshold=qc.inter_dedupe_seq_ratio,
        )
        final = ts._dedupe_against_db(deduped, self.cfg)

        saved = 0
        conn = indexer.open_db(self.cfg)
        try:
            for c in final:
                title = ts._auto_title(c.body)
                p = storage.Prompt.new(
                    title=title, body=c.body, scope="project",
                    project=c.source_project,
                    tags=[f"来自-{adapter_name}", f"于-{c.source_date[:7]}"],
                    origin="imported",
                )
                file_path = storage.save(
                    self.cfg, p,
                    commit_msg=f"GUI 扫描({adapter_name}): {c.source_session[:12]}",
                )
                indexer.upsert(conn, p, file_path)
                saved += 1
        finally:
            conn.close()
        return saved

    def _notify_status_changed(self) -> None:
        win = self.window()
        if hasattr(win, "_refresh_status"):
            win._refresh_status()


# ---------------------------------------------------------------------------
# 普通 tab：表格 + 详情
# ---------------------------------------------------------------------------

class PromptListView(QWidget):
    def __init__(self, cfg: Config, scope_filter: str | None, parent_page: LibraryPage,
                 is_template_filter: bool | None = None):
        super().__init__()
        self.cfg = cfg
        self.scope_filter = scope_filter
        self.is_template_filter = is_template_filter  # True=只看模板；False=只看原材料；None=不过滤
        self.parent_page = parent_page
        self._query: str = ""
        self._categories: list[str] = []
        self._sort_by: str = "score"
        self._build()

    def set_query(self, q: str) -> None:
        self._query = q

    def set_categories(self, cats: list[str]) -> None:
        self._categories = list(cats)

    def set_sort(self, sort_by: str) -> None:
        self._sort_by = sort_by

    def _build(self) -> None:
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 8, 0, 0)

        split = QSplitter(Qt.Orientation.Horizontal)
        split.setChildrenCollapsible(False)
        v.addWidget(split, 1)

        # Phase 9：5 列——名称 / 描述 / 类型 / 参考来源 / 用过
        # Phase 22.5：名称 / 场景 / 描述 / 类型 / 参考来源 / 用过（6 列）
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(["名称", "场景", "描述", "类型", "参考来源", "用过"])
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(self.table.SelectionBehavior.SelectRows)
        # P12-N1：允许 Ctrl/Shift 多选，分享按钮可批量导出
        self.table.setSelectionMode(self.table.SelectionMode.ExtendedSelection)
        self.table.setEditTriggers(self.table.EditTrigger.NoEditTriggers)
        # 6 列尺寸策略
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)  # 名称
        hh.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)  # 场景（4 字）
        hh.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)            # 描述
        hh.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)  # 类型
        hh.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)  # 参考来源
        hh.setSectionResizeMode(5, QHeaderView.ResizeMode.ResizeToContents)  # 用过
        self.table.itemSelectionChanged.connect(self._on_select_row)
        self.table.itemDoubleClicked.connect(lambda _: self._on_edit())
        # P12-N4：右键菜单
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._on_context_menu)
        split.addWidget(self.table)

        self.detail = QTextBrowser()
        self.detail.setOpenExternalLinks(True)
        self.detail.setMinimumWidth(420)
        split.addWidget(self.detail)
        split.setSizes([700, 480])

        from .. import icons as _icons
        from PySide6.QtCore import QSize as _QSize
        bottom = QHBoxLayout()
        bottom.addStretch(1)
        self.btn_delete = QPushButton(" 删除")
        self.btn_delete.setProperty("class", "danger")
        self.btn_delete.setIcon(_icons.icon("delete", color="#b91c1c"))
        self.btn_delete.setIconSize(_QSize(14, 14))
        self.btn_delete.clicked.connect(self._on_delete)
        self.btn_edit = QPushButton(" 编辑")
        self.btn_edit.setProperty("class", "subtle")
        self.btn_edit.setIcon(_icons.icon("edit"))
        self.btn_edit.setIconSize(_QSize(14, 14))
        self.btn_edit.clicked.connect(self._on_edit)
        self.btn_generalize = QPushButton(" 生成通用模板")
        self.btn_generalize.setProperty("class", "primary")
        self.btn_generalize.setIcon(_icons.icon_white("generalize"))
        self.btn_generalize.setIconSize(_QSize(14, 14))
        self.btn_generalize.setToolTip(
            "把这条针对性的提示词抽象成可跨项目复用的模板——"
            "项目名 / 路径 / 版本号会被换成 [占位符]"
        )
        self.btn_generalize.clicked.connect(self._on_generalize)
        self.btn_generalize.setVisible(self.is_template_filter is not True)
        self.btn_copy = QPushButton(" 复制内容")
        self.btn_copy.setProperty("class", "primary")
        self.btn_copy.setIcon(_icons.icon_white("copy"))
        self.btn_copy.setIconSize(_QSize(14, 14))
        self.btn_copy.clicked.connect(self._on_copy)

        # B2：使用反馈按钮（小巧、放在 delete 左侧）
        self.btn_useful = QPushButton(" 这次有用")
        self.btn_useful.setProperty("class", "subtle")
        self.btn_useful.setIcon(_icons.icon("success", color="#16a34a"))
        self.btn_useful.setIconSize(_QSize(13, 13))
        self.btn_useful.setToolTip("用过这条 prompt 觉得有用 → +1 成功信号（影响排序）")
        self.btn_useful.clicked.connect(self._on_useful)
        self.btn_not_useful = QPushButton(" 不够好")
        self.btn_not_useful.setProperty("class", "subtle")
        self.btn_not_useful.setIcon(_icons.icon("warning", color="#b45309"))
        self.btn_not_useful.setIconSize(_QSize(13, 13))
        self.btn_not_useful.setToolTip("觉得这条效果不好 → −1 成功信号（排序降权）")
        self.btn_not_useful.clicked.connect(self._on_not_useful)

        bottom.addWidget(self.btn_useful)
        bottom.addWidget(self.btn_not_useful)
        bottom.addWidget(self.btn_delete)
        bottom.addWidget(self.btn_edit)
        bottom.addWidget(self.btn_generalize)
        bottom.addWidget(self.btn_copy)
        v.addLayout(bottom)

    def refresh(self) -> None:
        conn = indexer.open_db(self.cfg)
        cats = self._categories or None
        if self._query:
            results = indexer.search(
                conn, self._query, scope=self.scope_filter,
                categories=cats, is_template=self.is_template_filter,
                top_k=300,
            )
            rows = [r for r, _s in results]
        else:
            rows = indexer.list_all(
                conn, scope=self.scope_filter, categories=cats, limit=500,
                sort_by=self._sort_by, is_template=self.is_template_filter,
            )
        conn.close()

        from ...core import placeholders as _ph
        # 预查所有带 optimized_from_id 的原条目，用于 fallback 显示"参考来源"
        parent_lookup: dict[str, dict] = {}
        parent_ids = [r["optimized_from_id"] for r in rows if r["optimized_from_id"]]
        if parent_ids:
            conn2 = indexer.open_db(self.cfg)
            placeholders_q = ",".join("?" * len(parent_ids))
            for pr in conn2.execute(
                f"SELECT id, title, project, source_ref FROM prompts WHERE id IN ({placeholders_q})",
                parent_ids,
            ):
                parent_lookup[pr["id"]] = dict(pr)
            conn2.close()

        self.table.setRowCount(0)
        for row in rows:
            i = self.table.rowCount()
            self.table.insertRow(i)
            # Phase 9：名称 / 描述 / 类型 / 参考来源 / 用过
            name = row["title"] or "(未命名)"
            try:
                desc = row["description"] or ""
            except (KeyError, IndexError):
                desc = ""
            if not desc:
                body = (row["body"] or "").strip().replace("\n", " ")
                desc = body[:80] + ("…" if len(body) > 80 else "")
            # B1：含占位符的模板，描述前加 [N 变量] badge
            ph_n = _ph.count(row["body"] or "")
            if ph_n > 0:
                desc = f"[{ph_n} 变量] {desc}"
            # 参考来源：优先 source_ref → project → 反查 optimized_from 原条目 → "—"
            try:
                ref_full = row["source_ref"] or row["project"]
            except (KeyError, IndexError):
                ref_full = row["project"] if "project" in row.keys() else None
            if not ref_full:
                parent = parent_lookup.get(row["optimized_from_id"]) if row["optimized_from_id"] else None
                if parent:
                    ref_full = (
                        parent.get("source_ref")
                        or parent.get("project")
                        or f"源条目: {parent.get('title', '')[:30]}"
                    )
            if not ref_full:
                ref_full = "—"
            # 长路径只显示尾段（最后两段），完整路径放 tooltip
            ref_display = ref_full
            if len(ref_full) > 40 and ("/" in ref_full or "\\" in ref_full):
                parts = ref_full.replace("\\", "/").split("/")
                tail = "/".join(parts[-2:]) if len(parts) > 2 else ref_full
                ref_display = "…/" + tail if tail else ref_full
            try:
                action_tag = row["action_tag"] or "—"
            except (KeyError, IndexError):
                action_tag = "—"
            self._set_item(i, 0, name, row["id"])
            self._set_item(i, 1, action_tag)
            self._set_item(i, 2, desc)
            self._set_item(i, 3, _SCOPE_LABEL.get(row["scope"], row["scope"]))
            self._set_item(i, 4, ref_display, tooltip=ref_full)
            self._set_item(i, 5, str(row["used"]))

        if self.table.rowCount() > 0:
            self.table.selectRow(0)
        else:
            self.detail.setMarkdown(self._empty_message())

    def _empty_message(self) -> str:
        # Phase 8：按 is_template_filter 分情况文案
        if self.is_template_filter is True:
            return (
                "## 还没有通用模板\n\n"
                "**PH 的核心价值在这里**——把你写过的提示词抽象成可跨项目复用的模板。\n\n"
                "**怎么生成**：\n"
                "1. 切到「原始材料」tab，选一条你写过的提示词\n"
                "2. 点详情页底部「生成通用模板」按钮，LLM 会把项目名、路径、版本号等具体细节抽象成 `[占位符]`\n"
                "3. 或在「待审」里挑挖到的候选 → 选「保留并通用化」一键产出模板\n\n"
                "通用模板可以分享给朋友、放进新项目直接用。"
            )
        if self.is_template_filter is False:
            return (
                "## 还没有原始材料\n\n"
                "原始材料 = 你写过的提示词 + 踩过的坑 + 项目专属经验（未通用化的版本）。\n\n"
                "**最快补充**：\n"
                "- 点右上「从 Claude 历史导入」自动挖你过去的会话\n"
                "- 「从文件导入」拖入其他 AI 工具的导出文件\n"
                "- 「推荐库」批量加几十条公开种子\n"
            )
        return ("## 这个分类还没有内容\n\n点右上「从 Claude 历史导入」开始挖掘。")

    def _set_item(
        self, row: int, col: int, text: str,
        prompt_id: str | None = None, tooltip: str | None = None,
    ) -> None:
        it = QTableWidgetItem(text)
        if col == 0 and prompt_id:
            it.setData(Qt.ItemDataRole.UserRole, prompt_id)
        if tooltip:
            it.setToolTip(tooltip)
        self.table.setItem(row, col, it)

    def _selected_id(self) -> str | None:
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            return None
        item = self.table.item(rows[0].row(), 0)
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def _on_select_row(self) -> None:
        pid = self._selected_id()
        if not pid:
            self.detail.clear()
            return
        conn = indexer.open_db(self.cfg)
        row = indexer.get_by_id(conn, pid)
        conn.close()
        if not row:
            return
        try:
            desc = row["description"] or ""
        except (KeyError, IndexError):
            desc = ""
        try:
            ref = row["source_ref"] or row["project"]
        except (KeyError, IndexError):
            ref = row["project"] if "project" in row.keys() else None
        if not ref and row["optimized_from_id"]:
            # 反查原条目，向上找参考来源
            conn3 = indexer.open_db(self.cfg)
            parent = conn3.execute(
                "SELECT title, project, source_ref FROM prompts WHERE id = ?",
                (row["optimized_from_id"],),
            ).fetchone()
            conn3.close()
            if parent:
                ref = (
                    parent["source_ref"] or parent["project"]
                    or f"源条目: {(parent['title'] or '')[:30]}"
                )
        if not ref:
            ref = "—"
        meta = (
            f"**类型**: {_SCOPE_LABEL.get(row['scope'], row['scope'])}  ·  "
            f"**参考来源**: {ref}  ·  "
            f"**用过**: {row['used']} 次\n\n"
            f"**标签**: {row['tags_csv'] or '—'}\n\n"
        )
        if desc:
            meta += f"**描述**：{desc}\n\n"
        else:
            meta += "**描述**：_（未填，编辑详情可手写或 LLM 生成）_\n\n"

        # C1：引用关系
        from ...core import references
        body = row["body"] or ""
        out_refs = references.find_references(body)
        if out_refs:
            meta += "**引用了**：" + "  ".join(f"`[[{r}]]`" for r in out_refs) + "\n\n"
        conn2 = indexer.open_db(self.cfg)
        try:
            backlinks = indexer.find_backlinks(conn2, row["title"])
        except Exception:
            backlinks = []
        # P14 T2：A/B 对显示
        try:
            pair = indexer.find_optimized_pair(conn2, row["id"])
        except Exception:
            pair = None
        conn2.close()
        if backlinks:
            meta += "**被引用**：" + "  ".join(f"`{r['title']}`" for r in backlinks[:6]) + "\n\n"
        if pair:
            my_signal = int(row["success_signal"] or 0)
            pair_signal = int(pair["success_signal"] or 0)
            verdict = ""
            if my_signal > pair_signal + 1:
                verdict = "（**当前版本反馈更好**，可考虑归并）"
            elif pair_signal > my_signal + 1:
                verdict = "（同源版本反馈更好，可考虑切换）"
            else:
                verdict = "（差距小，再用一阵看趋势）"
            meta += (
                f"**A/B 同源**：`{pair['title']}`  ·  "
                f"⭐ {pair_signal} 用 {pair['used']} 次 (vs 此条 ⭐ {my_signal} 用 {row['used']} 次) "
                f"{verdict}\n\n"
            )

        # P22：去掉 `---` 横线——markdown HR 渲染成视觉上像文本框边线。
        # 用两个空行做留白分隔，metadata 与 body 之间用 padding 自然区分。
        self.detail.setMarkdown(meta + "\n\n" + body)

    def _on_edit(self) -> None:
        from ..widgets.prompt_editor import PromptEditorDialog
        pid = self._selected_id()
        if not pid:
            return
        conn = indexer.open_db(self.cfg)
        row = indexer.get_by_id(conn, pid)
        conn.close()
        if not row:
            return
        prompt = storage.parse(Path(row["file_path"]).read_text(encoding="utf-8"))
        dlg = PromptEditorDialog(self.cfg, prompt=prompt, parent=self)
        if dlg.exec() == dlg.DialogCode.Accepted:
            self.parent_page.refresh()

    def _on_delete(self) -> None:
        pid = self._selected_id()
        if not pid:
            return
        ans = QMessageBox.question(self, "确认删除", "确定删除？")
        if ans != QMessageBox.StandardButton.Yes:
            return
        conn = indexer.open_db(self.cfg)
        row = indexer.get_by_id(conn, pid)
        if row:
            try:
                Path(row["file_path"]).unlink(missing_ok=True)
            except Exception:
                pass
            indexer.delete_by_id(conn, pid)
        conn.close()
        self.parent_page.refresh()

    def _on_copy(self) -> None:
        pid = self._selected_id()
        if not pid:
            return
        conn = indexer.open_db(self.cfg)
        row = indexer.get_by_id(conn, pid)
        if row:
            indexer.bump_used(conn, pid)
        conn.close()
        if not row:
            return

        body = row["body"] or ""
        # B1：检测占位符，有就弹填表对话框
        from ...core import placeholders, references
        names = placeholders.find(body)
        if names:
            from ..dialogs.fill_placeholders_dialog import FillPlaceholdersDialog
            dlg = FillPlaceholdersDialog(
                row["title"] or "untitled", body, names, parent=self,
            )
            if dlg.exec() != dlg.DialogCode.Accepted:
                return
            body = dlg.filled_text()

        # P13-T1：展开 [[ref]] 引用——按 title 查库，找到就替换为 body
        ref_names = references.find_references(body)
        expanded_n = 0
        if ref_names:
            conn = indexer.open_db(self.cfg)

            def _lookup(title: str):
                nonlocal expanded_n
                r = indexer.get_by_title(conn, title)
                if r:
                    expanded_n += 1
                    return r["body"] or ""
                return None

            body = references.expand_references(body, _lookup, max_depth=1)
            conn.close()

        from PySide6.QtWidgets import QApplication
        QApplication.clipboard().setText(body)
        extra = f"，展开了 {expanded_n} 个 [[引用]]" if expanded_n else ""
        QMessageBox.information(
            self, "已复制",
            f"已复制到剪贴板（{len(body)} 字{extra}）。粘到 Claude Code 输入框就能用。",
        )

    def _on_context_menu(self, pos) -> None:
        """P12-N4：表格行右键菜单。

        P13 修：如果右键点击的行不在当前 selection 内，才单选它；否则保留多选状态。
        """
        idx = self.table.indexAt(pos)
        if not idx.isValid():
            return
        sel_rows = {r.row() for r in self.table.selectionModel().selectedRows()}
        if idx.row() not in sel_rows:
            self.table.selectRow(idx.row())
        # 多选状态保持不动——用户能继续用菜单的"分享选中"等批量操作
        from PySide6.QtWidgets import QMenu
        m = QMenu(self)
        m.addAction("复制内容", self._on_copy)
        m.addAction("📋 复制副本（新建相同内容）", self._on_duplicate)
        m.addSeparator()
        m.addAction("👍 标记为有用", self._on_useful)
        m.addAction("👎 标记不够好", self._on_not_useful)
        m.addSeparator()
        m.addAction("⚡ 生成通用模板", self._on_generalize)
        m.addAction("编辑详情…", self._on_edit)
        m.addSeparator()
        m.addAction("删除", self._on_delete)
        m.exec(self.table.viewport().mapToGlobal(pos))

    def _on_duplicate(self) -> None:
        """P12-N4：复制一条 prompt 为新 id 同内容。"""
        pid = self._selected_id()
        if not pid:
            return
        conn = indexer.open_db(self.cfg)
        row = indexer.get_by_id(conn, pid)
        conn.close()
        if not row:
            return
        try:
            from pathlib import Path
            old_path = Path(row["file_path"])
            src = storage.load(old_path)
            new = storage.Prompt.new(
                title=f"{src.title} (副本)",
                body=src.body,
                scope=src.scope,
                project=src.project,
                tags=list(src.tags),
                stack=list(src.stack),
                triggers=list(src.triggers),
                origin=src.origin,
            )
            new.description = src.description
            new.source_ref = src.source_ref
            new.categories = list(src.categories)
            new.is_template = src.is_template
            file_path = storage.save(self.cfg, new, commit_msg=f"duplicate: {src.title[:30]}")
            conn = indexer.open_db(self.cfg)
            indexer.upsert(conn, new, file_path)
            conn.close()
            self.parent_page.refresh()
            win = self.window()
            if hasattr(win, "statusBar"):
                try:
                    win.statusBar().showMessage(f"✓ 已复制副本：{new.title}", 3000)
                except Exception:
                    pass
        except Exception as e:
            QMessageBox.warning(self, "复制失败", f"{type(e).__name__}: {e}")

    def _on_useful(self) -> None:
        pid = self._selected_id()
        if not pid:
            return
        conn = indexer.open_db(self.cfg)
        indexer.bump_success(conn, pid)
        row = indexer.get_by_id(conn, pid)
        conn.close()
        signal = row["success_signal"] if row else 0
        win = self.window()
        if hasattr(win, "statusBar"):
            try:
                win.statusBar().showMessage(f"✓ 已记录「有用」（当前成功信号：{signal}）", 3000)
            except Exception:
                pass
        self.refresh()

    def _on_not_useful(self) -> None:
        pid = self._selected_id()
        if not pid:
            return
        conn = indexer.open_db(self.cfg)
        indexer.bump_negative(conn, pid)
        row = indexer.get_by_id(conn, pid)
        conn.close()
        signal = row["success_signal"] if row else 0
        win = self.window()
        if hasattr(win, "statusBar"):
            try:
                win.statusBar().showMessage(f"⚠ 已记录「不够好」（当前成功信号：{signal}）", 3000)
            except Exception:
                pass
        self.refresh()

    def _on_generalize(self) -> None:
        """Phase 8 核心：把当前选中的原始 prompt 通用化为可跨项目模板。"""
        pid = self._selected_id()
        if not pid:
            QMessageBox.information(self, "未选中", "先在左侧表格选一条提示词。")
            return
        conn = indexer.open_db(self.cfg)
        row = indexer.get_by_id(conn, pid)
        conn.close()
        if not row:
            return

        # 启 dialog 异步跑 LLM
        from ..dialogs.generalize_dialog import GeneralizeDialog
        dlg = GeneralizeDialog(self.cfg, dict(row), parent=self)
        if dlg.exec() == dlg.DialogCode.Accepted and dlg.action != "cancel":
            self.parent_page.refresh()


# ---------------------------------------------------------------------------
# 待审 tab：候选卡片
# ---------------------------------------------------------------------------

class InboxView(QWidget):
    def __init__(self, cfg: Config, parent_page: LibraryPage):
        super().__init__()
        self.cfg = cfg
        self.parent_page = parent_page
        self._tag_filter: str = ""  # ""=全部
        self._build()

    def _build(self) -> None:
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 12, 0, 0)
        v.setSpacing(8)

        explainer = QLabel(
            "**这是什么**：装上 Claude Code 插件后，你写完一段好提示词、"
            "Claude 觉得「这条值得保存」时会自动放到这里等你确认。"
        )
        explainer.setWordWrap(True)
        explainer.setTextFormat(Qt.TextFormat.MarkdownText)
        explainer.setStyleSheet("color: #525252; font-size: 13px; padding: 0 4px 8px 4px;")
        v.addWidget(explainer)

        # Phase 22.5 N4：场景标签 chips 单选过滤
        from ...core.action_tags import ALL_TAGS
        from PySide6.QtWidgets import QPushButton as _QBtn
        chips_row = QHBoxLayout()
        chips_row.setSpacing(4)
        lbl = QLabel("场景：")
        lbl.setStyleSheet("color: #525252; font-size: 12px; padding-right: 4px;")
        chips_row.addWidget(lbl)
        self._chip_buttons: dict[str, _QBtn] = {}
        chip_qss = (
            "QPushButton { background: #f0f0f0; border: 0; border-radius: 11px;"
            "padding: 3px 10px; font-size: 11px; color: #525252; }"
            "QPushButton:checked { background: #0a0a0a; color: white; font-weight: 600; }"
            "QPushButton:hover:!checked { background: #e0e0e0; }"
        )
        all_btn = _QBtn("全部")
        all_btn.setCheckable(True); all_btn.setChecked(True)
        all_btn.setStyleSheet(chip_qss)
        all_btn.clicked.connect(lambda: self._on_chip_clicked(""))
        self._chip_buttons[""] = all_btn
        chips_row.addWidget(all_btn)
        for tag in ALL_TAGS:
            b = _QBtn(tag)
            b.setCheckable(True)
            b.setStyleSheet(chip_qss)
            b.clicked.connect(lambda _c=False, t=tag: self._on_chip_clicked(t))
            self._chip_buttons[tag] = b
            chips_row.addWidget(b)
        chips_row.addStretch(1)
        chips_host = QWidget(); chips_host.setLayout(chips_row)
        v.addWidget(chips_host)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.cards_host = QWidget()
        self.cards_layout = QVBoxLayout(self.cards_host)
        self.cards_layout.setContentsMargins(0, 0, 0, 0)
        self.cards_layout.setSpacing(8)
        self.scroll.setWidget(self.cards_host)
        v.addWidget(self.scroll, 1)

    def _on_chip_clicked(self, tag: str) -> None:
        if tag == self._tag_filter:
            self._chip_buttons[tag].setChecked(True)
            return
        self._tag_filter = tag
        for k, b in self._chip_buttons.items():
            b.setChecked(k == tag)
        self.refresh()

    def refresh(self) -> None:
        # 清空
        while self.cards_layout.count():
            w = self.cards_layout.takeAt(0).widget()
            if w:
                w.deleteLater()

        all_items: list[InboxItem] = []
        if self.cfg.inbox_dir.is_dir():
            for p in sorted(self.cfg.inbox_dir.glob("*.md")):
                try:
                    all_items.append(InboxItem.load(p))
                except Exception:
                    continue
        # 各 chip 的命中数 → 写到按钮文字
        counts: dict[str, int] = {"": len(all_items)}
        for it in all_items:
            tag = it.action_tag or ""
            counts[tag] = counts.get(tag, 0) + 1
        for k, b in self._chip_buttons.items():
            if k == "":
                b.setText(f"全部 ({counts.get('', 0)})")
            else:
                n = counts.get(k, 0)
                b.setText(f"{k} ({n})" if n > 0 else k)

        # 应用筛选
        items = (
            [it for it in all_items if (it.action_tag or "") == self._tag_filter]
            if self._tag_filter else all_items
        )
        items.sort(key=lambda x: (-x.confidence, x.created))

        if not items:
            empty = QLabel(
                "✓ 暂无待审候选。\n\n装上 Claude Code 插件后，写出值得保存的提示词时，"
                "Claude 会自动给你挑出来放在这里。"
            )
            empty.setStyleSheet(
                "color: #737373; padding: 32px; font-size: 13px; "
                "background: #fafafa; border-radius: 10px; line-height: 1.6;"
            )
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            empty.setWordWrap(True)
            self.cards_layout.addWidget(empty)
            self.cards_layout.addStretch(1)
            return

        for it in items:
            self.cards_layout.addWidget(InboxCard(self.cfg, it, self))
        self.cards_layout.addStretch(1)


class InboxCard(QFrame):
    def __init__(self, cfg: Config, item: InboxItem, parent_view: InboxView):
        super().__init__()
        self.cfg = cfg
        self.item = item
        self.parent_view = parent_view

        # P22：去掉浅灰背景——视觉上像被框住。改 transparent + hover 反馈。
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setStyleSheet("""
            InboxCard {
                background: transparent;
                border: 0;
                border-radius: 10px;
            }
            InboxCard:hover {
                background-color: #fafafa;
            }
        """)
        self._build()

    def _build(self) -> None:
        v = QVBoxLayout(self)
        v.setContentsMargins(18, 14, 18, 14)
        v.setSpacing(8)

        head = QHBoxLayout()
        conf = self.item.confidence
        color = "#16a34a" if conf >= 0.6 else ("#ca8a04" if conf >= 0.4 else "#9ca3af")
        badge = QLabel(f"匹配度 {int(conf * 100)}%")
        badge.setStyleSheet(f"color: {color}; font-weight: 600; font-size: 12px;")
        head.addWidget(badge)
        # Phase 22.5 N4：显示动作类型标签
        if self.item.action_tag:
            tag_badge = QLabel(f"  · {self.item.action_tag}")
            tag_badge.setStyleSheet(
                "color: white; background: #525252; padding: 2px 8px;"
                "border-radius: 9px; font-size: 11px; margin-left: 4px;"
            )
            head.addWidget(tag_badge)
        head.addStretch(1)

        self.btn_approve = QPushButton("保存")
        self.btn_approve.setProperty("class", "subtle")
        self.btn_approve.setToolTip("只保留原文，不通用化")
        self.btn_approve.clicked.connect(self._on_approve)
        # Phase 8：第三个动作——保留并通用化（双版本都存）
        from .. import icons as _icons2
        from PySide6.QtCore import QSize as _QSize2
        self.btn_approve_template = QPushButton(" 保留并通用化")
        self.btn_approve_template.setIcon(_icons2.icon_white("generalize"))
        self.btn_approve_template.setIconSize(_QSize2(13, 13))
        self.btn_approve_template.setProperty("class", "primary")
        self.btn_approve_template.setToolTip(
            "保留原文 + 异步生成通用模板版（可分享、可跨项目用）"
        )
        self.btn_approve_template.clicked.connect(self._on_approve_template)
        self.btn_dismiss = QPushButton("丢弃")
        self.btn_dismiss.setProperty("class", "danger")
        self.btn_dismiss.clicked.connect(self._on_dismiss)
        head.addWidget(self.btn_dismiss)
        head.addWidget(self.btn_approve)
        head.addWidget(self.btn_approve_template)
        v.addLayout(head)

        body_text = self.item.body.strip()
        body_preview = body_text if len(body_text) <= 600 else body_text[:600] + "…"
        body = QLabel(body_preview)
        body.setWordWrap(True)
        body.setStyleSheet("color: #1f2937; font-size: 13px; line-height: 1.6;")
        body.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        v.addWidget(body)

    def _on_approve(self) -> None:
        from ..widgets.prompt_editor import PromptEditorDialog
        prefilled = storage.Prompt.new(
            title=self.item.suggested_title or self.item.body.split("\n")[0][:30],
            body=self.item.body,
            scope="global",
            origin="mining",
        )
        dlg = PromptEditorDialog(self.cfg, prompt=prefilled, parent=self)
        if dlg.exec() == dlg.DialogCode.Accepted:
            try:
                self.item.path.unlink()
            except Exception:
                pass
            self.parent_view.parent_page.refresh()

    def _on_dismiss(self) -> None:
        ans = QMessageBox.question(self, "丢弃", "确定丢弃这条候选？")
        if ans != QMessageBox.StandardButton.Yes:
            return
        try:
            self.item.path.unlink()
        except Exception:
            pass
        self.parent_view.parent_page.refresh()

    def _on_approve_template(self) -> None:
        """Phase 8：保留原文 + 异步生成通用模板版（双版本都存）。"""
        # 先直接入库原文，跳过编辑器
        prefilled = storage.Prompt.new(
            title=self.item.suggested_title or self.item.body.split("\n")[0][:30],
            body=self.item.body,
            scope="global",
            origin="mining",
        )
        try:
            file_path = storage.save(
                self.cfg, prefilled,
                commit_msg=f"approve mining + template: {prefilled.title[:30]}",
            )
            conn = indexer.open_db(self.cfg)
            indexer.upsert(conn, prefilled, file_path)
            conn.close()
        except Exception as e:
            QMessageBox.warning(self, "入库失败", f"{type(e).__name__}: {e}")
            return
        # 删 inbox 文件
        try:
            self.item.path.unlink()
        except Exception:
            pass
        # 跳通用化对话框
        from ..dialogs.generalize_dialog import GeneralizeDialog
        row = {
            "id": prefilled.id,
            "title": prefilled.title,
            "body": prefilled.body,
            "file_path": str(file_path),
            "tags_csv": ",".join(prefilled.tags),
            "categories_csv": ",".join(prefilled.categories),
        }
        dlg = GeneralizeDialog(self.cfg, row, parent=self)
        dlg.exec()
        self.parent_view.parent_page.refresh()
