"""适配器接口：每种 AI 编程工具的历史会话存储格式不同，统一抽象成 RawMessage 流。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


@dataclass
class RawMessage:
    role: str  # "user" | "assistant"
    text: str
    source_session: str  # 文件名 stem 或会话 id
    source_project: str  # 推断的项目名
    source_date: str  # YYYY-MM-DD
    source_path: Path  # 来源文件
    line_index: int = 0


class TranscriptAdapter(ABC):
    """一种 AI 工具历史的适配器。"""

    name: str = "base"
    display_name: str = "未知工具"

    @abstractmethod
    def detect(self) -> bool:
        """是否检测到这个工具的历史目录在系统上。GUI 用来决定是否显示该选项。"""

    @abstractmethod
    def walk(self) -> Iterable[RawMessage]:
        """遍历所有会话，按时间顺序产出 user/assistant 消息流。"""


def all_adapters() -> list[TranscriptAdapter]:
    """工厂：返回当前已实现的所有 adapter 实例。"""
    from .claude_code import ClaudeCodeAdapter
    from .codex import CodexAdapter
    return [ClaudeCodeAdapter(), CodexAdapter()]
