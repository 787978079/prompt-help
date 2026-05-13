"""首次设置：单屏对话框，三个动作。

不要 5 页 wizard 那种重感觉，新手看一屏就完事：
- 显示 vault 在哪里（只读，让用户知道数据放哪）
- 可填 API key（可空，跳过也能用）
- 一个大按钮「扫描我的项目目录导入提示词」（默认 D:\\My_Project，可改）
- 完成 → 写 .onboarding_done，进主界面
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QCursor, QFont
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QDialog, QFileDialog, QFrame, QHBoxLayout,
    QLabel, QLineEdit, QMessageBox, QPushButton, QVBoxLayout, QWidget,
)

from ...core import indexer, proc
from ...core.config import Config, save_config


class OnboardingWizard(QDialog):
    """单屏首次设置。命名沿用以兼容现有调用，实质是 QDialog。"""

    def __init__(self, cfg: Config):
        super().__init__()
        self.cfg = cfg
        self.imported_count = 0
        self.setWindowTitle("Prompt Help · 欢迎")
        self.resize(680, 560)
        self.setModal(True)
        self._build_ui()

    def _build_ui(self) -> None:
        v = QVBoxLayout(self)
        v.setContentsMargins(40, 36, 40, 28)
        v.setSpacing(14)

        # 标题
        title = QLabel("欢迎使用 Prompt Help")
        f = QFont()
        f.setPointSize(22)
        f.setWeight(QFont.Weight.Bold)
        title.setFont(f)
        title.setStyleSheet("color: #0a0a0a; letter-spacing: -0.5px;")
        v.addWidget(title)

        sub = QLabel(
            "跨项目沉淀「提示词 / 系统记忆 / 项目踩坑点」，"
            "用 LLM 二次优化成可分享的通用模板。一分钟设置好，先扫一遍你的项目就有几百条种子。"
        )
        sub.setStyleSheet("color: #737373; font-size: 14px; padding-bottom: 12px;")
        sub.setWordWrap(True)
        v.addWidget(sub)

        v.addWidget(_separator())

        # ----- 步骤 1：扫描导入 -----
        step1 = QLabel("1. 扫描你的项目目录（推荐先做）")
        step1.setStyleSheet("color: #0a0a0a; font-size: 15px; font-weight: 600; padding-top: 6px;")
        v.addWidget(step1)

        explain1 = QLabel(
            "选你存放所有项目的总目录（如 D:\\My_Project），系统会自动找到所有 "
            "CLAUDE.md / AGENTS.md / .cursorrules，按二级标题拆成可搜索的提示词。"
            "「致命禁令 / 禁止」自动转成踩坑提醒。"
        )
        explain1.setStyleSheet("color: #737373; font-size: 13px; line-height: 1.6;")
        explain1.setWordWrap(True)
        v.addWidget(explain1)

        scan_row = QHBoxLayout()
        scan_row.setSpacing(8)
        self.scan_path = QLineEdit()
        default_root = self._auto_detect_project_root()
        self.scan_path.setText(default_root)
        self.scan_path.setPlaceholderText("还没找到项目目录？点「选择…」浏览")
        scan_row.addWidget(self.scan_path, 1)
        self.btn_pick = QPushButton("选择…")
        self.btn_pick.setProperty("class", "subtle")
        self.btn_pick.clicked.connect(self._on_pick_dir)
        scan_row.addWidget(self.btn_pick)
        self.btn_scan = QPushButton("扫描并导入")
        self.btn_scan.setProperty("class", "primary")
        self.btn_scan.clicked.connect(self._on_scan)
        scan_row.addWidget(self.btn_scan)
        v.addLayout(scan_row)

        self.scan_status = QLabel("")
        self.scan_status.setStyleSheet("color: #16a34a; font-size: 12px; padding-top: 4px;")
        v.addWidget(self.scan_status)

        v.addWidget(_separator())

        # ----- 步骤 2：API key -----
        step2 = QLabel("2. 可选：填 LLM API Key")
        step2.setStyleSheet("color: #0a0a0a; font-size: 15px; font-weight: 600; padding-top: 6px;")
        v.addWidget(step2)

        explain2 = QLabel(
            "用于「保存提示词」时自动 polish 改写，留空也能用，主功能不受影响。"
            "默认接 DeepSeek API（OpenAI 兼容协议）。"
        )
        explain2.setStyleSheet("color: #737373; font-size: 13px; line-height: 1.6;")
        explain2.setWordWrap(True)
        v.addWidget(explain2)

        key_row = QHBoxLayout()
        key_row.setSpacing(8)
        self.api_key = QLineEdit()
        self.api_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.api_key.setPlaceholderText("sk-...（留空跳过）")
        prefilled = os.environ.get(self.cfg.llm.api_key_env, "")
        if prefilled:
            self.api_key.setText(prefilled)
        key_row.addWidget(self.api_key, 1)
        self.btn_show_key = QPushButton("显示")
        self.btn_show_key.setProperty("class", "subtle")
        self.btn_show_key.setCheckable(True)
        self.btn_show_key.toggled.connect(
            lambda c: self.api_key.setEchoMode(
                QLineEdit.EchoMode.Normal if c else QLineEdit.EchoMode.Password
            )
        )
        key_row.addWidget(self.btn_show_key)
        v.addLayout(key_row)

        v.addStretch(1)

        # ----- 已有库？迁移入口 -----
        migrate_row = QHBoxLayout()
        migrate_label = QLabel("已经在另一台机器用过？")
        migrate_label.setStyleSheet("color: #737373; font-size: 12px;")
        migrate_row.addWidget(migrate_label)
        self.btn_migrate = QPushButton("从 GitHub 私仓迁移过来")
        self.btn_migrate.setProperty("class", "subtle")
        self.btn_migrate.setStyleSheet("font-size: 12px; padding: 4px 10px;")
        self.btn_migrate.clicked.connect(self._on_migrate)
        migrate_row.addWidget(self.btn_migrate)
        migrate_row.addStretch(1)
        v.addLayout(migrate_row)

        # ----- 底部：完成 + vault 提示 -----
        info = QLabel(f"数据存放：{self.cfg.vault_path}")
        info.setStyleSheet("color: #a3a3a3; font-size: 11px;")
        info.setWordWrap(True)
        v.addWidget(info)

        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        self.btn_skip = QPushButton("稍后再说")
        self.btn_skip.setProperty("class", "subtle")
        self.btn_skip.clicked.connect(self.reject)
        btn_row.addWidget(self.btn_skip)
        self.btn_done = QPushButton("完成")
        self.btn_done.setProperty("class", "primary")
        self.btn_done.clicked.connect(self._on_done)
        btn_row.addWidget(self.btn_done)
        v.addLayout(btn_row)

    # ------------------------------------------------------------------

    def _auto_detect_project_root(self) -> str:
        """自动探测常见项目目录。找到第一个存在的返回路径，都不存在返回空串。"""
        home = Path.home()
        candidates = [
            Path("D:/My_Project"),
            home / "Projects",
            home / "projects",
            home / "Documents" / "GitHub",
            home / "Documents" / "github",
            home / "source" / "repos",  # Windows VS 默认
            home / "Code",
            home / "code",
            home / "Desktop",
        ]
        for c in candidates:
            try:
                if c.is_dir():
                    return str(c)
            except OSError:
                continue
        return ""

    def _on_pick_dir(self) -> None:
        d = QFileDialog.getExistingDirectory(
            self, "选项目根目录", self.scan_path.text() or str(Path.home()),
        )
        if d:
            self.scan_path.setText(d)

    def _on_scan(self) -> None:
        path_str = self.scan_path.text().strip()
        if not path_str:
            QMessageBox.warning(
                self, "还没选目录",
                "请先在上方输入框点「选择…」浏览你的项目目录。\n\n"
                "如果暂时没有项目目录，可以直接跳过——之后用「推荐库」补种子。",
            )
            return
        scan_root = Path(path_str).resolve()
        if not scan_root.is_dir():
            QMessageBox.warning(
                self, "目录不存在",
                f"找不到目录：\n{scan_root}\n\n"
                "请点「选择…」浏览一个真实存在的项目根目录，"
                "或留空跳过这步，之后用「推荐库」补种子也行。",
            )
            return

        self._ensure_vault_ready()

        QApplication.setOverrideCursor(QCursor(Qt.CursorShape.WaitCursor))
        try:
            from ...cli import admin
            from ...core.fingerprint import fingerprint as fp_compute, to_dict

            roots = admin._find_project_roots(scan_root)
            roots = [r for r in roots if r.name != "Prompt help"]
            conn = indexer.open_db(self.cfg)

            saved_total = trap_total = projects_total = 0
            md_files_seen = 0
            for proj_root in roots:
                files = admin._collect_files_in_project(proj_root)
                md_files_seen += len(files)
                if not files:
                    continue
                fp = fp_compute(proj_root)
                fp.project_name = proj_root.name
                indexer.register_project(
                    conn, name=proj_root.name, cwd_path=str(proj_root),
                    fingerprint_json=json.dumps(to_dict(fp), ensure_ascii=False),
                )
                projects_total += 1
                for f in files:
                    saved, trap_count, _ = admin._import_one_file(
                        self.cfg, conn, f, proj_root.name,
                        polish=False, min_chars=80,
                    )
                    saved_total += saved
                    trap_total += trap_count
            conn.close()
            self.imported_count = saved_total
        finally:
            QApplication.restoreOverrideCursor()

        # Phase 7：分情况诊断
        if not roots:
            self.scan_status.setStyleSheet("color: #b45309; font-size: 12px; padding-top: 4px;")
            self.scan_status.setText(
                f"⚠ 在 {scan_root} 没发现任何子目录像项目根（没有 .git / package.json / pyproject.toml）。"
                f" 可改路径再扫，或先跳过，进主界面后用「推荐库」一键补种子。"
            )
            return
        if md_files_seen == 0:
            self.scan_status.setStyleSheet("color: #b45309; font-size: 12px; padding-top: 4px;")
            self.scan_status.setText(
                f"⚠ 扫描了 {len(roots)} 个项目目录，但没有任何 CLAUDE.md / AGENTS.md / .cursorrules。"
                f" 这正常——可以现在跳过，用「推荐库」补几十条种子；"
                f"以后写 CLAUDE.md 时再回头扫一次。"
            )
            return
        self.scan_status.setStyleSheet("color: #16a34a; font-size: 12px; padding-top: 4px;")
        self.scan_status.setText(
            f"✓ 已导入 {saved_total} 条提示词（含 {trap_total} 条踩坑提醒），"
            f"登记 {projects_total} 个项目（扫 {md_files_seen} 个 md 文件）。"
        )
        # P17：把扫描根目录保存下来，之后 GUI 首页「刷新本机项目」按钮能用
        try:
            from ...core import refresh as _refresh
            _refresh.add_scan_root(self.cfg, scan_root)
        except Exception:
            pass

    def _on_migrate(self) -> None:
        """从 GitHub 私仓克隆已有库到本机 vault 路径。"""
        from PySide6.QtWidgets import QInputDialog
        url, ok = QInputDialog.getText(
            self, "迁移已有库",
            "输入你之前用的 prompt-help 私仓 URL：\n"
            "（如 git@github.com:your-name/prompt-help-vault.git）",
        )
        if not ok or not url.strip():
            return
        url = url.strip()

        if self.cfg.vault_path.exists() and any(self.cfg.vault_path.iterdir()):
            ans = QMessageBox.question(
                self, "vault 已存在",
                f"{self.cfg.vault_path} 非空。继续会先备份当前内容到 .backup-<时间戳>，然后克隆。\n"
                "继续吗？",
            )
            if ans != QMessageBox.StandardButton.Yes:
                return
            import shutil, time
            backup = self.cfg.vault_path.with_suffix(f".backup-{int(time.time())}")
            shutil.move(str(self.cfg.vault_path), str(backup))

        QApplication.setOverrideCursor(QCursor(Qt.CursorShape.WaitCursor))
        try:
            self.cfg.vault_path.parent.mkdir(parents=True, exist_ok=True)
            r = proc.run(
                ["git", "clone", url, str(self.cfg.vault_path)],
                capture_output=True, text=True,
            )
            if r.returncode != 0:
                QMessageBox.warning(
                    self, "克隆失败",
                    f"git clone 退出码 {r.returncode}\n\n{r.stderr[:500]}",
                )
                return
            indexer.open_db(self.cfg).close()
            from ...core import indexer as _idx
            n = _idx.reindex_from_disk(self.cfg)
            (self.cfg.vault_path / ".onboarding_done").write_text("1", encoding="utf-8")
        finally:
            QApplication.restoreOverrideCursor()

        QMessageBox.information(
            self, "迁移完成",
            f"已恢复 vault；索引重建 {n} 条。点「完成」进主界面。",
        )
        self.imported_count = n
        # 自动接受 wizard
        self.accept()

    def _on_done(self) -> None:
        self._ensure_vault_ready()

        # 写 API key 到 .env（如填了）
        api_key = self.api_key.text().strip()
        if api_key:
            self._write_env(api_key)
            os.environ[self.cfg.llm.api_key_env] = api_key

        # 标记完成
        try:
            (self.cfg.vault_path / ".onboarding_done").write_text("1", encoding="utf-8")
        except Exception:
            pass

        # Phase 7：弹「下一步地图」对话框（如果库还很空）
        try:
            self._show_next_steps_map()
        except Exception:
            pass

        self.accept()

    def _show_next_steps_map(self) -> None:
        """完成 Wizard 后弹一张「接下来 5 分钟可做的事」对话框（Phase 7 T4）。"""
        from PySide6.QtWidgets import QDialog, QDialogButtonBox
        dlg = QDialog(self)
        dlg.setWindowTitle("接下来可以做的")
        dlg.resize(540, 380)
        v = QVBoxLayout(dlg)
        v.setContentsMargins(28, 24, 28, 20)
        v.setSpacing(12)

        if self.imported_count > 0:
            head_text = (
                f"✅ 已为你建好库，导入 {self.imported_count} 条种子。\n\n"
                "**接下来 5 分钟**，建议选一项试试："
            )
        else:
            head_text = (
                "✅ 库已建好。\n\n"
                "**接下来 5 分钟**，建议选一项试试："
            )
        head = QLabel(head_text)
        head.setTextFormat(Qt.TextFormat.MarkdownText)
        head.setWordWrap(True)
        head.setStyleSheet("font-size: 13px; color: #0a0a0a; line-height: 1.6;")
        v.addWidget(head)

        cards = [
            ("📦 浏览推荐库", "拉 awesome-claude-prompts、cursorrules 等公开源，几百条直接可用"),
            ("🔍 从历史会话挖掘", "扫你过去和 Claude / Codex 的对话，把写过的好提示词找回来"),
            ("🧩 装 Claude Code 插件", "让 Claude 在写代码时自动召回相关提示词 + 自动捕获新提示词"),
            ("🧭 试试产品发现", "LLM 苏格拉底访谈帮你想清楚下一个项目要建什么"),
        ]
        for emoji_title, desc in cards:
            card = QFrame()
            card.setStyleSheet(
                "QFrame { background: #fafafa; border: 0; border-radius: 8px; }"
            )
            ch = QVBoxLayout(card)
            ch.setContentsMargins(14, 10, 14, 10)
            ch.setSpacing(2)
            t = QLabel(emoji_title)
            t.setStyleSheet("font-size: 13px; font-weight: 600; color: #0a0a0a;")
            ch.addWidget(t)
            d = QLabel(desc)
            d.setStyleSheet("color: #525252; font-size: 11px;")
            d.setWordWrap(True)
            ch.addWidget(d)
            v.addWidget(card)

        tip = QLabel("提示：首页有完整的「上手 5 步」清单，每完成一项会自动勾掉。")
        tip.setStyleSheet("color: #737373; font-size: 11px; padding-top: 4px;")
        tip.setWordWrap(True)
        v.addWidget(tip)

        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok)
        btns.button(QDialogButtonBox.StandardButton.Ok).setText("好的，开始用")
        btns.accepted.connect(dlg.accept)
        v.addWidget(btns)
        dlg.exec()

    def _ensure_vault_ready(self) -> None:
        """创建 vault 必备目录、写默认 config、git init。幂等。"""
        for d in (
            self.cfg.vault_path,
            self.cfg.prompts_dir / "global",
            self.cfg.prompts_dir / "projects",
            self.cfg.prompts_dir / "traps",
            self.cfg.inbox_dir,
            self.cfg.briefs_dir,
            self.cfg.pulse_dir,
            self.cfg.logs_dir,
        ):
            d.mkdir(parents=True, exist_ok=True)
        if not self.cfg.config_file.is_file():
            save_config(self.cfg)
        if not (self.cfg.vault_path / ".git").is_dir():
            proc.run(["git", "init", "-q", str(self.cfg.vault_path)], check=False)
            proc.run(
                ["git", "-C", str(self.cfg.vault_path), "config", "user.name",
                 self.cfg.git.commit_user_name], check=False,
            )
            proc.run(
                ["git", "-C", str(self.cfg.vault_path), "config", "user.email",
                 self.cfg.git.commit_user_email], check=False,
            )
        # SQLite
        indexer.open_db(self.cfg).close()

    def _write_env(self, api_key: str) -> None:
        env_file = self.cfg.vault_path / ".env"
        existing: dict[str, str] = {}
        if env_file.is_file():
            for line in env_file.read_text(encoding="utf-8").splitlines():
                if "=" in line and not line.strip().startswith("#"):
                    k, _, v = line.partition("=")
                    existing[k.strip()] = v.strip()
        existing[self.cfg.llm.api_key_env] = api_key
        env_file.write_text(
            "\n".join(f"{k}={v}" for k, v in existing.items()) + "\n",
            encoding="utf-8",
        )
        gi = self.cfg.vault_path / ".gitignore"
        gi_lines = gi.read_text(encoding="utf-8").splitlines() if gi.is_file() else []
        if ".env" not in gi_lines:
            gi_lines.append(".env")
            gi.write_text("\n".join(gi_lines) + "\n", encoding="utf-8")


def _separator() -> QFrame:
    line = QFrame()
    line.setFrameShape(QFrame.Shape.HLine)
    line.setStyleSheet("color: #ececec; background-color: #ececec; max-height: 1px;")
    return line
