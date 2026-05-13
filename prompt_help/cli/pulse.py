"""pulse：MCP / skill / 工具生态周脉搏。

每周抓几个 markdown 源（awesome-mcp-servers、claude-code-templates 等），
按用户栈过滤新出现的条目，写成周报 digest 让 SessionStart 提醒。

设计：
- 用 urllib.request 抓（不引新依赖），超时 15s，UA 伪装
- 错误绝不阻塞：网络失败 / 解析失败都吞到 logs/pulse.log
- snapshot 按 (source, YYYY-MM-DD) 唯一；同一天重抓覆盖
- digest 按周聚合，frontmatter 含 read 标记供 SessionStart 决策
- sources 配置在 ~/.prompt-help/pulse/sources.toml，独立于主 config（频繁动）
"""

from __future__ import annotations

import datetime as dt
import json
import re
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import typer
import yaml
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib

import tomli_w

from ..core import indexer
from ..core.config import Config, load_config, load_dotenv_if_present
from ..core.fingerprint import from_dict

console = Console()
err_console = Console(stderr=True, style="red")


DEFAULT_SOURCES = [
    {
        "name": "awesome-mcp-servers",
        "url": "https://raw.githubusercontent.com/punkpeye/awesome-mcp-servers/main/README.md",
        "kind": "mcp",
    },
    {
        "name": "claude-code-templates",
        "url": "https://raw.githubusercontent.com/davila7/claude-code-templates/main/README.md",
        "kind": "skill",
    },
]


def _config() -> Config:
    load_dotenv_if_present()
    return load_config()


def _sources_file(cfg: Config) -> Path:
    return cfg.pulse_dir / "sources.toml"


def _snapshots_dir(cfg: Config) -> Path:
    d = cfg.pulse_dir / "snapshots"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _logs_path(cfg: Config) -> Path:
    cfg.logs_dir.mkdir(parents=True, exist_ok=True)
    return cfg.logs_dir / "pulse.log"


def _log(cfg: Config, msg: str) -> None:
    try:
        with _logs_path(cfg).open("a", encoding="utf-8") as f:
            ts = dt.datetime.now().isoformat(timespec="seconds")
            f.write(f"[{ts}] {msg}\n")
    except Exception:
        pass


# ---------------------------------------------------------------------------
# sources 管理
# ---------------------------------------------------------------------------

def _load_sources(cfg: Config) -> list[dict]:
    f = _sources_file(cfg)
    if not f.is_file():
        # 首次写入默认源
        f.parent.mkdir(parents=True, exist_ok=True)
        with f.open("wb") as fp:
            tomli_w.dump({"source": DEFAULT_SOURCES}, fp)
        return list(DEFAULT_SOURCES)
    try:
        with f.open("rb") as fp:
            data = tomllib.load(fp)
        return list(data.get("source") or [])
    except Exception:
        return list(DEFAULT_SOURCES)


def _save_sources(cfg: Config, sources: list[dict]) -> None:
    f = _sources_file(cfg)
    f.parent.mkdir(parents=True, exist_ok=True)
    with f.open("wb") as fp:
        tomli_w.dump({"source": sources}, fp)


# ---------------------------------------------------------------------------

def register(app: typer.Typer) -> None:
    pulse = typer.Typer(help="MCP / 工具生态周脉搏")
    pulse.command("list-sources")(list_sources_cmd)
    pulse.command("add-source")(add_source_cmd)
    pulse.command("remove-source")(remove_source_cmd)
    pulse.command("fetch")(fetch_cmd)
    pulse.command("digest")(digest_cmd)
    pulse.command("list")(list_cmd)
    pulse.command("show")(show_cmd)
    pulse.command("mark-read")(mark_read_cmd)
    app.add_typer(pulse, name="pulse")


# ---------------------------------------------------------------------------
# sources CLI
# ---------------------------------------------------------------------------

def list_sources_cmd(json_out: bool = typer.Option(False, "--json")):
    cfg = _config()
    sources = _load_sources(cfg)
    if json_out:
        console.print_json(json.dumps(sources, ensure_ascii=False))
        return
    if not sources:
        console.print("[dim]没有配置的 source[/dim]")
        return
    table = Table(title=f"pulse sources（{len(sources)}）")
    table.add_column("name"); table.add_column("kind"); table.add_column("url")
    for s in sources:
        table.add_row(s.get("name", ""), s.get("kind", "?"), s.get("url", ""))
    console.print(table)


