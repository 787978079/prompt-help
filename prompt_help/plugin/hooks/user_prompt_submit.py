"""UserPromptSubmit hook：用户发消息前扫描 trap 关键词，命中则注入提醒。

trap 是 scope=trap 的提示词，通过 triggers 字段（或 fallback 到 title）匹配。
"""

from __future__ import annotations

import sys
from pathlib import Path

_pkg_root = Path(__file__).resolve().parents[3]
if str(_pkg_root) not in sys.path:
    sys.path.insert(0, str(_pkg_root))

from prompt_help.plugin.hooks._runtime import safe_main  # noqa: E402


def run(inp: dict, cfg) -> str | None:
    if not cfg.trap_recall.enabled:
        return None
    if not cfg.index_db.is_file():
        return None

    user_text = (inp.get("prompt") or inp.get("user_message") or "").strip()
    if not user_text or len(user_text) < 8:
        return None

    from prompt_help.core import indexer
    try:
        conn = indexer.open_db(cfg)
        hits = indexer.search_traps_for_text(
            conn, user_text, max_n=cfg.trap_recall.max_traps_per_message
        )
        conn.close()
    except Exception:
        return None

    if not hits:
        return None

    parts = ["[prompt-help · 触发踩坑提醒]"]
    for r in hits:
        triggers = r["triggers_csv"] or r["title"]
        parts.append(f"⚠ {r['title']}（命中：{triggers}）")
        parts.append(r["body"].strip())
        parts.append("---")
    return "\n".join(parts).rstrip("-").rstrip()


if __name__ == "__main__":
    safe_main("UserPromptSubmit", run)
