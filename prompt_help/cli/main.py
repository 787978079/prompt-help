"""Typer 主入口。

子命令：
- init / doctor / install-plugin / link-remote / reindex / sync / prune / why-matched
- import-claude-md / save / find / show / list
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path
from typing import Optional

# Windows 控制台默认 GBK / mbcs，无法打印 ✓/✗/中文 emoji；
# stdin 在 Git Bash 下也常被 cp936 解码出 surrogate，写回 UTF-8 文件会报错。
# 先把 stdio 全部切到 utf-8（必须在 import rich/typer 前做）。
for _stream in (sys.stdin, sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    except Exception:
        pass

import typer
from rich.console import Console
from rich.table import Table

from ..core import indexer, proc, storage
from ..core.config import Config, load_config, load_dotenv_if_present, save_config

app = typer.Typer(
    name="prompt-help",
    help="跨项目提示词管理 · 对话挖掘 · 产品发现工具",
    no_args_is_help=True,
    add_completion=False,
)
console = Console()
err_console = Console(stderr=True, style="red")


def _config() -> Config:
    load_dotenv_if_present()
    return load_config()


# ---------------------------------------------------------------------------
# init / doctor / install-plugin / link-remote
# ---------------------------------------------------------------------------

@app.command()
def init(
    create_remote: bool = typer.Option(
        True, "--create-remote/--no-create-remote",
        help="是否调 gh 创建私仓 prompt-help-vault 并 push（需 gh auth login）",
    ),
    remote_name: str = typer.Option("prompt-help-vault", help="私仓名"),
):
    """初始化 ~/.prompt-help/：建目录、git init、写默认 config，可选建私仓。"""
    cfg = _config()
    vault = cfg.vault_path

    # 目录
    for d in (vault, cfg.prompts_dir / "global", cfg.prompts_dir / "projects",
              cfg.prompts_dir / "traps", cfg.inbox_dir, cfg.briefs_dir,
              cfg.pulse_dir, cfg.logs_dir):
        d.mkdir(parents=True, exist_ok=True)
    console.print(f"[green]✓[/green] 创建目录：{vault}")

    # git init
    if not (vault / ".git").is_dir():
        proc.run(["git", "init", "-q", str(vault)], check=False)
        # 配置 user
        proc.run(["git", "-C", str(vault), "config", "user.name", cfg.git.commit_user_name], check=False)
        proc.run(["git", "-C", str(vault), "config", "user.email", cfg.git.commit_user_email], check=False)
        console.print(f"[green]✓[/green] git init {vault}")
    else:
        console.print(f"[dim]○[/dim] git 仓已存在：{vault}")

    # config.toml
    if not cfg.config_file.is_file():
        save_config(cfg)
        console.print(f"[green]✓[/green] 写入默认配置：{cfg.config_file}")
    else:
        console.print(f"[dim]○[/dim] 配置文件已存在：{cfg.config_file}")

    # 写一个 README 到 vault
    vault_readme = vault / "README.md"
    if not vault_readme.is_file():
        vault_readme.write_text(
            "# prompt-help-vault\n\n"
            "由 [Prompt Help](https://github.com/) 维护的提示词金库。\n"
            "**这是私仓，含有项目内部信息和踩坑记录，绝不公开。**\n",
            encoding="utf-8",
        )

    # 初始 commit
    proc.run(["git", "-C", str(vault), "add", "-A"], check=False)
    proc.run(
        ["git", "-C", str(vault), "commit", "-q", "-m", "init: prompt-help vault"],
        check=False,
    )

    # SQLite 索引
    indexer.open_db(cfg).close()
    console.print(f"[green]✓[/green] SQLite FTS5 索引：{cfg.index_db}")

    # 创建远程私仓
    if create_remote:
        gh = shutil.which("gh")
        if not gh:
            console.print("[yellow]![/yellow] 未找到 gh CLI，跳过创建私仓；事后用 prompt-help link-remote 补")
        else:
            r = proc.run(
                ["gh", "repo", "create", remote_name, "--private",
                 "--source", str(vault), "--remote", "origin", "--push"],
                capture_output=True, text=True,
            )
            if r.returncode == 0:
                console.print(f"[green]✓[/green] 私仓已创建并 push：{remote_name}")
                cfg.git.auto_push = True
                # 抓 remote URL
                ru = proc.run(
                    ["git", "-C", str(vault), "config", "--get", "remote.origin.url"],
                    capture_output=True, text=True,
                )
                if ru.returncode == 0:
                    cfg.git.remote_url = ru.stdout.strip()
                save_config(cfg)
            else:
                console.print(f"[yellow]![/yellow] gh 创建私仓失败（可事后 link-remote）：{r.stderr.strip()}")

    console.print()
    console.print("[bold green]初始化完成[/bold green]。下一步：")
    console.print("  1. 复制 .env.example 为 .env，填入 DEEPSEEK_API_KEY")
    console.print("  2. prompt-help install-plugin")
    console.print("  3. prompt-help doctor  # 自检")
    console.print("  4. prompt-help import-claude-md ~/projects/*/CLAUDE.md  # 导入种子")


@app.command()
def doctor():
    """自检：路径、git、API key、CC 插件、SQLite。"""
    cfg = _config()
    rows: list[tuple[str, str, str]] = []  # (项, 状态, 详情)

    # vault
    rows.append(("vault 路径", "✓" if cfg.vault_path.is_dir() else "✗", str(cfg.vault_path)))
    rows.append(("config.toml", "✓" if cfg.config_file.is_file() else "✗", str(cfg.config_file)))
    rows.append(("SQLite 索引", "✓" if cfg.index_db.is_file() else "✗", str(cfg.index_db)))

    # API key
    api_key = cfg.get_api_key()
    rows.append((f"API key（{cfg.llm.api_key_env}）",
                 "✓" if api_key else "✗",
                 f"{api_key[:8]}...{api_key[-4:]}" if api_key else "未设置"))

    # git
    git_ok = (cfg.vault_path / ".git").is_dir()
    rows.append(("vault git 仓", "✓" if git_ok else "✗", str(cfg.vault_path / ".git")))
    if git_ok:
        ru = proc.run(
            ["git", "-C", str(cfg.vault_path), "remote", "-v"],
            capture_output=True, text=True,
        )
        remotes = ru.stdout.strip().splitlines()
        rows.append(("git remote", "✓" if remotes else "○",
                     remotes[0] if remotes else "（未配置远程，本地仓）"))

    # CC 插件
    plugin_dir = Path.home() / ".claude" / "plugins" / "prompt-help"
    rows.append(("CC 插件已安装", "✓" if plugin_dir.is_dir() else "✗", str(plugin_dir)))

    # 计数
    if cfg.index_db.is_file():
        try:
            conn = indexer.open_db(cfg)
            counts = indexer.count_all(conn)
            conn.close()
            rows.append(("提示词总数", "✓",
                         f"total={counts['total']}  global={counts.get('global', 0)}  "
                         f"project={counts.get('project', 0)}  trap={counts.get('trap', 0)}"))
        except Exception as e:
            rows.append(("提示词总数", "✗", f"读取失败：{e}"))

    table = Table(title="prompt-help doctor", show_lines=False)
    table.add_column("项")
    table.add_column("状态", justify="center")
    table.add_column("详情")
    for k, s, v in rows:
        color = "green" if s == "✓" else ("yellow" if s == "○" else "red")
        table.add_row(k, f"[{color}]{s}[/{color}]", v)
    console.print(table)


@app.command(name="install-plugin")
def install_plugin(
    force: bool = typer.Option(False, "--force", help="覆盖已存在的插件目录"),
):
    """把 plugin/ 目录复制到 ~/.claude/plugins/prompt-help/。"""
    src = Path(__file__).resolve().parents[1] / "plugin"
    dst = Path.home() / ".claude" / "plugins" / "prompt-help"

    if not src.is_dir():
        err_console.print(f"找不到插件源目录：{src}")
        raise typer.Exit(1)

    if dst.exists():
        if not force:
            console.print(f"[yellow]插件已存在 {dst}，加 --force 覆盖[/yellow]")
            raise typer.Exit(2)
        shutil.rmtree(dst)

    shutil.copytree(src, dst)
    console.print(f"[green]✓[/green] 插件已安装到 {dst}")
    console.print("Claude Code 启动时会自动加载。")


@app.command(name="link-remote")
def link_remote(url: str = typer.Argument(..., help="远程仓 URL（git@github.com:user/repo.git）")):
    """事后给 vault 加 git remote 并 push。"""
    cfg = _config()
    if not (cfg.vault_path / ".git").is_dir():
        err_console.print("vault 还没 git init，先跑 prompt-help init")
        raise typer.Exit(1)
    proc.run(["git", "-C", str(cfg.vault_path), "remote", "remove", "origin"],
                   capture_output=True)
    r = proc.run(["git", "-C", str(cfg.vault_path), "remote", "add", "origin", url], check=False)
    if r.returncode != 0:
        err_console.print("添加 remote 失败")
        raise typer.Exit(1)
    proc.run(["git", "-C", str(cfg.vault_path), "push", "-u", "origin", "HEAD"], check=False)
    cfg.git.remote_url = url
    cfg.git.auto_push = True
    save_config(cfg)
    console.print(f"[green]✓[/green] 已链接到 {url}")


@app.command(name="migrate")
def migrate(
    url: str = typer.Argument(..., help="远程仓 URL：git@github.com:user/prompt-help-vault.git"),
    force: bool = typer.Option(False, "--force", help="vault 已存在时强制覆盖"),
):
    """跨设备迁移：从 GitHub 私仓 git clone 已有库到本机。"""
    cfg = _config()
    if cfg.vault_path.exists() and any(cfg.vault_path.iterdir()):
        if not force:
            err_console.print(
                f"vault 路径 {cfg.vault_path} 已存在且非空。\n"
                "加 --force 强制覆盖（会先备份当前内容到 .backup-<timestamp>）"
            )
            raise typer.Exit(1)
        # 备份
        import shutil, time
        backup = cfg.vault_path.with_suffix(f".backup-{int(time.time())}")
        shutil.move(str(cfg.vault_path), str(backup))
        console.print(f"[yellow]已备份原 vault 到 {backup}[/yellow]")

    cfg.vault_path.parent.mkdir(parents=True, exist_ok=True)
    r = proc.run(
        ["git", "clone", url, str(cfg.vault_path)],
        check=False,
    )
    if r.returncode != 0:
        err_console.print("git clone 失败")
        raise typer.Exit(1)

    # 重建 SQLite 索引
    from . import admin as admin_mod
    n = indexer.reindex_from_disk(cfg)
    console.print(f"[green]✓[/green] 迁移完成：vault 已恢复，索引重建 {n} 条")
    console.print(f"vault: {cfg.vault_path}")


# ---------------------------------------------------------------------------
# 在 admin / actions 模块定义后再注册（见 cli/admin.py、cli/actions.py）
# ---------------------------------------------------------------------------

from . import (  # noqa: E402
    actions, admin, channels, embed, inbox, pm_mode, project_optimize,
    public_library, pulse, share, transcripts,
)

actions.register(app)
admin.register(app)
channels.register(app)
embed.register(app)
inbox.register(app)
pm_mode.register(app)
project_optimize.register(app)
public_library.register(app)
pulse.register(app)
share.register(app)
transcripts.register(app)


def main():
    app()


if __name__ == "__main__":
    main()
