"""fingerprint 单测。"""

import json
from pathlib import Path

from prompt_help.core.fingerprint import (
    Fingerprint,
    fingerprint,
    jaccard_similarity,
    stack_overlap,
)


def test_fingerprint_python_project(tmp_path: Path):
    (tmp_path / "main.py").write_text("print('hi')")
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "x"\ndependencies = ["typer", "rich"]\n'
    )
    (tmp_path / "README.md").write_text("# Hello\nThis is a CLI tool")
    fp = fingerprint(tmp_path)
    assert "python" in fp.langs
    assert "typer" in fp.frameworks
    assert "rich" in fp.frameworks
    assert "hello" in fp.keywords or "tool" in fp.keywords


def test_fingerprint_nextjs(tmp_path: Path):
    (tmp_path / "package.json").write_text(json.dumps({
        "name": "app",
        "dependencies": {"next": "^16", "react": "^19"},
        "devDependencies": {"typescript": "^5"},
    }))
    (tmp_path / "page.tsx").write_text("export default function() {}")
    fp = fingerprint(tmp_path)
    assert "javascript" in fp.langs
    assert "typescript" in fp.langs
    assert "react" in fp.frameworks
    assert "nextjs" in fp.frameworks


def test_jaccard():
    a = Fingerprint("a", langs={"python"}, frameworks={"typer"}, keywords={"cli"})
    b = Fingerprint("b", langs={"python"}, frameworks={"typer", "rich"}, keywords={"tool"})
    sim = jaccard_similarity(a, b)
    # union={python, typer, cli, rich, tool} = 5; inter={python, typer} = 2
    assert abs(sim - 2 / 5) < 0.01


def test_stack_overlap():
    fp = Fingerprint("x", langs={"python"}, frameworks={"fastapi", "sqlalchemy"})
    assert stack_overlap(["python", "fastapi"], fp) == 1.0
    assert stack_overlap(["nextjs"], fp) == 0.0
    assert 0.0 < stack_overlap(["python", "nextjs"], fp) < 1.0
