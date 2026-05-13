"""Phase 12 C1：[[]] 互引解析。"""

from prompt_help.core.references import (
    count_references, expand_references, find_references,
)


def test_find_basic():
    text = "用 [[playwright 验证]] 和 [[截图规范]] 检查改动。"
    assert find_references(text) == ["playwright 验证", "截图规范"]


def test_find_dedupes():
    text = "[[A]] then [[A]] again, [[B]], [[A]]"
    assert find_references(text) == ["A", "B"]


def test_find_skips_single_bracket():
    """单方括号 [占位符] 不算 reference。"""
    text = "[占位符] vs [[真引用]]"
    assert find_references(text) == ["真引用"]


def test_find_caps_length():
    long = "a" * 80
    text = f"[[{long}]] [[short]]"
    assert find_references(text) == ["short"]


def test_expand_simple():
    text = "请按 [[节奏]] 操作。"
    lookup = lambda name: "三步走" if name == "节奏" else None
    assert expand_references(text, lookup) == "请按 三步走 操作。"


def test_expand_keeps_unknown():
    text = "[[X]] [[Y]]"
    lookup = lambda name: "x" if name == "X" else None
    assert expand_references(text, lookup) == "x [[Y]]"


def test_expand_max_depth_zero():
    text = "[[A]]"
    lookup = lambda name: "expanded"
    assert expand_references(text, lookup, max_depth=0) == text


def test_count():
    assert count_references("[[A]] [[B]] [[A]]") == 2
    assert count_references("无引用") == 0