def add_source_cmd(
    name: str = typer.Argument(...),
    url: str = typer.Argument(...),
    kind: str = typer.Option("custom", "--kind", help="mcp / skill / template / custom"),
):
    cfg = _config()
    sources = _load_sources(cfg)
    if any(s.get("name") == name for s in sources):
        err_console.print(f"source '{name}' 已存在")
        raise typer.Exit(1)
    sources.append({"name": name, "url": url, "kind": kind})
    _save_sources(cfg, sources)
    console.print(f"[green]✓[/green] 已加：{name} → {url}")


def remove_source_cmd(name: str = typer.Argument(...)):
    cfg = _config()
    sources = _load_sources(cfg)
    new = [s for s in sources if s.get("name") != name]
    if len(new) == len(sources):
        err_console.print(f"未找到 source：{name}")
        raise typer.Exit(1)
    _save_sources(cfg, new)
    console.print(f"[green]✓[/green] 已删：{name}")


# ---------------------------------------------------------------------------
# fetch
# ---------------------------------------------------------------------------

def fetch_cmd(
    name: Optional[str] = typer.Argument(None, help="只抓某个 source；不传抓全部"),
    timeout: int = typer.Option(15, "--timeout", help="单源超时秒数"),
):
    """抓取所有（或指定）source，写到 pulse/snapshots/<name>/<date>.md。"""
    cfg = _config()
    sources = _load_sources(cfg)
    targets = [s for s in sources if not name or s.get("name") == name]
    if not targets:
        err_console.print(f"找不到 source：{name}")
        raise typer.Exit(1)

    today = dt.date.today().isoformat()
    ok = 0
    for s in targets:
        path = _snapshots_dir(cfg) / s["name"] / f"{today}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            req = urllib.request.Request(
                s["url"],
                headers={"User-Agent": "prompt-help-pulse/0.1 (+vibecoding tool)"},
            )
            with urllib.request.urlopen(req, timeout=timeout) as r:
                data = r.read().decode("utf-8", errors="replace")
            path.write_text(data, encoding="utf-8")
            console.print(f"[green]✓[/green] {s['name']}: {len(data)} 字节 → {path.name}")
            ok += 1
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            _log(cfg, f"fetch {s['name']} ({s['url']}) failed: {e}")
            console.print(f"[yellow]![/yellow] {s['name']}: 抓取失败（已记录到 logs/pulse.log）")
        except Exception as e:
            _log(cfg, f"fetch {s['name']} unexpected error: {e}")
            console.print(f"[red]✗[/red] {s['name']}: 未预期错误")

    console.print(f"\n抓取完成：{ok}/{len(targets)}")


# ---------------------------------------------------------------------------
# digest 装配
# ---------------------------------------------------------------------------

# 抽取 markdown bullet 条目：- [name](url) — desc / - **name**：desc / - name: desc
_BULLET_PATTERNS = [
    re.compile(r"^\s*[-*]\s+\[([^\]]+)\]\(([^)]+)\)\s*[—–:]?\s*(.*)$"),  # - [name](url) — desc
    re.compile(r"^\s*[-*]\s+\*\*([^*]+)\*\*\s*[—–:]?\s*(.*)$"),           # - **name** — desc
    re.compile(r"^\s*[-*]\s+([A-Za-z][\w\.\-]*)\s*[—–:]\s*(.*)$"),         # - name — desc
]


@dataclass
class PulseEntry:
    source: str
    name: str
    url: str
    desc: str
    raw_line: str


def _extract_entries(source_name: str, md: str) -> list[PulseEntry]:
    entries: list[PulseEntry] = []
    seen: set[str] = set()
    for line in md.splitlines():
        for pat in _BULLET_PATTERNS:
            m = pat.match(line)
            if not m:
                continue
            groups = m.groups()
            if len(groups) == 3:
                name, url, desc = groups
            else:
                name, desc = groups[0], groups[1]
                url = ""
            key = name.strip().lower()
            if key in seen or len(name.strip()) < 2:
                break
            seen.add(key)
            entries.append(PulseEntry(
                source=source_name,
                name=name.strip(),
                url=url.strip(),
                desc=desc.strip()[:300],
                raw_line=line.strip(),
            ))
            break
    return entries


