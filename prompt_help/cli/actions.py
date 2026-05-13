"""CLI 子命令：save / find / show / list / find-traps。

这些命令同时被 slash command 复用，所以**默认非交互**：所有输入走参数或 stdin，
输出走 stdout。需要 polish 确认时用 --polish 自动接受 / --polish-confirm 当面问。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Optional

import typer
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
    app.command(name="save")(save_cmd)
    app.command(name="find")(find_cmd)
    app.command(name="show")(show_cmd)
    app.command(name="list")(list_cmd)
    app.command(name="find-traps")(find_traps_cmd)
    app.command(name="generalize")(generalize_cmd)


# ---------------------------------------------------------------------------
# save
# ---------------------------------------------------------------------------

def save_cmd(
    title: str = typer.Option(..., "--title", "-t", help="提示词标题"),
    scope: str = typer.Option("global", "--scope", "-s", help="global / project / trap"),
    project: Optional[str] = typer.Option(None, "--project", "-p", help="scope=project 时必填"),
    tags: str = typer.Option("", "--tags", help="逗号分隔标签"),
    stack: str = typer.Option("", "--stack", help="逗号分隔技术栈（如 nextjs,react）"),
    triggers: str = typer.Option("", "--triggers", help="trap 触发关键词，逗号分隔"),
    origin: str = typer.Option("manual", "--origin", help="manual / mining / imported / github"),
    body_file: Optional[Path] = typer.Option(None, "--body-file", help="提示词正文文件；不传则读 stdin"),
    polish: bool = typer.Option(True, "--polish/--no-polish", help="是否调 LLM 优化"),
    polish_confirm: bool = typer.Option(
        False, "--polish-confirm", help="交互式让用户选 [k/o/b]；非交互模式下默认 keep"
    ),
):
    """保存一条提示词。正文从 --body-file 或 stdin 读。"""
    cfg = _config()

    if body_file:
        body = body_file.read_text(encoding="utf-8").strip()
    else:
        body = sys.stdin.read().strip()
    if not body:
        err_console.print("提示词正文为空（--body-file 或 stdin 都没内容）")
        raise typer.Exit(1)

    if scope == "project" and not project:
        err_console.print("scope=project 必须指定 --project <name>")
        raise typer.Exit(1)

    final_body = body
    polish_note: str | None = None
    if polish:
        result = optimizer.optimize(cfg, body)
        if result.success:
            console.print()
            console.print(Panel.fit(result.diff_text or "（无文本差异）", title="polish diff",
                                    border_style="cyan"))
            if polish_confirm:
                choice = typer.prompt("使用哪个版本？[k]eep / [o]ptimized / [b]oth", default="k")
                choice = choice.strip().lower()
                if choice == "o":
                    final_body = result.optimized
                elif choice == "b":
                    # 都存：先存原版，再存优化版（关联 optimized_from）
                    p_orig = storage.Prompt.new(
                        title=title, body=body, scope=scope, project=project,
                        tags=_split(tags), stack=_split(stack), origin=origin,
                        triggers=_split(triggers),
                    )
                    file_orig = storage.save(cfg, p_orig)
                    conn = indexer.open_db(cfg)
                    indexer.upsert(conn, p_orig, file_orig)
                    p_opt = storage.Prompt.new(
                        title=title + " (optimized)", body=result.optimized,
                        scope=scope, project=project, tags=_split(tags),
                        stack=_split(stack), origin=origin, triggers=_split(triggers),
                    )
                    p_opt.optimized_from = p_orig.id
                    file_opt = storage.save(cfg, p_opt)
                    indexer.upsert(conn, p_opt, file_opt)
                    conn.close()
                    console.print(f"[green]✓[/green] 已存两版（原版+优化版），id={p_orig.id} / {p_opt.id}")
                    return
                # else 'k' → keep
            else:
                final_body = result.optimized
                polish_note = "（已 polish，加 --polish-confirm 可选 keep/optimized/both）"
        else:
            console.print(f"[yellow]polish 跳过：{result.error}[/yellow]")

    p = storage.Prompt.new(
        title=title, body=final_body, scope=scope, project=project,
        tags=_split(tags), stack=_split(stack), origin=origin,
        triggers=_split(triggers),
    )
    file_path = storage.save(cfg, p)
    conn = indexer.open_db(cfg)
    indexer.upsert(conn, p, file_path)
    conn.close()

    msg = f"[green]✓[/green] 已保存：[bold]{title}[/bold]  id={p.id[-6:]}  → {file_path}"
    if polish_note:
        msg += f"  [dim]{polish_note}[/dim]"
    console.print(msg)


def _split(s: str) -> list[str]:
    return [x.strip() for x in s.split(",") if x.strip()]


# ---------------------------------------------------------------------------
# find / show / list
# ---------------------------------------------------------------------------

def find_cmd(
    query: str = typer.Argument(..., help="检索查询；可为空字符串配合 --project 取项目热门"),
    scope: Optional[str] = typer.Option(None, "--scope", "-s"),
    project: Optional[str] = typer.Option(None, "--project", "-p"),
    top_k: int = typer.Option(10, "--top-k", "-k"),
    show_body: bool = typer.Option(False, "--body", help="同时打印正文摘要"),
    json_out: bool = typer.Option(False, "--json", help="输出 JSON（slash 命令用）"),
    inline: bool = typer.Option(False, "--inline",
                                  help="Inline 模式：JSON 含完整 body + 占位符数；prompt-recall slash 命令用"),
    is_template: Optional[bool] = typer.Option(None, "--is-template/--no-template",
                                                help="只看通用模板 / 只看原始材料"),
    lang: Optional[str] = typer.Option(None, "--lang", help="zh/en 过滤（取 tags 中的 语言-X）"),
    action_tag: Optional[str] = typer.Option(
        None, "--action-tag",
        help="按动作类型四字标签筛（设计优化 / 环境检查 / ... 14 之一）",
    ),
):
    """跨项目检索提示词（含 --inline 模式给 slash command 用）。"""
    cfg = _config()
    conn = indexer.open_db(cfg)

    if action_tag:
        # indexer 当前签名不支持 action_tag 参数。先 SQL 直接拿全集再让 score 排序处理
        # 顺序：WHERE action_tag=? + 现有 scope/project/is_template 过滤 → 评分排序
        sql = "SELECT * FROM prompts WHERE action_tag = ?"
        params: list = [action_tag]
        if scope:
            sql += " AND scope = ?"; params.append(scope)
        if project:
            sql += " AND project = ?"; params.append(project)
        if is_template is not None:
            sql += " AND is_template = ?"; params.append(1 if is_template else 0)
        sql += " ORDER BY used*2 + success_signal*3 DESC, created DESC LIMIT ?"
        params.append(top_k)
        rows = list(conn.execute(sql, params))
        if query.strip():
            # 简单 body 包含过滤（不走 FTS5；action_tag 优先）
            q_low = query.lower()
            rows = [r for r in rows if q_low in (r["title"] or "").lower() or q_low in (r["body"] or "").lower()]
        results = [(r, 0.0) for r in rows]
    elif query.strip():
        results = indexer.search(
            conn, query, scope=scope, project=project,
            is_template=is_template, top_k=top_k,
        )
    else:
        rows = indexer.list_all(
            conn, scope=scope, project=project, limit=top_k,
            sort_by="score", is_template=is_template,
        )
        results = [(r, 0.0) for r in rows]
    conn.close()

    if inline or json_out:
        from ..core import placeholders as _ph
        out = []
        for r, score in results:
            body = r["body"] or ""
            entry = {
                "id": r["id"], "title": r["title"], "scope": r["scope"],
                "project": r["project"], "tags": r["tags_csv"],
                "used": r["used"], "success": r["success_signal"],
                "score": round(score, 4),
                "is_template": bool(r["is_template"]) if "is_template" in r.keys() else False,
            }
            if inline:
                # inline 模式返回完整 body + 占位符信息
                entry["body"] = body
                ph_names = _ph.find(body)
                entry["placeholders"] = ph_names
                entry["placeholder_count"] = len(ph_names)
                try:
                    entry["description"] = r["description"] or ""
                except (KeyError, IndexError):
                    entry["description"] = ""
            else:
                entry["preview"] = body[:160]
            out.append(entry)
        console.print_json(json.dumps(out, ensure_ascii=False))
        return

    if not results:
        console.print(f"[dim]没有命中：{query}[/dim]")
        return

    table = Table(title=f"top {len(results)} for '{query}'", show_lines=False)
    table.add_column("#", justify="right", style="dim")
    table.add_column("scope", style="magenta")
    table.add_column("title")
    table.add_column("project", style="cyan")
    table.add_column("tags", style="green")
    table.add_column("used", justify="right")
    table.add_column("score", justify="right", style="dim")
    for i, (r, score) in enumerate(results, 1):
        table.add_row(
            str(i), r["scope"], r["title"], r["project"] or "-",
            (r["tags_csv"] or "")[:30], str(r["used"]), f"{score:.3f}",
        )
    console.print(table)
    if show_body:
        for i, (r, _s) in enumerate(results, 1):
            console.print(Panel(
                Markdown((r["body"] or "")[:600] + ("..." if len(r["body"] or "") > 600 else "")),
                title=f"#{i} {r['title']}",
                border_style="dim",
            ))


def show_cmd(
    target: str = typer.Argument(..., help="提示词 id 或 title 精确匹配"),
    use: bool = typer.Option(True, "--use/--no-use", help="计入 used 计数"),
):
    """打印一条提示词的完整内容。"""
    cfg = _config()
    conn = indexer.open_db(cfg)
    row = indexer.get_by_id(conn, target) or indexer.get_by_title(conn, target)
    if not row:
        # 模糊检索取第一条
        results = indexer.search(conn, target, top_k=1)
        if results:
            row = results[0][0]
    if not row:
        err_console.print(f"未找到：{target}")
        conn.close()
        raise typer.Exit(1)

    console.print(Panel.fit(
        Markdown(row["body"] or ""),
        title=f"[bold]{row['title']}[/bold]  [dim]({row['scope']}/{row['project'] or 'global'})[/dim]",
        border_style="cyan",
    ))
    console.print(f"[dim]id={row['id']}  used={row['used']}  success={row['success_signal']}  "
                  f"tags={row['tags_csv']}[/dim]")

    if use:
        indexer.bump_used(conn, row["id"])
    conn.close()


def list_cmd(
    scope: Optional[str] = typer.Option(None, "--scope", "-s",
                                         help="global / project / trap，默认全部"),
    project: Optional[str] = typer.Option(None, "--project", "-p"),
    limit: int = typer.Option(50, "--limit", "-n"),
):
    """列出全库或某个 scope 的提示词。"""
    cfg = _config()
    conn = indexer.open_db(cfg)
    rows = indexer.list_all(conn, scope=scope, project=project, limit=limit)
    counts = indexer.count_all(conn)
    conn.close()

    if not rows:
        console.print("[dim]空库。先 prompt-help import-claude-md 或 /prompt-save 加几条。[/dim]")
        return

    table = Table(
        title=f"prompts (total={counts['total']}  global={counts.get('global',0)}  "
              f"project={counts.get('project',0)}  trap={counts.get('trap',0)})",
        show_lines=False,
    )
    table.add_column("scope", style="magenta")
    table.add_column("title")
    table.add_column("project", style="cyan")
    table.add_column("tags", style="green")
    table.add_column("used", justify="right")
    table.add_column("origin", style="dim")
    for r in rows:
        table.add_row(
            r["scope"], r["title"], r["project"] or "-",
            (r["tags_csv"] or "")[:30], str(r["used"]), r["origin"],
        )
    console.print(table)


def generalize_cmd(
    target: str = typer.Argument(..., help="提示词 id 或 title 关键词"),
    backend: str = typer.Option("auto", "--backend", help="auto / cc_cli / api"),
    save: bool = typer.Option(False, "--save", help="把生成的模板版作为新条目入库"),
):
    """把项目专属提示词通用化（抽象 path/UUID/版本号 为占位符）。"""
    cfg = _config()
    conn = indexer.open_db(cfg)
    row = indexer.get_by_id(conn, target) or indexer.get_by_title(conn, target)
    if not row:
        results = indexer.search(conn, target, top_k=1)
        if results:
            row = results[0][0]
    if not row:
        err_console.print(f"未找到：{target}")
        conn.close()
        raise typer.Exit(1)

    console.print(f"[dim]正在调用后端通用化（mode={backend}）……[/dim]")
    result = optimizer.generalize(cfg, row["body"], mode=backend)
    if not result.success:
        err_console.print(f"通用化失败：{result.error}")
        conn.close()
        raise typer.Exit(1)

    console.print(Panel.fit(result.diff_text or "（无文本差异）", title=f"通用化 diff (后端: {result.backend})", border_style="cyan"))

    if save:
        new_p = storage.Prompt.new(
            title=(row["title"] or "未命名") + "（通用模板）",
            body=result.optimized,
            scope="global",
            tags=[t.strip() for t in (row["tags_csv"] or "").split(",") if t.strip()] + ["通用化"],
            origin="imported",
        )
        new_p.optimized_from = row["id"]
        file_path = storage.save(cfg, new_p, commit_msg=f"generalize: {row['title']}")
        indexer.upsert(conn, new_p, file_path)
        console.print(f"[green]✓[/green] 已存模板版：{new_p.id[-6:]} → {file_path}")
    else:
        console.print("[dim]加 --save 把模板版入库[/dim]")

    conn.close()


def find_traps_cmd(
    text: str = typer.Argument(..., help="待扫描的文本（如用户消息）"),
    max_n: int = typer.Option(2, "--max", "-n", help="最多返回几条 trap"),
    json_out: bool = typer.Option(False, "--json"),
):
    """扫描文本，返回所有触发的 trap 提示词。供 UserPromptSubmit hook 用。"""
    cfg = _config()
    conn = indexer.open_db(cfg)
    hits = indexer.search_traps_for_text(conn, text, max_n=max_n)
    conn.close()
    if json_out:
        out = [{"id": r["id"], "title": r["title"], "body": r["body"],
                "triggers": r["triggers_csv"]} for r in hits]
        console.print_json(json.dumps(out, ensure_ascii=False))
        return
    for r in hits:
        console.print(Panel(Markdown(r["body"]), title=f"⚠ trap: {r['title']}",
                            border_style="yellow"))
