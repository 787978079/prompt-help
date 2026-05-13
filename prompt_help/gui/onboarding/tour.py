"""新手教程：5 屏图文引导。

内嵌在主窗口上方一个浮动 QDialog，每屏一张大卡片：
  1. 欢迎 + 一句话价值
  2. 三类提示词解释（通用/项目专属/踩坑提醒）+ 例子
  3. 怎么找一条用：搜索 → 详情 → 复制 → 粘到 Claude
  4. 怎么获取种子：从 Claude 历史导入
  5. 想做新产品 → 走 PM-Mode → 输出 brief 给 Claude 当上下文
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QDialog, QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget,
)


_PAGES = [
    {
        "emoji": "👋",
        "title": "欢迎使用 Prompt Help",
        "body": (
            "你跟 AI 写代码时，会写很多**有用的提示词**——\n"
            "「跑测试再截图给我看」、「这个项目用 Next.js 16 别记错」、「不要批量杀 node 进程」。\n\n"
            "这些好提示词散在历史会话里，**用一次就丢一次**。\n\n"
            "Prompt Help 帮你把它们**存下来、跨项目搜到、关键时刻自动提醒**。"
        ),
    },
    {
        "emoji": "📚",
        "title": "三类提示词，分开管理",
        "body": (
            "**通用**：跨项目都能用的。\n"
            "  例：「跑 Playwright 测试再截图，截图必须包含改前改后对比」\n\n"
            "**项目专属**：只对某个项目有意义。\n"
            "  例：「MinPEI 的生产 API 是手动 hotfix，不要碰 deploy 脚本」\n\n"
            "**踩坑提醒**：写消息时如果命中关键词，自动弹出来挡你一下。\n"
            "  例：你说「批量杀 node 进程」时，自动弹「不行！Claude 自己也是 node 进程」"
        ),
    },
    {
        "emoji": "🔍",
        "title": "怎么找一条用？3 步",
        "body": (
            "1. 在「我的库」搜索框输关键词（比如 `playwright`）\n"
            "2. 点列表条目，右侧看完整内容\n"
            "3. 点「复制内容」一键带走，粘到 Claude Code 输入框就用\n\n"
            "每用一次都会自动 +1 计数，常用的会自动排前面。"
        ),
    },
    {
        "emoji": "🪄",
        "title": "怎么获得起步的种子？",
        "body": (
            "「我的库」右上**「从 Claude 历史导入」**按钮。\n\n"
            "它会扫你 Claude Code 的所有历史会话（`~/.claude/projects/` 下面），\n"
            "把你写过的、长度合适、有结构的提示词**自动挖出来入库**。\n\n"
            "这是**你真实写过的内容**——不是模板、不是别人的。"
        ),
    },
    {
        "emoji": "🧭",
        "title": "想做新软件？先走「产品发现」",
        "body": (
            "Plan 模式回答「**怎么建**」，但 vibecoder 容易跳过「**建什么 / 为何 / 死穴**」。\n\n"
            "「产品发现」是 7 个问题（每屏一题，多选+自填）：\n"
            "痛点 → 用户 → 对手 → 独特点 → 范围 → 风险 → 成功定义\n\n"
            "走完输出 PRODUCT_BRIEF.md，让 Claude 知道你想要什么再去开发。\n\n"
            "[点「完成」开始用]"
        ),
    },
]


class TourDialog(QDialog):
    """5 屏图文教程。点下一页/上一页切。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Prompt Help · 新手教程")
        self.resize(660, 520)
        self.setModal(True)
        self.current = 0
        self._build()
        self._render(0)

    def _build(self) -> None:
        v = QVBoxLayout(self)
        v.setContentsMargins(36, 28, 36, 24)
        v.setSpacing(14)

        # 进度
        self.progress = QLabel("")
        self.progress.setStyleSheet("color: #a3a3a3; font-size: 12px;")
        v.addWidget(self.progress)

        # emoji + 标题
        self.emoji = QLabel("")
        self.emoji.setStyleSheet("font-size: 40px; padding-top: 6px;")
        v.addWidget(self.emoji)

        self.title = QLabel("")
        f = QFont()
        f.setPointSize(20)
        f.setWeight(QFont.Weight.Bold)
        self.title.setFont(f)
        self.title.setStyleSheet("color: #0a0a0a; padding-bottom: 6px;")
        self.title.setWordWrap(True)
        v.addWidget(self.title)

        # 正文
        self.body = QLabel("")
        self.body.setWordWrap(True)
        self.body.setTextFormat(Qt.TextFormat.MarkdownText)
        self.body.setStyleSheet("color: #404040; font-size: 14px; line-height: 1.8;")
        self.body.setAlignment(Qt.AlignmentFlag.AlignTop)
        v.addWidget(self.body, 1)

        # 底部
        bar = QHBoxLayout()
        self.btn_prev = QPushButton("上一页")
        self.btn_prev.setProperty("class", "subtle")
        self.btn_prev.clicked.connect(self._on_prev)
        bar.addWidget(self.btn_prev)
        bar.addStretch(1)
        self.btn_skip = QPushButton("跳过教程")
        self.btn_skip.setProperty("class", "subtle")
        self.btn_skip.clicked.connect(self.reject)
        bar.addWidget(self.btn_skip)
        self.btn_next = QPushButton("下一页 →")
        self.btn_next.setProperty("class", "primary")
        self.btn_next.clicked.connect(self._on_next)
        bar.addWidget(self.btn_next)
        v.addLayout(bar)

    def _render(self, i: int) -> None:
        i = max(0, min(i, len(_PAGES) - 1))
        self.current = i
        page = _PAGES[i]
        self.progress.setText(f"第 {i + 1} 页 / 共 {len(_PAGES)} 页")
        self.emoji.setText(page["emoji"])
        self.title.setText(page["title"])
        self.body.setText(page["body"])
        self.btn_prev.setEnabled(i > 0)
        self.btn_next.setText("完成" if i == len(_PAGES) - 1 else "下一页 →")

    def _on_prev(self) -> None:
        if self.current > 0:
            self._render(self.current - 1)

    def _on_next(self) -> None:
        if self.current < len(_PAGES) - 1:
            self._render(self.current + 1)
        else:
            self.accept()


def run_tour(parent: QWidget, cfg) -> None:
    dlg = TourDialog(parent)
    dlg.exec()
    try:
        (cfg.vault_path / ".tour_done").write_text("1", encoding="utf-8")
    except Exception:
        pass
