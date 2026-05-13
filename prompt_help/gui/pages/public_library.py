"""推荐库页（Phase 7 重写 + Phase 21 中文默认）：浏览公开提示词源、勾选入库、自动翻译。

设计原则：
- 用户大多是中文人，刷完源默认自动批量翻译落缓存（Phase 21 起新默认）。
  可在「设置」关 cfg.public_library.auto_translate_on_refresh 回到按需翻译模式。
- 卡片优先显示中文版（命中缓存秒回）；正在翻译时显示"⏳ 翻译中…"；
  失败 / 已关自动翻译时显示"⚠ 未翻译"+ 单条翻译按钮。
- 翻译走 optimizer.translate_to_zh + translation_cache，命中缓存 < 100ms。
- _RefreshThread 用 pub.refresh_sources 返回的结构化 results，失败按源分组显示。
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QCursor
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QFrame, QHBoxLayout, QLabel,
    QMessageBox, QPushButton, QScrollArea, QSizePolicy, QVBoxLayout, QWidget,
)

from ...cli import public_library as pub
from ...core import classify, indexer, optimizer, storage
from ...core.config import Config


class PublicLibraryPage(QWidget):
    def __init__(self, cfg: Config):
        super().__init__()
        self.cfg = cfg
        self.all_items: list[pub.PublicPrompt] = []
        self.checkboxes: list[tuple[QCheckBox, pub.PublicPrompt]] = []
        self._build()
        self._reload_cache()

    def _build(self) -> None:
        v = QVBoxLayout(self)
        v.setContentsMargins(40, 28, 40, 24)
        v.setSpacing(10)

        title = QLabel("推荐库")
        title.setObjectName("pageTitle")
        v.addWidget(title)

        hint = QLabel(
            "**外部公开提示词源**（awesome-claude-prompts / cursorrules / 中文版 ChatGPT 等）。"
            "刷新源后会**自动翻译为中文**（写入缓存，下次秒回）；可在「设置」关闭。"
        )
        hint.setObjectName("pageHint")
        hint.setTextFormat(Qt.TextFormat.MarkdownText)
        hint.setWordWrap(True)
        v.addWidget(hint)

        bar = QHBoxLayout()
        bar.setSpacing(8)

        from .. import icons as _icons
        from PySide6.QtCore import QSize as _QSize

        self.btn_refresh = QPushButton(" 刷新源")
        self.btn_refresh.setProperty("class", "subtle")
        self.btn_refresh.setIcon(_icons.icon("refresh"))
        self.btn_refresh.setIconSize(_QSize(14, 14))
        self.btn_refresh.setToolTip("从远程 GitHub 拉最新内容（需要网络）")
        self.btn_refresh.clicked.connect(self._on_refresh)
        bar.addWidget(self.btn_refresh)

        self.btn_translate_all = QPushButton(" 批量翻译全部")
        self.btn_translate_all.setProperty("class", "subtle")
        self.btn_translate_all.setIcon(_icons.icon("translate"))
        self.btn_translate_all.setIconSize(_QSize(14, 14))
        self.btn_translate_all.setToolTip(
            "把当前可见的所有英文 prompt 翻译成中文（写入缓存，后续秒回）。"
            "每条 ~10s，调用 LLM 后端"
        )
        self.btn_translate_all.clicked.connect(self._on_translate_all)
        bar.addWidget(self.btn_translate_all)

        self.btn_import_external = QPushButton(" 从网页 / 文件导入")
        self.btn_import_external.setProperty("class", "subtle")
        self.btn_import_external.setIcon(_icons.icon("import_external"))
        self.btn_import_external.setIconSize(_QSize(14, 14))
        self.btn_import_external.setToolTip(
            "贴一个 URL 或选个文件，LLM 自动识别其中可用的 prompt"
        )
        self.btn_import_external.clicked.connect(self._on_import_external)
        bar.addWidget(self.btn_import_external)

        self.source_combo = QComboBox()
        self.source_combo.setMinimumWidth(220)
        self.source_combo.currentIndexChanged.connect(lambda _: self._render_cards())
        bar.addWidget(self.source_combo)

        bar.addStretch(1)

        self.label_count = QLabel("")
        self.label_count.setStyleSheet("color: #737373; font-size: 12px;")
        bar.addWidget(self.label_count)

        self.btn_select_all = QPushButton("全选")
        self.btn_select_all.setProperty("class", "subtle")
        self.btn_select_all.clicked.connect(self._on_select_all)
        bar.addWidget(self.btn_select_all)

        self.btn_clear = QPushButton("清空选择")
        self.btn_clear.setProperty("class", "subtle")
        self.btn_clear.clicked.connect(self._on_clear_selection)
        bar.addWidget(self.btn_clear)

        self.btn_import = QPushButton("加入选中到我的库")
        self.btn_import.setProperty("class", "primary")
        self.btn_import.clicked.connect(self._on_import_selected)
        bar.addWidget(self.btn_import)

        v.addLayout(bar)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.cards_host = QWidget()
        self.cards_layout = QVBoxLayout(self.cards_host)
        self.cards_layout.setContentsMargins(0, 0, 0, 0)
        self.cards_layout.setSpacing(8)
        self.scroll.setWidget(self.cards_host)
        v.addWidget(self.scroll, 1)

    # ------------------------------------------------------------------
    # 数据
    # ------------------------------------------------------------------

    _last_show_ts: float = 0.0

    def on_show(self) -> None:
        # P21：30 秒节流——切 tab 来回时不重复 prefetch 整张 translation_cache
        # + 不重建 200 张卡片，这是用户感受到的 public_library 卡顿主因之一
        import time
        now = time.time()
        if now - self._last_show_ts < 30:
            return
        self._last_show_ts = now
        self._reload_cache()

    def hideEvent(self, event) -> None:
        """A5：用户切离推荐库时停掉批量翻译 thread。"""
        try:
            if hasattr(self, "_batch_thread") and self._batch_thread is not None:
                if self._batch_thread.isRunning():
                    self._batch_thread.cancel()
        except Exception:
            pass
        super().hideEvent(event)

    def _reload_cache(self) -> None:
        self.all_items = pub._load_cache(self.cfg)
        self.source_combo.blockSignals(True)
        self.source_combo.clear()
        self.source_combo.addItem("全部源", "")
        sources_seen: dict[str, str] = {}
        for it in self.all_items:
            if it.source_id not in sources_seen:
                sources_seen[it.source_id] = it.source_name
        for sid, sname in sorted(sources_seen.items()):
            n = sum(1 for it in self.all_items if it.source_id == sid)
            self.source_combo.addItem(f"{sname}（{n} 条）", sid)
        self.source_combo.blockSignals(False)
        self._render_cards()

    def _is_batch_translating(self) -> bool:
        t = getattr(self, "_batch_thread", None)
        return t is not None and t.isRunning()

    def _render_cards(self) -> None:
        while self.cards_layout.count():
            w = self.cards_layout.takeAt(0).widget()
            if w:
                w.deleteLater()
        self.checkboxes = []

        target_source = self.source_combo.currentData() or ""
        items = self.all_items
        if target_source:
            items = [it for it in items if it.source_id == target_source]

        # A4：批量预热翻译缓存——一次 SQL 拿全部 hash 集合，_make_card 命中 dict 即可
        self._tx_cache_prefetched: set[str] = set()
        try:
            from ...core.translation_cache import TranslationCache, hash_text
            cache = TranslationCache(self.cfg)
            import sqlite3
            with sqlite3.connect(cache.db_path) as c:
                all_hashes = {row[0] for row in c.execute("SELECT hash FROM translations")}
            wanted = {hash_text(it.body.strip()) for it in items[:200] if it.language != "zh"}
            self._tx_cache_prefetched = wanted & all_hashes
            # 同时把命中条目的中文加载进内存 dict
            self._tx_zh_cache: dict[str, str] = {}
            if self._tx_cache_prefetched:
                with sqlite3.connect(cache.db_path) as c:
                    placeholders = ",".join("?" * len(self._tx_cache_prefetched))
                    rows = c.execute(
                        f"SELECT hash, zh FROM translations WHERE hash IN ({placeholders})",
                        tuple(self._tx_cache_prefetched),
                    ).fetchall()
                    for h, zh in rows:
                        self._tx_zh_cache[h] = zh
        except Exception:
            self._tx_zh_cache = {}

        self.label_count.setText(f"共 {len(items)} 条")

        if not items:
            empty = QLabel(
                "缓存为空。点「刷新源」从远程 GitHub 拉一次（约 5-10 秒）。\n\n"
                "成功后这里会列出 awesome-claude-prompts、cursorrules、中文版 ChatGPT 等公开提示词。"
            )
            empty.setStyleSheet(
                "color: #737373; padding: 40px; font-size: 13px;"
                "background: #fafafa; border-radius: 10px; line-height: 1.7;"
            )
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            empty.setWordWrap(True)
            self.cards_layout.addWidget(empty)
            self.cards_layout.addStretch(1)
            return

        for it in items[:200]:
            self.cards_layout.addWidget(self._make_card(it))
        self.cards_layout.addStretch(1)

    def _make_card(self, item: pub.PublicPrompt) -> QFrame:
        # P22：去掉默认 #fafafa 背景——浅灰在白底页上勾出"虚拟边框"被用户排斥。
        # 改为完全透明，hover 时才出现淡灰背景，卡片之间靠 layout spacing 分隔。
        f = QFrame()
        f.setFrameShape(QFrame.Shape.NoFrame)
        f.setStyleSheet(
            "QFrame { background: transparent; border: 0; border-radius: 10px; }"
            "QFrame:hover { background-color: #fafafa; }"
        )

        h = QHBoxLayout(f)
        h.setContentsMargins(16, 12, 16, 12)
        h.setSpacing(12)

        cb = QCheckBox()
        cb.setStyleSheet("padding-top: 4px;")
        h.addWidget(cb)

        body_v = QVBoxLayout()
        body_v.setSpacing(4)

        head = QHBoxLayout()
        title = QLabel(item.title)
        title.setStyleSheet("font-size: 14px; font-weight: 600; color: #0a0a0a;")
        title.setWordWrap(True)
        head.addWidget(title, 1)

        lang_badge = QLabel(item.language.upper())
        lang_badge.setStyleSheet(
            "color: #525252; font-size: 11px; padding: 2px 6px; "
            "background: #f5f5f5; border-radius: 4px;"
        )
        head.addWidget(lang_badge)

        src_badge = QLabel(item.source_id)
        src_badge.setStyleSheet(
            "color: #737373; font-size: 11px; padding: 2px 8px; "
            "background: #fafafa; border-radius: 4px;"
        )
        head.addWidget(src_badge)
        body_v.addLayout(head)

        # A4：用预热的 dict 而不是同步查 SQLite（避免逐条 I/O）
        from ...core.translation_cache import hash_text
        body_raw = item.body.strip()
        if item.language == "zh":
            preview_text = body_raw
            zh_available = True
        else:
            cached_zh = getattr(self, "_tx_zh_cache", {}).get(hash_text(body_raw))
            if cached_zh:
                preview_text = cached_zh
                zh_available = True
            else:
                preview_text = body_raw
                zh_available = False

        if len(preview_text) > 200:
            preview_text = preview_text[:200] + "…"
        prev = QLabel(preview_text)
        prev.setStyleSheet("color: #525252; font-size: 12px; line-height: 1.5;")
        prev.setWordWrap(True)
        prev.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        body_v.addWidget(prev)

        if item.language != "zh" and not zh_available:
            if self._is_batch_translating():
                hint_label = QLabel("正在自动翻译为中文…（完成后这里会变中文）")
                hint_color = "#0a66c2"
            else:
                hint_label = QLabel("未翻译（点右下「翻译此条」或工具条「批量翻译全部」）")
                hint_color = "#b45309"
            hint_label.setStyleSheet(f"color: {hint_color}; font-size: 11px; padding-top: 2px;")
            body_v.addWidget(hint_label)

        action_row = QHBoxLayout()
        action_row.setContentsMargins(0, 4, 0, 0)
        action_row.setSpacing(6)
        action_row.addStretch(1)

        from .. import icons as _icons
        from PySide6.QtCore import QSize as _QSize

        btn_copy = QPushButton(" 复制原文")
        btn_copy.setProperty("class", "subtle")
        btn_copy.setIcon(_icons.icon("copy"))
        btn_copy.setIconSize(_QSize(12, 12))
        btn_copy.setStyleSheet("font-size: 11px; padding: 4px 10px;")
        btn_copy.clicked.connect(lambda _=False, it=item: self._copy_original(it))
        action_row.addWidget(btn_copy)

        if item.language != "zh":
            if zh_available:
                btn_zh = QPushButton(" 复制中文版")
                btn_zh.setIcon(_icons.icon("copy"))
                btn_zh.clicked.connect(
                    lambda _=False, it=item, b=btn_zh: self._copy_translated(it, b)
                )
            else:
                btn_zh = QPushButton(" 翻译此条")
                btn_zh.setIcon(_icons.icon("translate"))
                btn_zh.clicked.connect(
                    lambda _=False, it=item, b=btn_zh: self._translate_one(it, b)
                )
            btn_zh.setIconSize(_QSize(12, 12))
            btn_zh.setStyleSheet("font-size: 11px; padding: 4px 10px;")
            btn_zh.setProperty("class", "subtle")
            action_row.addWidget(btn_zh)

        body_v.addLayout(action_row)

        h.addLayout(body_v, 1)

        self.checkboxes.append((cb, item))
        return f

    # ------------------------------------------------------------------
    # 操作
    # ------------------------------------------------------------------

    def _on_select_all(self) -> None:
        for cb, _ in self.checkboxes:
            cb.setChecked(True)

    def _on_clear_selection(self) -> None:
        for cb, _ in self.checkboxes:
            cb.setChecked(False)

    def _on_refresh(self) -> None:
        self.btn_refresh.setEnabled(False)
        self.btn_refresh.setText("（拉取中…）")
        self._refresh_thread = _RefreshThread(self.cfg)
        self._refresh_thread.done.connect(self._on_refresh_done)
        self._refresh_thread.start()

    def _on_refresh_done(self, results: list) -> None:
        self.btn_refresh.setEnabled(True)
        self.btn_refresh.setText("刷新源")
        ok_n = sum(1 for r in results if r.get("ok"))
        lines = [f"刷新完成：{ok_n}/{len(results)} 个源拉取成功", ""]
        for r in results:
            if r.get("ok"):
                lines.append(f"  ✓ {r['name']}（{r['n']} 条）")
            else:
                lines.append(f"  ✗ {r['name']}：{r.get('error') or '未知错误'}")
        QMessageBox.information(self, "刷新结果", "\n".join(lines))
        self._reload_cache()
        # P21：刷完源默认自动开翻译，让中文用户开箱即用
        if getattr(self.cfg, "public_library", None) and self.cfg.public_library.auto_translate_on_refresh:
            self._auto_translate_after_refresh()

    def _auto_translate_after_refresh(self) -> None:
        """P21：刷新源后自动批量翻译当前缓存里所有未翻译条目（静默启动）。

        与 _on_translate_all 的差别：不弹确认对话框、不限于当前 source_combo 范围、
        受 cfg.public_library.auto_translate_max_items 上限保护。
        """
        from ...core.translation_cache import TranslationCache, hash_text
        cache = TranslationCache(self.cfg)
        max_n = self.cfg.public_library.auto_translate_max_items
        pending = [
            it for it in self.all_items
            if it.language != "zh" and cache.get(hash_text(it.body)) is None
        ][:max_n]
        if not pending:
            return
        if hasattr(self, "_batch_thread") and self._batch_thread is not None and self._batch_thread.isRunning():
            return  # 上轮还没跑完，别重叠
        self.btn_translate_all.setEnabled(False)
        self.btn_translate_all.setText(f"自动翻译 0/{len(pending)}")
        self._batch_thread = _BatchTranslateThread(self.cfg, pending)
        self._batch_thread.progress.connect(self._on_batch_progress)
        self._batch_thread.done.connect(self._on_batch_done)
        self._batch_thread.start()

    def _on_import_selected(self) -> None:
        targets = [item for cb, item in self.checkboxes if cb.isChecked()]
        if not targets:
            QMessageBox.information(self, "未选中", "先勾选要加入的提示词")
            return

        QApplication.setOverrideCursor(QCursor(Qt.CursorShape.WaitCursor))
        try:
            saved, skipped = self._save_to_vault(targets)
        finally:
            QApplication.restoreOverrideCursor()

        QMessageBox.information(
            self, "完成",
            f"已加 {saved} 条到「我的库」；跳过 {skipped} 条重名。\n\n"
            "提示：库里保留英文原文。需要中文时在「我的库」详情页点「复制中文版」即可。",
        )
        self._on_clear_selection()
        win = self.window()
        if hasattr(win, "_refresh_status"):
            win._refresh_status()

    def _save_to_vault(self, targets: list[pub.PublicPrompt]) -> tuple[int, int]:
        saved = 0
        skipped = 0
        conn = indexer.open_db(self.cfg)
        for item in targets:
            if indexer.get_by_title(conn, item.title):
                skipped += 1
                continue
            body = item.body
            cats = list(set(item.categories) | set(classify.rule_classify(body)))
            tags = [f"来源-{item.source_id}", f"语言-{item.language}"]
            p = storage.Prompt.new(
                title=item.title, body=body, scope="global",
                tags=tags, origin="github",
            )
            p.categories = cats
            p.source_url = item.id
            file_path = storage.save(
                self.cfg, p, commit_msg=f"library import GUI: {item.title[:30]}",
            )
            indexer.upsert(conn, p, file_path)
            saved += 1
        conn.close()
        return saved, skipped

    # ------------------------------------------------------------------
    # Output Language（按钮触发）
    # ------------------------------------------------------------------

    def _copy_original(self, item: pub.PublicPrompt) -> None:
        QApplication.clipboard().setText(item.body)
        self._toast(f"已复制原文：{item.title[:30]}")

    def _copy_translated(self, item: pub.PublicPrompt, btn: QPushButton) -> None:
        original_text = btn.text()
        btn.setEnabled(False)
        btn.setText("翻译中…")
        self._tx_thread = _SingleTranslateThread(self.cfg, item.body)
        self._tx_thread.done.connect(
            lambda zh, err, it=item, b=btn, ot=original_text:
                self._on_translate_done(it, b, ot, zh, err)
        )
        self._tx_thread.start()

    def _on_translate_done(
        self,
        item: pub.PublicPrompt,
        btn: QPushButton,
        original_text: str,
        zh: str,
        err: str,
    ) -> None:
        btn.setEnabled(True)
        btn.setText(original_text)
        if err:
            QMessageBox.warning(
                self, "翻译失败",
                f"翻译「{item.title[:30]}」时出错：\n\n{err}\n\n"
                "请检查 LLM 后端配置（设置 → API key 或 CC CLI 可达性）。",
            )
            return
        QApplication.clipboard().setText(zh)
        self._toast(f"已复制中文版：{item.title[:30]}")

    def _translate_one(self, item: pub.PublicPrompt, btn: QPushButton) -> None:
        """单条翻译。写缓存后刷新整页。"""
        original_text = btn.text()
        btn.setEnabled(False)
        btn.setText("翻译中…")
        self._tx_thread = _SingleTranslateThread(self.cfg, item.body)
        self._tx_thread.done.connect(
            lambda zh, err, it=item, b=btn, ot=original_text:
                self._on_single_tx_done(it, b, ot, zh, err)
        )
        self._tx_thread.start()

    def _on_single_tx_done(self, item, btn, original_text, zh, err) -> None:
        btn.setEnabled(True)
        btn.setText(original_text)
        if err or not zh:
            QMessageBox.warning(
                self, "翻译失败",
                f"翻译「{item.title[:30]}」失败：\n\n{err or '未知'}\n\n检查 LLM 后端配置。",
            )
            return
        self._toast(f"已翻译：{item.title[:30]}")
        self._render_cards()  # 刷新当前卡片显示

    def _on_translate_all(self) -> None:
        """批量翻译当前 source_combo 选中范围内所有未缓存的条目。"""
        from ...core.translation_cache import TranslationCache, hash_text
        target_source = self.source_combo.currentData() or ""
        items = self.all_items
        if target_source:
            items = [it for it in items if it.source_id == target_source]
        cache = TranslationCache(self.cfg)
        pending = [it for it in items
                   if it.language != "zh" and cache.get(hash_text(it.body)) is None]
        if not pending:
            QMessageBox.information(self, "无需翻译", "当前范围内所有条目都已翻译过。")
            return
        ans = QMessageBox.question(
            self, "批量翻译",
            f"要翻译 {len(pending)} 条未缓存条目？\n\n"
            f"预计耗时 {len(pending) * 10}s 左右（每条 ~10s），调用 LLM 后端。\n"
            f"完成后写入缓存，后续显示秒回。",
        )
        if ans != QMessageBox.StandardButton.Yes:
            return
        self.btn_translate_all.setEnabled(False)
        self.btn_translate_all.setText("翻译中…")
        self._batch_thread = _BatchTranslateThread(self.cfg, pending)
        self._batch_thread.progress.connect(self._on_batch_progress)
        self._batch_thread.done.connect(self._on_batch_done)
        self._batch_thread.start()

    def _on_batch_progress(self, done: int, total: int, title: str) -> None:
        self.btn_translate_all.setText(f"翻译 {done}/{total}：{title[:20]}")
        # P21：每 5 条让卡片渐进刷新一次，用户能眼看到中文从上到下铺开
        if done > 0 and done % 5 == 0:
            self._render_cards()

    def _on_batch_done(self, ok: int, failed: int) -> None:
        self.btn_translate_all.setEnabled(True)
        self.btn_translate_all.setText(" 批量翻译全部")
        QMessageBox.information(
            self, "批量翻译完成",
            f"成功 {ok} 条，失败 {failed} 条。刷新卡片显示。",
        )
        self._render_cards()

    def _on_import_external(self) -> None:
        """Phase 9 T6：从网页 URL 或文件导入，LLM 识别其中的 prompt。"""
        from ..dialogs.import_external_dialog import ImportExternalDialog
        dlg = ImportExternalDialog(self.cfg, parent=self)
        if dlg.exec() == dlg.DialogCode.Accepted:
            self._reload_cache()

    def _toast(self, msg: str) -> None:
        """轻量级 toast：用状态栏或标题栏临时显示。"""
        win = self.window()
        if hasattr(win, "statusBar"):
            try:
                win.statusBar().showMessage(msg, 3000)
                return
            except Exception:
                pass
        self.label_count.setText(msg)


class _BatchTranslateThread(QThread):
    """批量翻译多条 prompt（Phase 9 + A5 cancellation）。"""

    progress = Signal(int, int, str)  # (done, total, current_title)
    done = Signal(int, int)           # (ok, failed)

    def __init__(self, cfg: Config, items: list):
        super().__init__()
        self.cfg = cfg
        self.items = items
        self._cancelled = False

    def cancel(self) -> None:
        """主线程调，请求停止。下一条之前会退出。"""
        self._cancelled = True
        self.requestInterruption()

    def run(self) -> None:
        ok = 0
        failed = 0
        total = len(self.items)
        for i, item in enumerate(self.items):
            if self._cancelled or self.isInterruptionRequested():
                break
            self.progress.emit(i, total, item.title)
            try:
                r = optimizer.translate_to_zh(self.cfg, item.body)
                if r.success and r.optimized:
                    ok += 1
                else:
                    failed += 1
            except Exception:
                failed += 1
        self.done.emit(ok, failed)


class _SingleTranslateThread(QThread):
    """单条翻译。失败时 err 非空，zh 为空字符串。"""

    done = Signal(str, str)  # (zh, error)

    def __init__(self, cfg: Config, text: str):
        super().__init__()
        self.cfg = cfg
        self.text = text

    def run(self) -> None:
        try:
            r = optimizer.translate_to_zh(self.cfg, self.text)
            if r.success:
                self.done.emit(r.optimized, "")
            else:
                self.done.emit("", r.error or "未知错误")
        except Exception as e:
            self.done.emit("", f"{type(e).__name__}: {e}")


class _RefreshThread(QThread):
    """后台拉公开源；返回 pub.refresh_sources 的结构化 results。"""

    done = Signal(list)

    def __init__(self, cfg: Config):
        super().__init__()
        self.cfg = cfg

    def run(self) -> None:
        try:
            results = pub.refresh_sources(self.cfg)
        except Exception as e:
            results = [{"id": "?", "name": "(整体崩溃)", "ok": False, "n": 0,
                        "error": f"{type(e).__name__}: {e}"}]
        self.done.emit(results)
