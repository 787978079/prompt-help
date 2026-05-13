"""ElevenLabs 风格主题：白底 / 深字 / 单一墨色调 / 大留白 / 圆角（Phase 10 回归）。

设计原则（参考 elevenlabs.io / Vercel / Linear）：
- 不用彩色装饰，主色就是近黑 #0a0a0a
- 卡片靠 1px 边框区分，不靠阴影
- 字号偏大（13-14px 起），行高 1.55+
- 圆角 8-10px（不是 Material 的 20px pill）
- hover 用极淡灰 #f5f5f5
- 留白多，padding 在 20-32px 之间
"""

from __future__ import annotations

from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication


# ElevenLabs 风格色彩 token（单一墨色调）
INK_900 = "#0a0a0a"          # 主文字、主按钮、选中态
INK_700 = "#262626"          # 主按钮 hover
INK_500 = "#525252"          # 副文字
INK_400 = "#737373"          # 弱文字
INK_300 = "#a3a3a3"          # 占位、辅助
INK_200 = "#d4d4d4"          # 边框 hover、滚动条
INK_100 = "#e5e5e5"          # 主边框
INK_50 = "#ececec"           # 分隔线、深一档边框
SURFACE = "#ffffff"
SURFACE_ALT = "#fafafa"      # 侧边栏、悬浮卡背景
SURFACE_HOVER = "#f5f5f5"
DANGER = "#b91c1c"
DANGER_BG = "#fef2f2"
DANGER_BORDER = "#fecaca"
SUCCESS = "#16a34a"


