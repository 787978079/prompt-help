"""帮助页：极简 5 段说明 + 新手教程入口。

不堆专业术语，按 vibecoding 新手能 get 的方式重写。
"""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QPushButton, QScrollArea, QVBoxLayout, QWidget,
)

from ...core.config import Config
from .. import icons as _icons


class HelpPage(QWidget):
    def __init__(self, cfg: Config):
        super().__init__()
        self.cfg = cfg
        self._build()

    def _build(self) -> None:
        v = QVBoxLayout(self)
        v.setContentsMargins(40, 28, 40, 24)
        v.setSpacing(12)

        title = QLabel("帮助")
        title.setObjectName("pageTitle")
        v.addWidget(title)
        hint = QLabel("第一次用？这一页讲清楚 Prompt Help 是干嘛的、怎么开始。")
        hint.setObjectName("pageHint")
        v.addWidget(hint)

        bar = QHBoxLayout()
        bar.addStretch(1)
        from .. import icons as _icons
        from PySide6.QtCore import QSize as _QSize
        self.btn_replay = QPushButton("  打开新手教程")
        self.btn_replay.setProperty("class", "primary")
        self.btn_replay.setIcon(_icons.icon_white("play"))
        self.btn_replay.setIconSize(_QSize(13, 13))
        self.btn_replay.clicked.connect(self._on_replay)
        bar.addWidget(self.btn_replay)
        v.addLayout(bar)

        # 滚动内容区
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        host = QWidget()
        body = QVBoxLayout(host)
        body.setContentsMargins(8, 8, 8, 8)
        body.setSpacing(20)

        # P21：emoji 全换 qtawesome 矢量图标
        for icon_key, head, text in [
            ("bulb", "Prompt Help 是干嘛的？",
             "你跟 Claude（或别的 AI）写代码时，会写很多有用的提示词，比如「跑测试 → 截图 → 看效果」"
             "这种工作流模板。但每次都得手敲、翻历史。Prompt Help 把这些**好用的提示词存下来、跨"
             "项目搜得到、关键时刻自动提醒你**。"),
            ("books", "「我的库」里有什么？",
             "三类：\n"
             "  • **通用** — 跨项目都能用的，比如「让 Claude 跑测试再截图」\n"
             "  • **项目专属** — 只对某个项目有意义的，比如「MinPEI 的生产 API 在哪个文件」\n"
             "  • **踩坑提醒** — 你下次再写到某个关键词时会自动跳出来，比如「不要批量杀 node 进程」"),
            ("rotate", "怎么把已有提示词导入？",
             "「我的库」右上「从 Claude 历史导入」一键扫——会读你 Claude Code 的所有历史会话，"
             "把写过的、有结构的提示词自动挑出来入库。这是你**真实的提示词**，不是凭空生成。"),
            ("compass", "「产品发现」干嘛用？",
             "你冒出新 idea 想做新软件之前，先走 7 个问题：什么痛 → 谁用 → 已有什么 → 你的独特点 → "
             "MVP 多大 → 死穴在哪 → 怎么算成功。走完会输出一份 PRODUCT_BRIEF.md，让你和 Claude "
             "都对齐「要做啥」，再去做开发计划。"),
            ("robot", "Claude Code 插件能做什么？",
             "装上之后（「设置」一键装），Claude Code 跑代码时会：\n"
             "  • 写出好提示词时**自动放到「待审」**，等你确认要不要存\n"
             "  • 你说「我要 taskkill node」时**自动弹出踩坑提醒**\n"
             "  • 进新项目时**推荐相似历史项目的提示词**\n"
             "不装也能用，只是失去这些自动化。"),
            ("lock", "数据放哪里？安全吗？",
             "全部在你电脑本地的 `~/.prompt-help/`，是个 git 仓（自动备份每次修改）。\n"
             "**绝不会上传到任何公开服务**。API key 只在本机 .env 文件里，永远不进 git。"),
        ]:
            body.addWidget(_section_card(icon_key, head, text))

        body.addStretch(1)
        scroll.setWidget(host)
        v.addWidget(scroll, 1)

        about = QLabel(f"v0.1  ·  数据：{self.cfg.vault_path}")
        about.setStyleSheet("color: #a3a3a3; font-size: 11px;")
        v.addWidget(about)

    def on_show(self) -> None:
        pass

    def _on_replay(self) -> None:
        """Phase 7：跳到 spotlight 引导（替换原静态 5 屏 tour）。"""
        win = self.window()
        if hasattr(win, "force_start_global_tour"):
            win.force_start_global_tour()


def _section_card(icon_key: str, head: str, text: str) -> QFrame:
    f = QFrame()
    # P21：去 1px 边框，只用浅灰背景
    f.setStyleSheet("""
        QFrame {
            background-color: #fafafa;
            border: 0;
            border-radius: 10px;
        }
    """)
    v = QVBoxLayout(f)
    v.setContentsMargins(20, 16, 20, 16)
    v.setSpacing(6)

    head_row = QHBoxLayout()
    icon_lbl = QLabel()
    icon_lbl.setPixmap(_icons.icon(icon_key).pixmap(QSize(18, 18)))
    head_row.addWidget(icon_lbl)
    h = QLabel(head)
    h.setStyleSheet("font-size: 15px; font-weight: 600; color: #0a0a0a; padding-left: 6px;")
    head_row.addWidget(h)
    head_row.addStretch(1)
    v.addLayout(head_row)

    body = QLabel(text)
    body.setWordWrap(True)
    body.setTextFormat(Qt.TextFormat.MarkdownText)
    body.setStyleSheet("color: #525252; font-size: 13px; line-height: 1.7;")
    v.addWidget(body)
    return f
