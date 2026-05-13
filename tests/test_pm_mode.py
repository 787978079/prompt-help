"""PM-Mode 单测：状态机 + brief 装配 + 辅助命令。"""

import json
from pathlib import Path

import pytest
import typer
from typer.testing import CliRunner

from prompt_help.cli import pm_mode
from prompt_help.core import indexer, storage
from prompt_help.core.config import Config, GitConfig


@pytest.fixture
def cfg(tmp_path: Path, monkeypatch) -> Config:
    monkeypatch.setenv("PROMPT_HELP_VAULT_PATH", str(tmp_path))
    c = Config(vault_path=tmp_path)
    c.git = GitConfig(auto_commit=False)
    for d in ("prompts/global", "prompts/projects", "prompts/traps", "briefs/_active", "inbox"):
        (tmp_path / d).mkdir(parents=True)
    indexer.open_db(c).close()
    return c


@pytest.fixture
def app(cfg: Config) -> typer.Typer:
    a = typer.Typer()
    pm_mode.register(a)
    return a


def test_start_creates_draft(app, cfg: Config):
    runner = CliRunner()
    result = runner.invoke(app, [
        "pm-mode", "start", "auto-summarize arxiv to 1-page PDF",
        "--slug", "arxiv1p",
    ])
    assert result.exit_code == 0, result.output
    f = cfg.briefs_dir / "_active" / "arxiv1p.json"
    assert f.is_file()
    data = json.loads(f.read_text(encoding="utf-8"))
    assert data["slug"] == "arxiv1p"
    assert "arxiv" in data["idea"].lower()
    # 阶段框架已铺好
    assert all(s in data["stages"] for s in pm_mode.STAGES)


def test_set_records_answers(app, cfg: Config):
    runner = CliRunner()
    runner.invoke(app, ["pm-mode", "start", "test idea", "--slug", "x"])
    result = runner.invoke(app, [
        "pm-mode", "set", "problem",
        "pain_one_liner=Grad students drowning in arXiv",
        "motivation=b",
        "freshness=weekly",
    ])
    assert result.exit_code == 0, result.output
    data = json.loads((cfg.briefs_dir / "_active" / "x.json").read_text(encoding="utf-8"))
    assert data["stages"]["problem"]["pain_one_liner"] == "Grad students drowning in arXiv"
    assert data["stages"]["problem"]["motivation"] == "b"


def test_set_with_json_array(app, cfg: Config):
    runner = CliRunner()
    runner.invoke(app, ["pm-mode", "start", "x", "--slug", "y"])
    result = runner.invoke(app, [
        "pm-mode", "set", "scope",
        'in=["arxiv-fetch","pdf-render","summary"]',
        'later=["zotero","cite-graph"]',
        'never=["auth"]',
        "time_budget=1_week",
    ])
    assert result.exit_code == 0, result.output
    data = json.loads((cfg.briefs_dir / "_active" / "y.json").read_text(encoding="utf-8"))
    assert data["stages"]["scope"]["in"] == ["arxiv-fetch", "pdf-render", "summary"]
    assert data["stages"]["scope"]["never"] == ["auth"]


def test_brief_assembly(app, cfg: Config):
    runner = CliRunner()
    runner.invoke(app, ["pm-mode", "start", "arxiv 1pager",
                         "--slug", "arxiv", "--cwd", str(cfg.vault_path)])
    runner.invoke(app, ["pm-mode", "set", "problem",
                         "pain_one_liner=Grad students can't triage preprints",
                         "motivation=b", "freshness=weekly"])
    runner.invoke(app, ["pm-mode", "set", "users",
                         "archetype=ML grad students",
                         "trigger_moment=After Twitter scroll, 8 tabs open",
                         "current_solution=cobbled"])
    runner.invoke(app, ["pm-mode", "set", "scope",
                         'in=["arxiv-fetch","summary","pdf"]',
                         "time_budget=1_week", "success=5_friends"])
    runner.invoke(app, ["pm-mode", "set", "tech_risks",
                         'selected=["rate_limit","latex_extract"]',
                         "killer_risk=Summary inaccuracy destroys trust"])
    runner.invoke(app, ["pm-mode", "set", "metric",
                         "kpi=friend_quality_rating"])
    result = runner.invoke(app, ["pm-mode", "brief", "--archive-only"])
    assert result.exit_code == 0, result.output
    # 归档文件应已生成
    archives = list(cfg.briefs_dir.glob("*.md"))
    assert len(archives) == 1
    md = archives[0].read_text(encoding="utf-8")
    assert "kind: product_brief" in md
    assert "Grad students can't triage" in md
    assert "ML grad students" in md
    assert "1_week" in md
    assert "Summary inaccuracy" in md
    assert "friend_quality_rating" in md


