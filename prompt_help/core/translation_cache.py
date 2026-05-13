"""翻译结果缓存（Phase 7 T2）。

设计：
- SQLite 单表 translations(hash TEXT PK, zh TEXT, created_at INTEGER)
- 30 天 TTL；过期条由 cleanup_expired() 手动清。读路径不主动判过期（命中即返回），
  这样跨日子用户复制同一条 prompt 中文版仍秒回；过期清理放 CLI / 后台任务。
- content_hash 由调用者算（sha256 前 16 字节十六进制）。
"""

from __future__ import annotations

import hashlib
import sqlite3
import time
from pathlib import Path
from typing import Optional

from .config import Config


def hash_text(text: str) -> str:
    """统一的 hash 计算（sha256 前 32 hex 字符）。"""
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()[:32]


class TranslationCache:
    def __init__(self, cfg: Config):
        self.db_path: Path = cfg.vault_path / "translation_cache.sqlite"
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    def _conn(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path)

    def _ensure_schema(self) -> None:
        with self._conn() as c:
            c.execute(
                "CREATE TABLE IF NOT EXISTS translations ("
                "  hash TEXT PRIMARY KEY,"
                "  zh TEXT NOT NULL,"
                "  created_at INTEGER NOT NULL"
                ")"
            )

    def get(self, content_hash: str) -> Optional[str]:
        with self._conn() as c:
            row = c.execute(
                "SELECT zh FROM translations WHERE hash = ?", (content_hash,),
            ).fetchone()
        return row[0] if row else None

    def put(self, content_hash: str, zh_text: str) -> None:
        if not zh_text:
            return
        with self._conn() as c:
            c.execute(
                "INSERT OR REPLACE INTO translations(hash, zh, created_at) VALUES (?, ?, ?)",
                (content_hash, zh_text, int(time.time())),
            )

    def cleanup_expired(self, ttl_days: int = 30) -> int:
        cutoff = int(time.time()) - ttl_days * 86400
        with self._conn() as c:
            cur = c.execute(
                "DELETE FROM translations WHERE created_at < ?", (cutoff,),
            )
            return cur.rowcount

    def stats(self) -> dict:
        with self._conn() as c:
            total = c.execute("SELECT COUNT(*) FROM translations").fetchone()[0]
            oldest = c.execute(
                "SELECT MIN(created_at) FROM translations"
            ).fetchone()[0]
        return {"total": total, "oldest": oldest}
