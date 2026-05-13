"""首页：今天你能做什么。

不是表格，不是搜索，是 4-5 个大卡片：
  1. 看你最常用的提示词
  2. 看 Claude 自动给你抓的待审候选
  3. 想新做一个产品 - 走 PM-Mode
  4. 看上次踩过的坑

每张卡片个性化：库空时显示"先做扫描"，库满时显示"看看你的库存"。
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSize, Qt, QThread, Signal
from PySide6.QtGui import QCursor, QFont
from PySide6.QtWidgets import (
    QFrame, QGridLayout, QHBoxLayout, QLabel, QPushButton, QSizePolicy,
    QToolButton, QVBoxLayout, QWidget,
)

from ...core import indexer
from ...core.config import Config
from .. import icons as _icons
from ..components.onboarding_checklist import OnboardingChecklist
from ..components.project_recall_card import ProjectRecallCard
from ..components.stats_banner import StatsBanner


class HomePage(QWidget):
    """首页。点击卡片会发出 navigate 信号，由 MainWindow 接管切页。"""

    navigate = Signal(str)  # "library" / "library_inbox" / "pm" / "help" / "library_traps" / "settings" / "public_library"

    def __init__(self, cfg: Config):
        super().__init__()
        self.cfg = cfg
        self._build()
        # P19：启动时延迟所有重的 refresh，让主窗口先显示出来
        from PySide6.QtCore import QTimer
        QTimer.singleShot(0, self.refresh_data)
        if hasattr(self, "project_card"):
            QTimer.singleShot(120, self.project_card.refresh)

    def _build(self) -> None:
        # P21：首页内容（标题 + 刷新行 + 清单 + 项目卡 + 2x2 卡片网格 + 提示行）≈ 1100px，
        # 1240x800 窗口下尾部卡片被挤；包 ScrollArea 让窗口窄时能滚动看完
        from PySide6.QtWidgets import QScrollArea, QFrame
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
        v.setContentsMargins(40, 36, 40, 28)
        v.setSpacing(16)

        # 大标题 + 一句话
        title = QLabel("今天你能做什么")
        title.setObjectName("pageTitle")
        v.addWidget(title)
        sub = QLabel(
            "Prompt Help 帮你跨项目沉淀三件事：**提示词 · 系统记忆 · 项目踩坑点**，"
            "再用 LLM 二次优化成可分享的通用模板。"
        )
        sub.setObjectName("pageHint")
        sub.setWordWrap(True)
        v.addWidget(sub)

        # P17：刷新本机项目按钮
        refresh_row = QHBoxLayout()
        refresh_row.addStretch(1)
        from PySide6.QtWidgets import QPushButton
        from .. import icons as _icons
        self.btn_refresh_projects = QPushButton(" 刷新本机项目")
        self.btn_refresh_projects.setProperty("class", "subtle")
        self.btn_refresh_projects.setIcon(_icons.icon("refresh"))
        self.btn_refresh_projects.setIconSize(QSize(14, 14))
        self.btn_refresh_projects.setToolTip(
            "扫所有已配置的扫描根目录，把 CLAUDE.md / AGENTS.md / .cursorrules 的最新内容"
            "同步到 PH 库。新增 / 修改自动入库，不重复"
        )
        self.btn_refresh_projects.clicked.connect(self._on_refresh_projects)
        refresh_row.addWidget(self.btn_refresh_projects)
        v.addLayout(refresh_row)

        # 新手任务清单（Phase 7）
        self.checklist = OnboardingChecklist(self.cfg)
        self.checklist.navigate.connect(self.navigate)
        v.addWidget(self.checklist)

        # B4：跨项目自动召回卡片
        self.project_card = ProjectRecallCard(self.cfg)
        v.addWidget(self.project_card)

        # 卡片网格 2x2
        grid = QGridLayout()
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(16)

        # Phase 8 核心：通用模板卡作 C 位
        self.card_top = HomeCard(
            icon_name="templates",
            title="通用模板（核心价值）",
            hint_empty=(
                "还没有通用模板。\n\n"
                "**PH 的核心创新**——把你写过的提示词抽象成可跨项目复用的模板。\n"
                "去「原始材料」选一条 → 点「生成通用模板」→ LLM 自动抽象。"
            ),
            action_empty="去原始材料",
            hint_full="你有 {n} 条通用模板可以分享、跨项目复用",
            action_full="看通用模板",
        )
        self.card_top.action.clicked.connect(lambda: self.navigate.emit("library"))
        grid.addWidget(self.card_top, 0, 0)

        self.card_inbox = HomeCard(
            icon_name="inbox",
            title="待审候选",
            hint_empty="Claude 还没自动抓到值得保存的提示词。装上插件后会有。",
            action_empty="装插件",
            hint_full="有 {n} 条 Claude 替你抓的提示词，等你确认要不要留",
            action_full="去审阅",
        )
        self.card_inbox.action.clicked.connect(lambda: self.navigate.emit("library_inbox"))
        grid.addWidget(self.card_inbox, 0, 1)

        self.card_pm = HomeCard(
            icon_name="pm_mode",
            title="想做新产品？",
            hint_empty="走完 7 个问题，搞清楚到底要建什么 / 为什么 / 死穴在哪，再去开发。",
            action_empty="开始 PM-Mode",
            hint_full="你有 {n} 个 PM-Mode 草稿在跑",
            action_full="去 PM-Mode",
        )
        self.card_pm.action.clicked.connect(lambda: self.navigate.emit("pm"))
        grid.addWidget(self.card_pm, 1, 0)

        self.card_traps = HomeCard(
            icon_name="raw_material",
            title="原始材料库",
            hint_empty="还没有原始材料。从 Claude / Codex 历史挖一遍，或从推荐库拉公开种子。",
            action_empty="去原始材料",
            hint_full="积累了 {n} 条原始材料（待通用化的素材）",
            action_full="看原始材料",
        )
        self.card_traps.action.clicked.connect(lambda: self.navigate.emit("library_raw"))
        grid.addWidget(self.card_traps, 1, 1)

        v.addLayout(grid)

        # C2：统计 banner
        self.stats_banner = StatsBanner(self.cfg)
        v.addWidget(self.stats_banner)

        v.addStretch(1)

        # 底部小提示
        tip = QLabel(
            "第一次用？侧栏「帮助」里有完整入门指南，或随时打开新手引导教程。"
        )
        tip.setStyleSheet("color: #737373; font-size: 12px; padding-top: 8px;")
        v.addWidget(tip)

    # ------------------------------------------------------------------

    def on_show(self) -> None:
        # P19：refresh_data 主线程跑（快、必要——影响卡片显示）；
        # 其他几个 refresh 错开到事件循环下一步，避免一次性阻塞 200-500ms
        self.refresh_data()
        from PySide6.QtCore import QTimer
        # 50ms 让主框架先渲染一次
        if hasattr(self, "checklist"):
            QTimer.singleShot(0, self.checklist.refresh)
        if hasattr(self, "stats_banner"):
            QTimer.singleShot(30, self.stats_banner.refresh)
        if hasattr(self, "project_card"):
            # project_card 最重（要算 fingerprint），放最后
            QTimer.singleShot(80, self.project_card.refresh)

    def _on_refresh_projects(self) -> None:
        """P17：扫所有 scan_roots 刷新最新内容到库。"""
        from PySide6.QtWidgets import QFileDialog, QMessageBox
        from ...core import refresh as _refresh
        roots = _refresh.load_scan_roots(self.cfg)
        if not roots:
            ans = QMessageBox.question(
                self, "还没配置扫描根目录",
                "PH 还不知道扫哪些目录。要立即选一个项目根目录加进来吗？\n\n"
                "（比如 D:/My_Project，里面装着你所有项目子目录）",
            )
            if ans != QMessageBox.StandardButton.Yes:
                return
            d = QFileDialog.getExistingDirectory(
                self, "选项目根目录（含多个子项目）", str(Path.cwd()) if 'Path' in dir() else "",
            )
            if not d:
                return
            from pathlib import Path as _Path
            _refresh.add_scan_root(self.cfg, _Path(d))
            roots = _refresh.load_scan_roots(self.cfg)

        self.btn_refresh_projects.setEnabled(False)
        self.btn_refresh_projects.setText(" 刷新中…")
        self._refresh_thread = _RefreshProjectsThread(self.cfg)
        self._refresh_thread.progress.connect(self._on_refresh_progress)
        self._refresh_thread.done.connect(self._on_refresh_done)
        self._refresh_thread.start()

    def _on_refresh_progress(self, msg: str) -> None:
        win = self.window()
        if hasattr(win, "statusBar"):
            try:
                win.statusBar().showMessage(f"⟳ {msg}", 2000)
            except Exception:
                pass

    def _on_refresh_done(self, result_dict: dict) -> None:
        from PySide6.QtWidgets import QMessageBox
        self.btn_refresh_projects.setEnabled(True)
        self.btn_refresh_projects.setText(" 刷新本机项目")
        added = result_dict.get("added", 0)
        updated = result_dict.get("updated", 0)
        skipped = result_dict.get("skipped_same", 0)
        files = result_dict.get("files_scanned", 0)
        projects = result_dict.get("projects_scanned", 0)
        errors = result_dict.get("errors") or []
        msg = (
            f"刷新完成：\n\n"
            f"  · 新增：{added} 条\n"
            f"  · 更新：{updated} 条（内容变了的）\n"
            f"  · 跳过：{skipped} 条（完全相同）\n\n"
            f"扫描了 {projects} 个项目 / {files} 个 md 文件"
        )
        if errors:
            msg += "\n\n问题：\n" + "\n".join(f"  · {e}" for e in errors[:5])
        QMessageBox.information(self, "刷新完成", msg)
        # 触发首页数据 / 卡片刷新
        self.refresh_data()
        if hasattr(self, "project_card"):
            self.project_card.refresh()
        if hasattr(self, "stats_banner"):
            self.stats_banner.refresh()

    def refresh_data(self) -> None:
        tpl = {"templates": 0, "raw": 0, "total": 0}
        try:
            conn = indexer.open_db(self.cfg)
            counts = indexer.count_all(conn)
            tpl = indexer.count_templates(conn)
            conn.close()
        except Exception:
            counts = {"total": 0, "global": 0, "project": 0, "trap": 0}

        inbox_n = (
            len(list(self.cfg.inbox_dir.glob("*.md")))
            if self.cfg.inbox_dir.is_dir() else 0
        )

        try:
            briefs = list((self.cfg.briefs_dir / "_active").glob("*.json"))
        except Exception:
            briefs = []

        # Phase 8：card_top = 通用模板数；card_traps = 原始材料数
        self.card_top.set_state(
            populated=tpl["templates"] > 0,
            full_data={"n": tpl["templates"]},
        )
        self.card_inbox.set_state(populated=inbox_n > 0, full_data={"n": inbox_n})
        self.card_pm.set_state(populated=len(briefs) > 0, full_data={"n": len(briefs)})
        self.card_traps.set_state(
            populated=tpl["raw"] > 0,
            full_data={"n": tpl["raw"]},
        )


class HomeCard(QFrame):
    """首页卡片：矢量图标 + 标题 + 状态文字 + 主按钮。"""

    def __init__(
        self,
        icon_name: str,
        title: str,
        hint_empty: str,
        action_empty: str,
        hint_full: str,
        action_full: str,
    ):
        super().__init__()
        self.hint_empty = hint_empty
        self.action_empty = action_empty
        self.hint_full = hint_full
        self.action_full = action_full
        self.populated = False

        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setStyleSheet("""
            HomeCard {
                background-color: #fafafa;
                border: 0;
                border-radius: 12px;
            }
            HomeCard:hover { border-color: #d4d4d4; }
        """)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMinimumHeight(180)

        v = QVBoxLayout(self)
        v.setContentsMargins(24, 22, 24, 22)
        v.setSpacing(10)

        head = QHBoxLayout()
        head.setSpacing(12)
        # 矢量图标放在圆形浅灰背景里
        icon_box = QFrame()
        icon_box.setFrameShape(QFrame.Shape.NoFrame)
        icon_box.setFixedSize(40, 40)
        icon_box.setStyleSheet(
            "QFrame { background: #f5f5f5; border-radius: 20px; border: 0; }"
        )
        ibl = QVBoxLayout(icon_box)
        ibl.setContentsMargins(0, 0, 0, 0)
        icon_btn = QToolButton()
        icon_btn.setIcon(_icons.icon(icon_name))
        icon_btn.setIconSize(QSize(20, 20))
        icon_btn.setEnabled(False)  # 装饰用
        icon_btn.setStyleSheet("QToolButton { background: transparent; border: 0; }")
        ibl.addWidget(icon_btn, alignment=Qt.AlignmentFlag.AlignCenter)
        head.addWidget(icon_box)

        title_lbl = QLabel(title)
        title_lbl.setStyleSheet(
            "font-size: 16px; font-weight: 600; color: #0a0a0a;"
        )
        head.addWidget(title_lbl)
        head.addStretch(1)
        v.addLayout(head)

        self.hint = QLabel("")
        self.hint.setWordWrap(True)
        self.hint.setStyleSheet("color: #525252; font-size: 13px; line-height: 1.6;")
        self.hint.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        v.addWidget(self.hint, 1)

        bar = QHBoxLayout()
        bar.addStretch(1)
        self.action = QPushButton(action_empty)
        self.action.setProperty("class", "primary")
        self.action.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        bar.addWidget(self.action)
        v.addLayout(bar)

    def set_state(self, *, populated: bool, full_data: dict) -> None:
        self.populated = populated
        if populated:
            text = self.hint_full.format(**full_data)
            self.action.setText(self.action_full)
        else:
            text = self.hint_empty
            self.action.setText(self.action_empty)
        self.hint.setText(text)


class _RefreshProjectsThread(QThread):
    """P17：刷新本机项目后台 thread。"""

    progress = Signal(str)
    done = Signal(dict)

    def __init__(self, cfg):
        super().__init__()
        self.cfg = cfg

    def run(self) -> None:
        from ...core import refresh as _refresh
        try:
            r = _refresh.refresh_all(self.cfg, progress=lambda msg: self.progress.emit(msg))
            self.done.emit({
                "added": r.added,
                "updated": r.updated,
                "skipped_same": r.skipped_same,
                "files_scanned": r.files_scanned,
                "projects_scanned": r.projects_scanned,
                "errors": r.errors,
            })
        except Exception as e:
            self.done.emit({
                "added": 0, "updated": 0, "skipped_same": 0,
                "files_scanned": 0, "projects_scanned": 0,
                "errors": [f"{type(e).__name__}: {e}"],
            })
