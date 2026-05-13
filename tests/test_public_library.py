"""Phase 7 T1：推荐库 parser 单测（不联网，只测解析逻辑）。"""

from __future__ import annotations

import json

import pytest

from prompt_help.cli import public_library as pub


@pytest.fixture
def csv_source():
    return {
        "id": "test-csv", "name": "Test CSV",
        "language": "en", "default_categories": ["通用工程"],
    }


@pytest.fixture
def md_source():
    return {
        "id": "test-md", "name": "Test MD",
        "language": "en", "default_categories": [],
    }


@pytest.fixture
def ipynb_source():
    return {
        "id": "test-ipynb", "name": "Test Notebook",
        "language": "en", "default_categories": [],
    }


def test_parse_csv_basic(csv_source):
    raw = (
        "act,prompt\n"
        '"data analyst","You are a data analyst. Help me analyze data sets and find insights."\n'
        '"copywriter","You are a copywriter. Write compelling marketing copy for new products."\n'
    )
    out = pub._parse_csv(raw, csv_source)
    assert len(out) == 2
    assert out[0].title == "data analyst"
    assert "data analyst" in out[0].body.lower()
    assert out[0].language == "en"
    assert out[0].source_id == "test-csv"


def test_parse_csv_skips_short(csv_source):
    raw = "act,prompt\n短,too short\n"
    out = pub._parse_csv(raw, csv_source)
    assert out == []


def test_parse_csv_handles_huge_field(csv_source):
    """awesome-chatgpt-prompts 字段最长 6000+ 字符，默认 csv.field_size_limit 不够。"""
    big = "x" * 8000
    raw = f"act,prompt\nbig role,\"{big}\"\n"
    out = pub._parse_csv(raw, csv_source)
    assert len(out) == 1
    assert len(out[0].body) >= 8000


def test_parse_markdown_headings_recognizes_zh_signals(md_source):
    raw = (
        "## 我的提示词\n\n"
        "你是一名资深数据分析师，专门处理大型电商数据集。"
        "帮我分析数据集中的关键趋势、识别异常值、总结出可能的业务洞察和后续行动建议，"
        "并按重要程度排序输出。最后用一段话总结业务建议。\n\n"
        "## TOC\n\n这是导航，不要算\n"
    )
    out = pub._parse_markdown_headings(raw, md_source)
    titles = [p.title for p in out]
    assert "我的提示词" in titles
    assert "TOC" not in titles


def test_parse_markdown_headings_length_threshold(md_source):
    raw = (
        "## 短小精悍\n\n"
        "你是一名专业翻译，把英文技术文档翻译成自然地道的中文。"
        "保留专有名词、命令、代码片段、占位符不变；"
        "技术术语择优用标准中文译法，圈内常用英文的保留英文；"
        "第二人称用「你」不用「您」。\n"
    )
    out = pub._parse_markdown_headings(raw, md_source)
    assert len(out) >= 1
    assert "短小精悍" in [p.title for p in out]


def test_parse_markdown_headings_skips_too_short(md_source):
    raw = "## 极短\n\n你是助手。\n"  # < 80 chars
    out = pub._parse_markdown_headings(raw, md_source)
    assert out == []


def test_parse_jupyter_notebook_extracts_md_cells(ipynb_source):
    nb = {
        "cells": [
            {"cell_type": "markdown", "source":
                "## Prompt 1\n\n"
                "你是一名资深 Python 工程师，专长重构遗留代码。"
                "帮我把这段代码重构：提取可复用模块、加适当的类型注解、"
                "为关键函数补 docstring；保持业务逻辑不变。"},
            {"cell_type": "code", "source": "print('hello')"},
            {"cell_type": "markdown", "source": [
                "## Prompt 2\n\n",
                "act as a senior code reviewer for a Python service. "
                "Review this PR for security issues, race conditions, and "
                "missing error handling. Suggest concrete improvements with line numbers."
            ]},
        ]
    }
    raw = json.dumps(nb)
    out = pub._parse_jupyter_notebook(raw, ipynb_source)
    titles = [p.title for p in out]
    assert "Prompt 1" in titles
    assert "Prompt 2" in titles


def test_parse_jupyter_notebook_bad_json_raises(ipynb_source):
    with pytest.raises(ValueError):
        pub._parse_jupyter_notebook("not json", ipynb_source)


def test_parse_source_dispatches_by_format(csv_source, md_source):
    csv_source["format"] = "csv"
    md_source["format"] = "markdown_headings"

    csv_raw = "act,prompt\nrole,\"You are X. A full prompt here that meets the 50-char minimum body length easily.\"\n"
    md_raw = (
        "## 测试\n\n"
        "你是一名专业测试工程师，专门验证 markdown 提示词解析器的正确性。"
        "确保覆盖各种 edge case：短文本被拒、长文本被接受、中英文混合、"
        "TOC / nav 标题被过滤、有效 prompt 被保留。请输出测试结果。\n"
    )

    assert pub.parse_source(csv_raw, csv_source) != []
    assert pub.parse_source(md_raw, md_source) != []
