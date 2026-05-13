"""团队 channel 订阅（Phase 15 T2）。

设计：
- 订阅配置存 `<vault>/channels.json`，schema：
  [{name, git_url, last_pull?, last_sync_id?}]
- 每次 pull：
  1. clone --depth 1 到 sandbox `<vault>/_channels/<name>/`（已存在 → git pull --rebase）
  2. 扫 sandbox 内 prompts/*.md
  3. 对每条与本地比对（hash），新内容写到 inbox 等审；同 title 不同内容也进 inbox
- 用户在 GUI 的「待审」tab 看到带 `[来自频道-X]` 标记的候选

为什么不直接合并到主库：
- 朋友改动可能含敏感信息，必须用户人工过审
- 保留 PH 的"私有第二大脑"边界
"""

from __future__ import annotations

import datetime as dt
import json
import shutil
import subprocess
from dataclasses import asdict, dataclass, field

from . import proc
from pathlib import Path
from typing import Optional

from .config import Config


@dataclass
class Channel:
    name: str            # 短标识，文件夹用，禁含 /\\
    git_url: str         # git clone 用的 URL（http / ssh / file）
    last_pull: Optional[str] = None       # ISO 时间戳
    last_sync_id: Optional[str] = None    # 上次 manifest.sync_id（若对方导出时填了）
    note: str = ""


def _channels_file(cfg: Config) -> Path:
    return cfg.vault_path / "channels.json"


def _sandbox_dir(cfg: Config, name: str) -> Path:
    safe = "".join(c for c in name if c.isalnum() or c in "-_")[:60] or "unnamed"
    return cfg.vault_path / "_channels" / safe


def load_channels(cfg: Config) -> list[Channel]:
    f = _channels_file(cfg)
    if not f.is_file():
        return []
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
    except Exception:
        return []
    out: list[Channel] = []
    for item in data:
        try:
            out.append(Channel(**item))
        except TypeError:
            continue
    return out


def save_channels(cfg: Config, channels: list[Channel]) -> None:
    f = _channels_file(cfg)
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(
        json.dumps([asdict(c) for c in channels], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def add_channel(cfg: Config, name: str, git_url: str, note: str = "") -> Channel:
    """新增订阅。同 name 时覆盖 url/note。"""
    chans = load_channels(cfg)
    for c in chans:
        if c.name == name:
            c.git_url = git_url
            c.note = note
            save_channels(cfg, chans)
            return c
    new = Channel(name=name, git_url=git_url, note=note)
    chans.append(new)
    save_channels(cfg, chans)
    return new


def remove_channel(cfg: Config, name: str) -> bool:
    chans = load_channels(cfg)
    before = len(chans)
    chans = [c for c in chans if c.name != name]
    if len(chans) < before:
        save_channels(cfg, chans)
        sandbox = _sandbox_dir(cfg, name)
        if sandbox.is_dir():
            shutil.rmtree(sandbox, ignore_errors=True)
        return True
    return False


def pull_channel(cfg: Config, channel: Channel) -> dict:
    """从远程 git 仓库 pull 最新；新增/变化的 prompt 写到 inbox。

    返回 {pulled_n, new_in_inbox, error?}。
    """
    sandbox = _sandbox_dir(cfg, channel.name)
    sandbox.parent.mkdir(parents=True, exist_ok=True)

    try:
        if not sandbox.is_dir() or not (sandbox / ".git").is_dir():
            # 首次 clone
            if sandbox.exists():
                shutil.rmtree(sandbox, ignore_errors=True)
            r = proc.run(
                ["git", "clone", "--depth", "1", channel.git_url, str(sandbox)],
                capture_output=True, text=True, timeout=60,
            )
            if r.returncode != 0:
                return {"pulled_n": 0, "new_in_inbox": 0,
                        "error": f"git clone 失败：{r.stderr[:500]}"}
        else:
            r = proc.run(
                ["git", "-C", str(sandbox), "pull", "--rebase"],
                capture_output=True, text=True, timeout=60,
            )
            if r.returncode != 0:
                return {"pulled_n": 0, "new_in_inbox": 0,
                        "error": f"git pull 失败：{r.stderr[:500]}"}
    except subprocess.TimeoutExpired:
        return {"pulled_n": 0, "new_in_inbox": 0, "error": "git 操作超时（60s）"}
    except Exception as e:
        return {"pulled_n": 0, "new_in_inbox": 0,
                "error": f"{type(e).__name__}: {e}"}

    # 扫 sandbox 内的 prompts/*.md
    pulled_n, new_in_inbox = _scan_and_stage(cfg, channel, sandbox)

    # 更新 last_pull 时间
    channel.last_pull = dt.datetime.now(dt.timezone.utc).isoformat()
    chans = load_channels(cfg)
    for c in chans:
        if c.name == channel.name:
            c.last_pull = channel.last_pull
    save_channels(cfg, chans)

    return {"pulled_n": pulled_n, "new_in_inbox": new_in_inbox, "error": None}


def _scan_and_stage(cfg: Config, channel: Channel, sandbox: Path) -> tuple[int, int]:
    """扫 sandbox 找 .md prompts，新内容（基于 title 对比本地）写到 inbox。"""
    from . import indexer, storage
    inbox_dir = cfg.inbox_dir / f"from-channel-{channel.name}"
    inbox_dir.mkdir(parents=True, exist_ok=True)

    candidates: list[Path] = []
    # prompts/ 目录是 PH 导出的标准；如果不是 PH 格式但也是 md prompts，扫子目录
    for sub in ["prompts", "prompts/global", "prompts/projects",
                 "prompts/traps", "."]:
        d = sandbox / sub
        if d.is_dir():
            candidates.extend(d.rglob("*.md"))
    # 去重
    candidates = list({p.resolve() for p in candidates})

    pulled = 0
    new_in_inbox = 0
    conn = indexer.open_db(cfg)
    try:
        for md_path in candidates:
            try:
                content = md_path.read_text(encoding="utf-8", errors="replace")
                p = storage.parse(content)
            except Exception:
                continue
            pulled += 1
            local = indexer.get_by_title(conn, p.title)
            if local and (local["body"] or "") == p.body:
                # 完全相同，跳过
                continue
            # 写到 inbox 等用户审
            from ulid import ULID
            inbox_filename = f"{ULID()}-{p.title[:30].replace('/', '-')}.md"
            inbox_path = inbox_dir / inbox_filename
            # 附加 from_channel 标记到 frontmatter（用户审核后会重新走 storage.save）
            if "tags" in content or "---" in content:
                # 保留原格式，在末尾加注释
                content += f"\n<!-- from prompt-help channel: {channel.name} -->\n"
            try:
                inbox_path.write_text(content, encoding="utf-8")
                new_in_inbox += 1
            except Exception:
                continue
    finally:
        conn.close()
    return pulled, new_in_inbox
