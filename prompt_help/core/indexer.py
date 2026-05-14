"""SQLite FTS5 索引：提示词的全文检索 + 元数据查询。

设计：
- 主表 prompts 存所有元数据，文件系统始终是真相源（索引可重建）
- prompts_fts 虚拟表用 FTS5 做全文检索（title + tags + body）
- 评分 = BM25 × (1 + log(used + success*2 + 1))，再做时间衰减
- trap 召回：单独函数 search_traps_for_text(msg) 返回触发词命中
"""

from __future__ import annotations

import math
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from .config import Config
from .storage import Prompt, file_path_for, iter_prompts

SCHEMA = """
CREATE TABLE IF NOT EXISTS prompts (
    id              TEXT PRIMARY KEY,
    title           TEXT NOT NULL,
    scope           TEXT NOT NULL,
    project         TEXT,
    tags_csv        TEXT NOT NULL DEFAULT '',
    projects_csv    TEXT NOT NULL DEFAULT '',
    stack_csv       TEXT NOT NULL DEFAULT '',
    triggers_csv    TEXT NOT NULL DEFAULT '',
    origin          TEXT NOT NULL DEFAULT 'manual',
    used            INTEGER NOT NULL DEFAULT 0,
    success_signal  INTEGER NOT NULL DEFAULT 0,
    last_used       TEXT,
    created         TEXT NOT NULL,
    file_path       TEXT NOT NULL,
    body            TEXT NOT NULL
);

CREATE VIRTUAL TABLE IF NOT EXISTS prompts_fts USING fts5(
    title, tags, body,
    content='prompts', content_rowid='rowid',
    tokenize='unicode61 remove_diacritics 2'
);

CREATE TRIGGER IF NOT EXISTS prompts_ai AFTER INSERT ON prompts BEGIN
    INSERT INTO prompts_fts(rowid, title, tags, body)
    VALUES (new.rowid, new.title, new.tags_csv, new.body);
END;

CREATE TRIGGER IF NOT EXISTS prompts_ad AFTER DELETE ON prompts BEGIN
    INSERT INTO prompts_fts(prompts_fts, rowid, title, tags, body)
    VALUES('delete', old.rowid, old.title, old.tags_csv, old.body);
END;

CREATE TRIGGER IF NOT EXISTS prompts_au AFTER UPDATE ON prompts BEGIN
    INSERT INTO prompts_fts(prompts_fts, rowid, title, tags, body)
    VALUES('delete', old.rowid, old.title, old.tags_csv, old.body);
    INSERT INTO prompts_fts(rowid, title, tags, body)
    VALUES (new.rowid, new.title, new.tags_csv, new.body);
END;

CREATE INDEX IF NOT EXISTS idx_prompts_scope ON prompts(scope);
CREATE INDEX IF NOT EXISTS idx_prompts_project ON prompts(project);
CREATE INDEX IF NOT EXISTS idx_prompts_origin ON prompts(origin);

CREATE TABLE IF NOT EXISTS projects (
    name             TEXT PRIMARY KEY,
    cwd_path         TEXT,
    fingerprint_json TEXT,
    last_seen        TEXT
);

-- Phase 7：每次 prompt 被 use 时记一条；用于 trending 排序。
CREATE TABLE IF NOT EXISTS usage_log (
    prompt_id TEXT NOT NULL,
    used_at   INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_usage_log_prompt ON usage_log(prompt_id);
CREATE INDEX IF NOT EXISTS idx_usage_log_time ON usage_log(used_at);
"""


def open_db(cfg: Config) -> sqlite3.Connection:
    cfg.vault_path.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(cfg.index_db)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.executescript(SCHEMA)
    _migrate_schema(conn)
    return conn


