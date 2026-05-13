"""qtawesome 图标 helper（Phase 10 T2）。

集中管理图标：
- 用 Font Awesome 6 / Material Symbols Outlined / Lucide / Phosphor 等矢量图标
- 颜色跟随主题（默认 INK_900 = #0a0a0a）
- 提供 `icon(name)` 简写，name 用语义化 key 而非 raw prefix

为什么用矢量图标而非 emoji：
- emoji 在不同字体下渲染差异巨大（颜色 emoji vs 黑白线条）
- PyInstaller 打包后可能丢字体
- 矢量图标统一线条粗细，符合 ElevenLabs 极简风
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtGui import QIcon


# 语义 key → qtawesome 完整名称。
# 命名约定：用 fa6s（Font Awesome 6 solid）/ fa6r（regular）/ mdi6（Material Design Icons 6）
# 用 ph（Phosphor）/ msc（VSCode codicon）。
ICON_MAP = {
    # Sidebar
    "home": "fa6s.house",
    "library": "fa6s.book",
    "public_library": "fa6s.layer-group",
    "pm_mode": "fa6s.compass",
    "help": "fa6s.circle-question",
    "settings": "fa6s.gear",

    # Library tabs
    "templates": "fa6s.bullseye",
    "raw_material": "fa6s.box",
    "inbox": "fa6s.inbox",

    # Library actions
    "scan_history": "fa6s.clock-rotate-left",
    "import_file": "fa6s.file-arrow-up",
    "import_external": "fa6s.cloud-arrow-down",
    "new_item": "fa6s.plus",
    "edit": "fa6s.pen",
    "delete": "fa6s.trash",
    "copy": "fa6s.copy",
    "generalize": "fa6s.wand-magic-sparkles",
    "translate": "fa6s.language",
    "info": "fa6s.circle-info",
    "refresh": "fa6s.arrows-rotate",
    "search": "fa6s.magnifying-glass",
    "sort": "fa6s.arrow-down-wide-short",
    "trending": "fa6s.fire",

    # Status / Actions
    "check": "fa6s.check",
    "x": "fa6s.xmark",
    "play": "fa6s.play",
    "send": "fa6s.paper-plane",
    "warning": "fa6s.triangle-exclamation",
    "success": "fa6s.circle-check",
    "lightning": "fa6s.bolt",
    "star": "fa6s.star",

    # PM mode
    "chat": "fa6s.comments",
    "brief": "fa6s.file-lines",

    # External
    "github": "fa6b.github",
    "link": "fa6s.link",
    "url": "fa6s.globe",
    "file": "fa6s.file",

    # Project / pinning
    "pin": "fa6s.thumbtack",
    "folder": "fa6s.folder",

    # Help page sections (P21：替换 emoji)
    "bulb": "fa6s.lightbulb",
    "books": "fa6s.book-open",
    "rotate": "fa6s.rotate",
    "compass": "fa6s.compass",
    "robot": "fa6s.robot",
    "lock": "fa6s.lock",

    # P22：空心圆——用于 onboarding 清单未完成状态（替换大黑方块 QCheckBox）
    "circle_empty": "fa6r.circle",

    # P22：项目优化（新主导航页）—— "扳手+魔法棒" 表达"针对项目调优"
    "project_optimize": "fa6s.screwdriver-wrench",

    # P22 二轮：历史记录 / 后端连通性测试
    "history": "fa6s.clock-rotate-left",
    "play_test": "fa6s.flask",
}


def icon(name: str, color: Optional[str] = None, size: int = 16) -> QIcon:
    """按语义 key 取图标。color 默认随主题（None=黑色 #0a0a0a）。

    P21：包 try/except 把 qtawesome 失败写到 ~/.prompt-help/logs/icons.log，
    .exe 里图标显示空白时可查这个日志确认根因。
    """
    qa_name = ICON_MAP.get(name) or "fa6s.question"
    try:
        import qtawesome as qta
        return qta.icon(qa_name, color=color or "#0a0a0a")
    except Exception as e:
        _log_icon_failure(name, qa_name, e)
        return QIcon()  # 返回空图标，调用方按钮只剩文字，不崩溃


def _log_icon_failure(name: str, qa_name: str, err: Exception) -> None:
    """诊断 qtawesome 在 .exe 里加载失败。永不抛异常。"""
    try:
        from pathlib import Path
        log_dir = Path.home() / ".prompt-help" / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_file = log_dir / "icons.log"
        import datetime as _dt
        with log_file.open("a", encoding="utf-8") as f:
            f.write(
                f"{_dt.datetime.now().isoformat()} icon({name!r}) qa={qa_name!r} "
                f"failed: {type(err).__name__}: {err}\n"
            )
    except Exception:
        pass


def icon_white(name: str) -> QIcon:
    """白色图标，配深色背景按钮（如主按钮选中态、暗色 sidebar）。"""
    return icon(name, color="#ffffff")


def icon_muted(name: str) -> QIcon:
    """灰色图标，配次按钮。"""
    return icon(name, color="#737373")
