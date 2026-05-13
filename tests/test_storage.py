"""storage 模块单测：frontmatter 序列化 / 反序列化 / 落盘 / 路径生成。"""

from pathlib import Path

import pytest

from prompt_help.core import storage
from prompt_help.core.config import Config, GitConfig


@pytest.fixture
def cfg(tmp_path: Path) -> Config:
    c = Config(vault_path=tmp_path)
    c.git = GitConfig(auto_commit=False)  # 测试不触 git
    (tmp_path / "prompts" / "global").mkdir(parents=True)
    (tmp_path / "prompts" / "projects").mkdir()
    (tmp_path / "prompts" / "traps").mkdir()
    return c


def test_serialize_roundtrip():
    p = storage.Prompt.new(
        title="测试提示词",
        body="这里是正文\n含多行内容",
        scope="global",
        tags=["test", "playwright"],
        stack=["python"],
    )
    text = storage.serialize(p)
    assert "---" in text
    assert "title: 测试提示词" in text
    assert "tags:" in text

    parsed = storage.parse(text)
    assert parsed.title == p.title
    assert parsed.body.strip() == p.body.strip()
    assert parsed.tags == ["test", "playwright"]
    assert parsed.scope == "global"


def test_save_global(cfg: Config):
    p = storage.Prompt.new(title="hello world", body="一些内容", scope="global")
    path = storage.save(cfg, p)
    assert path.exists()
    assert "global" in str(path)

    loaded = storage.load(path)
    assert loaded.title == "hello world"
    assert loaded.id == p.id


def test_save_project(cfg: Config):
    p = storage.Prompt.new(
        title="MinPEI API 注意",
        body="生产是手动 hotfix",
        scope="project",
        project="minpei",
    )
    path = storage.save(cfg, p)
    assert "minpei" in str(path)
    assert path.exists()


def test_save_trap(cfg: Config):
    p = storage.Prompt.new(
        title="Node 自杀禁令",
        body="不要批量杀 node.exe",
        scope="trap",
        triggers=["taskkill node", "杀 node", "kill node"],
    )
    path = storage.save(cfg, p)
    assert "traps" in str(path)
    loaded = storage.load(path)
    assert loaded.triggers == ["taskkill node", "杀 node", "kill node"]


def test_iter_prompts(cfg: Config):
    storage.save(cfg, storage.Prompt.new(title="a", body="aaa", scope="global"))
    storage.save(cfg, storage.Prompt.new(title="b", body="bbb", scope="global"))
    storage.save(cfg, storage.Prompt.new(title="t", body="trap", scope="trap"))
    items = list(cfg.prompts_dir.rglob("*.md"))
    assert len(items) == 3

    found = [p.title for _path, p in storage.iter_prompts(cfg)]
    assert sorted(found) == ["a", "b", "t"]


def test_slugify():
    assert storage.slugify("Hello World!") == "hello-world"
    assert storage.slugify("中文 测试 prompt") == "中文-测试-prompt"
    assert storage.slugify("") == "untitled"
