"""跨项目自动召回卡片（Phase 12 B4）。

逻辑：
- 默认按当前 cwd 检测项目（fingerprint + 注册过的 projects 匹配）
- 用 indexer 拿该项目的 top 5 (按 used*2 + success*3 排序)
- 没匹配项目时显示"未识别当前项目"+ 项目选择下拉
- 点条目 → 复制到剪贴板 + bump used
"""

from __future__ import annotations

import time
from pathlib import Path

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import (
    QComboBox, QFrame, QHBoxLayout, QLabel, QPushButton, QSizePolicy,
    QVBoxLayout, QWidget,
)

from ...core import indexer
from ...core.config import Config
from ...core.fingerprint import fingerprint as fp_compute, stack_overlap, to_dict
from .. import icons as _icons


class ProjectRecallCard(QFrame):
    """首页"当前项目相关"卡片。"""

    open_prompt = Signal(str)  # prompt_id

    # P19：fingerprint 缓存（5 分钟）。重复进首页时不再扫项目目录
    _fp_cache: dict[str, tuple[float, object]] = {}

    def __init__(self, cfg: Config, parent: QWidget | None = None):
        super().__init__(parent)
        self.cfg = cfg
        self.setObjectName("projectRecallCard")
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setStyleSheet(
            "QFrame#projectRecallCard { background: #fafafa; border: 0;"
            "border-radius: 12px; }"
        )
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self._build()

    def _build(self) -> None:
        v = QVBoxLayout(self)
        v.setContentsMargins(20, 16, 20, 16)
        v.setSpacing(10)

        # 顶部：图标 + 标题 + 项目下拉
        head = QHBoxLayout()
        head.setSpacing(10)
        icon_lbl = QLabel()
        icon_lbl.setPixmap(_icons.icon("pm_mode").pixmap(QSize(18, 18)))
        head.addWidget(icon_lbl)

        self.title_lbl = QLabel("当前项目相关")
        self.title_lbl.setStyleSheet("font-size: 14px; font-weight: 600; color: #0a0a0a;")
        head.addWidget(self.title_lbl)
        head.addStretch(1)

        self.project_combo = QComboBox()
        self.project_combo.setMinimumWidth(180)
        self.project_combo.currentIndexChanged.connect(self._on_project_changed)
        head.addWidget(self.project_combo)

        # P16-T2：登记项目按钮——之前空项目时只有占位文字无操作入口
        # P21：去 📌 emoji，用 qtawesome 矢量图标
        self.btn_register = QPushButton(" 登记项目")
        self.btn_register.setProperty("class", "subtle")
        self.btn_register.setIcon(_icons.icon("pin"))
        self.btn_register.setIconSize(QSize(12, 12))
        self.btn_register.setStyleSheet("font-size: 11px; padding: 4px 10px;")
        self.btn_register.setToolTip(
            "选一个项目目录登记到 PH，之后 PH 会自动召回该项目相关的提示词"
        )
        self.btn_register.clicked.connect(self._on_register_project)
        head.addWidget(self.btn_register)

        # 用户反馈：识别到不属于自己的项目（如登记错的旧项目）需要删除入口
        self.btn_delete = QPushButton(" 删除项目")
        self.btn_delete.setProperty("class", "subtle")
        self.btn_delete.setIcon(_icons.icon("delete"))
        self.btn_delete.setIconSize(QSize(12, 12))
        self.btn_delete.setStyleSheet("font-size: 11px; padding: 4px 10px;")
        self.btn_delete.setToolTip("从已登记项目里删除当前选中的项目（不删该项目下的 prompts）")
        self.btn_delete.clicked.connect(self._on_delete_project)
        self.btn_delete.setVisible(False)  # 选中真实项目时才显示
        head.addWidget(self.btn_delete)

        v.addLayout(head)

        self.status = QLabel("")
        self.status.setStyleSheet("color: #737373; font-size: 11px;")
        v.addWidget(self.status)

        # 列表区
        self.list_layout = QVBoxLayout()
        self.list_layout.setSpacing(6)
        v.addLayout(self.list_layout)

    def refresh(self) -> None:
        """重算项目识别 + top 5 列表。"""
        conn = indexer.open_db(self.cfg)
        projects = indexer.list_projects(conn)
        conn.close()

        # P13 修：.exe 双击启动时 cwd 是 dist/，落不到任何项目内。
        # 回退到 vault 内 "last_active_project.txt" 记忆上次手动选择
        memory_path = self.cfg.vault_path / "last_active_project.txt"
        remembered: str = ""
        try:
            if memory_path.is_file():
                remembered = memory_path.read_text(encoding="utf-8").strip()
        except Exception:
            remembered = ""

        self.project_combo.blockSignals(True)
        self.project_combo.clear()
        cwd_match: str = ""
        try:
            cwd_str = str(Path.cwd().resolve()).replace("\\", "/").lower()
        except Exception:
            cwd_str = ""
        for p in projects:
            cwd_p = (p["cwd_path"] or "").replace("\\", "/").lower()
            label = f"{p['name']}"
            self.project_combo.addItem(label, p["name"])
            if cwd_str and cwd_p and (cwd_str == cwd_p or cwd_str.startswith(cwd_p + "/")):
                cwd_match = p["name"]
        # 不在任何登记项目下时——加"自动按指纹匹配"占位
        if not projects:
            self.project_combo.addItem("（还没登记项目）", "")
        else:
            self.project_combo.insertItem(0, "🔍 按当前 cwd 指纹匹配", "__auto__")
        self.project_combo.blockSignals(False)

        if cwd_match:
            idx = self.project_combo.findData(cwd_match)
            if idx >= 0:
                self.project_combo.setCurrentIndex(idx)
        elif remembered:
            # P13：cwd 没匹配上时 fallback 到上次手动选的项目
            idx = self.project_combo.findData(remembered)
            if idx >= 0:
                self.project_combo.setCurrentIndex(idx)
            else:
                self.project_combo.setCurrentIndex(0)
        else:
            self.project_combo.setCurrentIndex(0)

        # 删除按钮可见性跟当前选中
        cur = self._selected_project()
        self.btn_delete.setVisible(bool(cur) and cur != "__auto__")

        self._render_list()

    def _on_register_project(self) -> None:
        """P16-T2：选一个目录登记为项目。"""
        from PySide6.QtWidgets import QFileDialog, QInputDialog, QMessageBox
        # 1. 选目录
        d = QFileDialog.getExistingDirectory(
            self, "选项目根目录（含 .git / package.json / pyproject.toml 等）",
            str(Path.cwd()),
        )
        if not d:
            return
        proj_root = Path(d).resolve()
        # 2. 项目名默认 = 目录名
        default_name = proj_root.name
        name, ok = QInputDialog.getText(
            self, "登记项目",
            "项目名（短标识，建议小写字母数字）：",
            text=default_name,
        )
        if not ok or not name.strip():
            return
        name = name.strip()
        # 3. fingerprint + register
        try:
            import json
            from ...core.fingerprint import fingerprint as fp_compute, to_dict
            fp = fp_compute(proj_root)
            fp.project_name = name
            conn = indexer.open_db(self.cfg)
            indexer.register_project(
                conn, name=name, cwd_path=str(proj_root),
                fingerprint_json=json.dumps(to_dict(fp), ensure_ascii=False),
            )
            conn.close()
        except Exception as e:
            QMessageBox.warning(self, "登记失败", f"{type(e).__name__}: {e}")
            return
        # 4. 同时把这次的选择写到 last_active_project.txt
        try:
            (self.cfg.vault_path / "last_active_project.txt").write_text(
                name, encoding="utf-8",
            )
        except Exception:
            pass
        QMessageBox.information(
            self, "登记完成",
            f"已登记项目「{name}」（路径 {proj_root}）。\n\n"
            f"识别到的栈：langs={sorted(fp.langs)[:5] or '—'}  "
            f"frameworks={sorted(fp.frameworks)[:5] or '—'}",
        )
        self.refresh()

    def _on_project_changed(self, _idx: int) -> None:
        # P13：记忆用户的手动选择，下次启动用作 fallback
        target = self._selected_project()
        if target and target != "__auto__":
            try:
                (self.cfg.vault_path / "last_active_project.txt").write_text(
                    target, encoding="utf-8",
                )
            except Exception:
                pass
        # 删除按钮：仅当选中真实项目（非"按指纹"、非占位空选项）时显示
        self.btn_delete.setVisible(bool(target) and target != "__auto__")
        self._render_list()

    def _on_delete_project(self) -> None:
        """从 projects 表删除当前选中的项目。Prompts 数据不动。"""
        from PySide6.QtWidgets import QMessageBox
        target = self._selected_project()
        if not target or target == "__auto__":
            return
        ans = QMessageBox.warning(
            self,
            "确认删除项目登记",
            f"将从 PH 的已登记项目里删除「{target}」。\n\n"
            "这条操作只清掉项目注册（cwd 路径 + 栈指纹），\n"
            "**不删除**该项目下已经入库的提示词内容。\n\n"
            "如需删除提示词本身，去「我的库」按条删除。\n\n"
            "确定删除登记吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if ans != QMessageBox.StandardButton.Yes:
            return
        try:
            conn = indexer.open_db(self.cfg)
            n = indexer.delete_project(conn, target)
            conn.close()
        except Exception as e:
            QMessageBox.warning(self, "删除失败", f"{type(e).__name__}: {e}")
            return
        # 如果当前 last_active 指向被删的，清掉
        try:
            memory_path = self.cfg.vault_path / "last_active_project.txt"
            if memory_path.is_file() and memory_path.read_text(encoding="utf-8").strip() == target:
                memory_path.unlink(missing_ok=True)
        except Exception:
            pass
        QMessageBox.information(
            self, "已删除",
            f"已从 PH 删除项目登记「{target}」（影响 {n} 条登记记录）。",
        )
        self.refresh()

    def _selected_project(self) -> str:
        return self.project_combo.currentData() or ""

    def _clear_list(self) -> None:
        while self.list_layout.count():
            it = self.list_layout.takeAt(0)
            if it.widget():
                it.widget().deleteLater()

    def _render_list(self) -> None:
        self._clear_list()
        target = self._selected_project()
        rows: list = []
        conn = indexer.open_db(self.cfg)

        if not target:
            self.status.setText(
                "PH 还不知道你在做什么项目。点右上「登记项目」选项目根目录，"
                "之后这里会自动列出适合该项目的提示词（按使用次数 / 成功反馈排序）。"
            )
        elif target == "__auto__":
            # 指纹匹配（P19：5 分钟内存缓存避免重复扫项目目录）
            try:
                cwd_key = str(Path.cwd().resolve())
                cached = self._fp_cache.get(cwd_key)
                now = time.time()
                if cached and (now - cached[0]) < 300:
                    fp = cached[1]
                else:
                    fp = fp_compute(Path.cwd())
                    self._fp_cache[cwd_key] = (now, fp)
                projects = indexer.list_projects(conn)
                import json as _json
                best = None
                best_score = 0.0
                for p in projects:
                    try:
                        other_fp_dict = _json.loads(p["fingerprint_json"] or "{}")
                        score = stack_overlap(fp, _dict_to_fp(other_fp_dict))
                        if score > best_score:
                            best_score = score
                            best = p
                    except Exception:
                        continue
                if best and best_score > 0.1:
                    self.status.setText(
                        f"指纹匹配：{best['name']}（相似度 {best_score:.0%}）"
                    )
                    rows = list(conn.execute(
                        "SELECT * FROM prompts WHERE project = ? OR projects_csv LIKE ? "
                        "ORDER BY used*2 + success_signal*3 DESC LIMIT 5",
                        (best["name"], f"%{best['name']}%"),
                    ))
                else:
                    self.status.setText(
                        "没找到匹配的项目。可在上方下拉里选一个，"
                        "或点「登记项目」把当前工作目录登记一下。"
                    )
                    rows = list(conn.execute(
                        "SELECT * FROM prompts ORDER BY used*2 + success_signal*3 DESC LIMIT 5"
                    ))
            except Exception as e:
                self.status.setText(f"指纹匹配失败：{e}")
        else:
            self.status.setText(f"项目：{target}")
            rows = list(conn.execute(
                "SELECT * FROM prompts WHERE project = ? OR projects_csv LIKE ? "
                "ORDER BY used*2 + success_signal*3 DESC LIMIT 5",
                (target, f"%{target}%"),
            ))
        conn.close()

        if not rows:
            empty = QLabel(
                "这个项目还没有专属提示词。在「我的库」里编辑一条 prompt，"
                "把「项目」字段填上项目名，这里就能召回。"
            )
            empty.setStyleSheet("color: #a3a3a3; font-size: 12px; padding: 12px 0;")
            empty.setWordWrap(True)
            self.list_layout.addWidget(empty)
            return

        for r in rows:
            self.list_layout.addWidget(self._make_row(r))

    def _make_row(self, row) -> QFrame:
        f = QFrame()
        f.setFrameShape(QFrame.Shape.NoFrame)
        f.setStyleSheet(
            "QFrame { background: #fafafa; border: 0; border-radius: 8px; }"
            "QFrame:hover { background: #f0f0f0; }"
        )
        h = QHBoxLayout(f)
        h.setContentsMargins(12, 8, 12, 8)
        h.setSpacing(10)

        col = QVBoxLayout()
        col.setSpacing(2)
        title = QLabel(row["title"] or "(未命名)")
        title.setStyleSheet("font-size: 13px; font-weight: 500; color: #0a0a0a;")
        col.addWidget(title)
        try:
            desc = row["description"] or ""
        except (KeyError, IndexError):
            desc = ""
        if not desc:
            desc = (row["body"] or "").strip().replace("\n", " ")[:60]
        meta = QLabel(f"{desc}  ·  用过 {row['used']} 次  ·  ⭐ {row['success_signal']}")
        meta.setStyleSheet("color: #737373; font-size: 11px;")
        meta.setWordWrap(False)
        col.addWidget(meta)
        h.addLayout(col, 1)

        btn = QPushButton("复制")
        btn.setProperty("class", "subtle")
        btn.setIcon(_icons.icon("copy"))
        btn.setIconSize(QSize(13, 13))
        btn.setStyleSheet("font-size: 11px; padding: 4px 10px;")
        btn.clicked.connect(lambda _=False, pid=row["id"]: self._on_copy(pid))
        h.addWidget(btn)
        return f

    def _on_copy(self, pid: str) -> None:
        from PySide6.QtWidgets import QApplication
        from ...core import placeholders
        conn = indexer.open_db(self.cfg)
        row = indexer.get_by_id(conn, pid)
        if row:
            indexer.bump_used(conn, pid)
        conn.close()
        if not row:
            return
        body = row["body"] or ""
        names = placeholders.find(body)
        if names:
            from ..dialogs.fill_placeholders_dialog import FillPlaceholdersDialog
            dlg = FillPlaceholdersDialog(row["title"] or "untitled", body, names, parent=self)
            if dlg.exec() != dlg.DialogCode.Accepted:
                return
            body = dlg.filled_text()
        QApplication.clipboard().setText(body)
        win = self.window()
        if hasattr(win, "statusBar"):
            try:
                win.statusBar().showMessage(f"✓ 已复制：{row['title']}", 3000)
            except Exception:
                pass
        self.refresh()


def _dict_to_fp(d: dict):
    """fingerprint.to_dict 的反向。"""
    from ...core.fingerprint import StackFingerprint
    return StackFingerprint(
        project_name=d.get("project_name", ""),
        cwd=d.get("cwd", ""),
        langs=set(d.get("langs", [])),
        frameworks=set(d.get("frameworks", [])),
        keywords=set(d.get("keywords", [])),
    )
