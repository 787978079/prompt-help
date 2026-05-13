"""Phase 7 T2：翻译缓存层单测。"""

from __future__ import annotations

import time

import pytest

from prompt_help.core.config import Config, load_config
from prompt_help.core.translation_cache import TranslationCache, hash_text


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    monkeypatch.setenv("PROMPT_HELP_VAULT_PATH", str(tmp_path))
    return load_config()


def test_hash_text_stable():
    assert hash_text("hello") == hash_text("hello")
    assert hash_text("hello") != hash_text("hello!")
    assert len(hash_text("x" * 1000)) == 32


def test_put_and_get(cfg):
    c = TranslationCache(cfg)
    h = hash_text("translate this")
    assert c.get(h) is None
    c.put(h, "翻译过的中文")
    assert c.get(h) == "翻译过的中文"


def test_put_empty_skipped(cfg):
    c = TranslationCache(cfg)
    h = hash_text("xyz")
    c.put(h, "")
    assert c.get(h) is None


def test_put_overwrites(cfg):
    c = TranslationCache(cfg)
    h = hash_text("text")
    c.put(h, "版本 1")
    c.put(h, "版本 2")
    assert c.get(h) == "版本 2"


def test_cleanup_expired_removes_old(cfg):
    c = TranslationCache(cfg)
    h = hash_text("old")
    c.put(h, "旧译文")
    # 手动改 created_at 为 60 天前
    import sqlite3
    with sqlite3.connect(c.db_path) as conn:
        conn.execute(
            "UPDATE translations SET created_at = ? WHERE hash = ?",
            (int(time.time()) - 60 * 86400, h),
        )
    n = c.cleanup_expired(ttl_days=30)
    assert n == 1
    assert c.get(h) is None


def test_cleanup_keeps_fresh(cfg):
    c = TranslationCache(cfg)
    c.put(hash_text("fresh1"), "新译文 1")
    c.put(hash_text("fresh2"), "新译文 2")
    n = c.cleanup_expired(ttl_days=30)
    assert n == 0
    assert c.stats()["total"] == 2


def test_stats(cfg):
    c = TranslationCache(cfg)
    assert c.stats()["total"] == 0
    c.put(hash_text("a"), "x")
    c.put(hash_text("b"), "y")
    s = c.stats()
    assert s["total"] == 2
    assert s["oldest"] is not None
