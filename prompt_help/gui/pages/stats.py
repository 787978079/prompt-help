"""完整统计 dashboard 页（Phase 15 T3）。

无外部图表库依赖，用 QFrame + QLabel + 简易自绘条形图。

模块：
1. 顶部 4 大数字卡（总数 / 通用模板 / 本周新增 / 待审）
2. 分类分布（条形图 / 横向）
3. Top 20 最常用（表）
4. 未使用 90 天以上（清理建议）
5. A/B 同源对比清单
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QPainter
from PySide6.QtWidgets import (
    QFrame, QGridLayout, QHBoxLayout, QLabel, QScrollArea, QSizePolicy,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from ...core import indexer
from ...core.config import Config


class StatsPage(QWidget):
    def __init__(self, cfg: Config):
        super().__init__()
        self.cfg = cfg
        self._build()
        self.refresh()

    def _build(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(40, 28, 40, 24)
        outer.setSpacing(14)

        title = QLabel("统计")
        title.setObjectName("pageTitle")
        outer.addWidget(title)
        hint = QLabel("库的健康度 + 通用模板覆盖度 + 待清理项。每次进页面会重新计算。")
        hint.setObjectName("pageHint")
        outer.addWidget(hint)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        host = QWidget()
        v = QVBoxLayout(host)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(16)
        scroll.setWidget(host)
        outer.addWidget(scroll, 1)

        # 1. 顶部数字卡 grid 2x2
        self.num_grid = QGridLayout()
        self.num_grid.setHorizontalSpacing(12)
        self.num_grid.setVerticalSpacing(12)
        self.num_cards: dict[str, QLabel] = {}
        for i, key in enumerate(["total", "templates", "week_new", "inbox"]):
            cell = _NumberCard(self._card_meta(key))
            self.num_grid.addWidget(cell, i // 2, i % 2)
            self.num_cards[key] = cell.value_label
        v.addLayout(self.num_grid)

        # 2. 分类分布
        cat_box = QFrame()
        cat_box.setFrameShape(QFrame.Shape.NoFrame)
        cat_box.setStyleSheet("QFrame { background: #fafafa; border: 0; border-radius: 12px; }")
        cv = QVBoxLayout(cat_box)
        cv.setContentsMargins(20, 14, 20, 14)
        cat_title = QLabel("分类分布")
        cat_title.setStyleSheet("font-size: 13px; font-weight: 600;")
        cv.addWidget(cat_title)
        self.cat_chart = _HorizontalBarChart()
        cv.addWidget(self.cat_chart)
        v.addWidget(cat_box)

        # 3. Top 20
        top_box = QFrame()
        top_box.setFrameShape(QFrame.Shape.NoFrame)
        top_box.setStyleSheet("QFrame { background: #fafafa; border: 0; border-radius: 12px; }")
        tv = QVBoxLayout(top_box)
        tv.setContentsMargins(20, 14, 20, 14)
        top_title = QLabel("Top 20 最常用")
        top_title.setStyleSheet("font-size: 13px; font-weight: 600;")
        tv.addWidget(top_title)
        self.top_table = QTableWidget(0, 4)
        self.top_table.setHorizontalHeaderLabels(["名称", "类型", "用过", "成功信号"])
        self.top_table.verticalHeader().setVisible(False)
        self.top_table.setEditTriggers(self.top_table.EditTrigger.NoEditTriggers)
        # P21：去掉 maxHeight 限制，让表格自然撑开（外层 QScrollArea 负责滚动），
        # 之前 maxHeight=360 让 Top 20 只显示 ~10 行被用户误认为"被折叠"
        # P21：去掉表格自带边框 + 白底，融合到外层 #fafafa box
        self.top_table.setStyleSheet(
            "QTableWidget { background: transparent; border: 0; }"
            "QHeaderView::section { background: transparent; }"
        )
        tv.addWidget(self.top_table)
        v.addWidget(top_box)

        # 4. 未使用 90 天以上
        stale_box = QFrame()
        stale_box.setFrameShape(QFrame.Shape.NoFrame)
        stale_box.setStyleSheet("QFrame { background: #fafafa; border: 0; border-radius: 12px; }")
        sv = QVBoxLayout(stale_box)
        sv.setContentsMargins(20, 14, 20, 14)
        stale_title = QLabel("超过 90 天没用过（建议归并 / 删除）")
        stale_title.setStyleSheet("font-size: 13px; font-weight: 600;")
        sv.addWidget(stale_title)
        self.stale_table = QTableWidget(0, 3)
        self.stale_table.setHorizontalHeaderLabels(["名称", "创建于", "用过"])
        self.stale_table.verticalHeader().setVisible(False)
        self.stale_table.setEditTriggers(self.stale_table.EditTrigger.NoEditTriggers)
        # P21：去掉 maxHeight 限制（原因同 top_table）
        # P21：同上，去边框 + 透明背景
        self.stale_table.setStyleSheet(
            "QTableWidget { background: transparent; border: 0; }"
            "QHeaderView::section { background: transparent; }"
        )
        sv.addWidget(self.stale_table)
        v.addWidget(stale_box)

        v.addStretch(1)

    def _card_meta(self, key: str) -> dict:
        return {
            "total": {"label": "总条数", "color": "#0a0a0a"},
            "templates": {"label": "通用模板", "color": "#0a0a0a"},
            "week_new": {"label": "本周新增", "color": "#16a34a"},
            "inbox": {"label": "待审", "color": "#f59e0b"},
        }[key]

    # ---------------------------------------------------------------

    _last_refresh_ts: float = 0.0

    def on_show(self) -> None:
        # P20：30 秒内不重复刷新，避免来回切 tab 都全表 scan
        import time
        now = time.time()
        if now - self._last_refresh_ts < 30:
            return
        self._last_refresh_ts = now
        self.refresh()

    def refresh(self) -> None:
        try:
            conn = indexer.open_db(self.cfg)
            counts = indexer.count_all(conn)
            tpl = indexer.count_templates(conn)
            cutoff = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
            week_new = conn.execute(
                "SELECT COUNT(*) AS c FROM prompts WHERE created >= ?", (cutoff,),
            ).fetchone()["c"]
            # Top 20
            top_rows = list(conn.execute(
                "SELECT title, scope, used, success_signal, is_template FROM prompts "
                "ORDER BY used*2 + success_signal*3 DESC LIMIT 20"
            ))
            # Stale 90 days
            stale_cutoff = (datetime.now(timezone.utc) - timedelta(days=90)).isoformat()
            stale_rows = list(conn.execute(
                "SELECT title, created, used FROM prompts "
                "WHERE used = 0 AND created < ? "
                "ORDER BY created ASC LIMIT 50",
                (stale_cutoff,),
            ))
            # 分类分布
            cat_rows = list(conn.execute(
                "SELECT categories_csv FROM prompts WHERE categories_csv != ''"
            ))
            conn.close()
        except Exception:
            counts = {"total": 0}
            tpl = {"templates": 0, "raw": 0, "total": 0}
            week_new = 0
            top_rows = []
            stale_rows = []
            cat_rows = []

        try:
            inbox_n = len(list(self.cfg.inbox_dir.glob("*.md")))
        except Exception:
            inbox_n = 0

        # 顶部 4 数字
        self.num_cards["total"].setText(str(tpl["total"]))
        ratio = (tpl["templates"] / tpl["total"] * 100) if tpl["total"] else 0
        self.num_cards["templates"].setText(f"{tpl['templates']}  ·  {ratio:.0f}%")
        self.num_cards["week_new"].setText(f"+{week_new}")
        self.num_cards["inbox"].setText(str(inbox_n))

        # 分类分布
        cat_count: dict[str, int] = {}
        for r in cat_rows:
            for cat in (r["categories_csv"] or "").split(","):
                cat = cat.strip()
                if cat:
                    cat_count[cat] = cat_count.get(cat, 0) + 1
        self.cat_chart.set_data(sorted(cat_count.items(), key=lambda x: x[1], reverse=True))

        # Top 20
        _SCOPE = {"global": "通用", "project": "项目", "trap": "踩坑"}
        self.top_table.setRowCount(0)
        for r in top_rows:
            i = self.top_table.rowCount()
            self.top_table.insertRow(i)
            title = r["title"] or "(未命名)"
            # P21：去 🎯 emoji，类型列已能区分通用/项目/踩坑
            self.top_table.setItem(i, 0, QTableWidgetItem(title))
            self.top_table.setItem(i, 1, QTableWidgetItem(_SCOPE.get(r["scope"], r["scope"])))
            self.top_table.setItem(i, 2, QTableWidgetItem(str(r["used"])))
            self.top_table.setItem(i, 3, QTableWidgetItem(str(r["success_signal"])))
        self.top_table.resizeColumnsToContents()
        self.top_table.horizontalHeader().setStretchLastSection(True)
        _autosize_table(self.top_table)

        # Stale
        self.stale_table.setRowCount(0)
        for r in stale_rows:
            i = self.stale_table.rowCount()
            self.stale_table.insertRow(i)
            self.stale_table.setItem(i, 0, QTableWidgetItem(r["title"] or "(未命名)"))
            self.stale_table.setItem(i, 1, QTableWidgetItem((r["created"] or "")[:10]))
            self.stale_table.setItem(i, 2, QTableWidgetItem(str(r["used"])))
        self.stale_table.resizeColumnsToContents()
        self.stale_table.horizontalHeader().setStretchLastSection(True)
        _autosize_table(self.stale_table)


def _autosize_table(t: QTableWidget) -> None:
    """P21：让表格高度等于实际行高之和，不留多余空白，也不内部滚动。
    外层 QScrollArea 负责整页滚动。"""
    t.resizeRowsToContents()
    h = t.horizontalHeader().height()
    for i in range(t.rowCount()):
        h += t.rowHeight(i)
    # 2px 给框架，最低 80px 占位（空表也有点高度）
    t.setFixedHeight(max(80, h + 2))


class _NumberCard(QFrame):
    """简单大数字卡片。"""

    def __init__(self, meta: dict):
        super().__init__()
        self.setFrameShape(QFrame.Shape.NoFrame)
        # P21：用浅灰背景代替 1px 边框，避免与全局 QFrame border 叠合成丑陋的细黑线
        self.setStyleSheet(
            "QFrame { background: #fafafa; border: 0; border-radius: 12px; }"
        )
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.setMinimumHeight(96)
        v = QVBoxLayout(self)
        v.setContentsMargins(22, 16, 22, 16)
        v.setSpacing(2)
        self.value_label = QLabel("—")
        big = QFont()
        big.setPointSize(22)
        big.setWeight(QFont.Weight.Bold)
        self.value_label.setFont(big)
        self.value_label.setStyleSheet(f"color: {meta['color']};")
        v.addWidget(self.value_label)
        lbl = QLabel(meta["label"])
        lbl.setStyleSheet("color: #737373; font-size: 12px;")
        v.addWidget(lbl)


class _HorizontalBarChart(QWidget):
    """简易横向条形图（无外部库）。"""

    def __init__(self):
        super().__init__()
        self.data: list[tuple[str, int]] = []
        self.setMinimumHeight(20)

    def set_data(self, data: list[tuple[str, int]]) -> None:
        self.data = data
        self.setMinimumHeight(max(20, len(data) * 26 + 8))
        self.update()

    def paintEvent(self, _ev) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        if not self.data:
            painter.setPen(QColor("#a3a3a3"))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "（没有分类数据）")
            return
        max_v = max(v for _, v in self.data) or 1
        label_w = 100
        bar_x = label_w + 8
        bar_w_total = max(80, self.width() - bar_x - 40)
        row_h = 22
        for i, (name, val) in enumerate(self.data):
            y = i * (row_h + 4) + 4
            painter.setPen(QColor("#525252"))
            f = painter.font(); f.setPointSize(10); painter.setFont(f)
            painter.drawText(0, y, label_w, row_h,
                             Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, name)
            bw = int(bar_w_total * (val / max_v))
            painter.fillRect(bar_x, y + 5, bw, row_h - 10, QColor("#0a0a0a"))
            painter.setPen(QColor("#0a0a0a"))
            painter.drawText(bar_x + bw + 6, y, 40, row_h,
                             Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, str(val))