def _migrate_schema(conn: sqlite3.Connection) -> None:
    """加新列（categories_csv / is_template 等）—— 旧库升级用。"""
    cols = {row[1] for row in conn.execute("PRAGMA table_info(prompts)")}
    if "categories_csv" not in cols:
        try:
            conn.execute(
                "ALTER TABLE prompts ADD COLUMN categories_csv TEXT NOT NULL DEFAULT ''"
            )
            conn.commit()
        except Exception:
            pass
    if "is_template" not in cols:
        try:
            conn.execute(
                "ALTER TABLE prompts ADD COLUMN is_template INTEGER NOT NULL DEFAULT 0"
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_prompts_is_template ON prompts(is_template)")
            conn.commit()
        except Exception:
            pass
    # Phase 9：description / source_ref
    if "description" not in cols:
        try:
            conn.execute("ALTER TABLE prompts ADD COLUMN description TEXT NOT NULL DEFAULT ''")
            conn.commit()
        except Exception:
            pass
    if "source_ref" not in cols:
        try:
            conn.execute("ALTER TABLE prompts ADD COLUMN source_ref TEXT NOT NULL DEFAULT ''")
            conn.commit()
        except Exception:
            pass
    # Phase 12 C1：references_csv 缓存 [[标题]] 引用关系
    if "references_csv" not in cols:
        try:
            conn.execute("ALTER TABLE prompts ADD COLUMN references_csv TEXT NOT NULL DEFAULT ''")
            conn.commit()
        except Exception:
            pass
    # Phase 19：optimized_from_id 列，避免 find_optimized_pair 读文件扫盘卡死
    if "optimized_from_id" not in cols:
        try:
            conn.execute("ALTER TABLE prompts ADD COLUMN optimized_from_id TEXT NOT NULL DEFAULT ''")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_optimized_from ON prompts(optimized_from_id)")
            conn.commit()
        except Exception:
            pass
    # Phase 22.5：action_tag 列（14 个固定四字标签之一或空）
    if "action_tag" not in cols:
        try:
            conn.execute("ALTER TABLE prompts ADD COLUMN action_tag TEXT NOT NULL DEFAULT ''")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_action_tag ON prompts(action_tag)")
            conn.commit()
        except Exception:
            pass


def upsert(conn: sqlite3.Connection, p: Prompt, file_path: Path) -> None:
    # Phase 12 C1：自动算 references
    from . import references as _refs
    refs = _refs.find_references(p.body or "")
    refs_csv = ",".join(refs)
    conn.execute(
        """
        INSERT INTO prompts (id, title, scope, project, tags_csv, projects_csv, stack_csv,
                             triggers_csv, categories_csv, origin, used, success_signal,
                             last_used, created, file_path, body, is_template,
                             description, source_ref, references_csv, optimized_from_id,
                             action_tag)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            title=excluded.title,
            scope=excluded.scope,
            project=excluded.project,
            tags_csv=excluded.tags_csv,
            projects_csv=excluded.projects_csv,
            stack_csv=excluded.stack_csv,
            triggers_csv=excluded.triggers_csv,
            categories_csv=excluded.categories_csv,
            origin=excluded.origin,
            used=excluded.used,
            success_signal=excluded.success_signal,
            last_used=excluded.last_used,
            file_path=excluded.file_path,
            body=excluded.body,
            is_template=excluded.is_template,
            description=excluded.description,
            source_ref=excluded.source_ref,
            references_csv=excluded.references_csv,
            optimized_from_id=excluded.optimized_from_id,
            action_tag=excluded.action_tag
        """,
        (
            p.id, p.title, p.scope, p.project,
            ",".join(p.tags), ",".join(p.projects), ",".join(p.stack),
            ",".join(p.triggers), ",".join(p.categories),
            p.origin, p.used, p.success_signal,
            p.last_used, p.created, str(file_path), p.body,
            1 if p.is_template else 0,
            p.description, p.source_ref,
            refs_csv, p.optimized_from or "",
            p.action_tag or "",
        ),
    )
    conn.commit()


def find_optimized_pair(conn: sqlite3.Connection, prompt_id: str) -> sqlite3.Row | None:
    """P14 T2 + P19 重写：找同源对。**纯 SQL**，不再读文件，O(1)。

    依赖 optimized_from_id 列（Phase 19 schema migration）。
    旧库未填的可调 admin.reindex 一次填充。
    """
    # 方向 1：它的 optimized_from_id 指向某条
    me = conn.execute(
        "SELECT optimized_from_id FROM prompts WHERE id = ?", (prompt_id,),
    ).fetchone()
    if not me:
        return None
    try:
        parent_id = me["optimized_from_id"] or ""
    except (KeyError, IndexError):
        parent_id = ""
    if parent_id:
        other = conn.execute(
            "SELECT * FROM prompts WHERE id = ?", (parent_id,),
        ).fetchone()
        if other:
            return other
    # 方向 2：别人的 optimized_from_id 指向它
    other = conn.execute(
        "SELECT * FROM prompts WHERE optimized_from_id = ? LIMIT 1", (prompt_id,),
    ).fetchone()
    return other


def find_backlinks(conn: sqlite3.Connection, title: str) -> list[sqlite3.Row]:
    """找哪些 prompts 在正文 [[...]] 引用了 title。Phase 12 C1。

    SQL LIKE 转义：title 含 % / _ / \\ 时正确匹配。
    """
    # 转义 LIKE 通配符（用 \ 作 escape char）
    escaped = title.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    pattern = f"%{escaped}%"
    return list(conn.execute(
        "SELECT * FROM prompts WHERE references_csv LIKE ? ESCAPE '\\' AND title != ? "
        "ORDER BY used*2 + success_signal*3 DESC LIMIT 20",
        (pattern, title),
    ))


def delete_by_id(conn: sqlite3.Connection, prompt_id: str) -> None:
    conn.execute("DELETE FROM prompts WHERE id = ?", (prompt_id,))
    conn.commit()


def get_by_id(conn: sqlite3.Connection, prompt_id: str) -> sqlite3.Row | None:
    cur = conn.execute("SELECT * FROM prompts WHERE id = ?", (prompt_id,))
    return cur.fetchone()


def get_by_title(conn: sqlite3.Connection, title: str) -> sqlite3.Row | None:
    cur = conn.execute("SELECT * FROM prompts WHERE title = ? LIMIT 1", (title,))
    return cur.fetchone()


_SORT_CLAUSES = {
    "score": "(used*2 + success_signal*3) DESC, created DESC",
    "used": "used DESC, created DESC",
    "success": "success_signal DESC, used DESC",
    "last_used": "(last_used IS NULL) ASC, last_used DESC, created DESC",
    "created": "created DESC",
}


def list_all(conn: sqlite3.Connection, scope: str | None = None, project: str | None = None,
             categories: list[str] | None = None, limit: int = 200,
             sort_by: str = "score", is_template: bool | None = None,
             *, action_tag: str | None = None) -> list[sqlite3.Row]:
    """sort_by: score | used | success | last_used | created | trending

    is_template：None=不过滤；True=只看通用模板；False=只看原始材料（Phase 8）。
    action_tag：14 个动作类型标签之一；None=不过滤。
    """
    if sort_by == "trending":
        return _list_all_trending(conn, scope, project, categories, limit, is_template,
                                  action_tag=action_tag)

    sql = "SELECT * FROM prompts WHERE 1=1"
    params: list = []
    if scope:
        sql += " AND scope = ?"
        params.append(scope)
    if project:
        sql += " AND project = ?"
        params.append(project)
    if categories:
        cat_clauses = []
        for cat in categories:
            cat_clauses.append("categories_csv LIKE ?")
            params.append(f"%{cat}%")
        sql += " AND (" + " OR ".join(cat_clauses) + ")"
    if is_template is not None:
        sql += " AND is_template = ?"
        params.append(1 if is_template else 0)
    if action_tag:
        sql += " AND action_tag = ?"
        params.append(action_tag)
    order = _SORT_CLAUSES.get(sort_by, _SORT_CLAUSES["score"])
    sql += f" ORDER BY {order} LIMIT ?"
    params.append(limit)
    return list(conn.execute(sql, params))


def _list_all_trending(conn, scope, project, categories, limit, is_template=None,
                       *, action_tag=None):
    """trending：先查 trending 排序的 prompt_id 列表，再 join prompts。"""
    import time as _time
    cutoff = int(_time.time()) - 7 * 86400
    trending_rows = conn.execute(
        "SELECT prompt_id, COUNT(*) AS c FROM usage_log "
        "WHERE used_at >= ? GROUP BY prompt_id ORDER BY c DESC LIMIT ?",
        (cutoff, limit),
    ).fetchall()
    trending_ids = [r["prompt_id"] for r in trending_rows]
    if not trending_ids:
        return list_all(conn, scope=scope, project=project, categories=categories,
                        limit=limit, sort_by="last_used", is_template=is_template,
                        action_tag=action_tag)
    placeholders = ",".join("?" * len(trending_ids))
    sql = f"SELECT * FROM prompts WHERE id IN ({placeholders})"
    params: list = list(trending_ids)
    if scope:
        sql += " AND scope = ?"
        params.append(scope)
    if project:
        sql += " AND project = ?"
        params.append(project)
    if categories:
        cat_clauses = []
        for cat in categories:
            cat_clauses.append("categories_csv LIKE ?")
            params.append(f"%{cat}%")
        sql += " AND (" + " OR ".join(cat_clauses) + ")"
    if is_template is not None:
        sql += " AND is_template = ?"
        params.append(1 if is_template else 0)
    if action_tag:
        sql += " AND action_tag = ?"
        params.append(action_tag)
    rows = list(conn.execute(sql, params))
    order_map = {pid: i for i, pid in enumerate(trending_ids)}
    rows.sort(key=lambda r: order_map.get(r["id"], 9999))
    return rows


def count_templates(conn: sqlite3.Connection) -> dict:
    """统计 通用模板 vs 原始材料。Phase 8。"""
    row = conn.execute(
        "SELECT SUM(is_template) AS t, COUNT(*) AS total FROM prompts"
    ).fetchone()
    total = int(row["total"] or 0)
    templates = int(row["t"] or 0)
    return {"templates": templates, "raw": total - templates, "total": total}


def count_all(conn: sqlite3.Connection) -> dict[str, int]:
    out = {"total": 0, "global": 0, "project": 0, "trap": 0}
    for r in conn.execute("SELECT scope, COUNT(*) AS c FROM prompts GROUP BY scope"):
        out[r["scope"]] = r["c"]
    out["total"] = sum(v for k, v in out.items() if k != "total")
    return out


def count_by_action_tag(conn: sqlite3.Connection, *, is_template: bool | None = None) -> dict[str, int]:
    """聚合各 action_tag 的条目数（""=未标）。chips 用来显示每标签计数。"""
    sql = "SELECT action_tag, COUNT(*) AS c FROM prompts"
    params: list = []
    if is_template is not None:
        sql += " WHERE is_template = ?"
        params.append(1 if is_template else 0)
    sql += " GROUP BY action_tag"
    out: dict[str, int] = {}
    for r in conn.execute(sql, params):
        out[r["action_tag"] or ""] = r["c"]
    return out


def bump_used(conn: sqlite3.Connection, prompt_id: str) -> None:
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "UPDATE prompts SET used = used + 1, last_used = ? WHERE id = ?",
        (now, prompt_id),
    )
    # Phase 7：写 usage_log 用于 trending 排序
    import time as _time
    conn.execute(
        "INSERT INTO usage_log(prompt_id, used_at) VALUES (?, ?)",
        (prompt_id, int(_time.time())),
    )
    conn.commit()


