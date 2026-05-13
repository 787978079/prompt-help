"""提示词存储：单条 markdown + YAML frontmatter，文件系统是真相源。

Scope 决定落盘位置：
- global → prompts/global/<slug>.md
- project:<name> → prompts/projects/<name>/<slug>.md
- trap:<name> → prompts/traps/<name>.md  （trap 直接用 name 命名，便于关键词触发）

每次 save/update/delete 触发 git auto-commit（若 cfg.git.auto_commit）。
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Literal

import yaml
from ulid import ULID

from .config import Config

Scope = Literal["global", "project", "trap"]
Origin = Literal["manual", "mining", "imported", "github"]


@dataclass
class Prompt:
    id: str
    title: str
    body: str
    scope: Scope = "global"
    project: str | None = None  # scope=project 时使用
    tags: list[str] = field(default_factory=list)
    projects: list[str] = field(default_factory=list)  # 适用项目（可多个）
    stack: list[str] = field(default_factory=list)
    triggers: list[str] = field(default_factory=list)  # trap 关键词触发
    categories: list[str] = field(default_factory=list)  # T4 自动分类（前端/后端/...）
    origin: Origin = "manual"
    source_url: str | None = None
    created: str = field(default_factory=lambda: dt.datetime.now(dt.timezone.utc).isoformat())
    used: int = 0
    last_used: str | None = None
    success_signal: int = 0
    optimized_from: str | None = None
    is_template: bool = False  # Phase 8：True = 已通用化的可分享模板版
    description: str = ""       # Phase 9：用户写或 LLM 生成的一句话描述
    source_ref: str = ""        # Phase 9：参考来源（项目名 / 网页 URL / 文件路径）

    @classmethod
    def new(
        cls,
        title: str,
        body: str,
        scope: Scope = "global",
        project: str | None = None,
        tags: list[str] | None = None,
        stack: list[str] | None = None,
        origin: Origin = "manual",
        triggers: list[str] | None = None,
        *,
        # 之前这些字段必须 new() 完再手动赋值，结果调用方常漏。
        # 改成 kwargs 一次给齐，确保"参考来源"等元数据真的落盘。
        source_ref: str = "",
        source_url: str | None = None,
        description: str = "",
        optimized_from: str | None = None,
        is_template: bool = False,
        categories: list[str] | None = None,
        projects: list[str] | None = None,
    ) -> Prompt:
        return cls(
            id=str(ULID()),
            title=title.strip(),
            body=body.strip(),
            scope=scope,
            project=project,
            tags=tags or [],
            stack=stack or [],
            origin=origin,
            triggers=triggers or [],
            projects=projects if projects is not None else ([project] if project else []),
            source_ref=source_ref,
            source_url=source_url,
            description=description,
            optimized_from=optimized_from,
            is_template=is_template,
            categories=categories or [],
        )


_SLUG_RE = re.compile(r"[^a-zA-Z0-9一-鿿]+")


def slugify(text: str, max_len: int = 60) -> str:
    s = _SLUG_RE.sub("-", text.strip()).strip("-").lower()
    return s[:max_len] or "untitled"


def file_path_for(cfg: Config, p: Prompt) -> Path:
    base = cfg.prompts_dir
    if p.scope == "global":
        return base / "global" / f"{slugify(p.title)}-{p.id[-6:]}.md"
    if p.scope == "project":
        proj = p.project or "unknown"
        return base / "projects" / proj / f"{slugify(p.title)}-{p.id[-6:]}.md"
    if p.scope == "trap":
        return base / "traps" / f"{slugify(p.title)}-{p.id[-6:]}.md"
    raise ValueError(f"未知 scope: {p.scope}")


def serialize(p: Prompt) -> str:
    """转成 markdown + frontmatter 文本。"""
    fm = {
        "id": p.id,
        "title": p.title,
        "scope": p.scope,
        "project": p.project,
        "tags": p.tags,
        "projects": p.projects,
        "stack": p.stack,
        "triggers": p.triggers,
        "categories": p.categories,
        "origin": p.origin,
        "source_url": p.source_url,
        "created": p.created,
        "used": p.used,
        "last_used": p.last_used,
        "success_signal": p.success_signal,
        "optimized_from": p.optimized_from,
        "is_template": p.is_template if p.is_template else None,
        "description": p.description or None,
        "source_ref": p.source_ref or None,
    }
    # 移除 None 值
    fm = {k: v for k, v in fm.items() if v is not None}
    fm_str = yaml.safe_dump(fm, allow_unicode=True, sort_keys=False).strip()
    return f"---\n{fm_str}\n---\n\n{p.body.rstrip()}\n"


def parse(text: str) -> Prompt:
    """解析 markdown + frontmatter 文本。"""
    if not text.startswith("---"):
        raise ValueError("文件缺少 frontmatter 起始标记")
    parts = text.split("---", 2)
    if len(parts) < 3:
        raise ValueError("frontmatter 不完整")
    fm = yaml.safe_load(parts[1]) or {}
    body = parts[2].lstrip("\n").rstrip() + "\n"
    return Prompt(
        id=str(fm.get("id") or ULID()),
        title=str(fm.get("title", "untitled")),
        body=body,
        scope=fm.get("scope", "global"),
        project=fm.get("project"),
        tags=list(fm.get("tags") or []),
        projects=list(fm.get("projects") or []),
        stack=list(fm.get("stack") or []),
        triggers=list(fm.get("triggers") or []),
        categories=list(fm.get("categories") or []),
        origin=fm.get("origin", "manual"),
        source_url=fm.get("source_url"),
        created=fm.get("created") or dt.datetime.now(dt.timezone.utc).isoformat(),
        used=int(fm.get("used") or 0),
        last_used=fm.get("last_used"),
        success_signal=int(fm.get("success_signal") or 0),
        optimized_from=fm.get("optimized_from"),
        is_template=bool(fm.get("is_template", False)),
        description=str(fm.get("description") or ""),
        source_ref=str(fm.get("source_ref") or ""),
    )


def save(cfg: Config, p: Prompt, *, commit_msg: str | None = None) -> Path:
    """落盘并触发 git commit（若开启）。返回写入路径。"""
    path = file_path_for(cfg, p)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(serialize(p), encoding="utf-8")
    if cfg.git.auto_commit:
        _git_commit(cfg, [path], commit_msg or f"save: {p.title} ({p.id[-6:]})")
    return path


def update(cfg: Config, p: Prompt, *, commit_msg: str | None = None) -> Path:
    """更新已存在的提示词文件。"""
    return save(cfg, p, commit_msg=commit_msg or f"update: {p.title} ({p.id[-6:]})")


def delete(cfg: Config, p: Prompt) -> None:
    path = file_path_for(cfg, p)
    if path.is_file():
        path.unlink()
    if cfg.git.auto_commit:
        _git_commit(cfg, [path], f"delete: {p.title} ({p.id[-6:]})", allow_missing=True)


def load(path: Path) -> Prompt:
    return parse(path.read_text(encoding="utf-8"))


def iter_prompts(cfg: Config):
    """遍历 vault 内所有提示词文件。"""
    base = cfg.prompts_dir
    if not base.is_dir():
        return
    for md in base.rglob("*.md"):
        try:
            yield md, load(md)
        except Exception:
            # 损坏文件跳过，不阻塞
            continue


def _git_commit(cfg: Config, paths: list[Path], message: str, *, allow_missing: bool = False) -> None:
    """对 vault 仓库执行 add + commit；失败静默吞（不阻塞主流程）。"""
    try:
        from git import Repo
        from git.exc import InvalidGitRepositoryError, NoSuchPathError

        try:
            repo = Repo(cfg.vault_path)
        except (InvalidGitRepositoryError, NoSuchPathError):
            return  # 还没 init，正常跳过

        # 配置默认 user（若 vault 没设过）
        with repo.config_writer() as cw:
            try:
                if not cw.has_section("user"):
                    cw.add_section("user")
                cw.set("user", "name", cfg.git.commit_user_name)
                cw.set("user", "email", cfg.git.commit_user_email)
            except Exception:
                pass

        # add
        for p in paths:
            try:
                if p.exists():
                    repo.index.add([str(p.relative_to(cfg.vault_path))])
                elif allow_missing:
                    repo.index.remove([str(p.relative_to(cfg.vault_path))], working_tree=False)
            except Exception:
                continue

        # commit only if there are staged changes
        if repo.is_dirty(index=True, working_tree=False, untracked_files=False) or repo.untracked_files:
            try:
                repo.index.commit(message)
            except Exception:
                pass

        # 可选 push
        if cfg.git.auto_push and cfg.git.remote_url:
            try:
                origin = repo.remote(cfg.git.remote_name)
                origin.push()
            except Exception:
                pass
    except Exception:
        return  # 永不阻塞调用方
