"""管理命令：import-claude-md / reindex / sync / prune / why-matched / inbox-add。"""

from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from ..core import indexer, optimizer, proc, storage
from ..core.config import Config, load_config, load_dotenv_if_present
from ..core.fingerprint import fingerprint, jaccard_similarity, stack_overlap

console = Console()
err_console = Console(stderr=True, style="red")


def _config() -> Config:
    load_dotenv_if_present()
    return load_config()


def register(app: typer.Typer) -> None:
    app.command(name="import-claude-md")(import_claude_md_cmd)
    app.command(name="bulk-import-projects")(bulk_import_projects_cmd)
    app.command(name="reindex")(reindex_cmd)
    app.command(name="sync")(sync_cmd)
    app.command(name="prune")(prune_cmd)
    app.command(name="quality-audit")(quality_audit_cmd)
    app.command(name="backfill-source-ref")(backfill_source_ref_cmd)
    app.command(name="regenerate-titles")(regenerate_titles_cmd)
    app.command(name="inbox-rescore")(inbox_rescore_cmd)
    app.command(name="why-matched")(why_matched_cmd)
    app.command(name="inbox-add")(inbox_add_cmd)
    app.command(name="match-project")(match_project_cmd)
    app.command(name="register-project")(register_project_cmd)
    app.command(name="list-projects")(list_projects_cmd)
    app.command(name="reset")(reset_cmd)
    app.command(name="reclassify")(reclassify_cmd)
    app.command(name="quality-cleanup")(quality_cleanup_cmd)
    app.command(name="refresh-projects")(refresh_projects_cmd)
    app.command(name="scan-roots")(scan_roots_cmd)

    # Phase 7: 翻译缓存子命令组
    tc_sub = typer.Typer(help="翻译缓存管理（Phase 7）")
    tc_sub.command("stats")(translation_cache_stats_cmd)
    tc_sub.command("cleanup")(translation_cache_cleanup_cmd)
    app.add_typer(tc_sub, name="translation-cache")


# ---------------------------------------------------------------------------
# import-claude-md
# ---------------------------------------------------------------------------

_HEADING_RE = re.compile(r"^(#{1,3})\s+(.+?)\s*$")


def import_claude_md_cmd(
    files: list[Path] = typer.Argument(..., help="一个或多个 CLAUDE.md 文件路径"),
    scope: str = typer.Option("global", "--scope"),
    project: Optional[str] = typer.Option(None, "--project"),
    polish: bool = typer.Option(False, "--polish/--no-polish",
                                 help="逐条调 LLM 优化（数量大时慢且耗 API）"),
    min_chars: int = typer.Option(80, "--min-chars",
                                   help="过短的章节跳过"),
):
    """把 CLAUDE.md 按二级 / 三级标题拆分入库。"""
    cfg = _config()
    conn = indexer.open_db(cfg)
    saved = 0
    skipped = 0

    for f in files:
        if not f.is_file():
            err_console.print(f"跳过不存在的文件：{f}")
            continue
        sections = _split_by_headings(f.read_text(encoding="utf-8"))
        console.print(f"[cyan]{f}[/cyan]：拆出 {len(sections)} 段")
        # 走 quality 过滤——拒纯英文文档碎片 / AGENTS.md 章节碎片 / 噪声
        from ..core import quality as _quality
        qc = getattr(cfg, "quality", None) or _quality.QualityConfig()
        for title, body in sections:
            body = body.strip()
            if len(body) < min_chars:
                skipped += 1
                continue
            ok, reason = _quality.is_quality_prompt(body, qc)
            if not ok:
                skipped += 1
                console.print(f"  [dim]✗ 跳过 {title[:40]} ({reason})[/dim]")
                continue
            if polish:
                r = optimizer.optimize(cfg, body)
                if r.success:
                    body = r.optimized
            tags = _infer_tags(title, body)
            stack = _infer_stack(body)
            p = storage.Prompt.new(
                title=title, body=body, scope=scope, project=project,
                tags=tags, stack=stack, origin="imported",
            )
            file_path = storage.save(cfg, p, commit_msg=f"import: {f.name} → {title}")
            indexer.upsert(conn, p, file_path)
            saved += 1
    conn.close()
    console.print(f"[green]✓[/green] 已导入 {saved} 条，跳过 {skipped} 条（过短）")


def _split_by_headings(md: str) -> list[tuple[str, str]]:
    """按 #/## / ### 标题切分；返回 [(标题, 正文), ...]。"""
    sections: list[tuple[str, str]] = []
    current_title = "untitled"
    current_lines: list[str] = []
    in_code = False

    for line in md.splitlines():
        if line.strip().startswith("```"):
            in_code = not in_code
            current_lines.append(line)
            continue
        if in_code:
            current_lines.append(line)
            continue
        m = _HEADING_RE.match(line)
        if m and len(m.group(1)) <= 3:  # 只在 # / ## / ### 切
            if current_lines:
                sections.append((current_title, "\n".join(current_lines).strip()))
            current_title = m.group(2)
            current_lines = []
        else:
            current_lines.append(line)
    if current_lines:
        sections.append((current_title, "\n".join(current_lines).strip()))
    return [(t, b) for t, b in sections if b]


_TAG_HINTS = [
    ("playwright", ["playwright"]),
    ("ui", ["ui", "界面", "组件"]),
    ("test", ["test", "测试", "pytest"]),
    ("git", ["git ", "git\n", "github", "commit"]),
    ("python", ["python", ".py", "pyproject"]),
    ("nextjs", ["next.js", "nextjs", "next "]),
    ("react", ["react"]),
    ("typescript", ["typescript", " ts ", ".ts"]),
    ("powershell", ["powershell", "pwsh"]),
    ("docker", ["docker", "dockerfile"]),
    ("api", ["api", "endpoint", "接口"]),
    ("database", ["sqlite", "postgres", "mysql", "数据库"]),
]


def _infer_tags(title: str, body: str) -> list[str]:
    haystack = (title + "\n" + body).lower()
    out: list[str] = []
    for tag, hints in _TAG_HINTS:
        if any(h in haystack for h in hints):
            out.append(tag)
    return out


_STACK_HINTS = [
    ("nextjs", ["next.js", "nextjs"]),
    ("react", ["react"]),
    ("python", ["python", "pyproject"]),
    ("typescript", ["typescript"]),
    ("fastapi", ["fastapi"]),
    ("playwright", ["playwright"]),
    ("sqlite", ["sqlite"]),
]


def _infer_stack(body: str) -> list[str]:
    haystack = body.lower()
    out: list[str] = []
    for stack, hints in _STACK_HINTS:
        if any(h in haystack for h in hints):
            out.append(stack)
    return out


# ---------------------------------------------------------------------------
# reindex
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# bulk-import-projects（递归扫整个项目根，把所有提示词资源吸进库）
# ---------------------------------------------------------------------------

