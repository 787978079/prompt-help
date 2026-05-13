"""设置页：API key、mining 阈值、CC 插件开关。"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from ...core import proc

from PySide6.QtCore import Qt, QThread, QTimer, Signal
from PySide6.QtWidgets import (
    QButtonGroup, QCheckBox, QFormLayout, QFrame, QGroupBox, QHBoxLayout, QLabel,
    QLineEdit, QMessageBox, QProgressBar, QPushButton, QRadioButton, QScrollArea,
    QSpinBox, QVBoxLayout, QWidget,
)

from ...core.config import Config, load_dotenv_if_present, save_config


class SettingsPage(QWidget):
    def __init__(self, cfg: Config):
        super().__init__()
        self.cfg = cfg
        self._build()
        self.on_show()

    def _build(self) -> None:
        # P21：设置页内容超过单屏（9 个 GroupBox ≈ 1500px），用 QScrollArea 包起来，
        # 否则窗口高度不够时下方的「同步 / 翻译缓存 / 保存」直接被挤出可见区。
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        host = QWidget()
        scroll.setWidget(host)
        outer.addWidget(scroll)

        v = QVBoxLayout(host)
        v.setContentsMargins(16, 16, 16, 16)
        v.setSpacing(8)

        title = QLabel("设置")
        title.setObjectName("pageTitle")
        v.addWidget(title)
        hint = QLabel("调整 API key、mining 阈值、Claude Code 插件开关。改完点保存。")
        hint.setObjectName("pageHint")
        hint.setWordWrap(True)
        v.addWidget(hint)

        # ---- LLM ----
        llm_box = QGroupBox("LLM polish 配置")
        f = QFormLayout(llm_box)

        # Phase 22：3 后端单选 + 自动
        # 用户希望用 Codex CLI 时不必再配 DeepSeek key；选项靠 PATH 检测显示可用性
        backend_row = QHBoxLayout()
        self.backend_group = QButtonGroup(self)
        self.backend_auto = QRadioButton("自动")
        self.backend_auto.setToolTip(
            "按可用性挑：CC CLI 在 PATH > Codex CLI 在 PATH > DeepSeek API key"
        )
        self.backend_cc = QRadioButton("Claude Code CLI")
        self.backend_cc.setToolTip("用本机 claude -p（复用 Anthropic 订阅）")
        self.backend_codex = QRadioButton("Codex CLI")
        self.backend_codex.setToolTip(
            "用本机 codex exec（复用 OpenAI 订阅，无需 DeepSeek key）"
        )
        self.backend_api = QRadioButton("DeepSeek API")
        self.backend_api.setToolTip("走 DeepSeek（或其他 OpenAI 兼容）API，需要下方 API key")
        for rb in (self.backend_auto, self.backend_cc, self.backend_codex, self.backend_api):
            self.backend_group.addButton(rb)
            backend_row.addWidget(rb)
        backend_row.addStretch(1)
        backend_wrap = QWidget(); backend_wrap.setLayout(backend_row)
        f.addRow("后端", backend_wrap)

        # 检测状态（每次 on_show 刷新）
        self.backend_status = QLabel("…")
        self.backend_status.setStyleSheet("color: #525252; font-size: 11px; line-height: 1.5;")
        self.backend_status.setWordWrap(True)
        f.addRow("检测", self.backend_status)

        # 连通性测试：点一下跑最小 prompt 验证三个后端真能调通
        test_row = QHBoxLayout()
        self.btn_test_cc = QPushButton("测 CC CLI")
        self.btn_test_cc.setProperty("class", "subtle")
        self.btn_test_cc.setToolTip("跑一次最小 prompt 验证 Claude Code CLI 真能调通")
        self.btn_test_cc.clicked.connect(lambda: self._on_test_backend("cc_cli"))
        test_row.addWidget(self.btn_test_cc)

        self.btn_test_codex = QPushButton("测 Codex CLI")
        self.btn_test_codex.setProperty("class", "subtle")
        self.btn_test_codex.setToolTip("跑一次最小 prompt 验证 Codex CLI 真能调通")
        self.btn_test_codex.clicked.connect(lambda: self._on_test_backend("codex_cli"))
        test_row.addWidget(self.btn_test_codex)

        self.btn_test_api = QPushButton("测 API")
        self.btn_test_api.setProperty("class", "subtle")
        self.btn_test_api.setToolTip("跑一次最小 prompt 验证 DeepSeek API key 真能调通")
        self.btn_test_api.clicked.connect(lambda: self._on_test_backend("api"))
        test_row.addWidget(self.btn_test_api)
        test_row.addStretch(1)
        test_wrap = QWidget(); test_wrap.setLayout(test_row)
        f.addRow("连通性测试", test_wrap)

        self.test_progress = QProgressBar()
        self.test_progress.setRange(0, 1000)
        self.test_progress.setTextVisible(False)
        self.test_progress.setFixedHeight(5)
        self.test_progress.setVisible(False)
        f.addRow("", self.test_progress)

        self.test_result_lbl = QLabel("")
        self.test_result_lbl.setStyleSheet("color: #525252; font-size: 11px;")
        self.test_result_lbl.setWordWrap(True)
        f.addRow("", self.test_result_lbl)

        # ETA 进度
        self._test_tick_ms = 200
        self._test_elapsed_ms = 0
        self._test_eta_seconds = 0.0
        self._test_timer = QTimer(self)
        self._test_timer.setInterval(self._test_tick_ms)
        self._test_timer.timeout.connect(self._on_test_tick)
        self._test_backend_name = ""

        self.api_key = QLineEdit()
        self.api_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.api_key.setPlaceholderText("从环境变量 / .env 读取；这里改了会写到 .env")
        show_key = QPushButton("👁")
        show_key.setCheckable(True)
        show_key.setProperty("class", "subtle")
        show_key.toggled.connect(
            lambda c: self.api_key.setEchoMode(
                QLineEdit.EchoMode.Normal if c else QLineEdit.EchoMode.Password
            )
        )
        h = QHBoxLayout(); h.addWidget(self.api_key); h.addWidget(show_key)
        wrap = QWidget(); wrap.setLayout(h)
        f.addRow(self.cfg.llm.api_key_env, wrap)

        self.base_url = QLineEdit()
        f.addRow("base_url", self.base_url)
        self.model = QLineEdit()
        f.addRow("model", self.model)

        v.addWidget(llm_box)

        # ---- mining ----
        m_box = QGroupBox("自动挖掘（mining）")
        mf = QFormLayout(m_box)
        self.mining_enabled = QCheckBox("启用 Stop hook 当场推送")
        mf.addRow(self.mining_enabled)
        self.min_chars = QSpinBox(); self.min_chars.setRange(50, 5000); self.min_chars.setSingleStep(50)
        mf.addRow("最小字符数", self.min_chars)
        self.max_chars = QSpinBox(); self.max_chars.setRange(500, 20000); self.max_chars.setSingleStep(500)
        mf.addRow("最大字符数", self.max_chars)
        self.trap_enabled = QCheckBox("启用 UserPromptSubmit trap 召回")
        mf.addRow(self.trap_enabled)
        v.addWidget(m_box)

        # ---- 插件 ----
        pl_box = QGroupBox("Claude Code 插件")
        pl = QVBoxLayout(pl_box)
        self.plugin_status = QLabel()
        pl.addWidget(self.plugin_status)
        ph = QHBoxLayout()
        self.btn_install_plugin = QPushButton("一键安装")
        self.btn_install_plugin.setProperty("class", "primary")
        self.btn_install_plugin.clicked.connect(self._on_install_plugin)
        self.btn_uninstall_plugin = QPushButton("卸载")
        self.btn_uninstall_plugin.setProperty("class", "subtle")
        self.btn_uninstall_plugin.clicked.connect(self._on_uninstall_plugin)
        ph.addWidget(self.btn_install_plugin); ph.addWidget(self.btn_uninstall_plugin)
        ph.addStretch(1)
        pl.addLayout(ph)
        pl.addWidget(QLabel(
            "插件提供：自动挖掘 prompts、trap 召回、SessionStart 跨项目召回、PreCompact 二次挖掘。\n"
            "未安装时 GUI 仍可独立使用，只是失去这些自动化能力。"
        ))
        v.addWidget(pl_box)

        # ---- 路径 / vault ----
        v_box = QGroupBox("Vault")
        vf = QFormLayout(v_box)
        path = QLabel(str(self.cfg.vault_path))
        path.setStyleSheet("color: #6b7280;")
        vf.addRow("vault 路径", path)
        v.addWidget(v_box)

        # ---- 项目扫描根目录（P17） ----
        roots_box = QGroupBox("项目扫描根目录（用于「刷新本机项目」）")
        rf = QFormLayout(roots_box)
        rh = QLabel(
            "PH 会扫这些目录下的子项目，自动同步 CLAUDE.md / AGENTS.md / .cursorrules 到库。\n"
            "在首页点「⟳ 刷新本机项目」触发，自动「+ 新增 / ~ 更新 / = 跳过」。"
        )
        rh.setStyleSheet("color: #737373; font-size: 11px;")
        rh.setWordWrap(True)
        rf.addRow(rh)
        self.scan_roots_status = QLabel("…")
        self.scan_roots_status.setStyleSheet("color: #525252;")
        self.scan_roots_status.setWordWrap(True)
        rf.addRow("当前根目录", self.scan_roots_status)
        roots_row = QHBoxLayout()
        self.btn_roots_add = QPushButton("➕ 新增")
        self.btn_roots_add.setProperty("class", "subtle")
        self.btn_roots_add.clicked.connect(self._on_scan_root_add)
        roots_row.addWidget(self.btn_roots_add)
        self.btn_roots_remove = QPushButton("删除")
        self.btn_roots_remove.setProperty("class", "subtle")
        self.btn_roots_remove.clicked.connect(self._on_scan_root_remove)
        roots_row.addWidget(self.btn_roots_remove)
        roots_row.addStretch(1)
        roots_wrap = QWidget(); roots_wrap.setLayout(roots_row)
        rf.addRow("操作", roots_wrap)
        v.addWidget(roots_box)

        # ---- 团队 channel 订阅（P15 T2） ----
        ch_box = QGroupBox("团队 channel 订阅")
        chf = QFormLayout(ch_box)
        ch_hint = QLabel(
            "订阅朋友 / 团队的 git 仓库，PH 会把对方分享的提示词拉到「待审」让你逐条审核。"
        )
        ch_hint.setStyleSheet("color: #737373; font-size: 11px;")
        ch_hint.setWordWrap(True)
        chf.addRow(ch_hint)
        self.channel_status = QLabel("…")
        self.channel_status.setStyleSheet("color: #525252;")
        self.channel_status.setWordWrap(True)
        chf.addRow("已订阅", self.channel_status)
        ch_row = QHBoxLayout()
        self.btn_ch_add = QPushButton("➕ 新增订阅")
        self.btn_ch_add.setProperty("class", "subtle")
        self.btn_ch_add.clicked.connect(self._on_channel_add)
        ch_row.addWidget(self.btn_ch_add)
        self.btn_ch_pull_all = QPushButton("⟳ 拉所有")
        self.btn_ch_pull_all.setProperty("class", "primary")
        self.btn_ch_pull_all.clicked.connect(self._on_channel_pull_all)
        ch_row.addWidget(self.btn_ch_pull_all)
        self.btn_ch_remove = QPushButton("取消订阅")
        self.btn_ch_remove.setProperty("class", "subtle")
        self.btn_ch_remove.clicked.connect(self._on_channel_remove)
        ch_row.addWidget(self.btn_ch_remove)
        ch_row.addStretch(1)
        ch_wrap = QWidget(); ch_wrap.setLayout(ch_row)
        chf.addRow("操作", ch_wrap)
        v.addWidget(ch_box)

        # ---- Git 同步（P14 T3） ----
        sync_box = QGroupBox("Git 同步")
        syncf = QFormLayout(sync_box)
        self.sync_status = QLabel("…")
        self.sync_status.setStyleSheet("color: #525252;")
        self.sync_status.setWordWrap(True)
        syncf.addRow("状态", self.sync_status)
        sync_row = QHBoxLayout()
        self.btn_sync_now = QPushButton("立即 pull + push")
        self.btn_sync_now.setProperty("class", "primary")
        self.btn_sync_now.setToolTip("git pull --rebase && git push（冲突时取消并去命令行手动解决）")
        self.btn_sync_now.clicked.connect(self._on_sync_now)
        sync_row.addWidget(self.btn_sync_now)
        self.btn_sync_status = QPushButton("刷新状态")
        self.btn_sync_status.setProperty("class", "subtle")
        self.btn_sync_status.clicked.connect(self._refresh_sync_status)
        sync_row.addWidget(self.btn_sync_status)
        sync_row.addStretch(1)
        sync_wrap = QWidget(); sync_wrap.setLayout(sync_row)
        syncf.addRow("操作", sync_wrap)
        v.addWidget(sync_box)

        # ---- 推荐库（P21）----
        pl_box2 = QGroupBox("推荐库")
        plf2 = QFormLayout(pl_box2)
        self.pl_auto_translate = QCheckBox("刷新源后自动翻译为中文（中文用户推荐开）")
        self.pl_auto_translate.setToolTip(
            "默认开。关闭后回到「按需翻译」模式：卡片保留英文，"
            "点单条「翻译此条」或「批量翻译全部」时才翻译。"
        )
        plf2.addRow(self.pl_auto_translate)
        v.addWidget(pl_box2)

        # ---- 翻译缓存（P12-N2） ----
        tc_box = QGroupBox("翻译缓存")
        tcf = QFormLayout(tc_box)
        self.tc_status = QLabel("…")
        self.tc_status.setStyleSheet("color: #525252;")
        tcf.addRow("状态", self.tc_status)
        tc_row = QHBoxLayout()
        self.btn_tc_refresh = QPushButton("查看统计")
        self.btn_tc_refresh.setProperty("class", "subtle")
        self.btn_tc_refresh.clicked.connect(self._refresh_tc_stats)
        tc_row.addWidget(self.btn_tc_refresh)
        self.btn_tc_cleanup = QPushButton("清理 30 天以上过期")
        self.btn_tc_cleanup.setProperty("class", "subtle")
        self.btn_tc_cleanup.clicked.connect(self._on_tc_cleanup)
        tc_row.addWidget(self.btn_tc_cleanup)
        tc_row.addStretch(1)
        tc_wrap = QWidget(); tc_wrap.setLayout(tc_row)
        tcf.addRow("操作", tc_wrap)
        v.addWidget(tc_box)

        # ---- 保存 ----
        bar = QHBoxLayout()
        bar.addStretch(1)
        self.btn_save = QPushButton("保存设置")
        self.btn_save.setProperty("class", "primary")
        self.btn_save.clicked.connect(self._on_save)
        bar.addWidget(self.btn_save)
        v.addLayout(bar)

        v.addStretch(1)

    # ------------------------------------------------------------------

    def on_show(self) -> None:
        # 加载到 UI
        load_dotenv_if_present()
        self._refresh_tc_stats()
        self._refresh_sync_status()
        self._refresh_channels()
        self._refresh_scan_roots()
        self.api_key.setText(os.environ.get(self.cfg.llm.api_key_env, ""))
        self.base_url.setText(self.cfg.llm.base_url)
        self.model.setText(self.cfg.llm.model)

        # Phase 22：后端单选回显 + 检测刷新
        backend = (self.cfg.optimizer.backend or "auto").lower()
        if backend == "cc_cli":
            self.backend_cc.setChecked(True)
        elif backend == "codex_cli":
            self.backend_codex.setChecked(True)
        elif backend == "api":
            self.backend_api.setChecked(True)
        else:
            self.backend_auto.setChecked(True)
        self._refresh_backend_status()

        self.mining_enabled.setChecked(self.cfg.mining.enabled)
        self.min_chars.setValue(self.cfg.mining.min_chars)
        self.max_chars.setValue(self.cfg.mining.max_chars)
        self.trap_enabled.setChecked(self.cfg.trap_recall.enabled)
        self.pl_auto_translate.setChecked(self.cfg.public_library.auto_translate_on_refresh)
        self._refresh_plugin_status()

    def _refresh_backend_status(self) -> None:
        """Phase 22：检测 3 个后端的可用性并显示。"""
        parts = []
        # CC CLI
        cc_path = shutil.which(self.cfg.optimizer.cc_cli_path)
        if cc_path:
            parts.append(f"✓ Claude Code CLI（{cc_path}）")
        else:
            parts.append("✗ Claude Code CLI 未在 PATH")
        # Codex CLI
        cx_path = shutil.which(self.cfg.optimizer.codex_cli_path)
        if cx_path:
            parts.append(f"✓ Codex CLI（{cx_path}）")
        else:
            parts.append("✗ Codex CLI 未在 PATH（装：npm i -g @openai/codex 后 codex login）")
        # API key
        if os.environ.get(self.cfg.llm.api_key_env):
            parts.append(f"✓ {self.cfg.llm.api_key_env} 已配置")
        else:
            parts.append(f"✗ {self.cfg.llm.api_key_env} 未配置")
        # 自动模式当前会用哪个
        if self.cfg.optimizer.prefer_cc_cli and cc_path:
            auto_pick = "Claude Code CLI"
        elif cx_path:
            auto_pick = "Codex CLI"
        elif os.environ.get(self.cfg.llm.api_key_env):
            auto_pick = "DeepSeek API"
        else:
            auto_pick = "（无可用后端）"
        parts.append(f"· 「自动」模式当前会用：{auto_pick}")
        self.backend_status.setText("\n".join(parts))

    def _on_test_backend(self, backend: str) -> None:
        """点"测一下"：跑最小 prompt 验证后端真能调通。"""
        from ...core import llm_timings
        self.test_result_lbl.setStyleSheet("color: #525252; font-size: 11px;")
        self._test_backend_name = backend
        self._test_elapsed_ms = 0
        self._test_eta_seconds = llm_timings.estimate(self.cfg, backend, "test")
        self.test_progress.setValue(0)
        self.test_progress.setVisible(True)
        self.test_result_lbl.setText(
            f"正在测试 {backend} …  预计 ~{self._test_eta_seconds:.0f}s"
        )
        self._test_timer.start()
        for btn in (self.btn_test_cc, self.btn_test_codex, self.btn_test_api):
            btn.setEnabled(False)
        self._test_worker = _BackendTestWorker(self.cfg, backend)
        self._test_worker.done.connect(self._on_test_done)
        self._test_worker.start()

    def _on_test_tick(self) -> None:
        self._test_elapsed_ms += self._test_tick_ms
        elapsed_s = self._test_elapsed_ms / 1000.0
        if self._test_eta_seconds <= 0:
            ratio = 0.5
        else:
            ratio = elapsed_s / self._test_eta_seconds
        if ratio < 0.85:
            pct = int(ratio * 850)
        else:
            overshoot = ratio - 0.85
            pct = 850 + int(min(100, overshoot * 200))
        self.test_progress.setValue(min(pct, 950))
        remaining = max(0.0, self._test_eta_seconds - elapsed_s)
        if elapsed_s < self._test_eta_seconds * 0.95:
            eta_text = f"剩约 {remaining:.0f}s"
        elif elapsed_s < self._test_eta_seconds * 1.5:
            eta_text = "即将完成…"
        else:
            eta_text = "比预期慢，再等等"
        self.test_result_lbl.setText(
            f"测试 {self._test_backend_name}  已 {elapsed_s:.0f}s / 预计 ~{self._test_eta_seconds:.0f}s · {eta_text}"
        )

    def _on_test_done(self, backend: str, ok: bool, msg: str) -> None:
        from ...core import llm_timings
        self._test_timer.stop()
        elapsed = self._test_elapsed_ms / 1000.0
        # 记录耗时（即使失败也记，下次估算 fallback 用）
        if ok:
            llm_timings.record(self.cfg, backend, "test", elapsed)
        self.test_progress.setValue(1000)
        self.test_progress.setVisible(False)
        for btn in (self.btn_test_cc, self.btn_test_codex, self.btn_test_api):
            btn.setEnabled(True)
        if ok:
            self.test_result_lbl.setStyleSheet("color: #16a34a; font-size: 11px;")
            self.test_result_lbl.setText(f"✓ {backend} 通（耗时 {elapsed:.0f}s）：{msg}")
        else:
            self.test_result_lbl.setStyleSheet("color: #b91c1c; font-size: 11px;")
            self.test_result_lbl.setText(f"✗ {backend} 失败（耗时 {elapsed:.0f}s）：{msg}")

    def _refresh_plugin_status(self) -> None:
        plugin_dir = Path.home() / ".claude" / "plugins" / "prompt-help"
        if plugin_dir.is_dir():
            self.plugin_status.setText(f"✓ 已安装：{plugin_dir}")
            self.plugin_status.setStyleSheet("color: #16a34a;")
        else:
            self.plugin_status.setText(f"○ 未安装（路径：{plugin_dir}）")
            self.plugin_status.setStyleSheet("color: #6b7280;")

    def _on_save(self) -> None:
        # 写 .env（API key + base_url + model）
        try:
            self._write_env(
                {self.cfg.llm.api_key_env: self.api_key.text().strip(),
                 "DEEPSEEK_BASE_URL": self.base_url.text().strip(),
                 "DEEPSEEK_MODEL": self.model.text().strip()}
            )
        except Exception as e:
            QMessageBox.warning(self, ".env 写入失败", str(e))

        # 改 cfg 内存值
        self.cfg.llm.base_url = self.base_url.text().strip() or self.cfg.llm.base_url
        self.cfg.llm.model = self.model.text().strip() or self.cfg.llm.model

        # Phase 22：后端单选保存
        if self.backend_cc.isChecked():
            self.cfg.optimizer.backend = "cc_cli"
        elif self.backend_codex.isChecked():
            self.cfg.optimizer.backend = "codex_cli"
        elif self.backend_api.isChecked():
            self.cfg.optimizer.backend = "api"
        else:
            self.cfg.optimizer.backend = "auto"

        self.cfg.mining.enabled = self.mining_enabled.isChecked()
        self.cfg.mining.min_chars = self.min_chars.value()
        self.cfg.mining.max_chars = self.max_chars.value()
        self.cfg.trap_recall.enabled = self.trap_enabled.isChecked()
        self.cfg.public_library.auto_translate_on_refresh = self.pl_auto_translate.isChecked()

        # 落盘 config.toml
        save_config(self.cfg)
        # 同时刷新 os.environ
        if self.api_key.text().strip():
            os.environ[self.cfg.llm.api_key_env] = self.api_key.text().strip()

        QMessageBox.information(self, "已保存", "设置已写入 config.toml 和 .env。")

    def _write_env(self, kv: dict) -> None:
        # 写到 vault_path/.env，永远不进 git（vault 自带 .gitignore 加 .env？）
        env_file = self.cfg.vault_path / ".env"
        existing: dict[str, str] = {}
        if env_file.is_file():
            for line in env_file.read_text(encoding="utf-8").splitlines():
                if "=" in line and not line.strip().startswith("#"):
                    k, _, v = line.partition("=")
                    existing[k.strip()] = v.strip()
        existing.update({k: v for k, v in kv.items() if v})
        env_file.write_text(
            "\n".join(f"{k}={v}" for k, v in existing.items()) + "\n",
            encoding="utf-8",
        )
        # 确保 .gitignore 含 .env
        gi = self.cfg.vault_path / ".gitignore"
        gi_lines = gi.read_text(encoding="utf-8").splitlines() if gi.is_file() else []
        if ".env" not in gi_lines:
            gi_lines.append(".env")
            gi.write_text("\n".join(gi_lines) + "\n", encoding="utf-8")

    def _on_install_plugin(self) -> None:
        from ... import __file__ as pkg_init
        src = Path(pkg_init).resolve().parent / "plugin"
        dst = Path.home() / ".claude" / "plugins" / "prompt-help"
        if dst.exists():
            ans = QMessageBox.question(self, "已安装", "插件已存在，覆盖吗？")
            if ans != QMessageBox.StandardButton.Yes:
                return
            shutil.rmtree(dst)
        try:
            shutil.copytree(src, dst)
            QMessageBox.information(
                self, "已安装",
                f"插件已安装到 {dst}\n"
                "重启 Claude Code 后生效。\n"
                "如果钩子没自动挂上，参考 plugin/HOOKS_SETUP.md 手动合并到 ~/.claude/settings.json。",
            )
        except Exception as e:
            QMessageBox.warning(self, "安装失败", str(e))
        self._refresh_plugin_status()

    def _refresh_scan_roots(self) -> None:
        """P17：显示扫描根目录列表。"""
        from ...core import refresh as _refresh
        try:
            roots = _refresh.load_scan_roots(self.cfg)
        except Exception as e:
            self.scan_roots_status.setText(f"读取失败：{e}")
            return
        if not roots:
            self.scan_roots_status.setText(
                "（还没配置——点「➕ 新增」加你的项目总目录，如 D:/My_Project）"
            )
            return
        lines = []
        for r in roots:
            last = (r.get("last_scan") or "—")[:19]
            lines.append(f"· {r.get('path')}  · 上次扫：{last}")
        self.scan_roots_status.setText("\n".join(lines))

    def _on_scan_root_add(self) -> None:
        """P17：新增扫描根目录。"""
        from PySide6.QtWidgets import QFileDialog
        from pathlib import Path as _Path
        from ...core import refresh as _refresh
        d = QFileDialog.getExistingDirectory(
            self, "选项目总目录（含多个项目子目录）",
        )
        if not d:
            return
        try:
            added = _refresh.add_scan_root(self.cfg, _Path(d))
        except Exception as e:
            QMessageBox.warning(self, "新增失败", f"{type(e).__name__}: {e}")
            return
        if added:
            QMessageBox.information(self, "已新增", f"已加入：{d}\n\n首页点「⟳ 刷新本机项目」立即扫描。")
        else:
            QMessageBox.information(self, "已在列表", f"目录已在列表里：{d}")
        self._refresh_scan_roots()

    def _on_scan_root_remove(self) -> None:
        """P17：删除一个扫描根目录。"""
        from PySide6.QtWidgets import QInputDialog
        from ...core import refresh as _refresh
        roots = _refresh.load_scan_roots(self.cfg)
        if not roots:
            QMessageBox.information(self, "无配置", "扫描根目录列表为空。")
            return
        paths = [r.get("path", "") for r in roots]
        p, ok = QInputDialog.getItem(
            self, "删除扫描根目录", "选要删除的目录：", paths, 0, False,
        )
        if not ok:
            return
        if _refresh.remove_scan_root(self.cfg, p):
            QMessageBox.information(self, "完成", f"已删除：{p}")
        self._refresh_scan_roots()

    def _refresh_channels(self) -> None:
        """P15 T2：刷新订阅列表显示。"""
        from ...core import channels as ch
        try:
            chans = ch.load_channels(self.cfg)
        except Exception as e:
            self.channel_status.setText(f"读取失败：{e}")
            return
        if not chans:
            self.channel_status.setText("（还没订阅任何 channel）")
            return
        lines = []
        for c in chans:
            last = (c.last_pull or "—")[:19]
            lines.append(f"· {c.name}  {c.git_url}  · 上次拉：{last}")
        self.channel_status.setText("\n".join(lines))

    def _on_channel_add(self) -> None:
        """P15 T2：弹对话框新增订阅。"""
        from PySide6.QtWidgets import QInputDialog
        name, ok = QInputDialog.getText(
            self, "新增订阅",
            "频道名（短标识，仅字母数字_-）：",
        )
        if not ok or not name.strip():
            return
        url, ok = QInputDialog.getText(
            self, "新增订阅",
            "git 仓库 URL（支持 https / ssh / file://）：",
        )
        if not ok or not url.strip():
            return
        from ...core import channels as ch
        try:
            c = ch.add_channel(self.cfg, name.strip(), url.strip())
        except Exception as e:
            QMessageBox.warning(self, "新增失败", f"{type(e).__name__}: {e}")
            return
        ans = QMessageBox.question(
            self, "已订阅", f"{c.name} → {c.git_url}\n\n立即拉一次？",
        )
        if ans == QMessageBox.StandardButton.Yes:
            self._pull_single_channel(c)
        self._refresh_channels()

    def _on_channel_pull_all(self) -> None:
        """P15 T2：拉所有订阅。"""
        from ...core import channels as ch
        chans = ch.load_channels(self.cfg)
        if not chans:
            QMessageBox.information(self, "无订阅", "还没订阅任何 channel。")
            return
        total_new = 0
        errors: list[str] = []
        from PySide6.QtCore import Qt as _Qt
        from PySide6.QtGui import QCursor
        from PySide6.QtWidgets import QApplication
        QApplication.setOverrideCursor(QCursor(_Qt.CursorShape.WaitCursor))
        try:
            for c in chans:
                r = ch.pull_channel(self.cfg, c)
                if r.get("error"):
                    errors.append(f"{c.name}：{r['error']}")
                else:
                    total_new += r.get("new_in_inbox", 0)
        finally:
            QApplication.restoreOverrideCursor()
        msg = f"完成。共 {total_new} 条新内容进 inbox 等审。"
        if errors:
            msg += "\n\n失败：\n" + "\n".join(errors[:5])
        QMessageBox.information(self, "拉取完成", msg)
        self._refresh_channels()

    def _pull_single_channel(self, channel) -> None:
        from ...core import channels as ch
        from PySide6.QtCore import Qt as _Qt
        from PySide6.QtGui import QCursor
        from PySide6.QtWidgets import QApplication
        QApplication.setOverrideCursor(QCursor(_Qt.CursorShape.WaitCursor))
        try:
            r = ch.pull_channel(self.cfg, channel)
        finally:
            QApplication.restoreOverrideCursor()
        if r.get("error"):
            QMessageBox.warning(self, "拉取失败", r["error"])
            return
        QMessageBox.information(
            self, "拉取完成",
            f"{channel.name}：扫到 {r['pulled_n']} 条，"
            f"{r['new_in_inbox']} 条新内容已进 inbox。\n\n"
            f"打开「我的库 → 待审」查看 / 审核。",
        )

    def _on_channel_remove(self) -> None:
        """P15 T2：取消订阅。"""
        from ...core import channels as ch
        from PySide6.QtWidgets import QInputDialog
        chans = ch.load_channels(self.cfg)
        if not chans:
            QMessageBox.information(self, "无订阅", "没有可取消的订阅。")
            return
        names = [c.name for c in chans]
        name, ok = QInputDialog.getItem(
            self, "取消订阅", "选择要取消的频道：", names, 0, False,
        )
        if not ok:
            return
        ans = QMessageBox.question(
            self, "确认", f"取消订阅 {name}？\n（会同时删除 sandbox 目录）",
        )
        if ans != QMessageBox.StandardButton.Yes:
            return
        if ch.remove_channel(self.cfg, name):
            QMessageBox.information(self, "完成", f"已取消：{name}")
        self._refresh_channels()

    def _refresh_sync_status(self) -> None:
        """P14 T3 + P20：显示 git 同步状态。git subprocess 挪到后台 thread，不阻塞 UI。"""
        vault = self.cfg.vault_path
        if not (vault / ".git").is_dir():
            self.sync_status.setText("未启用 git——vault 还没初始化版本控制。先跑 `prompt-help init`。")
            return
        # 先显示"加载中"，再启 thread
        self.sync_status.setText("检查 git 状态中…")
        self._sync_thread = _GitStatusThread(vault)
        self._sync_thread.done.connect(self._on_sync_status_ready)
        self._sync_thread.start()

    def _on_sync_status_ready(self, info: dict) -> None:
        """P20：后台 thread 完成回调。"""
        if info.get("error"):
            self.sync_status.setText(f"git 检查失败：{info['error']}")
            return
        if not info.get("remotes"):
            self.sync_status.setText(
                f"🔌 本地仓（未配远程）\n"
                f"待提交：{info['uncommitted']}\n"
                f"最近：{info['last_commit']}\n\n"
                f"配远程：CLI 跑 `prompt-help link-remote <git-url>`"
            )
            return
        self.sync_status.setText(
            f"🔗 远程：{info['remotes'][0]}\n"
            f"待提交：{info['uncommitted']}\n"
            f"最近：{info['last_commit']}"
        )

    def _on_sync_now(self) -> None:
        """P14 T3：一键 pull --rebase + push。冲突时提示用户去命令行。"""
        from pathlib import Path
        vault = self.cfg.vault_path
        if not (vault / ".git").is_dir():
            QMessageBox.warning(self, "无 git 仓", "vault 没初始化 git。先跑 `prompt-help init`。")
            return
        self.btn_sync_now.setEnabled(False)
        self.btn_sync_now.setText("同步中…")
        try:
            # 先 commit 本地变动（如果有）
            proc.run(["git", "-C", str(vault), "add", "-A"], timeout=10)
            proc.run(
                ["git", "-C", str(vault), "commit", "-q", "-m", "manual sync"],
                timeout=10,
            )
            # pull --rebase
            r = proc.run(
                ["git", "-C", str(vault), "pull", "--rebase"],
                capture_output=True, text=True, timeout=30,
            )
            if r.returncode != 0:
                QMessageBox.warning(
                    self, "pull 失败 / 有冲突",
                    f"git pull --rebase 退出码 {r.returncode}：\n\n{r.stderr[:600]}\n\n"
                    f"打开终端到 vault 目录手动解决冲突：\n{vault}",
                )
                return
            # push
            r2 = proc.run(
                ["git", "-C", str(vault), "push"],
                capture_output=True, text=True, timeout=30,
            )
            if r2.returncode != 0:
                QMessageBox.warning(
                    self, "push 失败",
                    f"git push 退出码 {r2.returncode}：\n\n{r2.stderr[:600]}",
                )
                return
            QMessageBox.information(self, "同步完成", "git pull --rebase + push 都成功。")
        finally:
            self.btn_sync_now.setEnabled(True)
            self.btn_sync_now.setText("立即 pull + push")
            self._refresh_sync_status()

    def _refresh_tc_stats(self) -> None:
        """P12-N2：显示翻译缓存统计。"""
        from ...core.translation_cache import TranslationCache
        try:
            cache = TranslationCache(self.cfg)
            s = cache.stats()
            if s["total"] == 0:
                self.tc_status.setText("空（还没有翻译过任何内容）")
                return
            oldest_str = "—"
            if s["oldest"]:
                from datetime import datetime, timezone
                oldest_dt = datetime.fromtimestamp(s["oldest"], tz=timezone.utc).astimezone()
                oldest_str = oldest_dt.strftime("%Y-%m-%d %H:%M")
            self.tc_status.setText(f"{s['total']} 条 · 最早 {oldest_str}")
        except Exception as e:
            self.tc_status.setText(f"读取失败：{e}")

    def _on_tc_cleanup(self) -> None:
        """P12-N2：清理 30 天以上的翻译缓存。"""
        from ...core.translation_cache import TranslationCache
        try:
            cache = TranslationCache(self.cfg)
            n = cache.cleanup_expired(ttl_days=30)
            QMessageBox.information(
                self, "已清理",
                f"删除了 {n} 条过期翻译缓存（TTL 30 天）。",
            )
            self._refresh_tc_stats()
        except Exception as e:
            QMessageBox.warning(self, "清理失败", f"{type(e).__name__}: {e}")

    def _on_uninstall_plugin(self) -> None:
        dst = Path.home() / ".claude" / "plugins" / "prompt-help"
        if not dst.exists():
            QMessageBox.information(self, "未安装", "插件目录不存在，无需卸载。")
            return
        ans = QMessageBox.question(self, "确认卸载", f"删除 {dst} ？")
        if ans != QMessageBox.StandardButton.Yes:
            return
        try:
            shutil.rmtree(dst)
            QMessageBox.information(self, "已卸载", "插件已删除。重启 Claude Code 后完全生效。")
        except Exception as e:
            QMessageBox.warning(self, "卸载失败", str(e))
        self._refresh_plugin_status()


class _GitStatusThread(QThread):
    """P20：git status 后台线程——避免阻塞 UI 主线程。"""

    done = Signal(dict)

    def __init__(self, vault: Path):
        super().__init__()
        self.vault = vault

    def run(self) -> None:
        info: dict = {"remotes": [], "uncommitted": 0, "last_commit": "—", "error": None}
        try:
            r = proc.run(
                ["git", "-C", str(self.vault), "remote", "-v"],
                capture_output=True, text=True, timeout=3,
            )
            remotes = [l for l in r.stdout.splitlines() if l.strip()]
            info["remotes"] = [
                (line.split()[1] if len(line.split()) > 1 else "?") for line in remotes
            ]
            r2 = proc.run(
                ["git", "-C", str(self.vault), "status", "--porcelain"],
                capture_output=True, text=True, timeout=3,
            )
            info["uncommitted"] = len([l for l in r2.stdout.splitlines() if l.strip()])
            r3 = proc.run(
                ["git", "-C", str(self.vault), "log", "-1", "--format=%cr · %s"],
                capture_output=True, text=True, timeout=3,
            )
            info["last_commit"] = r3.stdout.strip() or "（无 commit）"
        except Exception as e:
            info["error"] = f"{type(e).__name__}: {e}"
        self.done.emit(info)


class _BackendTestWorker(QThread):
    """后端连通性测试：跑最小 prompt 验证 backend 真能调通。

    Phase 22 用户视角评测：用户在 settings 切了后端后，没办法快速验证后端是否真通；
    现在加一个测试按钮，跑一个 5 token 的最小输入，确认能正常返回。
    """

    done = Signal(str, bool, str)  # backend, ok, msg

    _MIN_PROMPT = "回答'PH 测试通过'六个字。不要任何其他内容。"

    def __init__(self, cfg: Config, backend: str):
        super().__init__()
        self.cfg = cfg
        self.backend = backend

    def run(self) -> None:
        from ...core import optimizer
        try:
            result = optimizer.optimize(self.cfg, self._MIN_PROMPT, mode=self.backend)
            if result.success and result.optimized:
                preview = result.optimized.strip().splitlines()[0][:60]
                self.done.emit(self.backend, True, f"返回 '{preview}'")
            else:
                err = result.error or "返回空"
                self.done.emit(self.backend, False, err[:200])
        except Exception as e:
            self.done.emit(self.backend, False, f"{type(e).__name__}: {e}"[:200])
