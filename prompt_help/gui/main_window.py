"""主窗口：4 项侧边栏（首页 / 我的库 / PM-Mode / 帮助）+ 右上齿轮。

不再让 Inbox 占侧边栏：合并进我的库作为「待审」tab。
设置页通过右上齿轮图标进入。
"""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtGui import QAction, QIcon
from PySide6.QtWidgets import (
    QButtonGroup, QFrame, QHBoxLayout, QLabel, QMainWindow, QPushButton,
    QStackedWidget, QStatusBar, QSystemTrayIcon, QVBoxLayout, QWidget,
)

from ..core.config import Config
from . import icons


class MainWindow(QMainWindow):
    def __init__(self, cfg: Config):
        super().__init__()
        self.cfg = cfg
        self.setWindowTitle("Prompt Help · 提示词 / 系统记忆 / 项目踩坑点")
        self.resize(1240, 800)
        self._build_ui()

    def _build_ui(self) -> None:
        root = QWidget()
        h = QHBoxLayout(root)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(0)
        self.setCentralWidget(root)

        sidebar = self._build_sidebar()
        h.addWidget(sidebar, 0)

        # 右侧：顶部条 + content stack
        right = QWidget()
        right_v = QVBoxLayout(right)
        right_v.setContentsMargins(0, 0, 0, 0)
        right_v.setSpacing(0)
        right_v.addWidget(self._build_top_bar())

        self.stack = QStackedWidget()
        self.stack.setObjectName("content")
        right_v.addWidget(self.stack, 1)
        h.addWidget(right, 1)

        # 页面真·懒加载（P21）：只在 __init__ 建 HomePage（默认首屏），
        # 其他页第一次切换/被信号引用时才 import + 构造，启动可省 1-2s 大头
        # 主要由 PySide6 widget tree 构建 + 子页面里多个 QThread/QTimer init 累计耗时。
        self._pages: dict[str, QWidget] = {}
        self._page_factories: dict[str, callable] = {
            "home": self._build_home_page,
            "library": self._build_library_page,
            "public": self._build_public_page,
            "pm": self._build_pm_page,
            "project_optimize": self._build_project_optimize_page,  # P22 新增
            "stats": self._build_stats_page,
            "settings": self._build_settings_page,
            "help": self._build_help_page,
        }
        home = self._ensure_page("home")

        self._wire_buttons()
        self.stack.setCurrentWidget(home)
        self.btn_home.setChecked(True)

        # 状态栏
        self.setStatusBar(QStatusBar())
        self._refresh_status()

        # 自动扫描服务（T6）
        self._setup_auto_scan()

        # Phase 7：spotlight 引导
        self._setup_tour()

        # P21：启动 2 秒后空闲预热 library——用户最可能切去的第二个页面，
        # 把首次构造的 ~1.5s 抢在用户点击之前完成；用 singleShot 而非 QThread
        # 是因为 PySide widget 必须主线程构造
        QTimer.singleShot(2000, lambda: self._ensure_page("library"))

    def _build_sidebar(self) -> QWidget:
        w = QFrame()
        w.setObjectName("sidebar")
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)

        logo = QLabel("Prompt Help")
        logo.setObjectName("logo")
        v.addWidget(logo)
        sub = QLabel("提示词 · 系统记忆 · 踩坑点")
        sub.setObjectName("subtitle")
        v.addWidget(sub)

        self.btn_home = self._mk_nav("  首页", "home", icon_name="home")
        self.btn_home.setObjectName("tour_nav_home")
        self.btn_library = self._mk_nav("  我的库", "library", icon_name="library")
        self.btn_library.setObjectName("tour_nav_library")
        self.btn_public = self._mk_nav("  推荐库", "public", icon_name="public_library")
        self.btn_public.setObjectName("tour_nav_public")
        self.btn_pm = self._mk_nav("  产品发现", "pm", icon_name="pm_mode")
        self.btn_pm.setObjectName("tour_nav_pm")
        # P22：项目优化 —— 粘贴提示词 + 选项目 → LLM 基于项目上下文重写
        self.btn_project_optimize = self._mk_nav(
            "  项目优化", "project_optimize", icon_name="project_optimize",
        )
        self.btn_project_optimize.setObjectName("tour_nav_project_optimize")
        self.btn_stats = self._mk_nav("  统计", "stats", icon_name="trending")
        self.btn_stats.setObjectName("tour_nav_stats")
        self.btn_help = self._mk_nav("  帮助", "help", icon_name="help")
        self.btn_help.setObjectName("tour_nav_help")

        for b in (self.btn_home, self.btn_library, self.btn_public,
                   self.btn_pm, self.btn_project_optimize,
                   self.btn_stats, self.btn_help):
            v.addWidget(b)
        v.addStretch(1)

        # 底部不再放设置——挪到右上角齿轮
        version = QLabel("v0.1")
        version.setStyleSheet("color: #a3a3a3; font-size: 11px; padding: 12px 20px;")
        v.addWidget(version)

        self.nav_group = QButtonGroup(self)
        self.nav_group.setExclusive(True)
        for b in (self.btn_home, self.btn_library, self.btn_public,
                   self.btn_pm, self.btn_project_optimize,
                   self.btn_stats, self.btn_help):
            self.nav_group.addButton(b)

        return w

    def _build_top_bar(self) -> QWidget:
        """右侧顶部细条：仅承载齿轮图标，主操作交给页面。"""
        w = QFrame()
        w.setStyleSheet("background: transparent;")
        h = QHBoxLayout(w)
        h.setContentsMargins(16, 12, 16, 0)
        h.addStretch(1)

        self.btn_settings = QPushButton("  设置")
        self.btn_settings.setProperty("class", "subtle")
        self.btn_settings.setObjectName("tour_top_settings")
        self.btn_settings.setToolTip("API key、自动化阈值、Claude Code 插件开关")
        try:
            self.btn_settings.setIcon(icons.icon("settings"))
            self.btn_settings.setIconSize(QSize(16, 16))
        except Exception:
            pass
        self.btn_settings.clicked.connect(self._on_open_settings)
        h.addWidget(self.btn_settings)

        # P18 T2：关于按钮
        self.btn_about = QPushButton("关于")
        self.btn_about.setProperty("class", "subtle")
        self.btn_about.setToolTip("版本、数据路径、log 路径、依赖")
        self.btn_about.clicked.connect(self._on_open_about)
        h.addWidget(self.btn_about)
        return w

    def _mk_nav(self, label: str, key: str, icon_name: str = "") -> QPushButton:
        b = QPushButton(label)
        b.setCheckable(True)
        b.setProperty("nav_key", key)
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        if icon_name:
            try:
                # 用同一图标——选中态时 QSS 不能切图标色，所以用 icon helper 提前算好
                b.setIcon(icons.icon_muted(icon_name))
                b.setIconSize(QSize(18, 18))
                # 选中时刷成白色图标：连 toggled 信号
                def _swap(checked, btn=b, name=icon_name):
                    btn.setIcon(icons.icon_white(name) if checked else icons.icon_muted(name))
                b.toggled.connect(_swap)
            except Exception:
                pass
        return b

    def _wire_buttons(self) -> None:
        for b in self.nav_group.buttons():
            key = b.property("nav_key")
            if key in self._page_factories:
                b.clicked.connect(lambda _checked=False, k=key: self._switch_to_key(k))

    def _ensure_page(self, key: str) -> QWidget:
        """按需构造页面并加入 stack。再次访问时直接返回缓存。"""
        page = self._pages.get(key)
        if page is None:
            page = self._page_factories[key]()
            self._pages[key] = page
            self.stack.addWidget(page)
        return page

    def _switch_to_key(self, key: str) -> None:
        page = self._ensure_page(key)
        self._switch_to(page)

    def _switch_to(self, page) -> None:
        self.stack.setCurrentWidget(page)
        if hasattr(page, "on_show"):
            page.on_show()
        self._refresh_status()

    # 工厂函数：与首次访问时间一一对应。home 之外的 import 推迟到这里
    def _build_home_page(self) -> QWidget:
        from .pages.home import HomePage
        p = HomePage(self.cfg)
        p.navigate.connect(self._on_home_navigate)
        return p

    def _build_library_page(self) -> QWidget:
        from .pages.library import LibraryPage
        return LibraryPage(self.cfg)

    def _build_public_page(self) -> QWidget:
        from .pages.public_library import PublicLibraryPage
        return PublicLibraryPage(self.cfg)

    def _build_pm_page(self) -> QWidget:
        from .pages.pm_mode import PmModePage
        return PmModePage(self.cfg)

    def _build_project_optimize_page(self) -> QWidget:
        from .pages.project_optimize import ProjectOptimizePage
        return ProjectOptimizePage(self.cfg)

    def _build_stats_page(self) -> QWidget:
        from .pages.stats import StatsPage
        return StatsPage(self.cfg)

    def _build_settings_page(self) -> QWidget:
        from .pages.settings import SettingsPage
        return SettingsPage(self.cfg)

    def _build_help_page(self) -> QWidget:
        from .pages.help import HelpPage
        return HelpPage(self.cfg)

    # 旧 attribute 访问兼容：self.page_xxx → self._ensure_page("xxx")
    # 既不影响测试，也让 _on_home_navigate / _nav_to_target 等老代码继续工作
    def __getattr__(self, name: str):
        # Qt 在 __init__ 早期可能访问 staticMetaObject 等，避开自启动死循环
        if name.startswith("page_"):
            key = name[5:]
            factories = self.__dict__.get("_page_factories")
            if factories and key in factories:
                return self._ensure_page(key)
        raise AttributeError(name)

    def _on_home_navigate(self, target: str) -> None:
        """HomePage 卡片发的导航信号。"""
        if target == "library":
            self.btn_library.setChecked(True)
            self._switch_to(self.page_library)
        elif target == "library_inbox":
            self.btn_library.setChecked(True)
            self.page_library.show_tab("inbox")
            self._switch_to(self.page_library)
        elif target == "library_traps":
            self.btn_library.setChecked(True)
            self.page_library.show_tab("raw")
            self._switch_to(self.page_library)
        elif target == "library_raw":
            self.btn_library.setChecked(True)
            self.page_library.show_tab("raw")
            self._switch_to(self.page_library)
        elif target == "library_templates":
            self.btn_library.setChecked(True)
            self.page_library.show_tab("templates")
            self._switch_to(self.page_library)
        elif target == "pm":
            self.btn_pm.setChecked(True)
            self._switch_to(self.page_pm)
        elif target == "help":
            self.btn_help.setChecked(True)
            self._switch_to(self.page_help)
        elif target == "settings":
            self._on_open_settings()
        elif target == "public_library":
            if hasattr(self, "btn_public"):
                self.btn_public.setChecked(True)
            if hasattr(self, "page_public"):
                self._switch_to(self.page_public)

    def _on_open_about(self) -> None:
        """P18 T2：弹关于对话框。"""
        from .dialogs.about_dialog import AboutDialog
        dlg = AboutDialog(self.cfg, parent=self)
        dlg.exec()

    def _on_open_settings(self) -> None:
        """齿轮按钮：弹设置对话框 / 切到设置页。"""
        # 设置页其实是个 QWidget，包成临时对话框比较麻烦，简单点：直接切到 stack 的它
        self.stack.setCurrentWidget(self.page_settings)
        # 取消所有 nav 按钮选中（设置不在主导航里）
        for b in self.nav_group.buttons():
            b.setChecked(False)
        self.page_settings.on_show()
        self._refresh_status()

    def _refresh_status(self) -> None:
        try:
            from ..core import indexer
            conn = indexer.open_db(self.cfg)
            counts = indexer.count_all(conn)
            inbox_n = (
                len(list(self.cfg.inbox_dir.glob("*.md")))
                if self.cfg.inbox_dir.is_dir() else 0
            )
            conn.close()
            sync_str = self._git_sync_status()
            self.statusBar().showMessage(
                f"库：{counts['total']} 条  ·  待审：{inbox_n}  ·  {sync_str}",
            )
        except Exception:
            self.statusBar().showMessage(f"数据存：{self.cfg.vault_path}")

    _git_status_cache: tuple = (0.0, "")  # (timestamp, text)

    def _git_sync_status(self) -> str:
        """P14 T3 + P20：状态栏文本。60s 缓存，避免每次 _refresh_status 都跑 subprocess。"""
        import time
        from ..core import proc
        cached_ts, cached_text = MainWindow._git_status_cache
        if time.time() - cached_ts < 60 and cached_text:
            return cached_text

        vault = self.cfg.vault_path
        if not (vault / ".git").is_dir():
            text = "本地（未配 git）"
            MainWindow._git_status_cache = (time.time(), text)
            return text
        try:
            r = proc.run(
                ["git", "-C", str(vault), "status", "--porcelain"],
                capture_output=True, text=True, timeout=2,
            )
            uncommitted = len([l for l in r.stdout.splitlines() if l.strip()])
        except Exception:
            uncommitted = 0
        remote = "本地仓"
        try:
            r = proc.run(
                ["git", "-C", str(vault), "remote", "-v"],
                capture_output=True, text=True, timeout=2,
            )
            if r.stdout.strip():
                remote = "🔗 已连远程"
        except Exception:
            pass
        parts = [remote]
        if uncommitted > 0:
            parts.append(f"{uncommitted} 待提交")
        text = "  ·  ".join(parts)
        MainWindow._git_status_cache = (time.time(), text)
        return text

    # ------------------------------------------------------------------
    # 自动扫描（T6）
    # ------------------------------------------------------------------

    def _setup_tour(self) -> None:
        """构建 spotlight 引导（Phase 7）。"""
        from .onboarding.spotlight_tour import SpotlightTour
        from .onboarding.tour_steps import build_global_tour
        steps = build_global_tour(self._nav_to_target)
        self.global_tour = SpotlightTour(self, "global_v2", steps)

    def _nav_to_target(self, target: str) -> None:
        """Tour 步骤回调：跳到指定主页面。"""
        if target == "home":
            self.btn_home.setChecked(True)
            self._switch_to(self.page_home)
        elif target == "library":
            self.btn_library.setChecked(True)
            self._switch_to(self.page_library)
        elif target == "public":
            self.btn_public.setChecked(True)
            self._switch_to(self.page_public)
        elif target == "pm":
            self.btn_pm.setChecked(True)
            self._switch_to(self.page_pm)
        elif target == "help":
            self.btn_help.setChecked(True)
            self._switch_to(self.page_help)

    def maybe_start_global_tour(self) -> None:
        """首次启动后被 app.py 调；已完成的话不弹。"""
        if hasattr(self, "global_tour"):
            self.global_tour.maybe_start()

    def force_start_global_tour(self) -> None:
        """帮助页「重新看新手引导」按钮调。"""
        if hasattr(self, "global_tour"):
            self.global_tour.start_forced()

    def _setup_auto_scan(self) -> None:
        from .services.auto_scan import AutoScanService

        self.auto_scan = AutoScanService(self.cfg, interval_minutes=30)
        self.auto_scan.new_candidates.connect(self._on_auto_scan_hits)

        # 系统托盘图标（用于通知）
        if QSystemTrayIcon.isSystemTrayAvailable():
            self.tray = QSystemTrayIcon(self)
            # 用窗口图标兜底（没单独的 tray icon 资源）
            self.tray.setIcon(self.windowIcon() or QIcon())
            self.tray.setToolTip("Prompt Help · 自动扫描中")
            self.tray.show()
        else:
            self.tray = None

        self.auto_scan.start()

    def _on_auto_scan_hits(self, count: int, by_source: dict) -> None:
        """有新候选时刷状态栏 + 系统托盘 toast。"""
        sources_str = "、".join(f"{k}: {v}" for k, v in by_source.items())
        self.statusBar().showMessage(
            f"📬 自动扫描发现 {count} 条新候选（{sources_str}）→ 我的库 → 待审",
            12000,
        )
        if self.tray and self.tray.isVisible():
            try:
                self.tray.showMessage(
                    "Prompt Help",
                    f"发现 {count} 条新提示词候选\n打开「我的库 → 待审」查看",
                    QSystemTrayIcon.MessageIcon.Information,
                    8000,
                )
            except Exception:
                pass
        self._refresh_status()