def test_brief_writes_to_cwd(app, cfg: Config, tmp_path: Path):
    runner = CliRunner()
    proj_root = tmp_path / "myproj"
    proj_root.mkdir()
    runner.invoke(app, ["pm-mode", "start", "y", "--slug", "yy",
                         "--cwd", str(proj_root)])
    runner.invoke(app, ["pm-mode", "set", "problem", "pain_one_liner=Y"])
    result = runner.invoke(app, ["pm-mode", "brief"])
    assert result.exit_code == 0, result.output
    assert (proj_root / "PRODUCT_BRIEF.md").is_file()


def test_list_drafts(app, cfg: Config):
    runner = CliRunner()
    runner.invoke(app, ["pm-mode", "start", "first idea", "--slug", "a"])
    runner.invoke(app, ["pm-mode", "start", "second idea", "--slug", "b"])
    result = runner.invoke(app, ["pm-mode", "list", "--json"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    slugs = {x["slug"] for x in data}
    assert slugs == {"a", "b"}


def test_motivation_risk_classification(app, cfg: Config):
    runner = CliRunner()
    runner.invoke(app, ["pm-mode", "start", "tech driven", "--slug", "t"])
    runner.invoke(app, ["pm-mode", "set", "problem", "motivation=c"])
    result = runner.invoke(app, ["pm-mode", "brief", "--archive-only"])
    assert result.exit_code == 0
    md = list(cfg.briefs_dir.glob("*.md"))[0].read_text(encoding="utf-8")
    assert "solution-in-search-of-problem" in md


def test_prior_art_suggest_uses_registered_projects(app, cfg: Config):
    # 登记一个项目
    conn = indexer.open_db(cfg)
    indexer.register_project(
        conn, name="bpgo",
        cwd_path="/d/projects/bpgo",
        fingerprint_json=json.dumps({
            "project_name": "bpgo",
            "langs": ["python"],
            "frameworks": ["pdf", "arxiv", "claude"],
            "keywords": ["business", "plan", "academic", "summary"],
        }, ensure_ascii=False),
    )
    conn.close()
    runner = CliRunner()
    result = runner.invoke(app, [
        "pm-mode", "prior-art-suggest",
        "auto-summarize arxiv academic papers", "--json",
    ])
    assert result.exit_code == 0
    data = json.loads(result.output)
    names = [p["name"] for p in data["your_past_projects"]]
    assert "bpgo" in names


def test_tech_risks_suggest_finds_traps(app, cfg: Config):
    # 入库一条 trap，stack 含 nextjs
    p = storage.Prompt.new(
        title="Tailwind 4 配置陷阱",
        body="不要用 tailwind.config.js，要在 globals.css 里 @theme inline",
        scope="trap",
        stack=["nextjs", "tailwind"],
        triggers=["tailwind.config.js", "tailwind 4"],
    )
    fp = storage.save(cfg, p)
    conn = indexer.open_db(cfg)
    indexer.upsert(conn, p, fp)
    conn.close()

    runner = CliRunner()
    result = runner.invoke(app, ["pm-mode", "tech-risks-suggest",
                                  "nextjs,tailwind", "--json"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    titles = [r["title"] for r in data["risks"]]
    assert "Tailwind 4 配置陷阱" in titles


def test_delete_draft(app, cfg: Config):
    runner = CliRunner()
    runner.invoke(app, ["pm-mode", "start", "x", "--slug", "todel"])
    assert (cfg.briefs_dir / "_active" / "todel.json").is_file()
    result = runner.invoke(app, ["pm-mode", "delete", "todel"])
    assert result.exit_code == 0
    assert not (cfg.briefs_dir / "_active" / "todel.json").is_file()
