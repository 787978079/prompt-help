"""PreCompact hook 单测：模拟 transcript 验证候选挖掘逻辑。"""

import json
from pathlib import Path

import pytest

from prompt_help.core import indexer
from prompt_help.core.config import Config, GitConfig
from prompt_help.plugin.hooks import pre_compact


@pytest.fixture
def cfg(tmp_path: Path) -> Config:
    c = Config(vault_path=tmp_path)
    c.git = GitConfig(auto_commit=False)
    for d in ("prompts/global", "inbox"):
        (tmp_path / d).mkdir(parents=True)
    indexer.open_db(c).close()
    return c


def _write_transcript(tmp: Path, user_msgs: list[str], assistant_msgs: list[str]) -> Path:
    """交错写入 user / assistant 消息。"""
    p = tmp / "session.jsonl"
    lines = []
    for i, u in enumerate(user_msgs):
        lines.append(json.dumps({"type": "user",
                                  "message": {"role": "user", "content": u}}))
        if i < len(assistant_msgs):
            lines.append(json.dumps({"type": "assistant", "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": assistant_msgs[i]}]}}))
    p.write_text("\n".join(lines), encoding="utf-8")
    return p


def test_extract_structured_user_messages(cfg: Config, tmp_path: Path):
    long_struct = (
        "你的任务：实现一个 SQLite FTS5 索引器，支撑跨项目提示词检索。\n\n"
        "要求：\n"
        "1. 支持 upsert：插入或更新一条提示词，按 id 唯一约束\n"
        "2. 全文检索：用 FTS5 虚拟表，title/tags/body 三列，unicode61 tokenizer\n"
        "3. 评分排序：BM25 反向 × 使用频次 boost × 时间新近度衰减\n"
        "4. trap 召回：扫所有 trap scope 的 triggers_csv，命中即返回\n"
        "5. 重建索引：从文件系统全量扫盘重建，幂等\n\n"
        "约束：所有错误必须吞掉，永不阻塞父进程；FTS5 触发器要正确处理 INSERT/UPDATE/DELETE。"
    )
    user_msgs = [
        "短消息忽略",
        long_struct,
        "另一个无结构的长消息" * 30,  # 长但无结构标记
    ]
    p = _write_transcript(tmp_path, user_msgs, ["收到", "好"])
    result = pre_compact.run({"transcript_path": str(p)}, cfg)
    assert result is not None
    assert "PreCompact 挖掘" in result
    # inbox 里应有 1 条
    items = list(cfg.inbox_dir.glob("*.md"))
    assert len(items) == 1
    body = items[0].read_text(encoding="utf-8")
    assert "SQLite FTS5" in body
    assert "origin: pre-compact" in body


def test_skips_when_all_too_similar_to_db(cfg: Config, tmp_path: Path):
    # 先把候选预存入库，模拟"已存在"
    from prompt_help.core import storage
    body = (
        "你的任务：实现一个 SQLite FTS5 索引器，支撑跨项目提示词检索。\n\n"
        "要求：\n"
        "1. 支持 upsert：插入或更新一条提示词，按 id 唯一约束\n"
        "2. 全文检索：用 FTS5 虚拟表，title/tags/body 三列，unicode61 tokenizer\n"
        "3. 评分排序：BM25 反向 × 使用频次 boost × 时间新近度衰减\n"
        "4. trap 召回：扫所有 trap scope 的 triggers_csv，命中即返回\n"
        "5. 重建索引：从文件系统全量扫盘重建，幂等"
    )
    p1 = storage.Prompt.new(title="FTS5 索引器", body=body, scope="global")
    f1 = storage.save(cfg, p1)
    conn = indexer.open_db(cfg)
    indexer.upsert(conn, p1, f1)
    conn.close()

    # 然后跑 PreCompact，候选应被去重过滤
    p = _write_transcript(tmp_path, [body], ["完美"])
    result = pre_compact.run({"transcript_path": str(p)}, cfg)
    assert result is None
    assert list(cfg.inbox_dir.glob("*.md")) == []


def test_no_transcript_path(cfg: Config):
    assert pre_compact.run({}, cfg) is None


def test_disabled_when_mining_off(cfg: Config, tmp_path: Path):
    cfg.mining.enabled = False
    body = "你的任务：" + "abc " * 80
    p = _write_transcript(tmp_path, [body], ["完美"])
    assert pre_compact.run({"transcript_path": str(p)}, cfg) is None
