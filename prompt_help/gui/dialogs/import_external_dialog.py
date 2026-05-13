"""从外部 URL / 文件导入 prompt（Phase 9 T6）。

流程：
1. 用户输入 URL 或选文件
2. 抓取 / 读取原始内容（HTML/MD/JSON/TXT）
3. LLM 自动识别其中可用的 prompt 段落（含标题、正文）
4. 落到 library_cache/<source_id>.json，刷新推荐库 tab
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
import urllib.error
import urllib.request
from dataclasses import asdict
from html.parser import HTMLParser
from pathlib import Path

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QDialog, QFileDialog, QHBoxLayout, QLabel, QLineEdit, QMessageBox,
    QPlainTextEdit, QProgressBar, QPushButton, QRadioButton, QVBoxLayout,
)

from ...cli import public_library as pub
from ...core import optimizer
from ...core.config import Config


class _HTMLToText(HTMLParser):
    """从 HTML 抽 <body> 内文本节点，跳过 script/style/noscript。"""

    SKIP_TAGS = {"script", "style", "noscript", "iframe", "head", "meta", "link"}
    BLOCK_TAGS = {"p", "div", "li", "h1", "h2", "h3", "h4", "h5", "h6", "br",
                  "tr", "section", "article", "header", "footer", "pre", "blockquote"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self._skip_depth = 0
        self._chunks: list[str] = []

    def handle_starttag(self, tag, _attrs):
        if tag in self.SKIP_TAGS:
            self._skip_depth += 1
        elif tag in self.BLOCK_TAGS:
            self._chunks.append("\n")

    def handle_endtag(self, tag):
        if tag in self.SKIP_TAGS and self._skip_depth > 0:
            self._skip_depth -= 1
        elif tag in self.BLOCK_TAGS:
            self._chunks.append("\n")

    def handle_data(self, data):
        if self._skip_depth == 0:
            stripped = data.strip()
            if stripped:
                self._chunks.append(stripped + " ")

    def get_text(self) -> str:
        text = "".join(self._chunks)
        # 折叠多余空行
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()


def _extract_text_from_html(html: str) -> str:
    parser = _HTMLToText()
    try:
        parser.feed(html)
    except Exception:
        # 解析失败兜底——回退正则
        text = re.sub(r"<script[^>]*>.*?</script>", "", html, flags=re.S | re.I)
        text = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.S | re.I)
        text = re.sub(r"<[^>]+>", "\n", text)
        return re.sub(r"\n{3,}", "\n\n", text)
    return parser.get_text()


EXTRACT_PROMPTS_SYSTEM_PROMPT = """\
你是提示词工程师。下面是用户提供的文本（可能来自网页 / Markdown / 自由文本）。
任务：从中识别所有可作为 AI 提示词复用的段落。

## 识别规则
- 必须有明确的「指令性」语气（你是 X、请你 X、Act as、I want you to 等）
- 必须有可复用价值（不是一次性的对话碎片、错误日志、无意义闲聊）
- 长度 ≥ 50 字，≤ 4000 字
- 每段独立——一条 prompt 一个对象，不要把多个混成一条

## 输出格式（严格 JSON）

只输出以下结构的 JSON 数组，不要任何 markdown 包裹、不要解释：
[
  {"title": "短标题，10-30 字", "body": "完整提示词正文"},
  ...
]