# 这些文件名一律识别为提示词来源
_KNOWN_FILES = [
    "CLAUDE.md", "claude.md",
    "AGENTS.md", "agents.md",
    "GEMINI.md", "gemini.md",
    ".cursorrules",
    ".clinerules",
    "PROMPT.md", "prompt.md",
]
# 这些目录里的 .md 文件全部识别为提示词
_KNOWN_DIRS = [
    "skills/prompts",
    ".claude/commands",
    ".claude/agents",
    "prompts",
]
# 跳过的目录（避免吸到 node_modules 里的 README.md）
_SKIP_DIRS = {
    "node_modules", ".git", ".venv", "venv", "__pycache__",
    "dist", "build", ".next", "out", ".pytest_cache", ".ruff_cache",
    "coverage", "htmlcov", ".idea", ".vscode",
}

# trap 关键词（标题或前 200 字含其一即视为 trap）
_TRAP_MARKERS = [
    "致命禁令", "致命", "禁止", "严禁", "绝对不", "绝对禁止",
    "踩坑", "陷阱", "坑点", "已知坑",
    "PROHIBITED", "FORBIDDEN", "NEVER ", "DO NOT",
    "fatal", "trap", "gotcha",
]


def bulk_import_projects_cmd(
    root: Path = typer.Argument(..., help="项目根目录，如 D:\\My_Project"),
    polish: bool = typer.Option(False, "--polish/--no-polish",
                                 help="逐条调 LLM 优化（慢且耗 API）"),
    min_chars: int = typer.Option(80, "--min-chars"),
    dry_run: bool = typer.Option(False, "--dry-run", help="只列扫描结果不入库"),
):
    """递归扫一个项目根目录，把所有 CLAUDE.md / AGENTS.md / .cursorrules / skills/prompts 里
    的提示词全部吸进库，按项目分组并自动 register-project。

    每个子目录被当作一个项目。\"致命禁令 / 禁止 / 严禁 / PROHIBITED\" 等标题自动转 trap。
    """
    import json as _json
    cfg = _config()
    root = root.resolve()
    if not root.is_dir():
        err_console.print(f"目录不存在：{root}")
        raise typer.Exit(1)

    conn = indexer.open_db(cfg) if not dry_run else None

    # 1) 先识别"真正的项目根"：根目录有 CLAUDE.md / AGENTS.md / .cursorrules 之一
    #    且不是 SGO 生成产物 / output 之类的临时目录
    project_roots = _find_project_roots(root)
    # 2) 每个项目根：扫根上的提示词文件 + skills/prompts、.claude/commands 等子目录
    findings: dict[str, list[Path]] = {}
    for proj_root in project_roots:
        if proj_root.name == "Prompt help":
            continue
        files = _collect_files_in_project(proj_root)
        if files:
            findings[proj_root.name] = files

    if not findings:
        console.print(f"[yellow]在 {root} 没找到任何提示词资源（CLAUDE.md / AGENTS.md / "
                      f".cursorrules / skills/prompts）[/yellow]")
        if conn:
            conn.close()
        return

    # 概览
    table = Table(title=f"扫描结果（{len(findings)} 个项目）")
    table.add_column("项目")
    table.add_column("文件数", justify="right")
    table.add_column("文件清单")
    for proj, files in findings.items():
        table.add_row(proj, str(len(files)),
                       ", ".join(str(f.relative_to(root)) for f in files[:5])
                       + ("..." if len(files) > 5 else ""))
    console.print(table)

    if dry_run:
        console.print("[dim]--dry-run，未入库[/dim]")
        return

    saved_total = 0
    trap_total = 0
    project_total = 0
    skipped_total = 0

    for proj_name, files in findings.items():
        proj_dir = root / proj_name
        # 注册项目
        try:
            from ..core.fingerprint import to_dict
            fp = fingerprint(proj_dir)
            fp.project_name = proj_name
            indexer.register_project(
                conn, name=proj_name, cwd_path=str(proj_dir),
                fingerprint_json=_json.dumps(to_dict(fp), ensure_ascii=False),
            )
            project_total += 1
        except Exception as e:
            err_console.print(f"  ! register {proj_name} 失败：{e}")

        # 导入文件
        for f in files:
            saved, trap_count, skipped = _import_one_file(
                cfg, conn, f, proj_name, polish, min_chars
            )
            saved_total += saved
            trap_total += trap_count
            skipped_total += skipped

    if conn:
        conn.close()

    console.print()
    console.print(
        f"[green]✓[/green] 完成。导入 [bold]{saved_total}[/bold] 条提示词"
        f"（其中 {trap_total} 条 trap），登记 {project_total} 个项目，跳过 {skipped_total} 段（过短）。"
    )


def _find_project_roots(scan_root: Path) -> list[Path]:
    """递归找所有"看起来是项目根"的目录：根上有 CLAUDE.md/AGENTS.md/.cursorrules 之一。

    跳过 _SKIP_DIRS 以及 SGO 输出类目录（generated_project / research / writing 等）。
    """
    found: list[Path] = []
    skip_extra = {
        "generated_project", "research", "writing", "chapter_outputs",
        "output", "outputs", "archive", "archives", "tmp", "temp",
        "snapshots", "backups", "_active", "inbox",
        "workspace", "workspaces", "writing_workspace", "test_runs",
    }
    known_lower = {n.lower() for n in _KNOWN_FILES}

    def _walk(d: Path):
        try:
            entries = list(d.iterdir())
        except (PermissionError, OSError):
            return
        # 当前目录是不是项目根？
        for e in entries:
            if e.is_file() and e.name.lower() in known_lower:
                found.append(d)
                break
        # 继续向下递归（无论当前是不是项目根，子目录里可能还有别的项目）
        for e in entries:
            if not e.is_dir():
                continue
            if e.name in _SKIP_DIRS or e.name in skip_extra:
                continue
            if e.name.startswith("."):
                continue  # 跳过 .git/.claude 等隐藏目录（_KNOWN_DIRS 单独处理）
            _walk(e)

    _walk(scan_root)
    # 去重：同名前缀 + 日期后缀（如 foo20260417 / foo20260423）只留最新那个
    return _dedupe_dated_snapshots(found)


_DATED_SUFFIX_RE = re.compile(r"^(.+?)(\d{6,12})$")


def _dedupe_dated_snapshots(dirs: list[Path]) -> list[Path]:
    """对 ['foo20260417', 'foo20260423', 'bar'] → 保留 'foo20260423' 和 'bar'。"""
    grouped: dict[str, list[tuple[Path, str]]] = {}
    keep: list[Path] = []
    for d in dirs:
        m = _DATED_SUFFIX_RE.match(d.name)
        if m and len(m.group(2)) >= 6:
            base = (str(d.parent), m.group(1))
            grouped.setdefault(base, []).append((d, m.group(2)))
        else:
            keep.append(d)
    for items in grouped.values():
        items.sort(key=lambda x: x[1], reverse=True)
        keep.append(items[0][0])  # 最新那个
    return keep