def aggregate_user_stack(cfg: Config) -> set[str]:
    """聚合用户栈：所有已登记项目的 langs+frameworks union + 全库 top 30 tags。"""
    tokens: set[str] = set()
    conn = indexer.open_db(cfg)
    try:
        for r in indexer.list_projects(conn):
            try:
                fp = from_dict(json.loads(r["fingerprint_json"]))
                tokens.update(fp.langs)
                tokens.update(fp.frameworks)
            except Exception:
                continue
        # top 标签
        tag_counts: dict[str, int] = {}
        for r in conn.execute("SELECT tags_csv FROM prompts WHERE tags_csv != ''"):
            for t in (r["tags_csv"] or "").split(","):
                t = t.strip().lower()
                if t:
                    tag_counts[t] = tag_counts.get(t, 0) + 1
        top_tags = sorted(tag_counts.items(), key=lambda x: x[1], reverse=True)[:30]
        tokens.update(t for t, _ in top_tags)
    finally:
        conn.close()
    # 去除明显无意义 token
    return {t for t in tokens if len(t) >= 2 and t.lower() not in {"misc", "other"}}


def _score_entry(entry: PulseEntry, stack: set[str]) -> tuple[int, list[str]]:
    """返回 (命中 token 数, 命中 token 列表)。"""
    haystack = (entry.name + " " + entry.desc).lower()
    hits = sorted(t for t in stack if t in haystack)
    return len(hits), hits


def digest_cmd(
    week: Optional[str] = typer.Option(None, "--week",
                                         help="ISO week 形如 2026-W19；默认当前周"),
    min_hits: int = typer.Option(1, "--min-hits", help="栈命中阈值，1 较宽松"),
    top: int = typer.Option(20, "--top", help="最多保留几条"),
    print_only: bool = typer.Option(False, "--print-only"),
):
    """从最新 snapshots 装配本周 digest。"""
    cfg = _config()
    today = dt.date.today()
    iso = today.isocalendar()
    week_label = week or f"{iso.year}-W{iso.week:02d}"

    stack = aggregate_user_stack(cfg)
    if not stack:
        console.print(
            "[yellow]![/yellow] 用户栈为空（没登记项目也没有标签）"
            "—— 装出来的 digest 不会过滤，全部命中。"
        )

    # 取每个 source 最新的 snapshot
    snap_root = _snapshots_dir(cfg)
    all_entries: list[tuple[PulseEntry, int, list[str]]] = []
    sources_seen: set[str] = set()
    for source_dir in snap_root.iterdir() if snap_root.is_dir() else []:
        if not source_dir.is_dir():
            continue
        snaps = sorted(source_dir.glob("*.md"))
        if not snaps:
            continue
        latest = snaps[-1]
        sources_seen.add(source_dir.name)
        try:
            md = latest.read_text(encoding="utf-8")
        except Exception:
            continue
        for e in _extract_entries(source_dir.name, md):
            n_hits, hits = _score_entry(e, stack) if stack else (1, [])
            if n_hits >= min_hits:
                all_entries.append((e, n_hits, hits))

    all_entries.sort(key=lambda x: x[1], reverse=True)
    picked = all_entries[:top]

    md = _render_digest(week_label, stack, picked, sources_seen)
    if print_only:
        console.print(Markdown(md))
        return

    out = cfg.pulse_dir / f"digest-{week_label}.md"
    out.write_text(md, encoding="utf-8")
    console.print(f"[green]✓[/green] {out}  ({len(picked)} 条命中)")


