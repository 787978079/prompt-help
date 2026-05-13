"""公开提示词库（T5）：拉 awesome-claude-prompts / cursorrules 等到本地缓存。

CLI：
  prompt-help library refresh                  # 拉所有源
  prompt-help library list [--source X]        # 列缓存内容
  prompt-help library import <id> [<id>...]    # 选条入库
"""

from __future__ import annotations

import csv
import datetime as dt
import io
import json
import re
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional

import typer
import yaml
from rich.console import Console
from rich.table import Table

from ..core import classify, indexer, storage
from ..core.config import Config, load_config, load_dotenv_if_present

console = Console()
err_console = Console(stderr=True, style="red")


def register(app: typer.Typer) -> None:
    sub = typer.Typer(help="公开提示词库（推荐源）")
    sub.command("refresh")(refresh_cmd)
    sub.command("list")(list_cmd)
    sub.command("import")(import_cmd)
    sub.command("sources")(sources_cmd)
    app.add_typer(sub, name="library")


def _config() -> Config:
    load_dotenv_if_present()
    return load_config()


def _sources_yaml() -> Path:
    return Path(__file__).resolve().parents[1] / "data" / "public_sources.yaml"


def _cache_dir(cfg: Config) -> Path:
    d = cfg.vault_path / "library_cache"
    d.mkdir(parents=True, exist_ok=True)
    return d


def load_sources() -> list[dict]:
    f = _sources_yaml()
    if not f.is_file():
        return []
    data = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
    return list(data.get("source") or [])


# ---------------------------------------------------------------------------

@dataclass
class PublicPrompt:
    id: str           # source-id 拼接 hash
    title: str
    body: str
    source_id: str
    source_name: str
    language: str
    categories: list[str]


def _http_get(url: str, timeout: int = 20) -> bytes:
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "prompt-help-library/0.1 (+vibecoding tool)"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


# ---------------------------------------------------------------------------
# 解析器
# ---------------------------------------------------------------------------

_HEADING_RE = re.compile(r"^(#{1,3})\s+(.+?)\s*$")


_NAV_TITLES = {
    "contents", "table of contents", "toc", "sponsorships", "awesome",
    "untitled", "tags", "license", "contributing", "stars", "credits",
    "thanks", "about", "introduction", "overview", "why", "what",
    "background", "categories", "更新日志", "changelog", "目录",
    "rate this repository", "rate this repo", "support",
}


def _parse_markdown_headings(content: str, source: dict) -> list[PublicPrompt]:
    out: list[PublicPrompt] = []
    sections: list[tuple[str, list[str]]] = []
    cur_title = "untitled"
    cur_lines: list[str] = []
    in_code = False
    for line in content.splitlines():
        if line.strip().startswith("```"):
            in_code = not in_code
            cur_lines.append(line)
            continue
        if in_code:
            cur_lines.append(line)
            continue
        m = _HEADING_RE.match(line)
        if m and len(m.group(1)) <= 3:
            if cur_lines:
                sections.append((cur_title, cur_lines))
            cur_title = m.group(2)
            cur_lines = []
        else:
            cur_lines.append(line)
    if cur_lines:
        sections.append((cur_title, cur_lines))

    for i, (title, lines) in enumerate(sections):
        body = "\n".join(lines).strip()
        # 阈值放宽：80-8000（Phase 7）。原 200-6000 把短而精的中文段全丢了。
        if len(body) < 80 or len(body) > 8000:
            continue
        title_lower = title.lower().strip().rstrip(":：")
        # 跳过导航 / TOC 类标题
        if title_lower in _NAV_TITLES:
            continue
        if title_lower.startswith(("why ", "what ", "how to install", "rate this")):
            continue
        # 真正的 prompt 通常含动作动词。Phase 7：放宽中文检测，加扮演/我希望你/作为/请你 等。
        body_lower = body.lower()
        zh_signals = (
            "你是", "请你", "扮演", "我希望你", "我想让你", "你扮演",
            "你将", "你需要", "作为一名", "请扮演", "你的任务",
            "请按以下", "我想要你",
        )
        en_signals = ("act as", "you are", "i want you to", "<task>")
        is_real_prompt = (
            any(s in body_lower for s in en_signals)
            or any(s in body for s in zh_signals)
            or body.lstrip().startswith(("Act ", "You are", "Imagine", "Suppose"))
        )
        if not is_real_prompt:
            continue
        out.append(PublicPrompt(
            id=f"{source['id']}-{i:04d}",
            title=title[:80],
            body=body,
            source_id=source["id"],
            source_name=source["name"],
            language=source.get("language", "en"),
            categories=list(source.get("default_categories") or []),
        ))
    return out


