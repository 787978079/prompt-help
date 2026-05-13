"""CLI：基于项目上下文优化提示词。

用法：
    prompt-help optimize-for-project --prompt "<原提示词>" --project <项目路径>
    cat prompt.txt | prompt-help optimize-for-project --project <项目路径>

输出：优化后的提示词到 stdout（纯文本）。
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

import typer

from ..core.config import load_config, load_dotenv_if_present
from ..core.project_optimize import optimize_for_project, extract_project_summary


def optimize_for_project_cmd(
    project: Path = typer.Option(
        ...,
        "--project", "-p",
        help="目标项目根目录（已登记或任意目录都行）",
        exists=True, file_okay=False, dir_okay=True,
    ),
    prompt: Optional[str] = typer.Option(
        None,
        "--prompt",
        help="原始提示词；不传则从 stdin 读",
    ),
    mode: str = typer.Option(
        "auto", "--mode",
        help="LLM 后端：auto / cc_cli / codex_cli / api",
    ),
    save: bool = typer.Option(
        False, "--save",
        help="优化完同时保存到我的库（origin=manual, scope=project）",
    ),
):
    """基于项目上下文用 LLM 优化提示词。"""
    load_dotenv_if_present()
    cfg = load_config()

    if not prompt:
        typer.echo("从 stdin 读取提示词…（Ctrl+D 结束）", err=True)
        prompt = sys.stdin.read()
    prompt = (prompt or "").strip()
    if not prompt:
        typer.echo("✗ 错误：提示词为空", err=True)
        raise typer.Exit(1)

    summary = extract_project_summary(project)
    typer.echo(
        f"项目摘要：{summary.project_name} "
        f"({len(summary.sections)} 个文件, {summary.total_chars} 字符)",
        err=True,
    )
    if not summary.sections:
        typer.echo("⚠ 项目目录里没找到 CLAUDE.md / package.json 等元信息文件，优化效果有限。", err=True)

    if mode not in ("auto", "cc_cli", "codex_cli", "api"):
        typer.echo(f"✗ 错误：--mode 必须是 auto/cc_cli/codex_cli/api，不是 {mode}", err=True)
        raise typer.Exit(1)

    result = optimize_for_project(cfg, prompt, project, mode=mode)  # type: ignore[arg-type]
    if not result.success or not result.optimized:
        typer.echo(f"✗ 失败（后端 {result.backend}）：{result.error or '空返回'}", err=True)
        raise typer.Exit(2)

    # 优化后的提示词：纯文本输出到 stdout
    sys.stdout.write(result.optimized)
    if not result.optimized.endswith("\n"):
        sys.stdout.write("\n")
    typer.echo(f"✓ 完成（后端 {result.backend}）", err=True)

    if save:
        from ..core import indexer, optimizer, storage
        first_line = result.optimized.splitlines()[0] if result.optimized else "项目优化"
        fallback_title = first_line.strip()[:10] or "未命名"
        typer.echo("生成精炼标题中…", err=True)
        title = optimizer.safe_generate_title(
            cfg, result.optimized, fallback=fallback_title, kind="project",
        )
        p = storage.Prompt.new(
            title=title,
            body=result.optimized,
            scope="project",
            project=project.name,
            tags=["项目优化"],
            origin="manual",
            source_ref=str(project),
            description=f"基于「{project.name}」项目上下文优化",
        )
        file_path = storage.save(cfg, p, commit_msg=f"CLI optimize-for-project: {project.name}")
        conn = indexer.open_db(cfg)
        try:
            indexer.upsert(conn, p, file_path)
        finally:
            conn.close()
        typer.echo(f"✓ 已保存到我的库：{file_path}", err=True)


def register(app: typer.Typer) -> None:
    app.command(name="optimize-for-project")(optimize_for_project_cmd)