def _render_digest(
    week_label: str,
    stack: set[str],
    picked: list[tuple[PulseEntry, int, list[str]]],
    sources_seen: set[str],
) -> str:
    fm = {
        "kind": "pulse_digest",
        "week": week_label,
        "generated": dt.datetime.now(dt.timezone.utc).isoformat(),
        "read": False,
        "stack_tokens": sorted(stack)[:20],
        "sources": sorted(sources_seen),
        "n_entries": len(picked),
    }
    fm_yaml = yaml.safe_dump(fm, allow_unicode=True, sort_keys=False).strip()

    lines = [f"---\n{fm_yaml}\n---", "", f"# Pulse Digest · {week_label}", ""]
    if not picked:
        lines.append("本周没有命中你栈的新工具。")
        lines.append("")
        lines.append("[提示] 用 `prompt-help pulse list-sources` 检查源；用 "
                      "`prompt-help register-project <name>` 登记更多项目以丰富栈。")
        return "\n".join(lines) + "\n"

    by_source: dict[str, list] = {}
    for e, n, hits in picked:
        by_source.setdefault(e.source, []).append((e, n, hits))

    for source, items in by_source.items():
        lines.append(f"## {source}（{len(items)} 条）")
        lines.append("")
        for e, n, hits in items:
            link = f"[{e.name}]({e.url})" if e.url else e.name
            hit_str = f"_命中：{', '.join(hits)}_" if hits else ""
            lines.append(f"- **{link}** {hit_str}")
            if e.desc:
                lines.append(f"  - {e.desc}")
        lines.append("")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# list / show / mark-read
# ---------------------------------------------------------------------------

def list_cmd():
    """列出所有 digest。"""
    cfg = _config()
    digests = sorted(cfg.pulse_dir.glob("digest-*.md"), reverse=True)
    if not digests:
        console.print("[dim]还没有 digest。先 prompt-help pulse fetch + digest[/dim]")
        return
    table = Table(title=f"pulse digests（{len(digests)}）")
    table.add_column("week"); table.add_column("read?"); table.add_column("entries")
    for f in digests:
        try:
            text = f.read_text(encoding="utf-8")
            fm = yaml.safe_load(text.split("---", 2)[1]) or {}
            read = "✓" if fm.get("read") else "○"
            n = fm.get("n_entries", "?")
            table.add_row(fm.get("week", f.stem), read, str(n))
        except Exception:
            table.add_row(f.stem, "?", "?")
    console.print(table)


def show_cmd(week: Optional[str] = typer.Argument(None, help="ISO week，默认最新")):
    cfg = _config()
    f = _resolve_digest(cfg, week)
    text = f.read_text(encoding="utf-8")
    console.print(Markdown(text))


def mark_read_cmd(week: Optional[str] = typer.Argument(None)):
    cfg = _config()
    f = _resolve_digest(cfg, week)
    text = f.read_text(encoding="utf-8")
    if text.startswith("---"):
        parts = text.split("---", 2)
        fm = yaml.safe_load(parts[1]) or {}
        fm["read"] = True
        new_fm = yaml.safe_dump(fm, allow_unicode=True, sort_keys=False).strip()
        new_text = f"---\n{new_fm}\n---\n{parts[2]}"
        f.write_text(new_text, encoding="utf-8")
        console.print(f"[green]✓[/green] 已标记已读：{f.name}")
    else:
        err_console.print(f"{f.name} 缺 frontmatter，无法标记")
        raise typer.Exit(1)


def _resolve_digest(cfg: Config, week: Optional[str]) -> Path:
    digests = sorted(cfg.pulse_dir.glob("digest-*.md"), reverse=True)
    if not digests:
        err_console.print("还没有 digest")
        raise typer.Exit(1)
    if not week:
        return digests[0]
    for d in digests:
        if week in d.stem:
            return d
    err_console.print(f"找不到 week：{week}")
    raise typer.Exit(1)


# ---------------------------------------------------------------------------
# 给 SessionStart hook 用的 helper
# ---------------------------------------------------------------------------

def latest_unread_digest_summary(cfg: Config) -> Optional[str]:
    """SessionStart 调用：返回当前周未读 digest 的一行摘要，没有返回 None。"""
    today = dt.date.today()
    iso = today.isocalendar()
    week_label = f"{iso.year}-W{iso.week:02d}"
    f = cfg.pulse_dir / f"digest-{week_label}.md"
    if not f.is_file():
        return None
    try:
        text = f.read_text(encoding="utf-8")
        fm = yaml.safe_load(text.split("---", 2)[1]) or {}
        if fm.get("read"):
            return None
        n = fm.get("n_entries", 0)
        if not n:
            return None
        return f"📡 本周 pulse digest 待读：{n} 条相关工具更新（/tool-pulse 查看）"
    except Exception:
        return None
