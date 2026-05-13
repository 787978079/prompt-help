"""插件 hook / slash command 共用的辅助函数。

可以独立调用：python -m prompt_help.plugin.helpers <subcommand> [args]
- list_recent_user_messages <n>  打印 JSON 格式最近 N 条用户消息（slash command 用）
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from ..core import transcript


def _find_active_transcript() -> Path | None:
    """slash command 直接调时拿不到 hook 注入的 transcript_path，从环境变量或最新文件猜。"""
    # CC 在环境变量里有 CLAUDE_TRANSCRIPT_PATH？没有可靠方式，遍历最新即可
    candidates = [
        Path(os.environ.get("CLAUDE_TRANSCRIPT_PATH", "")),
        Path.home() / ".claude" / "projects",
    ]
    for c in candidates:
        if c.is_file() and c.suffix == ".jsonl":
            return c
        if c.is_dir():
            jsonls = sorted(c.rglob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
            if jsonls:
                return jsonls[0]
    return None


def list_recent_user_messages(n: int = 5) -> None:
    path = _find_active_transcript()
    if not path:
        json.dump({"messages": [], "error": "无活动 transcript"}, sys.stdout, ensure_ascii=False)
        return
    msgs = transcript.last_user_messages(path, n=n)
    out = [{"index": i, "text": m.text[:600]} for i, m in enumerate(msgs)]
    json.dump({"transcript": str(path), "messages": out}, sys.stdout, ensure_ascii=False, indent=2)


def main() -> None:
    if len(sys.argv) < 2:
        print("用法: python -m prompt_help.plugin.helpers <subcommand>", file=sys.stderr)
        sys.exit(1)
    cmd = sys.argv[1]
    if cmd == "list_recent_user_messages":
        n = int(sys.argv[2]) if len(sys.argv) > 2 else 5
        list_recent_user_messages(n)
    else:
        print(f"未知子命令: {cmd}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
