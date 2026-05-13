"""Phase 7 T3：PM 对话引擎单测（不调真实 LLM，只测数据结构 + 解析）。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from prompt_help.core import pm_dialog


@pytest.fixture
def empty_session():
    return pm_dialog.PMSession(slug="test", idea="做个截止日期提醒 app")


def test_session_edit_user_truncates_after(empty_session):
    s = empty_session
    s.append_user("做个 app")
    s.append_assistant("反问 1")
    s.append_user("具体细节")
    s.append_assistant("反问 2")
    s.turn_count = 2
    s.scores = {"what": 5, "why": 3, "how": 2}
    s.ready = False

    s.edit_user_at(0, "改后的想法")
    assert len(s.history) == 1
    assert s.history[0]["content"] == "改后的想法"
    assert s.turn_count == 1
    assert s.ready is False


def test_session_edit_non_user_raises(empty_session):
    s = empty_session
    s.append_user("a")
    s.append_assistant("b")
    with pytest.raises(ValueError):
        s.edit_user_at(1, "改 AI 回复")


def test_parse_interview_response_valid():
    raw = '{"scores": {"what": 5, "why": 3, "how": 2}, "next_question": "用户痛点是什么？", "ready": false}'
    parsed = pm_dialog._parse_interview_response(raw)
    assert parsed is not None
    assert parsed["scores"] == {"what": 5, "why": 3, "how": 2}
    assert parsed["next_question"] == "用户痛点是什么？"
    assert parsed["ready"] is False


def test_parse_interview_response_with_markdown_fence():
    raw = '```json\n{"scores": {"what": 8, "why": 8, "how": 7}, "next_question": "", "ready": true}\n```'
    parsed = pm_dialog._parse_interview_response(raw)
    assert parsed is not None
    assert parsed["ready"] is True
    assert parsed["scores"]["what"] == 8


def test_parse_interview_response_clamps_scores():
    raw = '{"scores": {"what": 15, "why": -3, "how": "abc"}, "next_question": "x", "ready": false}'
    parsed = pm_dialog._parse_interview_response(raw)
    assert parsed["scores"]["what"] == 10  # 上限
    assert parsed["scores"]["why"] == 0   # 下限
    assert parsed["scores"]["how"] == 0   # 非数字降级


def test_parse_interview_response_garbage_returns_none():
    assert pm_dialog._parse_interview_response("not json at all") is None
    assert pm_dialog._parse_interview_response("") is None


def test_parse_brief_response_extracts_4_files():
    raw = json.dumps({
        "brief": "# Brief\n\n...",
        "user_stories": "# Stories\n\n...",
        "risks": "# Risks\n\n...",
        "decisions": "# Decisions\n\n...",
    })
    parsed = pm_dialog._parse_brief_response(raw)
    assert parsed is not None
    assert parsed["brief"].startswith("# Brief")
    assert parsed["user_stories"].startswith("# Stories")
    assert parsed["risks"].startswith("# Risks")
    assert parsed["decisions"].startswith("# Decisions")


def test_format_history_includes_idea_and_turns(empty_session):
    s = empty_session
    s.append_user("a")
    s.append_assistant("反问")
    s.turn_count = 1
    text = pm_dialog._format_history_for_llm(s)
    assert "做个截止日期提醒 app" in text
    assert "反问" in text
    assert "第 0 轮" in text or "1" in text
