"""Spotlight 引导特效（参考 MinPEI CMS 自研实现，移植到 PySide6）。

设计：
- 全屏半透明蒙层 QWidget，paintEvent 用 CompositionMode_Source 挖透明矩形高亮目标
- 浮动卡片 QFrame 无边框 + 阴影，按 placement 计算位置并避碰
- 进度条 + 上一步/下一步/跳过/完成
- 用 QSettings 存完成状态（key: prompt_help/tour/<tour_id>_v1）
- 监听主窗口 resize/show 自动重算位置
- 步骤通过 widget 对象名（setObjectName）定位

用法：
    from prompt_help.gui.onboarding.spotlight_tour import SpotlightTour, TourStep
    tour = SpotlightTour(main_window, "global_v1", [TourStep(...), ...])
    tour.maybe_start()  # 首次自动启动
    tour.start_forced()  # 手动重启（无视 done 状态）
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional

from PySide6.QtCore import QEvent, QObject, QPoint, QRect, QRectF, QSettings, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QProgressBar, QPushButton, QVBoxLayout, QWidget,
)


SPOTLIGHT_PADDING = 10
CARD_GAP = 18
CARD_WIDTH = 380


@dataclass
class TourStep:
    """一步引导。

    - target_object_name：要高亮的 widget setObjectName 值；空字符串则居中无 spotlight
    - title / desc：卡片内容
    - placement：right / left / top / bottom / center
    - on_step_enter：可选回调（如切到对应主页）
    """

    id: str
    title: str
    desc: str
    target_object_name: str = ""
    placement: str = "right"
    on_step_enter: Optional[Callable[[], None]] = field(default=None, repr=False)


class SpotlightOverlay(QWidget):
    """全屏半透明蒙层，paintEvent 挖一个透明矩形高亮目标。"""

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setAutoFillBackground(False)
        self._hole: Optional[QRect] = None
        self.setMouseTracking(False)

    def set_hole(self, hole: Optional[QRect]) -> None:
        self._hole = hole
        self.update()

    def resize_to_parent(self) -> None:
        if self.parent():
            self.setGeometry(self.parent().rect())

    def paintEvent(self, _ev) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        overlay_color = QColor(13, 22, 28, 165)

        if not self._hole or not self._hole.isValid():
            painter.fillRect(self.rect(), overlay_color)
            return

        # 几何减法：整个 rect 减去 hole，对剩下区域填半透明黑。
        # 不用 CompositionMode_Clear（普通 QWidget 不是 alpha buffer，挖不透）。
        full = QPainterPath()
        full.addRect(QRectF(self.rect()))
        hole_path = QPainterPath()
        hole_path.addRoundedRect(QRectF(self._hole), 6, 6)
        donut = full.subtracted(hole_path)
        painter.fillPath(donut, overlay_color)

        # 高亮边框（金色）+ 4 角延伸短线，方便定位
        pen = QPen(QColor("#c7a35a"))
        pen.setWidth(2)
        painter.setPen(pen)
        painter.drawRoundedRect(self._hole, 6, 6)


class TourCard(QFrame):
    """悬浮的步骤卡片（白底 + 阴影 + 进度条 + 控件）。"""

    skip_clicked = Signal()
    prev_clicked = Signal()
    next_clicked = Signal()
    finish_clicked = Signal()
    close_clicked = Signal()

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.setFixedWidth(CARD_WIDTH)
        self.setStyleSheet(
            "TourCard { background: white; border-radius: 12px; "
            "border: 1px solid #ececec; }"
        )
        # 阴影
        from PySide6.QtWidgets import QGraphicsDropShadowEffect
        eff = QGraphicsDropShadowEffect(self)
        eff.setBlurRadius(28)
        eff.setOffset(0, 8)
        eff.setColor(QColor(0, 0, 0, 120))
        self.setGraphicsEffect(eff)

        v = QVBoxLayout(self)
        v.setContentsMargins(20, 16, 20, 16)
        v.setSpacing(10)

        # 顶部：步骤计数 + 关闭
        top = QHBoxLayout()
        self.lbl_step = QLabel("1 / 5")
        self.lbl_step.setStyleSheet("color: #737373; font-size: 11px;")
        top.addWidget(self.lbl_step)
        top.addStretch(1)
        self.btn_close = QPushButton("✕")
        self.btn_close.setFixedSize(22, 22)
        self.btn_close.setStyleSheet(
            "QPushButton { background: transparent; border: 0; color: #737373; font-size: 14px; }"
            "QPushButton:hover { color: #0a0a0a; }"
        )
        self.btn_close.clicked.connect(self.close_clicked)
        top.addWidget(self.btn_close)
        v.addLayout(top)

        # 标题 + 描述
        self.lbl_title = QLabel("")
        ft = QFont()
        ft.setPointSize(14)
        ft.setWeight(QFont.Weight.Bold)
        self.lbl_title.setFont(ft)
        self.lbl_title.setStyleSheet("color: #0a0a0a;")
        self.lbl_title.setWordWrap(True)
        v.addWidget(self.lbl_title)

        self.lbl_desc = QLabel("")
        self.lbl_desc.setStyleSheet("color: #525252; font-size: 13px; line-height: 1.55;")
        self.lbl_desc.setWordWrap(True)
        v.addWidget(self.lbl_desc)

        # 进度条
        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(4)
        self.progress.setStyleSheet(
            "QProgressBar { background: #f5f5f5; border: 0; border-radius: 2px; }"
            "QProgressBar::chunk { background: #c7a35a; border-radius: 2px; }"
        )
        v.addWidget(self.progress)

        # 底部按钮
        bot = QHBoxLayout()
        self.btn_skip = QPushButton("跳过")
        self.btn_skip.setStyleSheet(
            "QPushButton { background: transparent; color: #737373; "
            "border: 0; font-size: 12px; padding: 4px 8px; }"
            "QPushButton:hover { color: #0a0a0a; }"
        )
        self.btn_skip.clicked.connect(self.skip_clicked)
        bot.addWidget(self.btn_skip)
        bot.addStretch(1)

        self.btn_prev = QPushButton("← 上一步")
        self.btn_prev.setStyleSheet(
            "QPushButton { background: #fafafa; color: #0a0a0a; border: 1px solid #ececec;"
            "border-radius: 6px; padding: 6px 12px; font-size: 12px; }"
            "QPushButton:hover { background: #f0f0f0; }"
        )
        self.btn_prev.clicked.connect(self.prev_clicked)
        bot.addWidget(self.btn_prev)

        self.btn_next = QPushButton("下一步 →")
        self.btn_next.setStyleSheet(
            "QPushButton { background: #0a0a0a; color: white; border: 0;"
            "border-radius: 6px; padding: 6px 14px; font-size: 12px; font-weight: 600; }"
            "QPushButton:hover { background: #262626; }"
        )
        self.btn_next.clicked.connect(self.next_clicked)
        bot.addWidget(self.btn_next)

        self.btn_finish = QPushButton("完成 ✓")
        self.btn_finish.setStyleSheet(self.btn_next.styleSheet())
        self.btn_finish.clicked.connect(self.finish_clicked)
        bot.addWidget(self.btn_finish)

        v.addLayout(bot)

    def set_step(self, idx: int, total: int, step: TourStep) -> None:
        self.lbl_step.setText(f"{idx + 1} / {total}")
        self.lbl_title.setText(step.title)
        self.lbl_desc.setText(step.desc)
        self.progress.setMaximum(total)
        self.progress.setValue(idx + 1)
        is_last = idx == total - 1
        is_first = idx == 0
        self.btn_prev.setVisible(not is_first)
        self.btn_next.setVisible(not is_last)
        self.btn_finish.setVisible(is_last)


class SpotlightTour(QObject):
    """引导管理器。绑定到主窗口，跑完写 QSettings。"""

    finished = Signal(str)

    def __init__(self, host: QWidget, tour_id: str, steps: list[TourStep]):
        super().__init__(host)
        self.host = host
        self.tour_id = tour_id
        self.steps = steps
        self.idx = 0
        self.active = False

        self.overlay = SpotlightOverlay(host)
        self.overlay.hide()
        self.card = TourCard(host)
        self.card.hide()

        self.card.skip_clicked.connect(self._on_skip)
        self.card.close_clicked.connect(self._on_skip)
        self.card.prev_clicked.connect(self._on_prev)
        self.card.next_clicked.connect(self._on_next)
        self.card.finish_clicked.connect(self._on_finish)

        # 监听主窗口 resize / 切页
        host.installEventFilter(self)
        # 定期重算（widget 内部布局变动时及时跟随）
        self._timer = QTimer(self)
        self._timer.setInterval(150)
        self._timer.timeout.connect(self._refresh_geometry)

    # ------------------------------------------------------------------
    # 启动 / 完成
    # ------------------------------------------------------------------

    def _settings_key(self) -> str:
        return f"prompt_help/tour/{self.tour_id}_v1"

    def is_done(self) -> bool:
        s = QSettings("PromptHelp", "PromptHelp")
        return bool(s.value(self._settings_key(), False, type=bool))

    def _mark_done(self) -> None:
        s = QSettings("PromptHelp", "PromptHelp")
        s.setValue(self._settings_key(), True)

    def maybe_start(self) -> None:
        """首次启动：未完成则启动一次。"""
        if not self.is_done():
            self.start_forced()

    def start_forced(self) -> None:
        """无视已完成状态，强制启动。"""
        if not self.steps:
            return
        self.idx = 0
        self.active = True
        self.overlay.resize_to_parent()
        self.overlay.raise_()
        self.overlay.show()
        self.card.raise_()
        self.card.show()
        self._timer.start()
        self._apply_step()

    def stop(self) -> None:
        self.active = False
        self._timer.stop()
        self.overlay.hide()
        self.card.hide()

    # ------------------------------------------------------------------
    # 事件
    # ------------------------------------------------------------------

    def eventFilter(self, obj: QObject, ev: QEvent) -> bool:
        if obj is self.host and ev.type() in (QEvent.Type.Resize, QEvent.Type.Move, QEvent.Type.Show):
            if self.active:
                self.overlay.resize_to_parent()
                self._refresh_geometry()
        return False

    def _on_skip(self) -> None:
        self._mark_done()
        self.stop()
        self.finished.emit("skipped")

    def _on_prev(self) -> None:
        if self.idx > 0:
            self.idx -= 1
            self._apply_step()

    def _on_next(self) -> None:
        if self.idx < len(self.steps) - 1:
            self.idx += 1
            self._apply_step()

    def _on_finish(self) -> None:
        self._mark_done()
        self.stop()
        self.finished.emit("completed")

    # ------------------------------------------------------------------
    # 渲染
    # ------------------------------------------------------------------

    def _apply_step(self) -> None:
        if not self.active or not self.steps:
            return
        step = self.steps[self.idx]
        if step.on_step_enter:
            try:
                step.on_step_enter()
            except Exception:
                pass
        # 给主窗口时间完成切页 / 布局
        QTimer.singleShot(60, self._refresh_geometry)
        self.card.set_step(self.idx, len(self.steps), step)

    def _find_target(self, object_name: str) -> Optional[QWidget]:
        if not object_name:
            return None
        return self.host.findChild(QWidget, object_name)

    def _refresh_geometry(self) -> None:
        if not self.active or not self.steps:
            return
        step = self.steps[self.idx]
        target = self._find_target(step.target_object_name)
        if target is None or not target.isVisible() or step.placement == "center":
            # 居中：无 spotlight，卡片屏幕中央
            self.overlay.set_hole(None)
            self._place_card_center()
            return
        rect_in_target = target.rect()
        top_left = target.mapTo(self.host, QPoint(0, 0))
        hole = QRect(top_left, rect_in_target.size()).adjusted(
            -SPOTLIGHT_PADDING, -SPOTLIGHT_PADDING, SPOTLIGHT_PADDING, SPOTLIGHT_PADDING,
        )
        self.overlay.set_hole(hole)
        self._place_card_near(hole, step.placement)

    def _place_card_near(self, hole: QRect, placement: str) -> None:
        """根据 placement 把卡片摆在 hole 旁边并避免出界。"""
        self.card.adjustSize()
        cw, ch = self.card.width(), self.card.height()
        host_rect = self.host.rect()

        if placement == "right":
            x = hole.right() + CARD_GAP
            y = hole.top() + (hole.height() - ch) // 2
        elif placement == "left":
            x = hole.left() - cw - CARD_GAP
            y = hole.top() + (hole.height() - ch) // 2
        elif placement == "top":
            x = hole.left() + (hole.width() - cw) // 2
            y = hole.top() - ch - CARD_GAP
        elif placement == "bottom":
            x = hole.left() + (hole.width() - cw) // 2
            y = hole.bottom() + CARD_GAP
        else:
            self._place_card_center()
            return

        # 避免出界
        x = max(16, min(x, host_rect.width() - cw - 16))
        y = max(16, min(y, host_rect.height() - ch - 16))
        self.card.move(x, y)
        self.card.raise_()

    def _place_card_center(self) -> None:
        self.card.adjustSize()
        cw, ch = self.card.width(), self.card.height()
        host_rect = self.host.rect()
        self.card.move((host_rect.width() - cw) // 2, (host_rect.height() - ch) // 2)
        self.card.raise_()