def _collect_files_in_project(proj_root: Path) -> list[Path]:
    """给定一个项目根，收集它下面所有提示词文件。"""
    found: list[Path] = []
    # 1. 根目录的已知文件名
    for name in _KNOWN_FILES:
        p = proj_root / name
        if p.is_file():
            found.append(p)
    # 2. 已知子目录里的 .md
    for sub in _KNOWN_DIRS:
        d = proj_root / sub
        if d.is_dir():
            for md in sorted(d.rglob("*.md")):
                if any(skip in md.parts for skip in _SKIP_DIRS):
                    continue
                found.append(md)
    return found


def _walk_for_prompt_files(root: Path):
    """递归遍历 root，产出所有可能的提示词文件路径。跳过 _SKIP_DIRS。"""
    known_filenames_lower = {n.lower() for n in _KNOWN_FILES}
    for path in root.rglob("*"):
        # 跳过被排除目录里的任何内容
        if any(skip in path.parts for skip in _SKIP_DIRS):
            continue
        if not path.is_file():
            continue
        # 已知文件名（CLAUDE.md / AGENTS.md / .cursorrules 等）
        if path.name.lower() in known_filenames_lower:
            yield path
            continue
        # 已知子目录里的 .md 文件
        if path.suffix.lower() == ".md":
            parts = [p.lower() for p in path.parts]
            for sub in _KNOWN_DIRS:
                sub_parts = [s.lower() for s in sub.replace("\\", "/").split("/")]
                # 检查 sub_parts 序列是否完整出现在 path 中
                for i in range(len(parts) - len(sub_parts) + 1):
                    if parts[i:i + len(sub_parts)] == sub_parts:
                        yield path
                        break
                else:
                    continue
                break


def _infer_project_root(file: Path, scan_root: Path) -> Path | None:
    """根据文件位置推断"项目根目录"的名字。

    规则：
    - 若文件直接在某目录下（如 likner-app/CLAUDE.md）→ 那个目录就是项目根
    - 若文件在 skills/prompts/X.md → 项目根是 skills 的父目录
    - 若文件在 .claude/commands/X.md → 项目根是 .claude 的父目录
    """
    try:
        rel = file.relative_to(scan_root)
    except ValueError:
        return None
    parts = rel.parts
    if len(parts) <= 1:
        # 文件就在 scan_root 根上，没有项目分组
        return None

    # 切掉文件名，从尾部往上找哪一段是 _KNOWN_DIRS 的开始
    chain = parts[:-1]  # 仅目录部分
    chain_lower = [c.lower() for c in chain]

    for sub in _KNOWN_DIRS:
        sub_parts_lower = [s.lower() for s in sub.replace("\\", "/").split("/")]
        # 在 chain 里找 sub_parts 的起点
        for i in range(len(chain_lower) - len(sub_parts_lower) + 1):
            if chain_lower[i:i + len(sub_parts_lower)] == sub_parts_lower:
                # 项目根 = chain[:i] 拼出来；如果 i==0 说明根本就没有项目分层
                if i == 0:
                    return None
                proj_root = scan_root
                for seg in chain[:i]:
                    proj_root = proj_root / seg
                return proj_root

    # 文件直接在某个项目目录下，比如 chain = ('Likner', 'likner-app') 文件叫 CLAUDE.md
    # 项目根 = chain 的最后一段
    proj_root = scan_root
    for seg in chain:
        proj_root = proj_root / seg
    return proj_root


def _import_one_file(
    cfg: Config,
    conn,
    path: Path,
    project_name: str,
    polish: bool,
    min_chars: int,
) -> tuple[int, int, int]:
    """返回 (入库条数, trap 条数, 跳过条数)。"""
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return 0, 0, 0

    name = path.name
    saved = 0
    trap_count = 0
    skipped = 0

    # 整文件作为单条（.cursorrules / AGENTS.md 这种小文件）
    is_small_file = name in (".cursorrules", ".clinerules") or path.parent.name == "prompts"
    if is_small_file and len(text.strip()) >= min_chars:
        title = f"{project_name} · {path.stem if path.stem else name}"
        body = text.strip()
        scope, project, triggers = _classify(title, body, project_name)
        tags = _infer_tags(title, body) + ([f"from-{path.parent.name}"] if path.parent.name != project_name else [])
        stack = _infer_stack(body)
        if polish:
            r = optimizer.optimize(cfg, body)
            if r.success:
                body = r.optimized
        p = storage.Prompt.new(
            title=title, body=body, scope=scope, project=project,
            tags=tags, stack=stack, triggers=triggers, origin="imported",
        )
        file_path = storage.save(cfg, p, commit_msg=f"bulk: {project_name} / {name}")
        indexer.upsert(conn, p, file_path)
        if scope == "trap":
            trap_count += 1
        saved += 1
        return saved, trap_count, skipped

    # 大文件按二级 / 三级标题拆
    sections = _split_by_headings(text)
    for sec_title, body in sections:
        body = body.strip()
        if len(body) < min_chars:
            skipped += 1
            continue

        title = f"{project_name} · {sec_title}"[:80]
        scope, project, triggers = _classify(sec_title, body, project_name)
        tags = _infer_tags(sec_title, body)
        stack = _infer_stack(body)

        if polish:
            r = optimizer.optimize(cfg, body)
            if r.success:
                body = r.optimized

        p = storage.Prompt.new(
            title=title, body=body, scope=scope, project=project,
            tags=tags, stack=stack, triggers=triggers, origin="imported",
        )
        file_path = storage.save(
            cfg, p, commit_msg=f"bulk: {project_name} / {name} → {sec_title}"
        )
        indexer.upsert(conn, p, file_path)
        if scope == "trap":
            trap_count += 1
        saved += 1

    return saved, trap_count, skipped


def _classify(title: str, body: str, project_name: str) -> tuple[str, Optional[str], list[str]]:
    """根据标题和正文前 200 字，决定 scope、project、triggers。"""
    head = (title + " " + body[:300]).lower()
    is_trap = any(m.lower() in head for m in _TRAP_MARKERS)
    if is_trap:
        triggers = _extract_triggers(title, body)
        return "trap", None, triggers
    return "project", project_name, []


def _extract_triggers(title: str, body: str) -> list[str]:
    """从 trap 内容里抽关键词作为触发词。"""
    haystack = title + "\n" + body[:500]
    triggers: list[str] = []
    # 反引号包裹的命令
    for m in re.finditer(r"`([^`\n]{2,40})`", haystack):
        cmd = m.group(1).strip()
        if 3 <= len(cmd) <= 40 and "/" in cmd or " " in cmd or "." in cmd or "_" in cmd:
            triggers.append(cmd)
    # 中文专有名词（2-6 字）
    for m in re.finditer(r"[一-鿿]{2,6}", haystack):
        word = m.group(0)
        if word in ("致命禁令", "踩坑", "陷阱"):
            continue
        if word not in triggers:
            triggers.append(word)
    return triggers[:8]


