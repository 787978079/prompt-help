"""分享：ZIP 导出 / 导入。

ZIP 内容：
  manifest.json — 导出时间、来源、提示词清单元数据
  prompts/<id>.md — 每条提示词原始 frontmatter（剔除 used / last_used / success_signal）

朋友拿到 ZIP 拖进 GUI 或跑 prompt-help import-zip <path> 即可入库。
import 时自动加 tag "from-friend"，scope 强制 global，origin="imported"。
"""

from __future__ import annotations

import datetime as dt
import json
import re
import zipfile
from dataclasses import asdict
from pathlib import Path
from typing import Optional

import typer
import yaml
from rich.console import Console
from rich.table import Table

from ..core import indexer, storage
from ..core.config import Config, load_config, load_dotenv_if_present

console = Console()
err_console = Console(stderr=True, style="red")


def register(app: typer.Typer) -> None:
    app.command(name="export-zip")(export_zip_cmd)
    app.command(name="import-zip")(import_zip_cmd)


def _config() -> Config:
    load_dotenv_if_present()
    return load_config()


# ---------------------------------------------------------------------------
# export
# ---------------------------------------------------------------------------

def export_zip(cfg, out_path: Path, *,
               scope: Optional[str] = None,
               tag: Optional[str] = None,
               project: Optional[str] = None,
               ids: Optional[list[str]] = None,
               delta: bool = False) -> int:
    """库函数版：返回写入条数。GUI 和 CLI 共用。

    P14-T4.1：
    - delta=True：只导出上次 export 之后修改过的条目（按 .last_export_sync 时间戳）
    - manifest 加 sync_id (UUID4) + delta_from + per-item modified_at + body_hash
    - 落 .last_export_sync 时间戳便于下次增量
    """
    import hashlib
    import uuid as _uuid

    last_export_path = cfg.vault_path / ".last_export_sync"
    last_export_ts = None
    last_sync_id = None
    if delta and last_export_path.is_file():
        try:
            saved = json.loads(last_export_path.read_text(encoding="utf-8"))
            last_export_ts = saved.get("exported_at")
            last_sync_id = saved.get("sync_id")
        except Exception:
            pass

    conn = indexer.open_db(cfg)
    if ids:
        rows = []
        for i in ids:
            r = indexer.get_by_id(conn, i)
            if r:
                rows.append(r)
    else:
        rows = indexer.list_all(conn, scope=scope, project=project, limit=10000)
        if tag:
            rows = [r for r in rows if tag in (r["tags_csv"] or "")]
    conn.close()

    # delta 模式：过滤出 created/last_used 在 last_export_ts 之后的
    if delta and last_export_ts:
        filtered = []
        for r in rows:
            try:
                modified_at = r["last_used"] or r["created"] or ""
                if modified_at and modified_at > last_export_ts:
                    filtered.append(r)
            except (KeyError, IndexError):
                filtered.append(r)
        rows = filtered

    if not rows:
        return 0

    out_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_items: list[dict] = []
    sync_id = str(_uuid.uuid4())
    now_iso = dt.datetime.now(dt.timezone.utc).isoformat()
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for r in rows:
            file_path = Path(r["file_path"])
            if not file_path.is_file():
                continue
            try:
                p = storage.parse(file_path.read_text(encoding="utf-8"))
            except Exception:
                continue
            # 剔除隐私字段
            p.used = 0
            p.last_used = None
            p.success_signal = 0
            content = storage.serialize(p)
            body_hash = hashlib.sha256(p.body.encode("utf-8")).hexdigest()[:16]
            arcname = f"prompts/{p.id}.md"
            zf.writestr(arcname, content)
            manifest_items.append({
                "id": p.id,
                "title": p.title,
                "scope": p.scope,
                "categories": p.categories,
                "tags": p.tags,
                "filename": arcname,
                "body_hash": body_hash,
                "modified_at": r["last_used"] or r["created"] or "",
            })

        manifest = {
            "kind": "prompt-help-share",
            "schema_version": 2,           # 升级到 2（含 sync_id / body_hash / modified_at）
            "exported_at": now_iso,
            "sync_id": sync_id,
            "delta_from": last_sync_id if delta else None,
            "n_prompts": len(manifest_items),
            "items": manifest_items,
        }
        zf.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))

    # 记录本次 sync 时间，供下次 delta 用
    try:
        last_export_path.write_text(
            json.dumps({"exported_at": now_iso, "sync_id": sync_id}, ensure_ascii=False),
            encoding="utf-8",
        )
    except Exception:
        pass

    return len(manifest_items)


