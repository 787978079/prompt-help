"""Inbox 候选的 confidence 计算 + 入库前去重检测。

为什么单独抽出来：v0.x 时 auto_scan.py 给所有候选硬编码 confidence=0.6，
UI 里显示为"匹配度 0.60"误导用户以为系统真的算过。现在所有入 inbox 路径
（Stop hook / 手动 inbox-add / auto_scan / PM-Mode 草稿）统一用这里的真公式。

公式（继承 stop.py:108 的成熟版本）：
    confidence = 0.4 + (1 - overlap) * 0.3 + (length_ratio) * 0.3

- overlap：跟库内已有 prompts 的最大 token 重合度（0~1，越低越独特）
- length_ratio：min(1.0, len / max_chars)，长度越接近上限越像完整指令
"""

from __future__ import annotations

import re
from pathlib import Path

from .config import Config


_TOKEN_RE = re.compile(r"[A-Za-z]{3,}|[一-鿿]{2,}")


def _token_set(text: str) -> set[str]:
    return {t for t in _TOKEN_RE.findall(text.lower())}


def is_duplicate_in_inbox(
    cfg: Config,
    body: str,
    *,
    token_threshold: float = 0.85,
    seq_threshold: float = 0.90,
) -> bool:
    """检查 body 是否与 inbox/*.md 里某条已有候选近似（≥90% 相似）。

    入库前调用，命中直接跳过写入。两段筛：
      1. token 集合 jaccard < 0.5 → 直接放过（远不可能重复）
      2. 否则上 quality.is_duplicate 精筛（SequenceMatcher）
    """
    if not body or not cfg.inbox_dir.is_dir():
        return False
    cand_tokens = _token_set(body)
    if not cand_tokens:
        return False

    # 延迟 import 避免循环
    from . import quality as _q

    cand_body_head = body[:800]
    for p in cfg.inbox_dir.glob("*.md"):
        try:
            text = p.read_text(encoding="utf-8")
        except OSError:
            continue
        # 拆 frontmatter 取 body
        existing_body = text
        if text.startswith("---"):
            parts = text.split("---", 2)
            if len(parts) >= 3:
                existing_body = parts[2].strip()
        # 粗筛：token jaccard
        ex_tokens = _token_set(existing_body)
        if not ex_tokens:
            continue
        inter = len(cand_tokens & ex_tokens)
        union = len(cand_tokens | ex_tokens)
        jaccard = inter / max(union, 1)
        if jaccard < 0.50:
            continue
        # 精筛：与 inbox 去重命令同款
        if _q.is_duplicate(
            cand_body_head, existing_body[:800],
            token_threshold=token_threshold,
            seq_threshold=seq_threshold,
        ):
            return True
    return False


def max_overlap_with_db(cfg: Config, candidate: str) -> float:
    """返回候选文本与库内任意条目的最大 token 重合度（0~1）。"""
    try:
        from . import indexer
        cand_tokens = _token_set(candidate)
        if not cand_tokens:
            return 0.0
        conn = indexer.open_db(cfg)
        try:
            max_ov = 0.0
            for _id, title, body in indexer.existing_titles_and_bodies(conn):
                other = _token_set((title or "") + " " + (body or "")[:600])
                if not other:
                    continue
                inter = cand_tokens & other
                ov = len(inter) / max(len(cand_tokens), 1)
                if ov > max_ov:
                    max_ov = ov
            return max_ov
        finally:
            conn.close()
    except Exception:
        return 0.0


def compute_confidence(
    cfg: Config,
    body: str,
    *,
    overlap: float | None = None,
) -> float:
    """计算候选的 confidence（0~1）。

    `overlap` 调用方已经算过时可以传入避免重复扫库；否则内部算。
    """
    if not body or not body.strip():
        return 0.0
    if overlap is None:
        overlap = max_overlap_with_db(cfg, body)
    max_chars = getattr(getattr(cfg, "mining", None), "max_chars", 4000) or 4000
    length_ratio = min(1.0, len(body) / max(max_chars, 1))
    score = 0.4 + (1.0 - overlap) * 0.3 + length_ratio * 0.3
    return round(min(1.0, max(0.0, score)), 2)