# ---------------------------------------------------------------------------
# reindex
# ---------------------------------------------------------------------------

def reindex_cmd():
    """从文件系统全量重建 SQLite FTS5。"""
    cfg = _config()
    n = indexer.reindex_from_disk(cfg)
    console.print(f"[green]✓[/green] 索引已重建：{n} 条")


def quality_cleanup_cmd(
    delete: bool = typer.Option(False, "--delete", help="真删；否则只列"),
):
    """对已入库的提示词重跑 quality 过滤，列出（或删除）不达标的。"""
    from ..core import quality
    cfg = _config()
    qc = cfg.quality
    bad: list[tuple] = []  # (path, prompt, reason)
    for path, p in storage.iter_prompts(cfg):
        passed, reason = quality.is_quality_prompt(p.body, qc)
        if not passed:
            bad.append((path, p, reason))

    if not bad:
        console.print("[green]✓[/green] 全库通过质量检查，无需清理")
        return

    table = Table(title=f"{'即将删除' if delete else '建议删除'} ({len(bad)} 条)")
    table.add_column("title")
    table.add_column("scope")
    table.add_column("拒因", style="yellow")
    for _path, p, reason in bad:
        table.add_row(p.title[:50], p.scope, reason)
    console.print(table)

    if not delete:
        console.print("[dim]加 --delete 真删；删后建议跑 prompt-help reindex[/dim]")
        return

    conn = indexer.open_db(cfg)
    for path, p, _reason in bad:
        try:
            path.unlink(missing_ok=True)
        except Exception:
            pass
        indexer.delete_by_id(conn, p.id)
    conn.close()
    console.print(f"[green]✓[/green] 已删 {len(bad)} 条")


def reclassify_cmd(
    dry_run: bool = typer.Option(False, "--dry-run", help="只看会改什么不真改"),
):
    """对全库每条提示词重新跑规则分类，更新 frontmatter 和索引。"""
    from ..core import classify
    cfg = _config()
    n_updated = 0
    n_total = 0
    conn = indexer.open_db(cfg)
    for path, p in storage.iter_prompts(cfg):
        n_total += 1
        new_cats = classify.rule_classify(p.body)
        if set(new_cats) != set(p.categories or []):
            if dry_run:
                console.print(f"  {p.title[:40]}: {p.categories or '[]'} → {new_cats}")
            else:
                p.categories = new_cats
                storage.save(cfg, p, commit_msg=f"reclassify: {p.title[:30]}")
                indexer.upsert(conn, p, path)
            n_updated += 1
    conn.close()
    if dry_run:
        console.print(f"[dim]共 {n_total} 条，{n_updated} 条会改（--dry-run，未真改）[/dim]")
    else:
        console.print(f"[green]✓[/green] 共 {n_total} 条，{n_updated} 条已更新分类")


# ---------------------------------------------------------------------------
# reset
# ---------------------------------------------------------------------------

def reset_cmd(
    keep_config: bool = typer.Option(
        True, "--keep-config/--wipe-all",
        help="保留 config.toml / .env / briefs / .git；否则全删（除 .git）",
    ),
    confirm: bool = typer.Option(
        False, "--confirm",
        help="必加 --confirm 才真删。否则只列出会删什么。",
    ),
):
    """清空提示词库（保留配置和 git 历史），用于换过滤策略后重跑导入。

    删除：prompts/ 下所有 .md、index.sqlite、inbox/、library_cache/、pulse/snapshots/
    保留（--keep-config 时）：config.toml、.env、briefs/、.git/、logs/
    """
    import shutil
    cfg = _config()
    targets = [
        cfg.prompts_dir,
        cfg.index_db,
        cfg.inbox_dir,
        cfg.vault_path / "library_cache",
        cfg.pulse_dir / "snapshots",
    ]
    if not keep_config:
        targets += [
            cfg.config_file,
            cfg.vault_path / ".env",
            cfg.briefs_dir,
        ]

    existing = [t for t in targets if t.exists()]
    if not existing:
        console.print("[dim]vault 已经是干净的[/dim]")
        return

    table = Table(title="即将删除的内容")
    table.add_column("路径")
    table.add_column("类型")
    for t in existing:
        table.add_row(str(t), "目录" if t.is_dir() else "文件")
    console.print(table)

    if not confirm:
        console.print("[yellow]加 --confirm 才真删[/yellow]")
        return

    for t in existing:
        try:
            if t.is_dir():
                shutil.rmtree(t)
            else:
                t.unlink()
        except Exception as e:
            err_console.print(f"删 {t} 失败：{e}")

    # 重建必要的空目录结构
    for d in (cfg.prompts_dir / "global", cfg.prompts_dir / "projects",
              cfg.prompts_dir / "traps", cfg.inbox_dir):
        d.mkdir(parents=True, exist_ok=True)
    indexer.open_db(cfg).close()

    # 自动 git commit 留底
    try:
        if (cfg.vault_path / ".git").is_dir():
            proc.run(
                ["git", "-C", str(cfg.vault_path), "add", "-A"],
                check=False, capture_output=True,
            )
            proc.run(
                ["git", "-C", str(cfg.vault_path), "commit",
                 "-q", "-m", "reset: cleared library before re-import"],
                check=False, capture_output=True,
            )
    except Exception:
        pass

    console.print(f"[green]✓[/green] vault 已清空，可以跑 import-from-transcripts 重新填充")


# ---------------------------------------------------------------------------
# sync
# ---------------------------------------------------------------------------

def sync_cmd():
    """git pull --rebase + push。"""
    cfg = _config()
    if not (cfg.vault_path / ".git").is_dir():
        err_console.print("vault 还没 git init")
        raise typer.Exit(1)
    if not cfg.git.remote_url and _no_remote(cfg.vault_path):
        err_console.print("没配 remote。先 prompt-help link-remote <url>")
        raise typer.Exit(1)
    proc.run(["git", "-C", str(cfg.vault_path), "pull", "--rebase"], check=False)
    proc.run(["git", "-C", str(cfg.vault_path), "push"], check=False)
    console.print("[green]✓[/green] sync 完成")


def _no_remote(vault: Path) -> bool:
    r = proc.run(["git", "-C", str(vault), "remote"], capture_output=True, text=True)
    return not r.stdout.strip()


# ---------------------------------------------------------------------------
# prune
# ---------------------------------------------------------------------------