_QSS = f"""
* {{
    font-family: "Inter", "Geist", "Microsoft YaHei UI",
                 "PingFang SC", "Noto Sans CJK SC", "Segoe UI", sans-serif;
}}

QMainWindow, QDialog, QWizard {{
    background-color: {SURFACE};
}}

/* P20：关掉所有 QFrame 默认 box 边框，避免与 setStyleSheet 的 border 叠加成丑陋的细黑框 */
QFrame {{
    border: 0;
}}

QLabel {{
    color: {INK_900};
}}

/* ----- 侧边栏 ----- */
#sidebar {{
    background-color: {SURFACE_ALT};
    border-right: 1px solid {INK_50};
    min-width: 220px;
    max-width: 240px;
}}
#sidebar QPushButton {{
    text-align: left;
    padding: 11px 18px;
    border: none;
    border-radius: 10px;
    margin: 2px 12px;
    font-size: 14px;
    color: {INK_500};
    font-weight: 500;
}}
#sidebar QPushButton:hover {{
    background-color: {SURFACE_HOVER};
    color: {INK_900};
}}
#sidebar QPushButton:checked {{
    background-color: {INK_900};
    color: {SURFACE};
    font-weight: 600;
}}
#sidebar #logo {{
    font-size: 21px;
    font-weight: 700;
    color: {INK_900};
    padding: 26px 22px 4px 22px;
    letter-spacing: -0.5px;
}}
#sidebar #subtitle {{
    font-size: 12px;
    color: {INK_400};
    padding: 0 22px 22px 22px;
}}

/* ----- 内容区 ----- */
#content {{
    background-color: {SURFACE};
    padding: 28px 40px;
}}

#pageTitle {{
    font-size: 28px;
    font-weight: 700;
    color: {INK_900};
    letter-spacing: -0.5px;
    padding-bottom: 6px;
}}
#pageHint {{
    font-size: 14px;
    color: {INK_400};
    padding-bottom: 20px;
    line-height: 1.55;
}}

/* ----- 按钮 ----- */
QPushButton[class="primary"] {{
    background-color: {INK_900};
    color: {SURFACE};
    border: none;
    border-radius: 8px;
    padding: 9px 18px;
    font-weight: 600;
    font-size: 13px;
}}
QPushButton[class="primary"]:hover {{ background-color: {INK_700}; }}
QPushButton[class="primary"]:disabled {{ background-color: {INK_200}; color: {SURFACE}; }}

QPushButton[class="subtle"] {{
    background-color: {SURFACE};
    color: {INK_900};
    border: 1px solid {INK_100};
    border-radius: 8px;
    padding: 8px 16px;
    font-size: 13px;
    font-weight: 500;
}}
QPushButton[class="subtle"]:hover {{
    background-color: {SURFACE_ALT};
    border-color: {INK_200};
}}
QPushButton[class="subtle"]:disabled {{
    color: {INK_300};
    border-color: {INK_50};
}}

QPushButton[class="danger"] {{
    background-color: {SURFACE};
    color: {DANGER};
    border: 1px solid {DANGER_BORDER};
    border-radius: 8px;
    padding: 8px 16px;
    font-size: 13px;
    font-weight: 500;
}}
QPushButton[class="danger"]:hover {{ background-color: {DANGER_BG}; }}

QWizard QPushButton, QDialogButtonBox QPushButton {{
    background-color: {INK_900};
    color: {SURFACE};
    border: none;
    border-radius: 8px;
    padding: 8px 18px;
    font-weight: 600;
    font-size: 13px;
}}
QWizard QPushButton:hover, QDialogButtonBox QPushButton:hover {{
    background-color: {INK_700};
}}
QWizard QPushButton:disabled, QDialogButtonBox QPushButton:disabled {{
    background-color: {INK_100};
    color: {INK_300};
}}

/* ----- 输入框 ----- */
/* P21：去 1px 边框，改浅灰背景代替。
   focus 时换成稍深的灰，给视觉反馈但不出现"线框"。 */
QLineEdit, QPlainTextEdit, QTextEdit, QComboBox, QSpinBox {{
    border: 0;
    border-radius: 8px;
    padding: 9px 12px;
    background-color: {SURFACE_ALT};
    selection-background-color: {INK_900};
    selection-color: {SURFACE};
    font-size: 13px;
    color: {INK_900};
}}
QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus,
QComboBox:focus, QSpinBox:focus {{
    background-color: #f0f0f0;
}}
QLineEdit::placeholder {{ color: {INK_300}; }}

/* P21：只读 input/edit 控件本质是展示文字，不要边框，按背景融入页面。
   Qt selector 用属性匹配 readOnly 状态。 */
QLineEdit[readOnly="true"], QPlainTextEdit[readOnly="true"], QTextEdit[readOnly="true"] {{
    border: 0;
    background: transparent;
    padding: 4px 0;
}}
QLineEdit[readOnly="true"]:focus, QPlainTextEdit[readOnly="true"]:focus, QTextEdit[readOnly="true"]:focus {{
    border: 0;
}}

QComboBox::drop-down {{ border: none; width: 24px; }}
QComboBox::down-arrow {{ image: none; }}

/* ----- 表格 ----- */
/* P21：去 1px 边框 + radius，与外层卡片融合（嵌套套娃感消失） */
QTableWidget, QTableView {{
    border: 0;
    background-color: {SURFACE};
    gridline-color: transparent;
    selection-background-color: {SURFACE_ALT};
    selection-color: {INK_900};
    outline: 0;
}}
QTableWidget::item, QTableView::item {{
    padding: 11px 14px;
    border-bottom: 1px solid {SURFACE_HOVER};
}}
QTableWidget::item:selected, QTableView::item:selected {{
    background-color: {SURFACE_ALT};
    color: {INK_900};
}}
QHeaderView::section {{
    background-color: {SURFACE};
    border: none;
    border-bottom: 1px solid {INK_50};
    padding: 10px 14px;
    font-weight: 600;
    font-size: 11px;
    color: {INK_400};
    text-transform: uppercase;
    letter-spacing: 0.6px;
}}

/* ----- 状态栏 ----- */
QStatusBar {{
    background-color: {SURFACE_ALT};
    color: {INK_400};
    border-top: 1px solid {INK_50};
    padding: 4px 14px;
}}

/* ----- 滚动条（极简） ----- */
QScrollBar:vertical {{
    background: transparent;
    width: 10px;
    margin: 0;
}}
QScrollBar::handle:vertical {{
    background: {INK_200};
    border-radius: 5px;
    min-height: 28px;
}}
QScrollBar::handle:vertical:hover {{ background: {INK_300}; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; }}
QScrollBar:horizontal {{
    background: transparent;
    height: 10px;
}}
QScrollBar::handle:horizontal {{
    background: {INK_200};
    border-radius: 5px;
    min-width: 28px;
}}

/* ----- 复选框 / 单选 ----- */
QCheckBox {{ color: {INK_900}; font-size: 13px; spacing: 8px; }}
QCheckBox::indicator {{
    width: 16px; height: 16px;
    border: 1px solid {INK_200};
    border-radius: 4px;
    background: {SURFACE};
}}
QCheckBox::indicator:checked {{
    background: {INK_900};
    border-color: {INK_900};
}}

QRadioButton {{ color: {INK_900}; font-size: 13px; spacing: 8px; }}
QRadioButton::indicator {{
    width: 16px; height: 16px;
    border: 1px solid {INK_200};
    border-radius: 8px;
    background: {SURFACE};
}}
QRadioButton::indicator:checked {{
    background: {SURFACE};
    border: 5px solid {INK_900};
}}

/* ----- 分组框 ----- */
/* P21：去 1px 边框，靠 margin + 标题区分 */
QGroupBox {{
    font-size: 13px;
    font-weight: 600;
    color: {INK_900};
    border: 0;
    border-radius: 10px;
    margin-top: 16px;
    padding: 18px 0 0 0;
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    left: 12px;
    padding: 0 6px;
    background-color: {SURFACE};
}}

/* ----- 文本浏览器 ----- */
QTextBrowser {{
    border: 0;
    border-radius: 10px;
    padding: 16px;
    background: {SURFACE_ALT};
    font-size: 13px;
    color: {INK_900};
}}

/* ----- Tabs ----- */
QTabWidget::pane {{
    border: 1px solid {INK_50};
    border-radius: 10px;
    top: -1px;
}}
QTabBar::tab {{
    background: transparent;
    border: none;
    padding: 10px 18px;
    color: {INK_400};
    font-size: 13px;
    font-weight: 500;
}}
QTabBar::tab:hover {{ color: {INK_900}; }}
QTabBar::tab:selected {{
    color: {INK_900};
    border-bottom: 2px solid {INK_900};
}}

/* ----- QSplitter ----- */
QSplitter::handle {{ background: transparent; }}
QSplitter::handle:horizontal {{ width: 1px; background: {INK_50}; }}
QSplitter::handle:vertical {{ height: 1px; background: {INK_50}; }}

/* ----- Tooltip ----- */
QToolTip {{
    background-color: {INK_900};
    color: {SURFACE};
    border: none;
    border-radius: 6px;
    padding: 6px 10px;
    font-size: 12px;
}}

/* ----- ProgressBar ----- */
QProgressBar {{
    background: {SURFACE_HOVER};
    border: 0;
    border-radius: 4px;
}}
QProgressBar::chunk {{
    background: {INK_900};
    border-radius: 4px;
}}
"""