def trending_score(conn: sqlite3.Connection, prompt_id: str, days: int = 7) -> int:
    """最近 N 天的使用次数。供 library.py 排序用。"""
    import time as _time
    cutoff = int(_time.time()) - days * 86400
    row = conn.execute(
        "SELECT COUNT(*) FROM usage_log WHERE prompt_id = ? AND used_at >= ?",
        (prompt_id, cutoff),
    ).fetchone()
    return int(row[0]) if row else 0


def list_trending(conn: sqlite3.Connection, days: int = 7, limit: int = 20) -> list:
    """按最近 N 天使用次数排序，返回 [(prompt_id, count), ...]。"""
    import time as _time
    cutoff = int(_time.time()) - days * 86400
    rows = conn.execute(
        "SELECT prompt_id, COUNT(*) AS c FROM usage_log "
        "WHERE used_at >= ? GROUP BY prompt_id ORDER BY c DESC LIMIT ?",
        (cutoff, limit),
    ).fetchall()
    return [(r["prompt_id"], r["c"]) for r in rows]


def bump_success(conn: sqlite3.Connection, prompt_id: str) -> None:
    conn.execute("UPDATE prompts SET success_signal = success_signal + 1 WHERE id = ?", (prompt_id,))
    conn.commit()


def bump_negative(conn: sqlite3.Connection, prompt_id: str) -> None:
    """Phase 11 B2：标记"不够好"，success_signal -1（但不低于负值上限 -5）。"""
    conn.execute(
        "UPDATE prompts SET success_signal = MAX(success_signal - 1, -5) WHERE id = ?",
        (prompt_id,),
    )
    conn.commit()