def _parse_csv(content: str, source: dict) -> list[PublicPrompt]:
    # awesome-chatgpt-prompts 单字段最长 6000+ 字符，默认 limit 131072 在某些 Python 版本是更小
    csv.field_size_limit(10_000_000)
    out: list[PublicPrompt] = []
    reader = csv.DictReader(io.StringIO(content))
    for i, row in enumerate(reader):
        # 兼容多种列名：act/prompt | role/content | name/text
        title = (row.get("act") or row.get("role") or row.get("name") or "untitled").strip()
        body = (row.get("prompt") or row.get("content") or row.get("text") or "").strip()
        if len(body) < 50:
            continue
        out.append(PublicPrompt(
            id=f"{source['id']}-{i:04d}",
            title=title[:80],
            body=body,
            source_id=source["id"],
            source_name=source["name"],
            language=source.get("language", "en"),
            categories=list(source.get("default_categories") or []),
        ))
    return out


def _parse_jupyter_notebook(content: str, source: dict) -> list[PublicPrompt]:
    """从 .ipynb（JSON）抽 markdown cells，再走 markdown_headings 解析。"""
    try:
        nb = json.loads(content)
    except json.JSONDecodeError as e:
        raise ValueError(f"非合法 ipynb JSON：{e}") from e
    md_chunks: list[str] = []
    for cell in nb.get("cells", []):
        if cell.get("cell_type") != "markdown":
            continue
        src = cell.get("source", "")
        if isinstance(src, list):
            md_chunks.append("".join(src))
        elif isinstance(src, str):
            md_chunks.append(src)
    if not md_chunks:
        return []
    return _parse_markdown_headings("\n\n".join(md_chunks), source)


def parse_source(content: str, source: dict) -> list[PublicPrompt]:
    fmt = source.get("format", "markdown_headings")
    if fmt == "csv":
        return _parse_csv(content, source)
    if fmt == "jupyter_notebook":
        return _parse_jupyter_notebook(content, source)
    return _parse_markdown_headings(content, source)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def sources_cmd():
    """列出已配置的公开源。"""
    sources = load_sources()
    if not sources:
        console.print("[yellow]找不到 public_sources.yaml[/yellow]")
        return
    table = Table(title=f"公开源（{len(sources)} 个）")
    table.add_column("id"); table.add_column("name"); table.add_column("lang")
    table.add_column("desc")
    for s in sources:
        table.add_row(
            s["id"], s["name"], s.get("language", "?"),
            s.get("description", "")[:60],
        )
    console.print(table)


def refresh_sources(
    cfg: Config,
    source_id: Optional[str] = None,
) -> list[dict]:
    """库函数版 refresh：返回每条源的 {id, name, ok, n, error?}，给 GUI 和 CLI 共用。"""
    cache = _cache_dir(cfg)
    sources = load_sources()
    if source_id:
        sources = [s for s in sources if s["id"] == source_id]

    results: list[dict] = []
    for s in sources:
        out_path = cache / f"{s['id']}.json"
        entry = {"id": s["id"], "name": s["name"], "ok": False, "n": 0, "error": None}
        try:
            raw = _http_get(s["url"]).decode("utf-8", errors="replace")
            prompts = parse_source(raw, s)
            payload = {
                "source_id": s["id"],
                "source_name": s["name"],
                "language": s.get("language", "en"),
                "fetched_at": dt.datetime.now(dt.timezone.utc).isoformat(),
                "n": len(prompts),
                "prompts": [asdict(p) for p in prompts],
            }
            out_path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            entry["ok"] = True
            entry["n"] = len(prompts)
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            entry["error"] = f"网络/抓取失败：{e}"
        except ValueError as e:
            entry["error"] = f"解析失败：{e}"
        except Exception as e:
            entry["error"] = f"未知错误：{type(e).__name__}: {e}"
        results.append(entry)
    return results