def prune_cmd(
    days: int = typer.Option(90, "--days", help="未使用 N 天且 used==0 的标记为可删"),
    delete: bool = typer.Option(False, "--delete", help="真的删除（默认只列出）"),
):
    """标记或删除久未使用的提示词。"""
    cfg = _config()
    conn = indexer.open_db(cfg)
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    cutoff_iso = cutoff.isoformat()

    rows = list(conn.execute(
        "SELECT * FROM prompts WHERE used = 0 AND created < ?",
        (cutoff_iso,),
    ))
    if not rows:
        console.print(f"[dim]没有 {days} 天以前且零使用的提示词[/dim]")
        return

    table = Table(title=f"{'即将删除' if delete else '建议删除'} ({len(rows)} 条)")
    table.add_column("title")
    table.add_column("scope")
    table.add_column("created")
    for r in rows:
        table.add_row(r["title"], r["scope"], r["created"])
    console.print(table)

    if delete:
        for r in rows:
            try:
                Path(r["file_path"]).unlink(missing_ok=True)
            except Exception:
                pass
            indexer.delete_by_id(conn, r["id"])
        conn.close()
        console.print(f"[green]✓[/green] 已删 {len(rows)} 条")
    else:
        conn.close()
        console.print("[dim]加 --delete 真的删除[/dim]")


# ---------------------------------------------------------------------------
# quality-audit：按当前 quality.QualityConfig 重新过滤库内所有条目
# ---------------------------------------------------------------------------

