"""Claude Code transcript（JSONL）解析。

Claude Code 把每次会话写到 JSONL 文件，每行是一条消息事件。
格式大致：
    {"type": "user", "message": {"role": "user", "content": "..."}, ...}
    {"type": "assistant", "message": {"role": "assistant", "content": [{"type":"text","text":"..."}, {"type":"tool_use",...}]}, ...}

我们关心的：抽取最近 N 条用户文本消息（剔除 tool_result 之类的伪用户回放）。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass
class TurnMessage:
    role: str  # "user" | "assistant"
    text: str  # 拼接后的纯文本（剔除 tool_use/tool_result）
    raw_index: int  # 在 JSONL 中的行号（0-based）


def parse_jsonl(path: Path) -> list[TurnMessage]:
    if not path.is_file():
        return []
    msgs: list[TurnMessage] = []
    try:
        for i, line in enumerate(path.read_text(encoding="utf-8", errors="ignore").splitlines()):
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except Exception:
                continue
            tm = _to_turn(obj, i)
            if tm:
                msgs.append(tm)
    except Exception:
        return msgs
    return msgs


def _to_turn(obj: dict, idx: int) -> TurnMessage | None:
    msg = obj.get("message") if isinstance(obj.get("message"), dict) else None
    if not msg:
        # 也兼容平铺结构
        if obj.get("role") in ("user", "assistant"):
            msg = obj
        else:
            return None
    role = msg.get("role")
    if role not in ("user", "assistant"):
        return None
    text = _extract_text(msg.get("content"))
    if not text:
        return None
    return TurnMessage(role=role, text=text, raw_index=idx)


def _extract_text(content) -> str:
    """content 可能是字符串、list of blocks。剔除 tool_use / tool_result / 图像等。"""
    if isinstance(content, str):
        # 过滤掉 system-reminder / tool 输出包裹的回放
        if content.startswith("<system-reminder>") or content.startswith("<command-"):
            return ""
        return content.strip()
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if not isinstance(block, dict):
                continue
            t = block.get("type")
            if t == "text":
                txt = (block.get("text") or "").strip()
                if txt and not txt.startswith("<system-reminder>"):
                    parts.append(txt)
            # tool_use / tool_result / image 全部跳过
        return "\n\n".join(parts).strip()
    return ""


def last_user_messages(path: Path, n: int = 5) -> list[TurnMessage]:
    """最近 N 条真实用户消息（剔除 tool_result 假冒的 user）。"""
    all_msgs = parse_jsonl(path)
    user_msgs = [m for m in all_msgs if m.role == "user" and m.text]
    return user_msgs[-n:] if len(user_msgs) > n else user_msgs


def last_assistant_text(path: Path) -> str:
    """最近一条 assistant 文本（用于 Stop hook 检测成功信号词）。"""
    all_msgs = parse_jsonl(path)
    for m in reversed(all_msgs):
        if m.role == "assistant" and m.text:
            return m.text
    return ""
