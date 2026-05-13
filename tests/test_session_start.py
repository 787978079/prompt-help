"""SessionStart hook：栈匹配 + 跨项目相似召回。"""

import json
from pathlib import Path

import pytest

from prompt_help.core import indexer, storage
from prompt_help.core.config import Config, GitConfig
from prompt_help.core.fingerprint import Fingerprint, to_dict
from prompt_help.plugin.hooks import session_start


@pytest.fixture
def cfg(tmp_path: Path) -> Config:
    c = Config(vault_path=tmp_path)
    c.git = GitConfig(auto_commit=False)
    for d in ("prompts/global", "prompts/projects/minpei", "prompts/traps", "inbox"):
        (tmp_path / d).mkdir(parents=True)
    return c


def test_empty_library(cfg: Config, tmp_path: Path):
    indexer.open_db(cfg).close()
    cwd = tmp_path / "fake-proj"; cwd.mkdir()
    assert session_start.run({"cwd": str(cwd)}, cfg) is None


def test_stack_match(cfg: Config, tmp_path: Path):
    # 入库一条带 stack 的 global 提示词
    p = storage.Prompt.new(
        title="Next.js 路由约定",
        body="App Router 用 file-based routing",
        scope="global",
        stack=["nextjs", "react"],
    )
    fp_path = storage.save(cfg, p)
    conn = indexer.open_db(cfg)
    indexer.upsert(conn, p, fp_path)
    conn.close()

    # 模拟一个 Next.js 项目 cwd
    cwd = tmp_path / "my-next-app"
    cwd.mkdir()
    (cwd / "package.json").write_text(
        json.dumps({"dependencies": {"next": "^16", "react": "^19"}}),
        encoding="utf-8",
    )
    (cwd / "page.tsx").write_text("x", encoding="utf-8")

    out = session_start.run({"cwd": str(cwd)}, cfg)
    assert out is not None
    assert "Next.js 路由约定" in out
    assert "stack" not in out  # source=stack 标签会省略


def test_cross_project_recall(cfg: Config, tmp_path: Path):
    # 登记一个历史项目 minpei，其指纹包含 nextjs+react+postgres
    fake_minpei_fp = Fingerprint(
        project_name="minpei",
        langs={"javascript", "typescript"},
        frameworks={"next", "react", "pg"},
        keywords={"heritage", "wuyi"},
    )
    conn = indexer.open_db(cfg)
    indexer.register_project(
        conn, name="minpei",
        cwd_path="/d/My_Project/MinPEI",
        fingerprint_json=json.dumps(to_dict(fake_minpei_fp), ensure_ascii=False),
    )

    # 在 minpei 下放一条 project-scope 提示词
    p = storage.Prompt.new(
        title="MinPEI 生产分歧注意",
        body="生产是手动 hotfix",
        scope="project",
        project="minpei",
    )
    fp_path = storage.save(cfg, p)
    indexer.upsert(conn, p, fp_path)
    conn.close()

    # 当前 cwd 模拟另一个 Next.js + React 项目
    cwd = tmp_path / "minpei-v2"
    cwd.mkdir()
    (cwd / "package.json").write_text(
        json.dumps({"dependencies": {"next": "^16", "react": "^19", "pg": "^8"}}),
        encoding="utf-8",
    )

    out = session_start.run({"cwd": str(cwd)}, cfg)
    assert out is not None
    assert "minpei" in out  # 应该被识别为相似历史项目
    assert "MinPEI 生产分歧注意" in out


def test_inbox_warning(cfg: Config, tmp_path: Path):
    # 库里塞一条提示词凑数据 + inbox 放一条候选
    p = storage.Prompt.new(title="any", body="b", scope="global")
    fp_path = storage.save(cfg, p)
    conn = indexer.open_db(cfg)
    indexer.upsert(conn, p, fp_path)
    conn.close()
    (cfg.inbox_dir / "20260509T010000-00001.md").write_text(
        "---\nconfidence: 0.7\n---\n\nbody", encoding="utf-8"
    )
    cwd = tmp_path / "x"; cwd.mkdir()
    out = session_start.run({"cwd": str(cwd)}, cfg)
    assert out is not None
    assert "1 条 mining 候选待审" in out