def quality_audit_cmd(
    apply: bool = typer.Option(False, "--apply", help="真删（默认只 dry-run 列出）"),
    scope: Optional[str] = typer.Option(
        None, "--scope",
        help="仅检查指定 scope（global | project | trap）",
    ),
    origin: Optional[str] = typer.Option(
        "imported", "--origin",
        help="仅检查指定 origin（默认 imported——主要是自动扫的文档碎片来源）。"
        "传 'all' 检查所有来源",
    ),
    save_report: Optional[Path] = typer.Option(
        None, "--save-report",
        help="把不通过条目清单写到这个文件（json），便于审核",
    ),
    llm_judge: bool = typer.Option(
        False, "--llm-judge",
        help="规则层放过的条目再调 LLM yes/no 判定（慢，每条 ~10-30s）。"
        "用于库内深度审计。",
    ),
):
    """用当前 quality.QualityConfig 重新审查库内所有条目，列出 / 删除不通过的。

    用户实测 v0.1 后反馈：自动扫描 refresh-projects 把 AGENTS.md / CLAUDE.md
    章节碎片（"Coding Style"、"Project Structure" 等纯英文文档段）当 prompt
    入库。这条命令用当前过滤规则重新审，把这类条目挑出来 / 删掉。
    """
    cfg = _config()
    from ..core import quality as _quality
    qc = getattr(cfg, "quality", None) or _quality.QualityConfig()

    conn = indexer.open_db(cfg)
    where = []
    params: list = []
    if scope:
        where.append("scope = ?")
        params.append(scope)
    if origin and origin != "all":
        where.append("origin = ?")
        params.append(origin)
    sql = "SELECT * FROM prompts"
    if where:
        sql += " WHERE " + " AND ".join(where)
    rows = list(conn.execute(sql, params))
    console.print(f"扫 {len(rows)} 条（scope={scope or '全部'}, origin={origin}）")

    rejected: list[dict] = []
    llm_checked = 0
    for r in rows:
        body = (r["body"] or "").strip()
        if not body:
            continue
        ok, reason = _quality.is_quality_prompt(body, qc)
        if not ok:
            rejected.append({
                "id": r["id"],
                "title": r["title"],
                "scope": r["scope"],
                "origin": r["origin"],
                "reason": reason,
                "preview": body[:120].replace("\n", " "),
                "file_path": r["file_path"],
            })
        elif llm_judge:
            # 规则放过的再走 LLM 二次判定（慢，仅在用户显式启用时跑）
            is_prompt, verdict = _quality.llm_judge_is_prompt(cfg, body)
            llm_checked += 1
            if not is_prompt:
                rejected.append({
                    "id": r["id"],
                    "title": r["title"],
                    "scope": r["scope"],
                    "origin": r["origin"],
                    "reason": f"llm_judge_no({verdict})",
                    "preview": body[:120].replace("\n", " "),
                    "file_path": r["file_path"],
                })
    if llm_checked:
        console.print(f"[dim]LLM 二次判定了 {llm_checked} 条规则层通过的条目[/dim]")

    if not rejected:
        console.print("[green]✓[/green] 没有不通过的条目，库很干净")
        conn.close()
        return

    table = Table(
        title=f"{'即将删除' if apply else 'Dry-run 不通过'} ({len(rejected)} 条 / 共 {len(rows)})",
        show_lines=False,
    )
    table.add_column("reason", style="yellow")
    table.add_column("title", overflow="fold")
    table.add_column("preview", overflow="fold")
    for item in rejected[:80]:
        table.add_row(item["reason"], item["title"][:50], item["preview"])
    console.print(table)
    if len(rejected) > 80:
        console.print(f"[dim]… 还有 {len(rejected) - 80} 条未列出[/dim]")

    # 按 reason 分组统计
    by_reason: dict[str, int] = {}
    for item in rejected:
        by_reason[item["reason"]] = by_reason.get(item["reason"], 0) + 1
    console.print("[bold]按拒因分布[/bold]")
    for reason, count in sorted(by_reason.items(), key=lambda x: -x[1]):
        console.print(f"  {count:4d}  {reason}")

    if save_report:
        try:
            save_report.parent.mkdir(parents=True, exist_ok=True)
            save_report.write_text(
                json.dumps(rejected, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            console.print(f"[dim]报告已写入 {save_report}[/dim]")
        except OSError as e:
            err_console.print(f"写报告失败: {e}")

    if apply:
        deleted = 0
        for item in rejected:
            try:
                Path(item["file_path"]).unlink(missing_ok=True)
            except Exception:
                pass
            indexer.delete_by_id(conn, item["id"])
            deleted += 1
        conn.close()
        # auto-commit
        try:
            from ..core import proc as _proc
            _proc.run(
                ["git", "-C", str(cfg.vault_path), "add", "-A"],
                capture_output=True, check=False,
            )
            _proc.run(
                ["git", "-C", str(cfg.vault_path), "commit",
                 "-m", f"quality-audit: prune {deleted} doc-fragment / non-prompt entries"],
                capture_output=True, check=False,
            )
        except Exception:
            pass
        console.print(f"[green]✓[/green] 已删 {deleted} 条（已 git commit）")
    else:
        conn.close()
        console.print("[dim]加 --apply 真的删除（删之前会 git commit 一次方便回滚）[/dim]")


# ---------------------------------------------------------------------------
# backfill-source-ref：为已存在的模板版条目回填 source_ref
# ---------------------------------------------------------------------------

def backfill_source_ref_cmd(
    apply: bool = typer.Option(False, "--apply", help="真写回（默认 dry-run）"),
):
    """旧版 generalize 没正确写 source_ref；这条命令通过 optimized_from_id
    反查原条目，把 "源项目: <原 project>" 或 "源条目: <原 title>" 回填到模板版的
    source_ref 字段。
    """
    cfg = _config()
    conn = indexer.open_db(cfg)
    rows = list(conn.execute(
        "SELECT id, title, file_path, source_ref, project, optimized_from_id "
        "FROM prompts WHERE optimized_from_id != '' AND (source_ref IS NULL OR source_ref = '')"
    ))
    if not rows:
        console.print("[green]✓[/green] 没有缺 source_ref 的模板版条目")
        conn.close()
        return

    # 预查所有原条目
    parent_ids = list({r["optimized_from_id"] for r in rows})
    placeholders_q = ",".join("?" * len(parent_ids))
    parent_lookup = {}
    for pr in conn.execute(
        f"SELECT id, title, project, source_ref FROM prompts WHERE id IN ({placeholders_q})",
        parent_ids,
    ):
        parent_lookup[pr["id"]] = dict(pr)

    table = Table(title=f"{'即将回填' if apply else 'Dry-run 计划回填'} ({len(rows)} 条)")
    table.add_column("模板版标题", overflow="fold")
    table.add_column("→ 新 source_ref", overflow="fold")
    plan: list[tuple] = []
    for r in rows:
        parent = parent_lookup.get(r["optimized_from_id"])
        if not parent:
            new_ref = f"原条目已删: {r['optimized_from_id'][-8:]}"
        else:
            new_ref = (
                parent["source_ref"] or parent["project"]
                or f"源条目: {(parent['title'] or '')[:30]}"
            )
        plan.append((r["id"], r["file_path"], new_ref))
        table.add_row(r["title"][:40], new_ref)
    console.print(table)

    if not apply:
        conn.close()
        console.print("[dim]加 --apply 真的写回（同时改 SQLite + .md frontmatter）[/dim]")
        return

    updated = 0
    for prompt_id, file_path, new_ref in plan:
        # 1. 改 SQLite
        conn.execute("UPDATE prompts SET source_ref = ? WHERE id = ?", (new_ref, prompt_id))
        # 2. 改 .md frontmatter（让 git 历史也对得上）
        try:
            fp = Path(file_path)
            if fp.is_file():
                p = storage.load(fp)
                p.source_ref = new_ref
                storage.save(cfg, p, commit_msg=f"backfill source_ref: {p.title[:30]}")
        except Exception as e:
            console.print(f"  [yellow]warn[/yellow] {file_path}: {e}")
        updated += 1
    conn.commit()
    conn.close()
    console.print(f"[green]✓[/green] 已回填 {updated} 条")


# ---------------------------------------------------------------------------
# regenerate-titles：用 LLM 给现有条目批量重命名（≤10 字纯描述）
# ---------------------------------------------------------------------------

def regenerate_titles_cmd(
    apply: bool = typer.Option(False, "--apply", help="真改（默认 dry-run）"),
    scope: Optional[str] = typer.Option(
        None, "--scope", help="仅处理指定 scope（global / project / trap）",
    ),
    is_template: Optional[bool] = typer.Option(
        None, "--template/--no-template",
        help="True=只处理通用模板；False=只处理原始；不传=全部",
    ),
    only_over: int = typer.Option(
        12, "--only-over",
        help="仅处理标题超过 N 字的条目（默认 12，避免重命名已经够短的）",
    ),
    limit: int = typer.Option(
        50, "--limit", help="最多处理 N 条（默认 50，避免一次跑太久）",
    ),
):
    """用 LLM 给现有条目批量重命名为 ≤10 字纯描述。

    用户实测反馈：旧条目标题太长 / 带"(通用模板)"等后缀。这个命令调 LLM 重新命名。
    单条 5-30 秒，50 条 ≈ 8-25 分钟。dry-run 先看打算改哪些，再 --apply。
    """
    cfg = _config()
    from ..core import optimizer as _opt
    conn = indexer.open_db(cfg)

    where = ["LENGTH(title) > ?"]
    params: list = [only_over]
    if scope:
        where.append("scope = ?")
        params.append(scope)
    if is_template is not None:
        where.append("is_template = ?")
        params.append(1 if is_template else 0)
    sql = "SELECT id, title, body, file_path, is_template FROM prompts WHERE " + " AND ".join(where)
    sql += " ORDER BY created DESC LIMIT ?"
    params.append(limit)
    rows = list(conn.execute(sql, params))
    console.print(f"找到 {len(rows)} 条候选（标题 > {only_over} 字, limit={limit}）")
    if not rows:
        conn.close()
        return

    plan: list[tuple] = []
    for i, r in enumerate(rows, 1):
        body = (r["body"] or "").strip()
        if not body:
            continue
        kind = "template" if r["is_template"] else "project"
        fallback = (r["title"] or "")[:10]
        console.print(f"[dim]({i}/{len(rows)}) 生成中：{r['title'][:40]}…[/dim]")
        new_title = _opt.safe_generate_title(cfg, body, fallback=fallback, kind=kind)
        plan.append((r["id"], r["file_path"], r["title"], new_title))
        console.print(f"  {r['title'][:30]:<32}  →  {new_title}")

    if not apply:
        conn.close()
        console.print(f"\n[dim]共 {len(plan)} 条会改。加 --apply 真的写回。[/dim]")
        return

    updated = 0
    for prompt_id, file_path, old_title, new_title in plan:
        if not new_title or new_title == old_title:
            continue
        try:
            fp = Path(file_path)
            if fp.is_file():
                p = storage.load(fp)
                p.title = new_title
                storage.save(cfg, p, commit_msg=f"rename: {old_title[:20]} → {new_title}")
            conn.execute("UPDATE prompts SET title = ? WHERE id = ?", (new_title, prompt_id))
            updated += 1
        except Exception as e:
            console.print(f"  [yellow]warn[/yellow] {old_title[:20]}: {e}")
    conn.commit()
    conn.close()
    console.print(f"[green]✓[/green] 已重命名 {updated} 条")


# ---------------------------------------------------------------------------
# inbox-rescore：把旧的 confidence=0.6 硬编码值用真公式重新算
# ---------------------------------------------------------------------------

def inbox_rescore_cmd(
    apply: bool = typer.Option(False, "--apply", help="真改（默认 dry-run）"),
):
    """重新计算 inbox/ 里所有候选的 confidence（真实公式：与库重合度 + 长度）。

    v0.x auto_scan 把所有候选写死 0.6，UI 显示成"匹配度 0.60"误导用户。
    跑这条命令把历史数据按真公式重算。
    """
    cfg = _config()
    from ..core import scoring
    if not cfg.inbox_dir.is_dir():
        console.print("[dim]没有 inbox 目录[/dim]")
        return
    files = sorted(cfg.inbox_dir.glob("*.md"))
    if not files:
        console.print("[dim]inbox 为空[/dim]")
        return

    plan: list[tuple[Path, float, float, str]] = []
    for f in files:
        try:
            raw = f.read_text(encoding="utf-8")
        except OSError:
            continue
        # 拆 frontmatter + body
        m = re.match(r"---\n(.*?)\n---\n(.*)", raw, re.DOTALL)
        if not m:
            continue
        fm_text, body = m.group(1), m.group(2).strip()
        # 抓 old confidence
        old_conf = 0.0
        for line in fm_text.splitlines():
            if line.startswith("confidence:"):
                try:
                    old_conf = float(line.split(":", 1)[1].strip())
                except ValueError:
                    pass
                break
        if not body:
            continue
        new_conf = scoring.compute_confidence(cfg, body)
        if abs(new_conf - old_conf) < 0.01:
            continue
        plan.append((f, old_conf, new_conf, fm_text))

    if not plan:
        console.print("[green]✓[/green] 所有 inbox 候选 confidence 都已是真实值")
        return

    table = Table(title=f"{'即将重算' if apply else 'Dry-run 计划重算'} ({len(plan)} 条)")
    table.add_column("文件")
    table.add_column("旧 conf")
    table.add_column("新 conf", justify="right")
    table.add_column("变化")
    for f, old, new, _ in plan[:30]:
        delta = new - old
        sign = "↑" if delta > 0 else ("↓" if delta < 0 else "—")
        table.add_row(f.name[:40], f"{old:.2f}", f"{new:.2f}", f"{sign} {abs(delta):.2f}")
    console.print(table)
    if len(plan) > 30:
        console.print(f"[dim]… 还有 {len(plan) - 30} 条未列[/dim]")

    if not apply:
        console.print(f"[dim]共 {len(plan)} 条。加 --apply 真的写回[/dim]")
        return

    written = 0
    for f, _old, new, fm_text in plan:
        new_fm_lines = []
        replaced = False
        for line in fm_text.splitlines():
            if line.startswith("confidence:"):
                new_fm_lines.append(f"confidence: {new:.2f}")
                replaced = True
            else:
                new_fm_lines.append(line)
        if not replaced:
            new_fm_lines.append(f"confidence: {new:.2f}")
        raw = f.read_text(encoding="utf-8")
        m = re.match(r"---\n(.*?)\n---\n(.*)", raw, re.DOTALL)
        if not m:
            continue
        body = m.group(2)
        f.write_text(
            "---\n" + "\n".join(new_fm_lines) + "\n---\n" + body,
            encoding="utf-8",
        )
        written += 1
    console.print(f"[green]✓[/green] 已重算 {written} 条 inbox 候选 confidence")


# ---------------------------------------------------------------------------
# why-matched
# ---------------------------------------------------------------------------

def why_matched_cmd(
    prompt_id: str = typer.Argument(..., help="提示词 id（或末 6 位）"),
    query: str = typer.Argument(..., help="要解释的查询"),
):
    """解释为什么某条提示词在某查询下被命中。"""
    cfg = _config()
    conn = indexer.open_db(cfg)
    # id 末 6 位 fuzzy 匹配
    row = conn.execute(
        "SELECT * FROM prompts WHERE id = ? OR id LIKE ?",
        (prompt_id, f"%{prompt_id}"),
    ).fetchone()
    if not row:
        err_console.print(f"未找到：{prompt_id}")
        raise typer.Exit(1)

    q_tokens = {t.lower() for t in re.findall(r"\w+", query) if len(t) >= 2}
    title_tokens = {t.lower() for t in re.findall(r"\w+", row["title"]) if len(t) >= 2}
    tag_tokens = {t.lower() for t in (row["tags_csv"] or "").split(",") if t.strip()}
    body_tokens = {t.lower() for t in re.findall(r"\w+", row["body"][:500]) if len(t) >= 3}

    table = Table(title=f"为什么 '{row['title']}' 命中 '{query}'")
    table.add_column("信号")
    table.add_column("命中 token")
    table.add_row("标题重合", ", ".join(sorted(q_tokens & title_tokens)) or "—")
    table.add_row("标签重合", ", ".join(sorted(q_tokens & tag_tokens)) or "—")
    table.add_row("正文重合（前 500 字）",
                  ", ".join(sorted(q_tokens & body_tokens)) or "—")
    table.add_row("使用频次 boost",
                  f"used={row['used']}, success={row['success_signal']}")
    console.print(table)
    conn.close()


# ---------------------------------------------------------------------------
# inbox-add（mining 候选）
# ---------------------------------------------------------------------------

def inbox_add_cmd(
    title: str = typer.Option("(mining 候选)", "--title"),
    confidence: Optional[float] = typer.Option(
        None, "--confidence",
        help="0.0-1.0；不传则按 core.scoring 真实公式计算（与库重合度 + 长度）",
    ),
    body_file: Optional[Path] = typer.Option(None, "--body-file"),
):
    """把一个 mining 候选写到 inbox/ 留待 /prompt-review。

    confidence 不传时按 (1) 与库 token 重合度 (2) 内容长度 真实计算，与 stop.py
    用同一个 core.scoring.compute_confidence 公式。
    """
    cfg = _config()
    cfg.inbox_dir.mkdir(parents=True, exist_ok=True)
    body = body_file.read_text(encoding="utf-8") if body_file else sys.stdin.read()
    body = body.strip()
    if not body:
        return

    if confidence is None:
        from ..core import scoring
        confidence = scoring.compute_confidence(cfg, body)
    else:
        if confidence < 0.0 or confidence > 1.0:
            err_console.print(f"confidence 必须在 0.0-1.0 之间，收到 {confidence}")
            raise typer.Exit(2)

    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    out = cfg.inbox_dir / f"{ts}-{abs(hash(body)) % 100000:05d}.md"
    out.write_text(
        f"---\nconfidence: {confidence:.2f}\nsuggested_title: {title}\ncreated: {ts}\n---\n\n{body}\n",
        encoding="utf-8",
    )
    console.print(f"[green]✓[/green] inbox: {out.name}  (conf {confidence:.2f})")


# ---------------------------------------------------------------------------
# match-project（SessionStart hook 调）
# ---------------------------------------------------------------------------

def register_project_cmd(
    name: str = typer.Argument(..., help="项目名（短）"),
    path: Path = typer.Option(Path.cwd(), "--path", help="项目目录，默认当前 cwd"),
):
    """登记一个项目的 cwd 与栈指纹快照（供跨项目相似召回用）。"""
    import json as _json
    cfg = _config()
    fp = fingerprint(path.resolve())
    fp.project_name = name
    from prompt_help.core.fingerprint import to_dict
    conn = indexer.open_db(cfg)
    indexer.register_project(
        conn, name=name, cwd_path=str(path.resolve()),
        fingerprint_json=_json.dumps(to_dict(fp), ensure_ascii=False),
    )
    conn.close()
    console.print(
        f"[green]✓[/green] 登记项目 [bold]{name}[/bold]：langs={sorted(fp.langs)} "
        f"frameworks={sorted(fp.frameworks)[:5]}"
    )


def list_projects_cmd(json_out: bool = typer.Option(False, "--json")):
    """列出所有已登记项目。"""
    import json as _json
    cfg = _config()
    conn = indexer.open_db(cfg)
    rows = indexer.list_projects(conn)
    conn.close()
    if json_out:
        out = [{"name": r["name"], "cwd": r["cwd_path"], "last_seen": r["last_seen"]} for r in rows]
        console.print_json(_json.dumps(out, ensure_ascii=False))
        return
    if not rows:
        console.print("[dim]还没登记项目。用 `prompt-help register-project <name>` 登记。[/dim]")
        return
    table = Table(title="已登记项目")
    table.add_column("name"); table.add_column("path"); table.add_column("last_seen")
    for r in rows:
        table.add_row(r["name"], r["cwd_path"] or "-", r["last_seen"] or "-")
    console.print(table)


def match_project_cmd(
    cwd: Path = typer.Option(Path.cwd(), "--cwd"),
    top_k: int = typer.Option(5, "--top-k"),
    json_out: bool = typer.Option(False, "--json"),
):
    """根据 cwd 的项目指纹召回相关提示词（top-k）。"""
    import json as _json
    cfg = _config()
    fp = fingerprint(cwd)
    conn = indexer.open_db(cfg)
    candidates = list(conn.execute(
        "SELECT * FROM prompts WHERE scope IN ('global', 'project') ORDER BY used DESC LIMIT 200"
    ))
    scored: list[tuple] = []
    for r in candidates:
        stack = [s for s in (r["stack_csv"] or "").split(",") if s.strip()]
        ovr = stack_overlap(stack, fp)
        if ovr > 0.0:
            scored.append((r, ovr))
    scored.sort(key=lambda x: x[1], reverse=True)
    top = scored[:top_k]
    conn.close()

    if json_out:
        out = [{"id": r["id"], "title": r["title"], "scope": r["scope"],
                "stack": r["stack_csv"], "overlap": round(o, 3)} for r, o in top]
        console.print_json(_json.dumps(out, ensure_ascii=False))
        return
    if not top:
        console.print(f"[dim]没有匹配 {fp.project_name} 栈（{', '.join(sorted(fp.langs))}）的提示词[/dim]")
        return
    table = Table(title=f"匹配 {fp.project_name} ({', '.join(sorted(fp.langs))})")
    table.add_column("title"); table.add_column("scope"); table.add_column("stack")
    table.add_column("overlap", justify="right")
    for r, o in top:
        table.add_row(r["title"], r["scope"], r["stack_csv"] or "—", f"{o:.2f}")
    console.print(table)


# ---------------------------------------------------------------------------
# Phase 7：翻译缓存
# ---------------------------------------------------------------------------

def translation_cache_stats_cmd():
    """看翻译缓存统计。"""
    from ..core.translation_cache import TranslationCache
    cfg = _config()
    cache = TranslationCache(cfg)
    s = cache.stats()
    console.print(f"缓存条数：{s['total']}")
    if s["oldest"]:
        from datetime import datetime, timezone
        oldest_dt = datetime.fromtimestamp(s["oldest"], tz=timezone.utc).astimezone()
        console.print(f"最早条目：{oldest_dt.isoformat(timespec='seconds')}")
    console.print(f"数据库：{cache.db_path}")


def translation_cache_cleanup_cmd(
    ttl_days: int = typer.Option(30, "--ttl-days", help="超过 N 天的条目删除"),
):
    """删除过期翻译缓存。"""
    from ..core.translation_cache import TranslationCache
    cfg = _config()
    cache = TranslationCache(cfg)
    n = cache.cleanup_expired(ttl_days)
    console.print(f"[green]✓[/green] 删除 {n} 条过期缓存（TTL {ttl_days} 天）")


# ---------------------------------------------------------------------------
# Phase 17：刷新本机项目（增量同步 CLAUDE.md 等）
# ---------------------------------------------------------------------------

def refresh_projects_cmd(
    add_root: Optional[Path] = typer.Option(None, "--add-root",
                                              help="新增扫描根目录（一次性也会保存）"),
):
    """扫所有已配置的扫描根目录，刷新库内项目相关 prompt。"""
    from ..core import refresh
    cfg = _config()
    if add_root:
        if not add_root.is_dir():
            err_console.print(f"目录不存在：{add_root}")
            raise typer.Exit(1)
        added = refresh.add_scan_root(cfg, add_root)
        if added:
            console.print(f"[green]✓[/green] 已新增扫描根目录：{add_root}")
        else:
            console.print(f"[dim]目录已在列表中：{add_root}[/dim]")

    roots = refresh.load_scan_roots(cfg)
    if not roots:
        err_console.print(
            "还没配置任何扫描根目录。先用 `prompt-help refresh-projects --add-root <path>` 添加。"
        )
        raise typer.Exit(1)

    console.print(f"[dim]扫 {len(roots)} 个根目录…[/dim]")
    result = refresh.refresh_all(
        cfg, progress=lambda msg: console.print(f"[dim]  · {msg}[/dim]"),
    )
    console.print(f"\n[green]✓[/green] {result.summary()}")
    if result.errors:
        console.print("\n[yellow]问题：[/yellow]")
        for e in result.errors:
            console.print(f"  · {e}")


def scan_roots_cmd(
    add: Optional[Path] = typer.Option(None, "--add", help="新增"),
    remove: Optional[str] = typer.Option(None, "--remove", help="删除（按路径）"),
):
    """管理项目扫描根目录列表。无参数时列出所有。"""
    from ..core import refresh
    cfg = _config()
    if add:
        if not add.is_dir():
            err_console.print(f"目录不存在：{add}")
            raise typer.Exit(1)
        if refresh.add_scan_root(cfg, add):
            console.print(f"[green]✓[/green] 已新增：{add}")
        else:
            console.print(f"[dim]已在列表：{add}[/dim]")
        return
    if remove:
        if refresh.remove_scan_root(cfg, remove):
            console.print(f"[green]✓[/green] 已删除：{remove}")
        else:
            err_console.print(f"找不到：{remove}")
            raise typer.Exit(1)
        return
    roots = refresh.load_scan_roots(cfg)
    if not roots:
        console.print("[dim]还没配置任何扫描根目录[/dim]")
        return
    table = Table(title=f"扫描根目录（{len(roots)} 个）")
    table.add_column("path"); table.add_column("last_scan")
    for r in roots:
        table.add_row(r.get("path", "?"), (r.get("last_scan") or "—")[:19])
    console.print(table)