def _ensure_checkbox_check_image() -> str:
    """P22：把白色 ✓ icon 渲染成 PNG，QSS 用 image: url() 引用。
    解决 QCheckBox::indicator:checked 默认黑底无 ✓ 的问题（看起来像大黑方块）。
    返回 PNG 绝对路径（Qt QSS url 需要 / 作为分隔符；Windows 反斜杠也接受但易出错）。
    """
    from pathlib import Path
    cache_dir = Path.home() / ".prompt-help" / "cache"
    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
        png = cache_dir / "check_white_v1.png"
        if not png.is_file():
            import qtawesome as qta
            qta.icon("fa6s.check", color="#ffffff").pixmap(14, 14).save(str(png), "PNG")
        return str(png).replace("\\", "/")
    except Exception:
        return ""


def apply(app: QApplication) -> None:
    """应用 ElevenLabs 风格配色 + QSS。"""
    pal = app.palette()
    pal.setColor(QPalette.ColorRole.Window, QColor(SURFACE))
    pal.setColor(QPalette.ColorRole.WindowText, QColor(INK_900))
    pal.setColor(QPalette.ColorRole.Base, QColor(SURFACE))
    pal.setColor(QPalette.ColorRole.AlternateBase, QColor(SURFACE_ALT))
    pal.setColor(QPalette.ColorRole.Text, QColor(INK_900))
    pal.setColor(QPalette.ColorRole.Button, QColor(SURFACE))
    pal.setColor(QPalette.ColorRole.ButtonText, QColor(INK_900))
    pal.setColor(QPalette.ColorRole.Highlight, QColor(INK_900))
    pal.setColor(QPalette.ColorRole.HighlightedText, QColor(SURFACE))
    pal.setColor(QPalette.ColorRole.Mid, QColor(INK_300))
    pal.setColor(QPalette.ColorRole.Link, QColor(INK_900))
    app.setPalette(pal)
    # 注入白色 ✓ image url 到 indicator:checked
    check_img_url = _ensure_checkbox_check_image()
    if check_img_url:
        check_qss = (
            f"QCheckBox::indicator:checked {{ image: url({check_img_url}); }}"
        )
        app.setStyleSheet(_QSS + "\n" + check_qss)
    else:
        app.setStyleSheet(_QSS)
