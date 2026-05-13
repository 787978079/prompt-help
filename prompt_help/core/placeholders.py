"""占位符检测与替换（Phase 11 B1）。

支持两种占位符语法：
- `[占位符名]`：generalize 引擎默认产出形式
- `{变量名}`：Postman / Mustache 风格

用例：
    >>> from prompt_help.core.placeholders import find, fill
    >>> body = "你是一名 [角色]，目标是 {target}。"
    >>> find(body)
    ['角色', 'target']
    >>> fill(body, {"角色": "工程师", "target": "重构代码"})
    '你是一名 工程师，目标是 重构代码。'
"""

from __future__ import annotations

import re


# `[...]` 内不含 \n 和嵌套 [/]，最长 30 字
_BRACKET_RE = re.compile(r"\[([^\[\]\n]{1,30})\]")
# `{...}` 同上，避开 `{{...}}` 双花括号（Jinja 等）
_BRACE_RE = re.compile(r"(?<!\{)\{([^\{\}\n]{1,30})\}(?!\})")


# 不算占位符的常见词（generalize LLM 可能输出 [选项] / [可选] 等修饰）
_BLACKLIST = {
    "可选", "必填", "选项", "示例", "例如", "如:", "其它", "其他",
    "todo", "TODO", "...", "…",
}


def find(text: str) -> list[str]:
    """返回所有占位符名，按出现顺序去重保留。"""
    out: list[str] = []
    seen: set[str] = set()
    for m in _BRACKET_RE.finditer(text):
        name = m.group(1).strip()
        if name in _BLACKLIST or not name or name in seen:
            continue
        out.append(name)
        seen.add(name)
    for m in _BRACE_RE.finditer(text):
        name = m.group(1).strip()
        if name in _BLACKLIST or not name or name in seen:
            continue
        out.append(name)
        seen.add(name)
    return out


def fill(text: str, values: dict[str, str]) -> str:
    """用 values 替换文本中的占位符。未提供值的保留原样。"""
    def _repl_bracket(m):
        name = m.group(1).strip()
        v = values.get(name)
        return v if v is not None else m.group(0)

    def _repl_brace(m):
        name = m.group(1).strip()
        v = values.get(name)
        return v if v is not None else m.group(0)

    out = _BRACKET_RE.sub(_repl_bracket, text)
    out = _BRACE_RE.sub(_repl_brace, out)
    return out


def count(text: str) -> int:
    return len(find(text))
