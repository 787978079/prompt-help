"""OpenAI Codex CLI 历史适配器。

Codex CLI 的存档位置不同版本不同。运行时探测：
  - ~/.codex/sessions/*.jsonl 或 *.json
  - ~/.config/codex/sessions/*
  - ~/.local/share/codex/*
  - ~/AppData/Roaming/codex/sessions/*

格式探测：JSONL（按行 JSON）或 JSON（整文件 dict 含 messages 数组）。

兼容多种 schema（OpenAI ChatCompletion 标准的 {role, content[]} 是最常见）。
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Iterable

from .base import RawMessage, TranscriptAdapter


_CODEX_PATH_CANDIDATES = [
    "~/.codex/sessions",
    "~/.codex/history",
    "~/.config/codex/sessions",
    "~/.local/share/codex/sessions",
    "~/AppData/Roaming/codex/sessions",
    "~/AppData/Local/codex/sessions",
]


class CodexAdapter(TranscriptAdapter):
    name = "codex"
    display_name = "Codex 历史"

    def __init__(self, root: Path | None = None):
        self.root: Path | None = root.expanduser().resolve() if root else None
        if self.root is None:
            self.root = self._probe_root()

    def _probe_root(self) -> Path | None:
        for cand in _CODEX_PATH_CANDIDATES:
            p = Path(cand).expanduser()
            if p.is_dir():
                return p.resolve()
        return None

    def detect(self) -> bool:
        return self.root is not None and self.root.is_dir() and self._has_session_files()

    def _has_session_files(self) -> bool:
        if not self.root:
            return False
        return any(self.root.rglob("*.jsonl")) or any(self.root.rglob("*.json"))

    def walk(self) -> Iterable[RawMessage]:
        if not self.root or not self.root.is_dir():
            return
        for f in sorted(self.root.rglob("*")):
            if not f.is_file() or f.suffix.lower() not in (".jsonl", ".json"):
                continue
            try:
                date_str = dt.datetime.fromtimestamp(
                    f.stat().st_mtime
                ).strftime("%Y-%m-%d")
            except Exception:
                date_str = "0000-00-00"
            project_name = "codex"  # Codex CLI 不绑定项目目录，统一归类
            session_id = f.stem
            try:
                if f.suffix.lower() == ".jsonl":
                    yield from _walk_jsonl(f, project_name, session_id, date_str)
                else:
                    yield from _walk_json(f, project_name, session_id, date_str)
            except Exception:
                continue


def _walk_jsonl(f: Path, project: str, session: str, date: str) -> Iterable[RawMessage]:
    for i, line in enumerate(f.read_text(encoding="utf-8", errors="replace").splitlines()):
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except Exception:
            continue
        msg = _normalize_message(obj)
        if msg:
            role, text = msg
            yield RawMessage(
                role=role, text=text,
                source_session=session, source_project=project,
                source_date=date, source_path=f, line_index=i,
            )


def _walk_json(f: Path, project: str, session: str, date: str) -> Iterable[RawMessage]:
    try:
        data = json.loads(f.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return
    # 多种可能的顶层结构：{messages: [...]}, {history: [...]}, [...]
    msgs = None
    if isinstance(data, dict):
        for key in ("messages", "history", "transcript", "conversation"):
            if isinstance(data.get(key), list):
                msgs = data[key]
                break
    elif isinstance(data, list):
        msgs = data
    if not isinstance(msgs, list):
        return
    for i, obj in enumerate(msgs):
        msg = _normalize_message(obj)
        if msg:
            role, text = msg
            yield RawMessage(
                role=role, text=text,
                source_session=session, source_project=project,
                source_date=date, source_path=f, line_index=i,
            )


def _normalize_message(obj) -> tuple[str, str] | None:
    """从异构 message 对象抽出 (role, text)。"""
    if not isinstance(obj, dict):
        return None
    role = obj.get("role") or (obj.get("message") or {}).get("role")
    if role not in ("user", "assistant"):
        return None
    content = obj.get("content")
    if content is None:
        m = obj.get("message")
        if isinstance(m, dict):
            content = m.get("content")
    if isinstance(content, str):
        text = content.strip()
    elif isinstance(content, list):
        parts = []
        for blk in content:
            if isinstance(blk, dict):
                t = blk.get("type") or blk.get("kind")
                if t in ("text", None) and isinstance(blk.get("text"), str):
                    parts.append(blk["text"])
        text = "\n\n".join(p.strip() for p in parts if p.strip())
    else:
        return None
    if not text:
        return None
    return role, text
