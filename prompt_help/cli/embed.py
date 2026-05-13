"""embedding CLI：算 / 查 / 看进度（V2 启用，默认 cfg.embedding.enabled=False）。"""

from __future__ import annotations

import typer
from rich.console import Console
from rich.table import Table

from ..core import embeddings, indexer
from ..core.config import Config, load_config, load_dotenv_if_present

console = Console()
err_console = Console(stderr=True, style="red")


def register(app: typer.Typer) -> None:
    sub = typer.Typer(help="embedding 检索（V2，默认关）")
    sub.command("compute")(compute_cmd)
    sub.command("query")(query_cmd)
    sub.command("status")(status_cmd)
    app.add_typer(sub, name="embed")


def _config() -> Config:
    load_dotenv_if_present()
    return load_config()


def status_cmd():
    """看 embedding 配置和库内已嵌入情况。"""
    cfg = _config()
    table = Table(title="embedding 状态")
    table.add_column("项")
    table.add_column("值")
    table.add_row("启用", "✓" if cfg.embedding.enabled else "○ 关闭（默认；库 100+ 条后再开）")
    table.add_row("provider", cfg.embedding.provider)
    table.add_row("base_url", cfg.embedding.base_url)
    table.add_row("model", cfg.embedding.model)
    table.add_row("维度", str(cfg.embedding.dim))
    console.print(table)
    console.print(
        "\n[dim]启用步骤：编辑 ~/.prompt-help/config.toml 的 [embedding] 段："
        "enabled=true + 选 provider/model；然后 prompt-help embed compute[/dim]"
    )


def compute_cmd(
    force: bool = typer.Option(False, "--force", help="重新计算所有，不跳过已有"),
):
    """给库里所有提示词算 embedding。"""
    cfg = _config()
    if not cfg.embedding.enabled:
        err_console.print(
            "embedding 未启用。先在 ~/.prompt-help/config.toml 改 [embedding] enabled = true"
        )
        raise typer.Exit(1)

    try:
        backend = embeddings.get_backend(cfg)
    except Exception as e:
        err_console.print(f"后端初始化失败：{e}")
        raise typer.Exit(1)

    # TODO（V2）：indexer 加 embedding_vec BLOB 列、computed_at TEXT 列
    # 这里框架已就绪，待 indexer schema migration + 集成 search() 时启用
    console.print(
        "[yellow]embed compute 框架已就绪[/yellow]：indexer 列扩展 + search 集成排在 V2。\n"
        "目前可手工调 embeddings.get_backend(cfg).embed([...]) 验证后端联通。"
    )


def query_cmd(
    text: str = typer.Argument(..., help="语义查询文本"),
    top_k: int = typer.Option(5, "--top-k"),
):
    """语义检索 top-k（V2）。"""
    cfg = _config()
    if not cfg.embedding.enabled:
        err_console.print("embedding 未启用。先 prompt-help embed status 看说明。")
        raise typer.Exit(1)

    try:
        backend = embeddings.get_backend(cfg)
        q_vec = backend.embed([text])[0]
        console.print(f"[green]✓[/green] 后端联通；query 向量维度 {len(q_vec)}")
        console.print(
            "[yellow]检索 top-k 待 indexer schema 扩展后启用。[/yellow]\n"
            "现在可改用 prompt-help find 走 FTS5 全文匹配。"
        )
    except Exception as e:
        err_console.print(f"后端调用失败：{e}")
        raise typer.Exit(1)
