"""Phase 11 B1：占位符检测 + 替换。"""

from prompt_help.core.placeholders import count, fill, find


def test_find_bracket_zh():
    text = "你是一名 [角色]，目标是 [目标]。"
    assert find(text) == ["角色", "目标"]


def test_find_brace_en():
    text = "Hello {name}, your task is {task}."
    assert find(text) == ["name", "task"]


def test_find_mixed():
    text = "Act as [role]，处理 {filename} 的问题。"
    assert find(text) == ["role", "filename"]


def test_find_deduplicates():
    text = "[X] then [X] again, [X]"
    assert find(text) == ["X"]


def test_find_skips_blacklist():
    text = "[选项] 选 [角色]，[TODO] 待补"
    assert find(text) == ["角色"]


def test_find_skips_double_brace():
    """Jinja {{ var }} 不算占位符。"""
    text = "Hello {{name}}, {real_var} should match"
    assert find(text) == ["real_var"]


def test_find_caps_length():
    """名字超长（>30）的不算。"""
    text = f"[{'a' * 50}] [short]"
    assert find(text) == ["short"]


def test_fill_replaces_provided():
    body = "[角色] 帮我 {action}"
    out = fill(body, {"角色": "工程师", "action": "重构"})
    assert out == "工程师 帮我 重构"


def test_fill_keeps_unprovided():
    body = "[A] [B] [C]"
    out = fill(body, {"A": "x"})
    assert out == "x [B] [C]"


def test_count():
    assert count("[a] [b]") == 2
    assert count("纯文本无占位符") == 0
