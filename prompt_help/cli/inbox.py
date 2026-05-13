"""inbox CLI：管理 Stop / PreCompact hook 自动挖掘出来的候选提示词。

每个候选是 ~/.prompt-help/inbox/<timestamp>-<hash>.md，含 frontmatter（confidence、
suggested_title、created、origin）和正文。

子命令：
- inbox list                      列出所有候选 + confidence + 摘要
- inbox preview <file>            看完整内容
- inbox approve <file> --title …  搬进库，删原文件
- inbox dismiss <file>            直接删
- inbox clear --older-than <N>    批量删 N 天以前的
"""

from __future__ import annotations

import datetime as dt
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import typer
import yaml
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table

from ..core import indexer, optimizer, storage
from ..core.config import Config, load_config, load_dotenv_if_present

console = Console()
err_console = Console(stderr=True, style="red")


def _config() -> Config:
    load_dotenv_if_present()
    return load_config()


def register(app: typer.Typer) -> None:
    inbox_app = typer.Typer(help="管理 mining 留下的候选提示词")
    inbox_app.command("list")(list_cmd)
    inbox_app.command("preview")(preview_cmd)
    inbox_app.command("approve")(approve_cmd)
    inbox_app.command("dismiss")(dismiss_cmd)
    inbox_app.command("clear")(clear_cmd)
    app.add_typer(inbox_app, name="inbox")


@dataclass
class InboxItem:
    path: Path
    confidence: float
    suggested_title: str
    created: str
    origin: str
    body: str
    action_tag: str = ""

    @classmethod
    def load(cls, path: Path) -> "InboxItem":
        text = path.read_text(encoding="utf-8")
        if text.startswith("---"):
            parts = text.split("---", 2)
            fm = yaml.safe_load(parts[1]) or {} if len(parts) >= 3 else {}
            body = parts[2].strip() if len(parts) >= 3 else text
        else:
            fm = {}
            body = text
        return cls(
            path=path,
            confidence=float(fm.get("confidence") or 0.0),
            suggested_title=str(fm.get("suggested_title") or "").strip(),
            created=str(fm.get("created") or ""),
            origin=str(fm.get("origin") or "stop"),
            body=body,
            action_tag=str(fm.get("action_tag") or "").strip(),
        )


def _all_items(cfg: Config) -> list[InboxItem]:
    if not cfg.inbox_dir.is_dir():
        return []
    items: list[InboxItem] = []
    for p in sorted(cfg.inbox_dir.glob("*.md")):
        try:
            items.append(InboxItem.load(p))
        except Exception:
            continue
    items.sort(key=lambda x: (-x.confidence, x.created))
    return items


def _summary(text: str, max_chars: int = 120) -> str:
    text = " ".join(text.split())
    return text if len(text) <= max_chars else text[: max_chars - 1] + "…"


# ---------------------------------------------------------------------------
# list
# ---------------------------------------------------------------------------

def list_cmd(
    json_out: bool = typer.Option(False, "--json"),
    limit: int = typer.Option(20, "--limit", "-n"),
):
    """列出 inbox 里的候选。"""
    cfg = _config()
    items = _all_items(cfg)[:limit]

    if json_out:
        out = [{
            "filename": i.path.name,
            "confidence": i.confidence,
            "suggested_title": i.suggested_title,
            "created": i.created,
            "origin": i.origin,
            "preview": _summary(i.body, 200),
        } for i in items]
        console.print_json(json.dumps(out, ensure_ascii=False))
        return

    if not items:
        console.print("[dim]inbox 为空[/dim]")
        return

    table = Table(title=f"inbox 候选（{len(items)} 条）")
    table.add_column("#", justify="right", style="dim")
    table.add_column("conf", justify="right")
    table.add_column("origin", style="magenta")
    table.add_column("file")
    table.add_column("摘要")
    for i, it in enumerate(items, 1):
        conf_color = "green" if it.confidence >= 0.6 else ("yellow" if it.confidence >= 0.4 else "dim")
        table.add_row(
            str(i),
            f"[{conf_color}]{it.confidence:.2f}[/{conf_color}]",
            it.origin,
            it.path.name[:25],
            _summary(it.body, 80),
        )
    console.print(table)
    console.print("[dim]preview / approve / dismiss <file> 来处理[/dim]")


# ---------------------------------------------------------------------------
# preview
# ---------------------------------------------------------------------------

