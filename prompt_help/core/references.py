"""Prompt 互引（Phase 12 C1）。

支持 `[[标题]]` 语法在 prompt 正文里引用其他 prompt（参考 Logseq / Obsidian）。

- `find_references(text)`: 抽出所有引用的标题
- `expand_references(text, lookup)`: 把 [[xxx]] 替换为目标 prompt 的 body（递归一层，防环）
- 与 `[占位符]` 区分：互引用双方括号 `[[...]]`，占位符用单方括号 `[...]`
"""

from __future__ import annotations

import re
from typing import Callable, Optional


_REF_RE = re.compile(r"\[\[([^\[\]\n]{1,60})\]\]")


def find_references(text: str) -> list[str]:
    """返回所有 [[标题]] 中的标题（去重，按出现顺序）。"""
    out: list[str] = []
    seen: set[str] = set()
    for m in _REF_RE.finditer(text):
        name = m.group(1).strip()
        if name and name not in seen:
            out.append(name)
            seen.add(name)
    return out


def expand_references(
    text: str,
    lookup: Callable[[str], Optional[str]],
    max_depth: int = 1,
) -> str:
    """把 [[标题]] 替换为 lookup(标题) 返回的 body。

    max_depth=1 表示只展开一层，避免循环引用。
    lookup 返回 None 时保留原 [[标题]] 不动。
    """
    if max_depth <= 0:
        return text

    def _repl(m):
        name = m.group(1).strip()
        body = lookup(name)
        if body is None:
            return m.group(0)
        return body

    return _REF_RE.sub(_repl, text)


def count_references(text: str) -> int:
    return len(find_references(text))