def export_zip_cmd(
    out: Path = typer.Option(..., "--out", "-o", help="输出 ZIP 路径"),
    scope: Optional[str] = typer.Option(None, "--scope", help="只导某个 scope"),
    tag: Optional[str] = typer.Option(None, "--tag", help="只导含某 tag 的"),
    project: Optional[str] = typer.Option(None, "--project"),
    ids: Optional[str] = typer.Option(None, "--ids", help="逗号分隔 id 列表"),
    delta: bool = typer.Option(False, "--delta",
                                 help="增量包：只导出上次 export 之后修改过的"),
):
    """导出符合条件的提示词到 ZIP，分享给朋友。"""
    cfg = _config()
    id_list = [i.strip() for i in ids.split(",") if i.strip()] if ids else None
    n = export_zip(cfg, out, scope=scope, tag=tag, project=project, ids=id_list, delta=delta)
    if n == 0:
        msg = "[yellow]没有符合条件的提示词[/yellow]"
        if delta:
            msg += "（增量模式：上次 export 之后无变动）"
        console.print(msg)
        raise typer.Exit(1)
    mode = "增量" if delta else "全量"
    console.print(f"[green]✓[/green] 已{mode}导出 {n} 条 → {out}")


# ---------------------------------------------------------------------------
# import
# ---------------------------------------------------------------------------

def inspect_zip(cfg, archive: Path) -> dict:
    """读取 ZIP 但不导入——返回 {items, conflicts, manifest, error?}。

    P14-T4.3 用于冲突 UI 预审：让用户看到哪些会冲突 / 内容差异，再决定策略。
    每个 item 含：title / scope / body / 是否本地已存在 / body_hash / local_body_hash
    """
    import hashlib
    if not archive.is_file():
        return {"items": [], "conflicts": [], "error": f"文件不存在：{archive}"}
    items: list[dict] = []
    conflicts: list[dict] = []
    conn = indexer.open_db(cfg)
    try:
        with zipfile.ZipFile(archive, "r") as zf:
            try:
                manifest = json.loads(zf.read("manifest.json").decode("utf-8"))
            except KeyError:
                return {"items": [], "conflicts": [], "manifest": None,
                        "error": "ZIP 缺 manifest.json"}
            if manifest.get("kind") != "prompt-help-share":
                return {"items": [], "conflicts": [], "manifest": manifest,
                        "error": f"manifest.kind 不对：{manifest.get('kind')}"}
            for item in manifest.get("items") or []:
                arcname = item.get("filename")
                if not arcname:
                    continue
                try:
                    content = zf.read(arcname).decode("utf-8", errors="replace")
                    p = storage.parse(content)
                except Exception:
                    continue
                local = indexer.get_by_title(conn, p.title)
                local_body = local["body"] if local else ""
                local_hash = hashlib.sha256((local_body or "").encode("utf-8")).hexdigest()[:16]
                remote_hash = hashlib.sha256(p.body.encode("utf-8")).hexdigest()[:16]
                rec = {
                    "title": p.title,
                    "remote_body": p.body,
                    "remote_scope": p.scope,
                    "remote_tags": list(p.tags),
                    "remote_hash": remote_hash,
                    "filename": arcname,
                    "local_exists": local is not None,
                    "local_body": local_body,
                    "local_hash": local_hash,
                    "identical": local is not None and local_hash == remote_hash,
                }
                items.append(rec)
                # 冲突 = 本地存在 + 内容不同
                if local is not None and local_hash != remote_hash:
                    conflicts.append(rec)
    finally:
        conn.close()
    return {"items": items, "conflicts": conflicts, "manifest": manifest, "error": None}