_FTS_SAFE_RE = re.compile(r"[^\w一-鿿]+", re.UNICODE)


def _sanitize_query(q: str) -> str:
    """把任意输入转成 FTS5 安全的 query：拆词后用 OR 连接，每个词加 *  做前缀匹配。"""
    tokens = [t for t in _FTS_SAFE_RE.split(q.strip()) if t]
    if not tokens:
        return ""
    # 用 OR 连，前缀匹配，避免单词拼写差异导致零命中
    return " OR ".join(f'"{t}"*' for t in tokens)


def search(
    conn: sqlite3.Connection,
    query: str,
    *,
    scope: str | None = None,
    project: str | None = None,
    categories: list[str] | None = None,
    is_template: bool | None = None,
    top_k: int = 10,
    action_tag: str | None = None,
) -> list[tuple[sqlite3.Row, float]]:
    """FTS5 检索 + 评分排序。返回 (row, score) 列表。"""
    fts_q = _sanitize_query(query)
    if not fts_q:
        return []

    sql = """
        SELECT p.*, bm25(prompts_fts) AS bm25
        FROM prompts_fts
        JOIN prompts p ON p.rowid = prompts_fts.rowid
        WHERE prompts_fts MATCH ?
    """
    params: list = [fts_q]
    if scope:
        sql += " AND p.scope = ?"
        params.append(scope)
    if project:
        sql += " AND p.project = ?"
        params.append(project)
    if categories:
        cat_clauses = []
        for cat in categories:
            cat_clauses.append("p.categories_csv LIKE ?")
            params.append(f"%{cat}%")
        sql += " AND (" + " OR ".join(cat_clauses) + ")"
    if is_template is not None:
        sql += " AND p.is_template = ?"
        params.append(1 if is_template else 0)
    if action_tag:
        sql += " AND p.action_tag = ?"
        params.append(action_tag)
    sql += f" ORDER BY bm25 ASC LIMIT {max(top_k * 5, 50)}"

    rows = list(conn.execute(sql, params))
    scored = [(r, _score(r)) for r in rows]
    scored.sort(key=lambda x: x[1], reverse=True)
    return scored[:top_k]


