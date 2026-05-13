"""团队 channel 订阅 CLI（Phase 15 T2）。

用法：
  prompt-help channel add <name> <git_url> [--note "..."]
  prompt-help channel list
  prompt-help channel pull [<name>]      # 不指定则拉所有
  prompt-help channel remove <name>
"""

from __future__ import annotations

from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from ..core import channels as ch
from ..core.config import Config, load_config, load_dotenv_if_present

console = Console()
err_console = Console(stderr=True, style="red")


def register(app: typer.Typer) -> None:
    sub = typer.Typer(help="团队 channel 订阅（拉朋友的 git 仓库到 inbox 等审）")
    sub.command("add")(add_cmd)
    sub.command("list")(list_cmd)
    sub.command("pull")(pull_cmd)
    sub.command("remove")(remove_cmd)
    app.add_typer(sub, name="channel")


def _config() -> Config:
    load_dotenv_if_present()
    return load_config()


def add_cmd(
    name: str = typer.Argument(..., help="频道名（短标识，禁含空格）"),
    git_url: str = typer.Argument(..., help="git 仓库 URL"),
    note: str = typer.Option("", "--note", "-n"),
):
    """订阅一个朋友的 git 仓库。同名覆盖。"""
    cfg = _config()
    c = ch.add_channel(cfg, name, git_url, note=note)
    console.print(f"[green]✓[/green] 已订阅：{c.name} → {c.git_url}")
    console.print(f"下一步：[bold]prompt-help channel pull {c.name}[/bold]")


def list_cmd():
    """列出所有订阅。"""
    cfg = _config()
    channels = ch.load_channels(cfg)
    if not channels:
        console.print("[dim]还没订阅任何 channel。用 `prompt-help channel add <name> <git_url>` 添加[/dim]")
        return
    table = Table(title=f"已订阅 channel（{len(channels)} 个）")
    table.add_column("name"); table.add_column("git_url"); table.add_column("last_pull")
    table.add_column("note")
    for c in channels:
        table.add_row(c.name, c.git_url, (c.last_pull or "—")[:19], c.note or "")
    console.print(table)


def pull_cmd(
    name: Optional[str] = typer.Argument(None, help="频道名；不指定则拉所有"),
):
    """从远程 pull 最新内容；新增 / 变化的 prompt 写到 inbox 等审。"""
    cfg = _config()
    channels = ch.load_channels(cfg)
    if name:
        channels = [c for c in channels if c.name == name]
        if not channels:
            err_console.print(f"找不到 channel：{name}")
            raise typer.Exit(1)

    if not channels:
        console.print("[dim]没有任何订阅可拉[/dim]")
        return

    total_new = 0
    for c in channels:
        console.print(f"[dim]→ pull {c.name}（{c.git_url}）…[/dim]")
        r = ch.pull_channel(cfg, c)
        if r.get("error"):
            console.print(f"[red]✗[/red] {c.name}：{r['error']}")
            continue
        console.print(
            f"[green]✓[/green] {c.name}：扫到 {r['pulled_n']} 条，"
            f"{r['new_in_inbox']} 条新内容已放进 inbox 等审"
        )
        total_new += r["new_in_inbox"]
    if total_new > 0:
        console.print(f"\n共 {total_new} 条进 inbox：GUI 的「待审」tab 查看 / `prompt-help inbox list`")


def remove_cmd(name: str = typer.Argument(...)):
    """取消订阅 + 删 sandbox。"""
    cfg = _config()
    if ch.remove_channel(cfg, name):
        console.print(f"[green]✓[/green] 已取消订阅：{name}")
    else:
        err_console.print(f"找不到 channel：{name}")
        raise typer.Exit(1)