def preview_cmd(filename: str = typer.Argument(..., help="inbox 文件名（如 20260509T...md）")):
    """看一条候选的完整内容。"""
    cfg = _config()
    item = _resolve(cfg, filename)
    console.print(Panel.fit(
        Markdown(item.body),
        title=f"[bold]{item.path.name}[/bold]  "
              f"[dim](conf={item.confidence:.2f}, origin={item.origin})[/dim]",
        border_style="cyan",
    ))


# ---------------------------------------------------------------------------
# approve
# ---------------------------------------------------------------------------

def approve_cmd(
    filename: str = typer.Argument(...),
    title: Optional[str] = typer.Option(None, "--title", "-t"),
    scope: str = typer.Option("global", "--scope", "-s"),
    project: Optional[str] = typer.Option(None, "--project", "-p"),
    tags: str = typer.Option("", "--tags"),
    stack: str = typer.Option("", "--stack"),
    triggers: str = typer.Option("", "--triggers"),
    polish: bool = typer.Option(True, "--polish/--no-polish"),
):
    """把一条候选搬进正式库（删除原 inbox 文件）。"""
    cfg = _config()
    item = _resolve(cfg, filename)
    final_title = title or item.suggested_title or item.body.strip().split("\n")[0][:40]
    if not final_title:
        err_console.print("缺标题（--title 或在 frontmatter 里写 suggested_title）")
        raise typer.Exit(1)

    if scope == "project" and not project:
        err_console.print("scope=project 必须 --project <name>")
        raise typer.Exit(1)

    body = item.body
    if polish:
        r = optimizer.optimize(cfg, body)
        if r.success:
            body = r.optimized

    p = storage.Prompt.new(
        title=final_title.strip(),
        body=body,
        scope=scope,
        project=project,
        tags=[t.strip() for t in tags.split(",") if t.strip()],
        stack=[s.strip() for s in stack.split(",") if s.strip()],
        triggers=[t.strip() for t in triggers.split(",") if t.strip()],
        origin="mining",
    )
    file_path = storage.save(cfg, p, commit_msg=f"approve: {final_title} (from inbox)")
    conn = indexer.open_db(cfg)
    indexer.upsert(conn, p, file_path)
    conn.close()

    item.path.unlink(missing_ok=True)
    console.print(f"[green]✓[/green] 已搬入：{final_title}  → {file_path}")


# ---------------------------------------------------------------------------
# dismiss / clear
# ---------------------------------------------------------------------------

def dismiss_cmd(filename: str = typer.Argument(...)):
    """直接删除一条候选。"""
    cfg = _config()
    item = _resolve(cfg, filename)
    item.path.unlink()
    console.print(f"[green]✓[/green] 已删：{item.path.name}")


def clear_cmd(
    older_than_days: int = typer.Option(7, "--older-than"),
    yes: bool = typer.Option(False, "--yes", "-y"),
):
    """删 N 天以前的所有候选。"""
    cfg = _config()
    items = _all_items(cfg)
    cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=older_than_days)
    to_del: list[InboxItem] = []
    for it in items:
        try:
            t = dt.datetime.strptime(it.created[:15], "%Y%m%dT%H%M%S").replace(tzinfo=dt.timezone.utc)
            if t < cutoff:
                to_del.append(it)
        except Exception:
            continue
    if not to_del:
        console.print(f"[dim]没有 {older_than_days} 天前的候选[/dim]")
        return
    if not yes:
        console.print(f"[yellow]即将删除 {len(to_del)} 条[/yellow]，加 --yes 确认")
        return
    for it in to_del:
        it.path.unlink(missing_ok=True)
    console.print(f"[green]✓[/green] 已删 {len(to_del)} 条")


# ---------------------------------------------------------------------------

def _resolve(cfg: Config, filename: str) -> InboxItem:
    """允许传完整文件名、不带扩展名、或仅时间戳前缀。"""
    candidates: list[Path] = []
    direct = cfg.inbox_dir / filename
    if direct.is_file():
        candidates.append(direct)
    elif (cfg.inbox_dir / f"{filename}.md").is_file():
        candidates.append(cfg.inbox_dir / f"{filename}.md")
    else:
        # 前缀匹配
        for p in cfg.inbox_dir.glob(f"{filename}*"):
            candidates.append(p)
    if not candidates:
        err_console.print(f"找不到 inbox 文件：{filename}")
        raise typer.Exit(1)
    if len(candidates) > 1:
        err_console.print(f"前缀 '{filename}' 匹配多个：{[p.name for p in candidates]}")
        raise typer.Exit(1)
    return InboxItem.load(candidates[0])
