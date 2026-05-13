"""PreCompact hook：CC 即将压缩上下文前，从整段 transcript 二次挖掘。

不同于 Stop hook（只看最近一轮）：
- 扫整个 transcript 的 user messages
- 不强制相邻成功信号（压缩时刻已经晚了）
- 启发式更宽松：长度 + 结构 + 与 inbox / 库的去重
- 安静落 inbox，不发系统提醒（避免在压缩节点抢戏）
- 一次最多挖 3 条，按"价值分"排序
"""

from __future__ import annotations

import datetime as dt
import re
import sys
from pathlib import Path

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


def _structure_score(text: str) -> int:
    return sum(1 for p in _STRUCT_PATTERNS if p.search(text))


def _token_set(text: str) -> set[str]:
    return {t for t in re.findall(r"[A-Za-z]{3,}|[一-鿿]{2,}", text.lower())}


def _max_overlap_with_db(cfg, candidate: str) -> float:
    try:
        from prompt_help.core import indexer
        cand = _token_set(candidate)
        if not cand:
            return 0.0
        conn = indexer.open_db(cfg)
        max_ov = 0.0
        for _id, title, body in indexer.existing_titles_and_bodies(conn):
            other = _token_set(title + " " + (body or "")[:600])
            if not other:
                continue
            ov = len(cand & other) / max(len(cand), 1)
            if ov > max_ov:
                max_ov = ov
                if max_ov >= 0.95:
                    break
        conn.close()
        return max_ov
    except Exception:
        return 0.0


def _max_overlap_with_inbox(cfg, candidate: str) -> float:
    """避免重复入 inbox。"""
    cand = _token_set(candidate)
    if not cand or not cfg.inbox_dir.is_dir():
        return 0.0
    max_ov = 0.0
    for f in cfg.inbox_dir.glob("*.md"):
        try:
            text = f.read_text(encoding="utf-8")
            other = _token_set(text)
            if not other:
                continue
            ov = len(cand & other) / max(len(cand), 1)
            if ov > max_ov:
                max_ov = ov
                if max_ov >= 0.95:
                    break
        except Exception:
            continue
    return max_ov


def _value_score(text: str, struct: int, db_ov: float) -> float:
    """长度 / 结构 / 新颖度的综合价值分（0-1）。"""
    length_score = min(1.0, len(text) / 2000.0)  # 2000 字以上饱和
    structure_score = min(1.0, struct / 3.0)
    novelty = 1.0 - db_ov
    return round(0.4 * novelty + 0.3 * length_score + 0.3 * structure_score, 3)


def _write_to_inbox(cfg, body: str, confidence: float, origin: str) -> Path | None:
    try:
        cfg.inbox_dir.mkdir(parents=True, exist_ok=True)
        ts = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%S")
        out = cfg.inbox_dir / f"{ts}-{abs(hash(body)) % 100000:05d}.md"
        if out.exists():
            return None
        out.write_text(
            f"---\nconfidence: {confidence}\norigin: {origin}\n"
            f"suggested_title: \ncreated: {ts}\n---\n\n{body}\n",
            encoding="utf-8",
        )
        return out
    except Exception:
        return None


def run(inp: dict, cfg) -> str | None:
    if not cfg.mining.enabled:
        return None

    transcript_path = inp.get("transcript_path")
    if not transcript_path:
        return None

    from prompt_help.core import transcript

    p = Path(transcript_path)
    all_msgs = transcript.parse_jsonl(p)
    user_texts = [m.text for m in all_msgs if m.role == "user" and m.text]
    if not user_texts:
        return None

    # 候选筛选
    candidates: list[tuple[str, float]] = []
    for text in user_texts:
        if not (cfg.mining.min_chars <= len(text) <= cfg.mining.max_chars):
            continue
        struct = _structure_score(text)
        if struct < 1:
            continue
        db_ov = _max_overlap_with_db(cfg, text)
        if db_ov >= cfg.mining.dedup_overlap_threshold:
            continue
        if _max_overlap_with_inbox(cfg, text) >= 0.85:
            continue
        score = _value_score(text, struct, db_ov)
        candidates.append((text, score))

    if not candidates:
        return None

    # 按价值分排序，去重（候选间相似度 > 0.7 的合并取高分）
    candidates.sort(key=lambda x: x[1], reverse=True)
    picked: list[tuple[str, float]] = []
    for text, score in candidates:
        too_close = False
        for picked_text, _ in picked:
            ov = len(_token_set(text) & _token_set(picked_text)) / max(len(_token_set(text)), 1)
            if ov >= 0.7:
                too_close = True
                break
        if not too_close:
            picked.append((text, score))
        if len(picked) >= 3:
            break

    written = 0
    for text, score in picked:
        if _write_to_inbox(cfg, text, score, "pre-compact"):
            written += 1

    if written == 0:
        return None

    # 给 CC 一个简短系统提醒：下次 SessionStart / /prompt-review 时会看到
    return (
        f"[prompt-help · PreCompact 挖掘] 上下文压缩前已沉淀 {written} 条候选到 inbox，"
        f"用 `/prompt-review` 批量过审。"
    )


if __name__ == "__main__":
    safe_main("PreCompact", run)