def import_zip(cfg, archive: Path, *,
               prefix_tag: str = "来自朋友",
               scope: str = "global",
               merge_strategy: str = "skip",
               per_item_actions: Optional[dict] = None) -> dict:
    """库函数版：返回 {saved, skipped, replaced, kept_both, error?}。

    P14-T4.2 三种合并策略：
    - "skip"：本地已存在同 title 时跳过（默认，保守）
    - "replace"：覆盖本地
    - "both"：保留为副本（title 加"（朋友 X 版）"后缀）

    per_item_actions: {title: "skip"|"replace"|"both"} 覆盖默认策略（GUI 用）。
    """
    if not archive.is_file():
        return {"saved": 0, "skipped": 0, "replaced": 0, "kept_both": 0,
                "error": f"文件不存在：{archive}"}
    saved = 0
    skipped = 0
    replaced = 0
    kept_both = 0
    per_item_actions = per_item_actions or {}
    conn = indexer.open_db(cfg)

    try:
        with zipfile.ZipFile(archive, "r") as zf:
            try:
                manifest = json.loads(zf.read("manifest.json").decode("utf-8"))
            except KeyError:
                conn.close()
                return {"saved": 0, "skipped": 0, "replaced": 0, "kept_both": 0,
                        "error": "ZIP 缺 manifest.json"}

            if manifest.get("kind") != "prompt-help-share":
                conn.close()
                return {"saved": 0, "skipped": 0, "replaced": 0, "kept_both": 0,
                        "error": f"manifest.kind 不对：{manifest.get('kind')}"}

            for item in manifest.get("items") or []:
                arcname = item.get("filename")
                if not arcname:
                    continue
                try:
                    content = zf.read(arcname).decode("utf-8", errors="replace")
                    p = storage.parse(content)
                except Exception:
                    continue
                p.scope = scope
                p.project = None if scope == "global" else p.project
                if prefix_tag and prefix_tag not in p.tags:
                    p.tags = list(p.tags) + [prefix_tag]
                p.used = 0
                p.success_signal = 0
                p.last_used = None
                p.origin = "imported"

                local = indexer.get_by_title(conn, p.title)
                action = per_item_actions.get(p.title, merge_strategy)

                if local is None:
                    # 新条目，直接入
                    from ulid import ULID
                    p.id = str(ULID())
                    file_path = storage.save(
                        cfg, p, commit_msg=f"import-zip: {p.title[:30]}",
                    )
                    indexer.upsert(conn, p, file_path)
                    saved += 1
                    continue

                if action == "skip":
                    skipped += 1
                    continue
                if action == "replace":
                    # 用同 id 覆盖
                    p.id = local["id"]
                    file_path = storage.save(
                        cfg, p, commit_msg=f"import-zip replace: {p.title[:30]}",
                    )
                    indexer.upsert(conn, p, file_path)
                    replaced += 1
                    continue
                if action == "both":
                    from ulid import ULID
                    p.id = str(ULID())
                    p.title = f"{p.title}（{prefix_tag} 版）"
                    file_path = storage.save(
                        cfg, p, commit_msg=f"import-zip both: {p.title[:30]}",
                    )
                    indexer.upsert(conn, p, file_path)
                    kept_both += 1
                    continue
    finally:
        conn.close()
    return {"saved": saved, "skipped": skipped, "replaced": replaced,
            "kept_both": kept_both, "error": None}


def import_zip_cmd(
    archive: Path = typer.Argument(..., help="朋友给的 ZIP 文件"),
    prefix_tag: str = typer.Option(
        "来自朋友", "--prefix-tag",
        help="导入的提示词自动加这个 tag",
    ),
    scope: str = typer.Option(
        "global", "--scope",
        help="导入后的 scope；默认 global，让你跨项目用",
    ),
):
    """从朋友给的 ZIP 包导入提示词到自己库。"""
    if not archive.is_file():
        err_console.print(f"文件不存在：{archive}")
        raise typer.Exit(1)

    cfg = _config()
    saved = 0
    skipped_dup = 0
    conn = indexer.open_db(cfg)

    with zipfile.ZipFile(archive, "r") as zf:
        try:
            manifest = json.loads(zf.read("manifest.json").decode("utf-8"))
        except KeyError:
            err_console.print("ZIP 缺 manifest.json，可能不是 prompt-help 格式")
            raise typer.Exit(1)

        if manifest.get("kind") != "prompt-help-share":
            err_console.print(
                f"manifest.kind 不对：{manifest.get('kind')}（期望 prompt-help-share）"
            )
            raise typer.Exit(1)

        console.print(
            f"导入 {manifest.get('n_prompts', '?')} 条，"
            f"导出时间：{manifest.get('exported_at', '?')[:19]}"
        )

        for item in manifest.get("items") or []:
            arcname = item.get("filename")
            if not arcname:
                continue
            try:
                content = zf.read(arcname).decode("utf-8", errors="replace")
                p = storage.parse(content)
            except Exception as e:
                err_console.print(f"  ! {arcname} 解析失败：{e}")
                continue

            # 改写：scope / tags + 重新生成 id 防冲突
            p.scope = scope
            p.project = None if scope == "global" else p.project
            if prefix_tag and prefix_tag not in p.tags:
                p.tags = list(p.tags) + [prefix_tag]
            from ulid import ULID
            p.id = str(ULID())
            p.used = 0
            p.success_signal = 0
            p.last_used = None
            p.origin = "imported"

            # 库内去重检查
            existing = indexer.get_by_title(conn, p.title)
            if existing:
                skipped_dup += 1
                continue

            file_path = storage.save(
                cfg, p, commit_msg=f"import-zip: {p.title[:30]}",
            )
            indexer.upsert(conn, p, file_path)
            saved += 1

    conn.close()
    console.print(
        f"[green]✓[/green] 已导入 {saved} 条；跳过 {skipped_dup} 条重复（标题完全相同）"
    )