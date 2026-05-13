"""新手任务清单组件（Phase 7 T4）。

5 项任务持久化到 ~/.prompt-help/onboarding_state.json：
1. 配置 LLM 后端（env API key 或 CC CLI 在 PATH）
2. 导入第一条提示词（counts.total > 0）
3. 试用搜索（手动勾选 / 在 LibraryPage 搜过任意词上报）
4. 装 Claude Code 插件（~/.claude/plugins/prompt-help/plugin.json 存在）
5. 完成第一个 PM-Mode 访谈（briefs/_active_dialog/*.json 存在或 ready=true）

任务全部完成后卡片折叠为"✅ 上手完成"，点击可重新展开。
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QPushButton,
    QSizePolicy, QVBoxLayout, QWidget,
)

from .. import icons as _icons
from ...core import indexer
from ...core.config import Config


TASKS = [
    {
        "id": "llm_backend",
        "label": "1. 配置 LLM 后端",
        "desc": "Claude Code CLI / Codex CLI / DeepSeek API 任一可用",
        "action": "去设置",
        "nav": "settings",
    },
    {
        "id": "first_prompt",
        "label": "2. 导入第一条提示词",
        "desc": "手动新建 / 从历史挖 / 推荐库——任一即可",
        "action": "去推荐库",
        "nav": "library",  # PublicLibrary 通过 main_window nav 跳
    },
    {
        "id": "try_search",
        "label": "3. 试用搜索",
        "desc": "在「我的库」搜任意关键词，体验下查找速度",
        "action": "去搜索",
        "nav": "library",
    },
    {
        "id": "install_plugin",
        "label": "4. 装 Claude Code 插件",
        "desc": "插件能在写代码时自动召回相关提示词",
        "action": "看说明",
        "nav": "help",
    },
    {
        "id": "first_pm_chat",
        "label": "5. 试一次产品发现",
        "desc": "LLM 苏格拉底访谈帮你想清楚要建什么",
        "action": "去产品发现",
        "nav": "pm",
    },
]


def _state_path(cfg: Config) -> Path:
    return cfg.vault_path / "onboarding_state.json"


def load_state(cfg: Config) -> dict:
    p = _state_path(cfg)
    if p.is_file():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"manual": {}, "collapsed": False}


def save_state(cfg: Config, state: dict) -> None:
    try:
        _state_path(cfg).write_text(
            json.dumps(state, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except OSError:
        pass


def detect_task_done(cfg: Config, task_id: str, manual: dict) -> bool:
    """自动检测一项任务是否完成。manual dict 包含用户手动勾选的项。"""
    if manual.get(task_id):
        return True
    if task_id == "llm_backend":
        # P22：三选一任一可用即认为完成
        if os.environ.get(cfg.llm.api_key_env):
            return True
        if shutil.which(cfg.optimizer.cc_cli_path):
            return True
        if shutil.which(cfg.optimizer.codex_cli_path):
            return True
        return False
    if task_id == "first_prompt":
        try:
            conn = indexer.open_db(cfg)
            n = indexer.count_all(conn)["total"]
            conn.close()
            return n > 0
        except Exception:
            return False
    if task_id == "try_search":
        # 没有自动信号，靠 manual 勾选
        return False
    if task_id == "install_plugin":
        plugin_json = Path.home() / ".claude" / "plugins" / "prompt-help" / "plugin.json"
        return plugin_json.is_file()
    if task_id == "first_pm_chat":
        dialog_dir = cfg.briefs_dir / "_active_dialog"
        if dialog_dir.is_dir() and any(dialog_dir.glob("*.json")):
            return True
        if cfg.briefs_dir.is_dir():
            for child in cfg.briefs_dir.iterdir():
                if child.is_dir() and (child / "PRODUCT_BRIEF.md").is_file():
                    return True
        return False
    return False


class OnboardingChecklist(QFrame):
    """新手任务卡，按完成进度自动收起。"""

    navigate = Signal(str)  # 触发导航

    def __init__(self, cfg: Config):
        super().__init__()
        self.cfg = cfg
        self.state = load_state(cfg)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setStyleSheet(
            "OnboardingChecklist { background: #fafafa; border: 0; "
            "border-radius: 12px; }"
        )
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self._build()
        self.refresh()

    def _build(self) -> None:
        self.outer = QVBoxLayout(self)
        self.outer.setContentsMargins(20, 16, 20, 16)
        self.outer.setSpacing(10)

        head = QHBoxLayout()
        self.title_lbl = QLabel("上手 5 步")
        self.title_lbl.setStyleSheet("font-size: 14px; font-weight: 600; color: #0a0a0a;")
        head.addWidget(self.title_lbl)
        head.addStretch(1)
        self.btn_toggle = QPushButton("折叠")
        self.btn_toggle.setProperty("class", "subtle")
        self.btn_toggle.setStyleSheet("font-size: 11px; padding: 2px 8px;")
        self.btn_toggle.clicked.connect(self._on_toggle)
        head.addWidget(self.btn_toggle)
        self.outer.addLayout(head)

        self.tasks_host = QWidget()
        self.tasks_layout = QVBoxLayout(self.tasks_host)
        self.tasks_layout.setContentsMargins(0, 0, 0, 0)
        self.tasks_layout.setSpacing(6)
        self.outer.addWidget(self.tasks_host)

    def _on_toggle(self) -> None:
        self.state["collapsed"] = not self.state.get("collapsed", False)
        save_state(self.cfg, self.state)
        self.refresh()

    def refresh(self) -> None:
        # 重建任务列表
        while self.tasks_layout.count():
            it = self.tasks_layout.takeAt(0)
            if it.widget():
                it.widget().deleteLater()

        manual = self.state.setdefault("manual", {})
        done_states = [(t, detect_task_done(self.cfg, t["id"], manual)) for t in TASKS]
        done_count = sum(1 for _, d in done_states if d)
        all_done = done_count == len(TASKS)

        # P16-T1 修：之前 `or all_done` 让 all_done 时 collapsed 永远 True 点不开。
        # 现改为：all_done 默认折叠，但 state["collapsed"] 显式 False 时尊重用户「展开」选择
        if all_done:
            collapsed = self.state.get("collapsed", True)
        else:
            collapsed = self.state.get("collapsed", False)

        if all_done:
            self.title_lbl.setText("上手完成（5 / 5 全部完成）")
            self.title_lbl.setStyleSheet("font-size: 14px; font-weight: 600; color: #16a34a;")
        else:
            self.title_lbl.setText(f"上手 5 步（{done_count}/{len(TASKS)} 完成）")
            self.title_lbl.setStyleSheet("font-size: 14px; font-weight: 600; color: #0a0a0a;")
        self.btn_toggle.setText("展开" if collapsed else "折叠")

        self.tasks_host.setVisible(not collapsed)

        if collapsed:
            return

        for task, done in done_states:
            self.tasks_layout.addWidget(self._make_task_row(task, done, manual))

    def _make_task_row(self, task: dict, done: bool, manual: dict) -> QFrame:
        f = QFrame()
        f.setStyleSheet("QFrame { background: transparent; border: 0; }")
        h = QHBoxLayout(f)
        h.setContentsMargins(0, 2, 0, 2)
        h.setSpacing(10)

        # P22：放弃 QCheckBox（默认 checked 状态是大黑方块，丑）。
        # done → 绿色 circle-check icon；undone → 灰色空心 circle icon。
        # 仅 "try_search" 那条因为没有自动信号、需要手动勾选，做成可点击切换。
        if task["id"] == "try_search":
            indicator = QPushButton()
            indicator.setFixedSize(20, 20)
            indicator.setFlat(True)
            indicator.setCursor(Qt.CursorShape.PointingHandCursor)
            indicator.setIconSize(QSize(18, 18))
            indicator.setStyleSheet(
                "QPushButton { background: transparent; border: 0; padding: 0; }"
            )
            if done:
                indicator.setIcon(_icons.icon("success", color="#16a34a"))
            else:
                indicator.setIcon(_icons.icon("circle_empty", color="#a3a3a3"))
            indicator.clicked.connect(
                lambda _=False, tid=task["id"], cur=done: self._on_manual_toggle(tid, not cur)
            )
            h.addWidget(indicator)
        else:
            indicator = QLabel()
            indicator.setFixedSize(20, 20)
            if done:
                indicator.setPixmap(
                    _icons.icon("success", color="#16a34a").pixmap(18, 18)
                )
            else:
                indicator.setPixmap(
                    _icons.icon("circle_empty", color="#d4d4d4").pixmap(18, 18)
                )
            h.addWidget(indicator)

        col = QVBoxLayout()
        col.setSpacing(2)
        label = QLabel(task["label"])
        style_color = "#a3a3a3" if done else "#0a0a0a"
        style_strike = "text-decoration: line-through;" if done else ""
        label.setStyleSheet(
            f"font-size: 13px; font-weight: 500; color: {style_color}; {style_strike}"
        )
        col.addWidget(label)
        desc = QLabel(task["desc"])
        desc.setStyleSheet("color: #737373; font-size: 11px;")
        col.addWidget(desc)
        h.addLayout(col, 1)

        if not done:
            btn = QPushButton(task["action"])
            btn.setProperty("class", "subtle")
            btn.setStyleSheet("font-size: 11px; padding: 4px 12px;")
            btn.clicked.connect(lambda _=False, nav=task["nav"]: self.navigate.emit(nav))
            h.addWidget(btn)
        return f

    def _on_manual_toggle(self, task_id: str, checked: bool) -> None:
        self.state.setdefault("manual", {})[task_id] = checked
        save_state(self.cfg, self.state)
        self.refresh()