如果文本里没有任何可识别的 prompt，输出空数组 []。
"""


class _ExtractThread(QThread):
    """后台抓取 + LLM 提取。"""

    progress = Signal(str)
    done = Signal(list, str)  # (extracted prompts, error or empty)

    def __init__(self, cfg: Config, source_type: str, source_value: str):
        super().__init__()
        self.cfg = cfg
        self.source_type = source_type  # "url" | "file"
        self.source_value = source_value

    def run(self) -> None:
        try:
            self.progress.emit("读取内容…")
            content = self._fetch_content()
            if not content.strip():
                self.done.emit([], "读到的内容为空")
                return
            self.progress.emit(f"已读取 {len(content)} 字符，LLM 提取中…")
            extracted = self._llm_extract(content)
            self.done.emit(extracted, "")
        except Exception as e:
            self.done.emit([], f"{type(e).__name__}: {e}")

    def _fetch_content(self) -> str:
        if self.source_type == "url":
            req = urllib.request.Request(
                self.source_value,
                headers={"User-Agent": "prompt-help-importer/0.1"},
            )
            with urllib.request.urlopen(req, timeout=20) as r:
                raw = r.read().decode("utf-8", errors="replace")
            # A8：用标准库 html.parser 提取 <body> 内文本，避开正则去标签的脆弱性
            return _extract_text_from_html(raw)
        path = Path(self.source_value)
        return path.read_text(encoding="utf-8", errors="replace")

    def _llm_extract(self, content: str) -> list[dict]:
        # 控制 token：长内容截 12K 字符
        if len(content) > 12000:
            content = content[:12000] + "\n\n…（内容截断）"
        # 复用 optimizer 的 _run 路径
        result = optimizer._run(
            self.cfg, content,
            system_prompt=EXTRACT_PROMPTS_SYSTEM_PROMPT, mode="auto",
        )
        if not result.success:
            raise RuntimeError(f"LLM 提取失败：{result.error}")
        raw = result.optimized.strip()
        # 抽出 JSON 数组
        start = raw.find("[")
        end = raw.rfind("]")
        if start < 0 or end <= start:
            raise RuntimeError(f"LLM 返回不含 JSON 数组：{raw[:300]}")
        try:
            data = json.loads(raw[start: end + 1])
        except json.JSONDecodeError as e:
            raise RuntimeError(f"JSON 解析失败：{e}\n\n原始返回：{raw[:300]}") from e
        if not isinstance(data, list):
            raise RuntimeError("LLM 输出不是数组")
        return data


class ImportExternalDialog(QDialog):
    def __init__(self, cfg: Config, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self.setWindowTitle("📥 从网页 / 文件导入")
        self.resize(640, 520)
        self._extracted: list[dict] = []
        self._build()

    def _build(self) -> None:
        v = QVBoxLayout(self)
        v.setContentsMargins(20, 18, 20, 16)
        v.setSpacing(10)

        title = QLabel("从外部来源导入 prompt")
        title.setStyleSheet("font-size: 15px; font-weight: 600; color: #0a0a0a;")
        v.addWidget(title)

        hint = QLabel(
            "贴一个网页 URL 或选个本地文件（.md / .txt / .html / .json），"
            "LLM 会自动识别其中可作为提示词复用的段落，"
            "整理后加入推荐库。"
        )
        hint.setStyleSheet("color: #525252; font-size: 12px; line-height: 1.5;")
        hint.setWordWrap(True)
        v.addWidget(hint)

        # 来源选择
        type_row = QHBoxLayout()
        self.radio_url = QRadioButton("网页 URL")
        self.radio_url.setChecked(True)
        self.radio_file = QRadioButton("本地文件")
        type_row.addWidget(self.radio_url)
        type_row.addWidget(self.radio_file)
        type_row.addStretch(1)
        v.addLayout(type_row)

        input_row = QHBoxLayout()
        self.input_field = QLineEdit()
        self.input_field.setPlaceholderText("https://… 或 文件路径")
        input_row.addWidget(self.input_field, 1)
        self.btn_browse = QPushButton("浏览…")
        self.btn_browse.clicked.connect(self._on_browse)
        input_row.addWidget(self.btn_browse)
        v.addLayout(input_row)

        self.btn_extract = QPushButton(" LLM 识别 prompt")
        self.btn_extract.setProperty("class", "primary")
        from ...gui import icons as _icons
        from PySide6.QtCore import QSize as _QSize
        self.btn_extract.setIcon(_icons.icon_white("generalize"))
        self.btn_extract.setIconSize(_QSize(14, 14))
        self.btn_extract.clicked.connect(self._on_extract)
        v.addWidget(self.btn_extract)

        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setVisible(False)
        v.addWidget(self.progress)

        self.status = QLabel("")
        self.status.setStyleSheet("color: #737373; font-size: 12px;")
        v.addWidget(self.status)

        self.preview = QPlainTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setPlaceholderText("识别结果会显示在这里…")
        self.preview.setStyleSheet(
            "QPlainTextEdit { background: #fafafa; border: 0; "
            "border-radius: 6px; padding: 8px; font-size: 12px; }"
        )
        v.addWidget(self.preview, 1)

        bottom = QHBoxLayout()
        bottom.addStretch(1)
        self.btn_cancel = QPushButton("取消")
        self.btn_cancel.clicked.connect(self.reject)
        bottom.addWidget(self.btn_cancel)
        self.btn_save = QPushButton("加入推荐库")
        self.btn_save.setProperty("class", "primary")
        self.btn_save.setEnabled(False)
        self.btn_save.clicked.connect(self._on_save)
        bottom.addWidget(self.btn_save)
        v.addLayout(bottom)

    def _on_browse(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "选要导入的文件", "", "支持的格式 (*.md *.txt *.html *.json)",
        )
        if path:
            self.input_field.setText(path)
            self.radio_file.setChecked(True)

    def _on_extract(self) -> None:
        value = self.input_field.text().strip()
        if not value:
            QMessageBox.information(self, "缺输入", "请先填 URL 或选文件。")
            return
        source_type = "url" if self.radio_url.isChecked() else "file"
        self.btn_extract.setEnabled(False)
        self.progress.setVisible(True)
        self.status.setText("启动后台…")
        self._thread = _ExtractThread(self.cfg, source_type, value)
        self._thread.progress.connect(self.status.setText)
        self._thread.done.connect(self._on_extract_done)
        self._thread.start()

    def _on_extract_done(self, extracted: list, err: str) -> None:
        self.btn_extract.setEnabled(True)
        self.progress.setVisible(False)
        if err:
            self.status.setText(f"失败：{err}")
            self.preview.setPlainText(
                "（提取失败。改 URL / 文件后再点上方「LLM 识别 prompt」重试。\n\n"
                f"错误：{err[:500]}）"
            )
            # P16-T4 修：失败时让 btn_extract 醒目（可重试），保持 btn_save 灰但 btn_cancel 总能用
            self.btn_extract.setText("🔄 重新识别")
            QMessageBox.warning(
                self, "提取失败",
                f"{err}\n\n"
                "可改 URL / 文件后点上方「重新识别」重试，或取消退出。",
            )
            return
        if not extracted:
            self.status.setText("ℹ️ LLM 没识别到任何可复用 prompt（来源内容可能不含指令性段落）")
            return
        self._extracted = extracted
        self.status.setText(f"✓ 识别到 {len(extracted)} 条 prompt")
        # 预览
        lines = []
        for i, p in enumerate(extracted, 1):
            lines.append(f"#{i} 【{p.get('title', '?')}】")
            body = p.get("body", "")
            lines.append(body[:200] + ("…" if len(body) > 200 else ""))
            lines.append("")
        self.preview.setPlainText("\n".join(lines))
        self.btn_save.setEnabled(True)

    def _on_save(self) -> None:
        if not self._extracted:
            return
        value = self.input_field.text().strip()
        h = hashlib.sha256(value.encode("utf-8")).hexdigest()[:10]
        source_type = "url" if self.radio_url.isChecked() else "file"
        source_id = f"external-{source_type}-{h}"
        source_name = f"外部导入：{value[:40]}{'…' if len(value) > 40 else ''}"

        # 拼装 PublicPrompt 列表落到 library_cache
        prompts = []
        for i, p in enumerate(self._extracted):
            prompts.append(pub.PublicPrompt(
                id=f"{source_id}-{i:04d}",
                title=str(p.get("title", "untitled"))[:80],
                body=str(p.get("body", "")),
                source_id=source_id,
                source_name=source_name,
                language="zh" if self._guess_zh(p.get("body", "")) else "en",
                categories=["通用工程"],
            ))

        cache_dir = self.cfg.vault_path / "library_cache"
        cache_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "source_id": source_id,
            "source_name": source_name,
            "language": "mixed",
            "fetched_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            "n": len(prompts),
            "prompts": [asdict(p) for p in prompts],
            "external_source": value,
        }
        (cache_dir / f"{source_id}.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        QMessageBox.information(
            self, "完成",
            f"已加入推荐库：{len(prompts)} 条来自「{source_name}」\n\n"
            f"在「📦 推荐库」tab 找新源即可勾选导入。",
        )
        self.accept()

    @staticmethod
    def _guess_zh(text: str) -> bool:
        cjk = sum(1 for c in text if "一" <= c <= "鿿")
        return cjk / max(len(text), 1) > 0.30
