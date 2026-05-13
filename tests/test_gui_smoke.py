"""GUI 烟测：导入、主窗口构建、页面切换、新建提示词。

只测核心结构，不测具体 UI 渲染（QT_QPA_PLATFORM=offscreen）。
"""

import os
from pathlib import Path

import pytest

# 必须在 import PySide6 之前设置
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication

from prompt_help.core import indexer
from prompt_help.core.config import Config, GitConfig


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def gui_cfg(tmp_path: Path, monkeypatch) -> Config:
    monkeypatch.setenv("PROMPT_HELP_VAULT_PATH", str(tmp_path))
    c = Config(vault_path=tmp_path)
    c.git = GitConfig(auto_commit=False)
    for d in ("prompts/global", "prompts/projects", "prompts/traps",
              "inbox", "briefs/_active", "pulse", "logs"):
        (tmp_path / d).mkdir(parents=True)
    (tmp_path / ".onboarding_done").write_text("1")
    (tmp_path / ".tour_done").write_text("1")
    indexer.open_db(c).close()
    return c


def test_main_window_constructs(qapp, gui_cfg: Config):
    from prompt_help.gui.main_window import MainWindow
    win = MainWindow(gui_cfg)
    assert win.windowTitle().startswith("Prompt Help")
    # P21：纯懒加载，启动只构造 HomePage
    assert win.stack.count() == 1
    # P22：8 个页面工厂（新增 project_optimize）
    assert set(win._page_factories.keys()) == {
        "home", "library", "public", "pm", "project_optimize",
        "stats", "settings", "help",
    }


def test_page_switching(qapp, gui_cfg: Config):
    from prompt_help.gui.main_window import MainWindow
    win = MainWindow(gui_cfg)
    # 主导航 5 个按钮（设置走右上齿轮）
    for btn, expected_widget_name in [
        (win.btn_home, "page_home"),
        (win.btn_library, "page_library"),
        (win.btn_public, "page_public"),
        (win.btn_pm, "page_pm"),
        (win.btn_help, "page_help"),
    ]:
        btn.setChecked(True)
        btn.clicked.emit()
        assert win.stack.currentWidget() is getattr(win, expected_widget_name)


def test_create_prompt_via_editor(qapp, gui_cfg: Config):
    from prompt_help.gui.widgets.prompt_editor import PromptEditorDialog
    dlg = PromptEditorDialog(gui_cfg)
    dlg.title.setText("test prompt")
    dlg.body.setPlainText("test body content")
    dlg._on_save()
    # 库里应有一条
    conn = indexer.open_db(gui_cfg)
    counts = indexer.count_all(conn)
    conn.close()
    assert counts["total"] == 1


def test_library_page_renders(qapp, gui_cfg: Config):
    # 先入库 3 条
    from prompt_help.core import storage
    for i in range(3):
        p = storage.Prompt.new(title=f"item {i}", body=f"body {i}", scope="global")
        fp = storage.save(gui_cfg, p)
        conn = indexer.open_db(gui_cfg)
        indexer.upsert(conn, p, fp)
        conn.close()

    from prompt_help.gui.pages.library import LibraryPage
    page = LibraryPage(gui_cfg)
    # Phase 8：第一个 tab 是「通用模板」(is_template=True)——3 条都是普通条目应为空
    _key, tpl_view = page.tab_filters[0]
    assert _key == "templates"
    assert tpl_view.table.rowCount() == 0
    # 第二个 tab「原始材料」(is_template=False) 应有 3 行
    _key, raw_view = page.tab_filters[1]
    assert _key == "raw"
    raw_view.refresh()
    assert raw_view.table.rowCount() == 3


def test_library_inbox_tab_empty(qapp, gui_cfg: Config):
    from prompt_help.gui.pages.library import LibraryPage, InboxView
    page = LibraryPage(gui_cfg)
    # 找 inbox tab 视图
    inbox_view = None
    for key, view in page.tab_filters:
        if key == "inbox":
            inbox_view = view; break
    assert isinstance(inbox_view, InboxView)
    # P21：inbox 非默认 tab，懒刷——切到才填空状态；测试显式 refresh
    inbox_view.refresh()
    # 空 inbox 时 cards_layout 应含空状态 label + stretch
    assert inbox_view.cards_layout.count() == 2


def test_pm_mode_page_no_drafts(qapp, gui_cfg: Config):
    from prompt_help.gui.pages.pm_mode import PmModePage
    page = PmModePage(gui_cfg)
    # Phase 7 重写：stack 只有 list / chat 两屏
    assert page.stack.count() == 2
    assert page.session is None
    # 三维度进度条都存在且初值 0
    assert page.bar_what["bar"].value() == 0
    assert page.bar_why["bar"].value() == 0
    assert page.bar_how["bar"].value() == 0


def test_home_page_constructs(qapp, gui_cfg: Config):
    from prompt_help.gui.pages.home import HomePage
    page = HomePage(gui_cfg)
    assert page.card_top is not None
    assert page.card_inbox is not None
    assert page.card_pm is not None
    assert page.card_traps is not None


def test_settings_page_loads_values(qapp, gui_cfg: Config):
    from prompt_help.gui.pages.settings import SettingsPage
    page = SettingsPage(gui_cfg)
    assert page.base_url.text() == gui_cfg.llm.base_url
    assert page.model.text() == gui_cfg.llm.model
    assert page.mining_enabled.isChecked() == gui_cfg.mining.enabled


def test_onboarding_dialog_constructs(qapp, gui_cfg: Config):
    from prompt_help.gui.onboarding.wizard import OnboardingWizard
    dlg = OnboardingWizard(gui_cfg)
    # 现在是单屏对话框，含 scan_path / api_key / btn_done 三个核心字段
    assert dlg.scan_path is not None
    assert dlg.api_key is not None
    assert dlg.btn_done is not None
    assert dlg.windowTitle().startswith("Prompt Help")
