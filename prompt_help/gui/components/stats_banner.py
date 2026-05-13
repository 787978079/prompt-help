"""使用统计 banner（Phase 12 C2）。

一行横向小卡片：总数 / 通用模板比例 / 本周新增 / Top 命中。
不做大型 dashboard——首页底部一条信息密度高的 banner 就够。
"""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget,
)

from ...core import indexer
from ...core.config import Config


class StatsBanner(QFrame):
    def __init__(self, cfg: Config, parent: QWidget | None = None):
        super().__init__(parent)
        self.cfg = cfg
        self.setObjectName("statsBanner")
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setStyleSheet(
            "QFrame#statsBanner { background: #fafafa; border: 0;"
            "border-radius: 12px; }"
        )
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self._build()
        self.refresh()

    def _build(self) -> None:
        h = QHBoxLayout(self)
        h.setContentsMargins(20, 14, 20, 14)
        h.setSpacing(0)

        self.cells: list[tuple[QLabel, QLabel]] = []
        labels = ["总条数", "通用模板", "本周新增", "Top 用过", "Top 反馈"]
        for i, lbl in enumerate(labels):
            cell = QVBoxLayout()
            cell.setSpacing(2)
            big = QLabel("—")
            big.setStyleSheet("font-size: 18px; font-weight: 600; color: #0a0a0a;")
            small = QLabel(lbl)
            small.setStyleSheet("color: #737373; font-size: 11px;")
            cell.addWidget(big)
            cell.addWidget(small)
            container = QFrame()
            container.setFrameShape(QFrame.Shape.NoFrame)
            container.setLayout(cell)
            h.addWidget(container, 1)
            self.cells.append((big, small))
            if i < len(labels) - 1:
                sep = QFrame()
                sep.setFrameShape(QFrame.Shape.NoFrame)
                sep.setFixedWidth(1)
                sep.setStyleSheet("background: #ececec; border: 0;")
                h.addWidget(sep)

    _last_refresh_ts: float = 0.0

    def refresh(self) -> None:
        # P20：30 秒节流——切 tab 来回不重复全表 scan
        import time
        now = time.time()
        if now - self._last_refresh_ts < 30:
            return
        self._last_refresh_ts = now
        try:
            conn = indexer.open_db(self.cfg)
            tpl = indexer.count_templates(conn)
            # 本周新增
            from datetime import datetime, timedelta, timezone
            cutoff = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
            row = conn.execute(
                "SELECT COUNT(*) AS c FROM prompts WHERE created >= ?", (cutoff,),
            ).fetchone()
            week_new = int(row["c"] or 0) if row else 0
            # Top used
            top_used = conn.execute(
                "SELECT title, used FROM prompts ORDER BY used DESC LIMIT 1"
            ).fetchone()
            # Top success_signal
            top_signal = conn.execute(
                "SELECT title, success_signal FROM prompts ORDER BY success_signal DESC LIMIT 1"
            ).fetchone()
            conn.close()
        except Exception:
            tpl = {"templates": 0, "raw": 0, "total": 0}
            week_new = 0
            top_used = None
            top_signal = None

        # 写值
        self.cells[0][0].setText(str(tpl["total"]))
        ratio = (tpl["templates"] / tpl["total"] * 100) if tpl["total"] > 0 else 0
        self.cells[1][0].setText(f"{tpl['templates']}  ·  {ratio:.0f}%")
        self.cells[2][0].setText(f"+{week_new}")
        if top_used:
            t = top_used["title"][:14] + ("…" if len(top_used["title"]) > 14 else "")
            self.cells[3][0].setText(f"{top_used['used']}")
            self.cells[3][1].setText(f"Top 用过 · {t}")
        if top_signal:
            t = top_signal["title"][:14] + ("…" if len(top_signal["title"]) > 14 else "")
            self.cells[4][0].setText(str(top_signal["success_signal"]))
            self.cells[4][1].setText(f"Top 反馈 · {t}")
