"""transcript 解析单测。"""

import json
from pathlib import Path

from prompt_help.core import transcript


def test_parse_simple_jsonl(tmp_path: Path):
    f = tmp_path / "session.jsonl"
    f.write_text(
        "\n".join([
            json.dumps({"type": "user", "message": {"role": "user", "content": "hello"}}),
            json.dumps({"type": "assistant", "message": {"role": "assistant",
                       "content": [{"type": "text", "text": "hi there"}]}}),
            json.dumps({"type": "user", "message": {"role": "user", "content": "second message"}}),
        ]),
        encoding="utf-8",
    )
    msgs = transcript.parse_jsonl(f)
    assert len(msgs) == 3
    assert msgs[0].text == "hello"
    assert msgs[1].text == "hi there"
    assert msgs[2].text == "second message"


def test_filters_tool_use(tmp_path: Path):
    f = tmp_path / "session.jsonl"
    f.write_text(
        "\n".join([
            json.dumps({"type": "assistant", "message": {"role": "assistant",
                "content": [
                    {"type": "text", "text": "running tool"},
                    {"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "ls"}},
                ]}}),
            json.dumps({"type": "user", "message": {"role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": "t1", "content": "fake output"},
                ]}}),
            json.dumps({"type": "user", "message": {"role": "user", "content": "real next msg"}}),
        ]),
        encoding="utf-8",
    )
    user_msgs = transcript.last_user_messages(f, n=5)
    # tool_result 假冒的 user 应该被剔除
    assert len(user_msgs) == 1
    assert user_msgs[0].text == "real next msg"


def test_skips_system_reminders(tmp_path: Path):
    f = tmp_path / "session.jsonl"
    f.write_text(
        json.dumps({"type": "user", "message": {"role": "user",
                   "content": "<system-reminder>noise</system-reminder>"}}) + "\n" +
        json.dumps({"type": "user", "message": {"role": "user", "content": "real one"}}),
        encoding="utf-8",
    )
    msgs = transcript.last_user_messages(f, n=5)
    assert len(msgs) == 1
    assert msgs[0].text == "real one"


def test_missing_file(tmp_path: Path):
    msgs = transcript.parse_jsonl(tmp_path / "nope.jsonl")
    assert msgs == []
