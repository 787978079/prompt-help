"""Claude Code 历史适配器。

CC 把每次会话存到 ~/.claude/projects/<encoded-cwd>/<session-id>.jsonl
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Iterable

from ...core.transcript import parse_jsonl
from .base import RawMessage, TranscriptAdapter


# 已知项目名（按长度倒序匹配，避免短前缀截胡）
_KNOWN_PROJECTS = [
    "software-generation-orchestrator",
    "bp-generation-orchestrator",
    "kt-generation-orchestrator",
    "sr-generation-orchestrator",
    "sgo-mainline",
    "likner-app",
    "prompt-help",
    "minpei",
    "wangye",
    "zhuanli",
    "sgo",
    "pmo",
    "mb",
]


def decode_project(encoded: str) -> str:
    """把 CC 的 encoded-cwd 还原成项目名（最长子串匹配 + 兜底）。"""
    enc_lower = encoded.lower()
    for kp in _KNOWN_PROJECTS:
        if (
            f"-{kp}-" in f"-{enc_lower}-"
            or enc_lower.endswith("-" + kp)
            or enc_lower == kp
        ):
            return kp
    if "writing-workspace" in enc_lower:
        return "writing-workspace"
    if "workspace" in enc_lower:
        return "workspace"
    if enc_lower.startswith("c--users-"):
        return "全局会话"
    parts = [p for p in encoded.split("-") if p]
    if len(parts) >= 2:
        return f"{parts[-2]}-{parts[-1]}"
    return parts[-1] if parts else "unknown"


class ClaudeCodeAdapter(TranscriptAdapter):
    name = "claude_code"
    display_name = "Claude Code 历史"

    def __init__(self, root: Path | None = None):
        self.root = (root or (Path.home() / ".claude" / "projects")).expanduser().resolve()

    def detect(self) -> bool:
        return self.root.is_dir() and any(self.root.glob("*/*.jsonl"))

    def walk(self) -> Iterable[RawMessage]:
        if not self.root.is_dir():
            return
        for proj_dir in self.root.iterdir():
            if not proj_dir.is_dir():
                continue
            project_name = decode_project(proj_dir.name)
            for jsonl in proj_dir.glob("*.jsonl"):
                try:
                    date_str = dt.datetime.fromtimestamp(
                        jsonl.stat().st_mtime
                    ).strftime("%Y-%m-%d")
                except Exception:
                    date_str = "0000-00-00"
                session_id = jsonl.stem
                msgs = parse_jsonl(jsonl)
                for m in msgs:
                    yield RawMessage(
                        role=m.role,
                        text=m.text,
                        source_session=session_id,
                        source_project=project_name,
                        source_date=date_str,
                        source_path=jsonl,
                        line_index=m.raw_index,
                    )