def refresh_cmd(
    source_id: Optional[str] = typer.Option(None, "--source", help="只刷新某一源"),
):
    """从远程拉所有公开源（缓存到 ~/.prompt-help/library_cache/）。"""
    cfg = _config()
    sources = load_sources()
    if source_id and not any(s["id"] == source_id for s in sources):
        err_console.print(f"找不到 source：{source_id}")
        raise typer.Exit(1)

    results = refresh_sources(cfg, source_id)
    ok = 0
    for r in results:
        if r["ok"]:
            console.print(f"[green]✓[/green] {r['id']}: {r['n']} 条")
            ok += 1
        else:
            console.print(f"[red]✗[/red] {r['id']}: {r['error']}")

    console.print(f"\n刷新完成：{ok}/{len(results)}")


def _load_cache(cfg: Config, source_id: Optional[str] = None) -> list[PublicPrompt]:
    cache = _cache_dir(cfg)
    out: list[PublicPrompt] = []
    for f in cache.glob("*.json"):
        if source_id and f.stem != source_id:
            continue
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            for p in data.get("prompts") or []:
                out.append(PublicPrompt(**p))
        except Exception:
            continue
    return out


def list_cmd(
    source_id: Optional[str] = typer.Option(None, "--source"),
    limit: int = typer.Option(30, "--limit", "-n"),
):
    """看缓存内容（按源过滤）。"""
    cfg = _config()
    items = _load_cache(cfg, source_id)
    if not items:
        console.print("[dim]缓存为空。先 prompt-help library refresh[/dim]")
        return
    table = Table(title=f"缓存内容（{len(items)} 条）")
    table.add_column("id"); table.add_column("title")
    table.add_column("source"); table.add_column("lang")
    for it in items[:limit]:
        table.add_row(it.id, it.title[:40], it.source_id, it.language)
    console.print(table)
    console.print(
        f"\n[dim]共 {len(items)} 条；--limit 控制显示。导入：prompt-help library import <id>[/dim]"
    )


def import_cmd(
    ids: list[str] = typer.Argument(..., help="一个或多个 id（可用 'all-<source>' 导整源）"),
    translate: bool = typer.Option(
        False, "--translate/--no-translate",
        help="导入时翻译为中文（默认关；推荐保留英文原文，使用时用 prompt-help find --lang zh）",
    ),
):
    """把缓存里的提示词导入到自己库。"""
    from ..core import optimizer
    cfg = _config()
    all_items = _load_cache(cfg)
    by_id = {p.id: p for p in all_items}

    targets: list[PublicPrompt] = []
    for i in ids:
        if i.startswith("all-"):
            sid = i[4:]
            targets.extend(p for p in all_items if p.source_id == sid)
        elif i in by_id:
            targets.append(by_id[i])
        else:
            err_console.print(f"找不到 id：{i}")

    if not targets:
        raise typer.Exit(1)

    saved = 0
    skipped = 0
    failed_tx = 0
    conn = indexer.open_db(cfg)
    for n, it in enumerate(targets, 1):
        if indexer.get_by_title(conn, it.title):
            skipped += 1
            continue
        body = it.body
        translated = False
        if translate:
            console.print(f"[dim]翻译 {n}/{len(targets)}：{it.title[:40]}…[/dim]")
            r = optimizer.translate_to_zh(cfg, body)
            if r.success and r.error != "already_zh":
                body = r.optimized
                translated = True
            elif r.error and r.error != "already_zh":
                failed_tx += 1
        cats = list(set(it.categories) | set(classify.rule_classify(body)))
        tags = [f"来源-{it.source_id}", f"语言-{it.language}"]
        if translated:
            tags.append("已翻译")
        p = storage.Prompt.new(
            title=it.title, body=body, scope="global",
            tags=tags, origin="github",
        )
        p.categories = cats
        p.source_url = it.id
        file_path = storage.save(cfg, p, commit_msg=f"library import: {it.title[:30]}")
        indexer.upsert(conn, p, file_path)
        saved += 1
    conn.close()

    msg = f"[green]✓[/green] 导入 {saved} 条；跳过 {skipped} 条重名"
    if translate:
        msg += f"；翻译失败 {failed_tx} 条（这些保留原文）"
    console.print(msg)
