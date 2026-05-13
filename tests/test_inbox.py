"""inbox CLI 单测：InboxItem 解析 + approve/dismiss。"""

from pathlib import Path

import pytest
from typer.testing import CliRunner

import typer

from prompt_help.cli import inbox as inbox_cli
from prompt_help.core import indexer
from prompt_help.core.config import Config, GitConfig


@pytest.fixture
def cfg(tmp_path: Path, monkeypatch) -> Config:
    monkeypatch.setenv("PROMPT_HELP_VAULT_PATH", str(tmp_path))
    c = Config(vault_path=tmp_path)
    c.git = GitConfig(auto_commit=False)
    for d in ("prompts/global", "prompts/projects", "prompts/traps", "inbox"):
        (tmp_path / d).mkdir(parents=True)
    return c


def _make_inbox_file(cfg: Config, body: str, confidence: float = 0.7,
                    suggested_title: str = "") -> Path:
    f = cfg.inbox_dir / "20260509T120000-12345.md"
    f.write_text(
        f"---\nconfidence: {confidence}\nsuggested_title: {suggested_title}\n"
        f"created: 20260509T120000\norigin: stop\n---\n\n{body}\n",
        encoding="utf-8",
    )
    return f


def test_inbox_item_load(cfg: Config):
    f = _make_inbox_file(cfg, "测试正文", confidence=0.85, suggested_title="测试标题")
    item = inbox_cli.InboxItem.load(f)
    assert item.confidence == 0.85
    assert item.suggested_title == "测试标题"
    assert item.body.strip() == "测试正文"
    assert item.origin == "stop"


def test_inbox_list_and_approve(cfg: Config):
    body = "你的任务：写一个 CLI 工具。\n要求：1. 用 Typer\n2. 有 doctor 命令"
    f = _make_inbox_file(cfg, body, confidence=0.8, suggested_title="CLI 工具脚手架")

    items = inbox_cli._all_items(cfg)
    assert len(items) == 1
    assert items[0].suggested_title == "CLI 工具脚手架"

    # approve（不调 polish 避免依赖 API key）
    app = typer.Typer()
    inbox_cli.register(app)
    runner = CliRunner()
    result = runner.invoke(app, [
        "inbox", "approve", f.name,
        "--title", "我的 CLI 模板",
        "--scope", "global",
        "--tags", "cli,python",
        "--no-polish",
    ])
    assert result.exit_code == 0, result.output

    # 原文件应已删除
    assert not f.exists()

    # 库里应有一条
    conn = indexer.open_db(cfg)
    counts = indexer.count_all(conn)
    assert counts["total"] == 1
    rows = indexer.list_all(conn)
    assert rows[0]["title"] == "我的 CLI 模板"
    assert rows[0]["origin"] == "mining"
    conn.close()


def test_inbox_dismiss(cfg: Config):
    f = _make_inbox_file(cfg, "废弃的候选")
    app = typer.Typer()
    inbox_cli.register(app)
    runner = CliRunner()
    result = runner.invoke(app, ["inbox", "dismiss", f.name])
    assert result.exit_code == 0
    assert not f.exists()


def test_inbox_resolve_by_prefix(cfg: Config):
    f = _make_inbox_file(cfg, "前缀匹配测试")
    item = inbox_cli._resolve(cfg, "20260509T120000")
    assert item.path == f