def _score(row: sqlite3.Row) -> float:
    """综合评分：FTS5 bm25 反向 × 使用频次 boost × 时间新近度。

    SQLite FTS5 bm25() 返回值越**负**越相关（典型范围 -30 到 0）。
    所以转成 base = -bm25，再做正向加成。
    """
    bm25 = float(row["bm25"]) if row["bm25"] is not None else 0.0
    base = max(-bm25, 0.001)  # 越负越好 → 越大越好
    used = int(row["used"] or 0)
    success = int(row["success_signal"] or 0)
    usage_boost = 1.0 + math.log(used * 2 + success * 3 + 1)
    decay = _recency_decay(row["last_used"] or row["created"])
    return base * usage_boost * decay


def _recency_decay(iso_time: str | None) -> float:
    """近期使用 → 1.0；半年没动 → 0.5。半衰期 180 天。"""
    if not iso_time:
        return 0.7
    try:
        t = datetime.fromisoformat(iso_time.replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        days = max(0.0, (now - t).total_seconds() / 86400.0)
        return 0.5 ** (days / 180.0)
    except Exception:
        return 0.7


def search_traps_for_text(conn: sqlite3.Connection, text: str, *, max_n: int = 2) -> list[sqlite3.Row]:
    """trap 召回：扫所有 trap 的 triggers_csv，命中即返回。"""
    text_lc = text.lower()
    hits: list[sqlite3.Row] = []
    for row in conn.execute("SELECT * FROM prompts WHERE scope = 'trap'"):
        triggers = [t.strip().lower() for t in (row["triggers_csv"] or "").split(",") if t.strip()]
        if not triggers:
            # 没显式 triggers 时，用 title 关键词兜底
            triggers = [row["title"].lower()]
        if any(trig in text_lc for trig in triggers):
            hits.append(row)
            if len(hits) >= max_n:
                break
    return hits


def reindex_from_disk(cfg: Config) -> int:
    """从文件系统全量重建索引。返回索引条目数。"""
    if cfg.index_db.exists():
        cfg.index_db.unlink()
    conn = open_db(cfg)
    n = 0
    for path, p in iter_prompts(cfg):
        upsert(conn, p, path)
        n += 1
    conn.close()
    return n


def existing_titles_and_bodies(conn: sqlite3.Connection) -> Iterable[tuple[str, str, str]]:
    """供 mining 去重用：返回 (id, title, body) 流。"""
    for row in conn.execute("SELECT id, title, body FROM prompts"):
        yield row["id"], row["title"], row["body"]


# ---------------------------------------------------------------------------
# 项目注册表（SessionStart 跨项目召回用）
# ---------------------------------------------------------------------------

def register_project(
    conn: sqlite3.Connection,
    name: str,
    cwd_path: str,
    fingerprint_json: str,
) -> None:
    """登记 / 更新一个项目的 cwd 和指纹快照。"""
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """
        INSERT INTO projects (name, cwd_path, fingerprint_json, last_seen)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(name) DO UPDATE SET
            cwd_path = excluded.cwd_path,
            fingerprint_json = excluded.fingerprint_json,
            last_seen = excluded.last_seen
        """,
        (name, cwd_path, fingerprint_json, now),
    )
    conn.commit()


def list_projects(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return list(conn.execute("SELECT * FROM projects ORDER BY last_seen DESC"))


def delete_project(conn: sqlite3.Connection, name: str) -> int:
    """删除一个已登记项目。不删除该项目下的 prompts（用户可能想保留），
    只清掉 projects 表里的注册记录。返回删除条数。"""
    cur = conn.execute("DELETE FROM projects WHERE name = ?", (name,))
    conn.commit()
    return cur.rowcount
