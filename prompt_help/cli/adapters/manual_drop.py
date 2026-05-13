"""通用拖拽：用户手动拖 .md / .json / .txt 文件进 GUI 解析入库。

支持 schema：
  - .md：按 ## / ### 标题拆段，整段当一条提示词
  - .json：尝试 OpenAI ChatCompletion 的 {messages:[{role, content}]} / Aider / Continue.dev
  - .txt：整文件作为单条候选
"""

from __future__ import annotations

import datetime as dt
import json
import re
from pathlib import Path
from typing import Iterable

from .base import RawMessage, TranscriptAdapter


_HEADING_RE = re.compile(r"^(#{2,3})\s+(.+?)\s*$")


class ManualDropAdapter(TranscriptAdapter):
    name = "manual_drop"
    display_name = "手动拖拽文件"

    def __init__(self, files: list[Path]):
        self.files = [Path(f).expanduser().resolve() for f in files]

    def detect(self) -> bool:
        return bool(self.files) and any(f.is_file() for f in self.files)

    def walk(self) -> Iterable[RawMessage]:
        for f in self.files:
            if not f.is_file():
                continue
            try:
                date_str = dt.datetime.fromtimestamp(
                    f.stat().st_mtime
                ).strftime("%Y-%m-%d")
            except Exception:
                date_str = "0000-00-00"
            session_id = f.stem
            project_name = f.parent.name or "外部导入"
            ext = f.suffix.lower()
            try:
                if ext == ".md":
                    yield from _walk_md(f, project_name, session_id, date_str)
                elif ext == ".json":
                    yield from _walk_json(f, project_name, session_id, date_str)
                elif ext == ".txt":
                    yield from _walk_txt(f, project_name, session_id, date_str)
                elif ext == ".jsonl":
                    yield from _walk_jsonl(f, project_name, session_id, date_str)
            except Exception:
                continue


def _walk_md(f: Path, project: str, session: str, date: str) -> Iterable[RawMessage]:
    """按二级 / 三级标题拆段；每段当一个用户消息。"""
    text = f.read_text(encoding="utf-8", errors="replace")
    sections: list[tuple[str, list[str]]] = []
    cur_title = "untitled"
    cur_lines: list[str] = []
    in_code = False
    for line in text.splitlines():
        if line.strip().startswith("```"):
            in_code = not in_code
            cur_lines.append(line)
            continue
        if in_code:
            cur_lines.append(line)
            continue
        m = _HEADING_RE.match(line)
        if m:
            if cur_lines:
                sections.append((cur_title, cur_lines))
            cur_title = m.group(2)
            cur_lines = []
        else:
            cur_lines.append(line)
    if cur_lines:
        sections.append((cur_title, cur_lines))

    for i, (_title, lines) in enumerate(sections):
        body = "\n".join(lines).strip()
        if body:
            yield RawMessage(
                role="user", text=body,
                source_session=session, source_project=project,
                source_date=date, source_path=f, line_index=i,
            )


def _walk_txt(f: Path, project: str, session: str, date: str) -> Iterable[RawMessage]:
    text = f.read_text(encoding="utf-8", errors="replace").strip()
    if text:
        yield RawMessage(
            role="user", text=text,
            source_session=session, source_project=project,
            source_date=date, source_path=f, line_index=0,
        )


def _walk_json(f: Path, project: str, session: str, date: str) -> Iterable[RawMessage]:
    """支持多种 JSON schema。"""
    try:
        data = json.loads(f.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return
    # 顶层 messages / history / chat / transcript 数组
    msgs = None
    if isinstance(data, dict):
        for key in ("messages", "history", "chat", "transcript", "conversation",
                     "session", "events"):
            if isinstance(data.get(key), list):
                msgs = data[key]
                break
    elif isinstance(data, list):
        msgs = data
    if not isinstance(msgs, list):
        # 整 dict 当作单条 user 消息
        if isinstance(data, dict) and isinstance(data.get("content"), str):
            yield RawMessage(
                role="user", text=data["content"].strip(),
                source_session=session, source_project=project,
                source_date=date, source_path=f, line_index=0,
            )
        return
    for i, obj in enumerate(msgs):
        from .codex import _normalize_message
        m = _normalize_message(obj)
        if m:
            role, text = m
            yield RawMessage(
                role=role, text=text,
                source_session=session, source_project=project,
                source_date=date, source_path=f, line_index=i,
            )


def _walk_jsonl(f: Path, project: str, session: str, date: str) -> Iterable[RawMessage]:
    from .codex import _walk_jsonl as cwj
    yield from cwj(f, project, session, date)
