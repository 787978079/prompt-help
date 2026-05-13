"""Stop hook：assistant 回合结束时挖掘"值得保存的提示词"，主动推送系统提醒。

启发式（用户选了"积极推送"模式）：
- 最后一条用户消息长度 200-4000 字符
- 有结构化标记（编号列表、'你的任务'、代码块、'按以下步骤'）
- 2 回合内出现成功信号词（perfect/works/exactly/搞定/对的）
- 与现有提示词 token 重合 < dedup_overlap_threshold
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

# 让本文件能直接 python 执行
_pkg_root = Path(__file__).resolve().parents[3]
if str(_pkg_root) not in sys.path:
    sys.path.insert(0, str(_pkg_root))

from prompt_help.plugin.hooks._runtime import safe_main  # noqa: E402


_STRUCT_PATTERNS = [
    re.compile(r"^\s*[-*]\s+", re.MULTILINE),
    re.compile(r"^\s*\d+[\.、]\s+", re.MULTILINE),
    re.compile(r"```"),
    re.compile(r"<task>|<context>|<constraints>|<output_format>", re.IGNORECASE),
    re.compile(r"你的任务|按以下步骤|你需要|要求如下|请按照|step\s*\d", re.IGNORECASE),
]


def _has_structure(text: str) -> bool:
    return sum(1 for p in _STRUCT_PATTERNS if p.search(text)) >= 1


def _has_success_signal(assistant_text: str, signals: list[str]) -> bool:
    if not assistant_text:
        return False
    lc = assistant_text.lower()
    return any(s.lower() in lc for s in signals)


def _token_set(text: str) -> set[str]:
    return {t for t in re.findall(r"[A-Za-z]{3,}|[一-鿿]{2,}", text.lower())}


def _max_overlap_with_db(cfg, candidate: str) -> float:
    """对全库做粗粒度 token 重合度检查（避免重复入库）。"""
    try:
        from prompt_help.core import indexer
        cand_tokens = _token_set(candidate)
        if not cand_tokens:
            return 0.0
        conn = indexer.open_db(cfg)
        max_ov = 0.0
        for _id, title, body in indexer.existing_titles_and_bodies(conn):
            other = _token_set(title + " " + (body or "")[:600])
            if not other:
                continue
            inter = cand_tokens & other
            ov = len(inter) / max(len(cand_tokens), 1)
            if ov > max_ov:
                max_ov = ov
                if max_ov >= 0.95:
                    break
        conn.close()
        return max_ov
    except Exception:
        return 0.0


def _summarize(text: str, max_chars: int = 80) -> str:
    text = re.sub(r"\s+", " ", text.strip())
    return text if len(text) <= max_chars else text[:max_chars - 1] + "…"


def run(inp: dict, cfg) -> str | None:
    if not cfg.mining.enabled:
        return None

    transcript_path = inp.get("transcript_path")
    if not transcript_path:
        return None

    from prompt_help.core import transcript

    p = Path(transcript_path)
    user_msgs = transcript.last_user_messages(p, n=2)
    if not user_msgs:
        return None

    last_user = user_msgs[-1].text
    if not (cfg.mining.min_chars <= len(last_user) <= cfg.mining.max_chars):
        return None
    if not _has_structure(last_user):
        return None

    last_assistant = transcript.last_assistant_text(p)
    if not _has_success_signal(last_assistant, cfg.mining.success_signals):
        return None

    overlap = _max_overlap_with_db(cfg, last_user)
    if overlap >= cfg.mining.dedup_overlap_threshold:
        return None

    summary = _summarize(last_user, 80)
    # 统一走 core/scoring（之前每个调用方各自抄公式，现在共用一份）
    from prompt_help.core import scoring as _scoring
    confidence = _scoring.compute_confidence(cfg, last_user, overlap=overlap)

    # 同时把候选写到 inbox（双闸门：当场推送 + inbox 兜底）
    try:
        cfg.inbox_dir.mkdir(parents=True, exist_ok=True)
        import datetime as dt
        ts = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%S")
        out = cfg.inbox_dir / f"{ts}-{abs(hash(last_user)) % 100000:05d}.md"
        if not out.exists():
            out.write_text(
                f"---\nconfidence: {confidence}\nsuggested_title: \ncreated: {ts}\n---\n\n{last_user}\n",
                encoding="utf-8",
            )
    except Exception:
        pass

    return (
        f"[prompt-help · 检测到值得保存的提示词 · confidence={confidence}]\n"
        f"摘要：{summary}\n"
        f"运行 `/prompt-save` 当场保存（推荐），或留待之后 `/prompt-review` 批量过审。"
    )


if __name__ == "__main__":
    safe_main("Stop", run)
