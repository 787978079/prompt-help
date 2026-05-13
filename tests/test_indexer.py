"""indexer 模块：FTS5 检索、评分、trap 召回。"""

from pathlib import Path

import pytest

from prompt_help.core import indexer, storage
from prompt_help.core.config import Config, GitConfig


@pytest.fixture
def cfg(tmp_path: Path) -> Config:
    c = Config(vault_path=tmp_path)
    c.git = GitConfig(auto_commit=False)
    (tmp_path / "prompts" / "global").mkdir(parents=True)
    (tmp_path / "prompts" / "traps").mkdir()
    return c


def test_open_and_count(cfg: Config):
    conn = indexer.open_db(cfg)
    counts = indexer.count_all(conn)
    assert counts["total"] == 0
    conn.close()


def test_upsert_and_search(cfg: Config):
    conn = indexer.open_db(cfg)
    p1 = storage.Prompt.new(
        title="Playwright UI 验证流程",
        body="跑 npx playwright test，再用 take_screenshot 验证",
        scope="global",
        tags=["playwright", "ui"],
    )
    f1 = storage.save(cfg, p1)
    indexer.upsert(conn, p1, f1)

    p2 = storage.Prompt.new(
        title="Python pytest 风格",
        body="用 pytest fixture，避免 setUp",
        scope="global",
        tags=["python", "test"],
    )
    f2 = storage.save(cfg, p2)
    indexer.upsert(conn, p2, f2)

    # FTS 搜索 "playwright" 应命中 p1
    results = indexer.search(conn, "playwright")
    assert len(results) >= 1
    assert results[0][0]["id"] == p1.id

    # 搜索 "pytest" 应命中 p2
    results = indexer.search(conn, "pytest")
    assert len(results) >= 1
    assert results[0][0]["id"] == p2.id
    conn.close()


def test_trap_recall(cfg: Config):
    conn = indexer.open_db(cfg)
    trap = storage.Prompt.new(
        title="Node 自杀禁令",
        body="不要 taskkill /im node.exe",
        scope="trap",
        triggers=["taskkill node", "kill node", "杀 node"],
    )
    f = storage.save(cfg, trap)
    indexer.upsert(conn, trap, f)

    hits = indexer.search_traps_for_text(conn, "我打算 taskkill node 把 dev server 杀了")
    assert len(hits) == 1
    assert hits[0]["id"] == trap.id

    miss = indexer.search_traps_for_text(conn, "今天天气不错")
    assert miss == []
    conn.close()


def test_bump_used_and_score_ordering(cfg: Config):
    conn = indexer.open_db(cfg)
    a = storage.Prompt.new(title="cool prompt about playwright", body="test")
    b = storage.Prompt.new(title="another playwright thing", body="test")
    fa = storage.save(cfg, a); fb = storage.save(cfg, b)
    indexer.upsert(conn, a, fa); indexer.upsert(conn, b, fb)

    # 给 a 加 5 次 used，应排在 b 前
    for _ in range(5):
        indexer.bump_used(conn, a.id)
    results = indexer.search(conn, "playwright")
    ids = [r["id"] for r, _s in results]
    assert ids[0] == a.id
    conn.close()


def test_reindex_from_disk(cfg: Config):
    storage.save(cfg, storage.Prompt.new(title="reindex test", body="abc def", scope="global"))
    n = indexer.reindex_from_disk(cfg)
    assert n == 1
    conn = indexer.open_db(cfg)
    res = indexer.search(conn, "reindex")
    assert len(res) == 1
    conn.close()
