"""测试：项目优化的摘要抽取与 prompt 渲染。

LLM 调用本身由 optimizer 的现有 backend 处理，这里只测摘要 + 拼装路径。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from prompt_help.core.project_optimize import (
    extract_project_summary,
    render_summary_for_prompt,
)


@pytest.fixture
def fake_project(tmp_path: Path) -> Path:
    """伪造一个有 CLAUDE.md + package.json + 目录结构的项目。"""
    (tmp_path / "CLAUDE.md").write_text(
        "# 项目约定\n\n禁止批量杀 node.exe。\n\n用 npx tsc 验证类型。",
        encoding="utf-8",
    )
    (tmp_path / "package.json").write_text(
        '{"name": "fake-app", "scripts": {"test": "vitest"}}',
        encoding="utf-8",
    )
    (tmp_path / "README.md").write_text("# Fake App\n\nDemo project for tests.", encoding="utf-8")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.ts").write_text("// entry", encoding="utf-8")
    (tmp_path / "tests").mkdir()
    (tmp_path / "node_modules").mkdir()  # 应被排除
    return tmp_path


def test_extract_finds_priority_files(fake_project: Path):
    s = extract_project_summary(fake_project)
    names = [n for n, _ in s.sections]
    assert "CLAUDE.md" in names
    assert "package.json" in names
    assert "README.md" in names
    assert "（顶层目录结构）" in names
    assert s.project_name == fake_project.name
    assert s.total_chars > 0


def test_extract_skips_node_modules(fake_project: Path):
    s = extract_project_summary(fake_project)
    tree = dict(s.sections).get("（顶层目录结构）", "")
    assert "node_modules" not in tree
    assert "src/" in tree
    assert "tests/" in tree


def test_extract_handles_missing_files(tmp_path: Path):
    """空目录不该崩——sections 可以为空。"""
    s = extract_project_summary(tmp_path)
    # 顶层目录结构那一项可能仍会有（即使是空内容也行）
    file_names = [n for n, _ in s.sections if not n.startswith("（")]
    assert file_names == []
    assert not s.truncated  # 没数据不算截断


def test_extract_truncates_long_file(tmp_path: Path):
    big = "x" * 10000
    (tmp_path / "CLAUDE.md").write_text(big, encoding="utf-8")
    s = extract_project_summary(tmp_path)
    claude_content = dict(s.sections)["CLAUDE.md"]
    # CLAUDE.md 单文件上限 4000，应被截
    assert len(claude_content) <= 4100  # 留点截断标记余量
    assert "截断" in claude_content


def test_render_summary_is_xml(fake_project: Path):
    s = extract_project_summary(fake_project)
    rendered = render_summary_for_prompt(s)
    assert rendered.startswith("<project ")
    assert "</project>" in rendered
    assert "<file path=\"CLAUDE.md\">" in rendered
    assert "</file>" in rendered


def test_summary_respects_total_budget(tmp_path: Path):
    # 三个大文件加起来超过 _MAX_SUMMARY_CHARS（6500）
    (tmp_path / "CLAUDE.md").write_text("a" * 4500, encoding="utf-8")  # 限 4000
    (tmp_path / "AGENTS.md").write_text("b" * 2500, encoding="utf-8")  # 限 2000
    (tmp_path / "README.md").write_text("c" * 4000, encoding="utf-8")  # 限 3000
    s = extract_project_summary(tmp_path)
    assert s.total_chars <= 6600  # 余 100 字给截断标记
    assert s.truncated  # 应该标记截断
