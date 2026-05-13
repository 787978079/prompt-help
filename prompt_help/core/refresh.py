"""刷新本机项目（Phase 17）。

逻辑：
1. 读 `<vault>/scan_roots.json` 拿用户配置的扫描根目录列表
2. 对每个根目录调 admin._find_project_roots 找子项目
3. 每个项目调 admin._collect_files_in_project + _split_by_headings
4. 对每条切片 prompt（title 由 project + heading 组成）：
   - 库内存在同 title + body 相同 → 跳过
   - 库内存在同 title + body 不同 → 用同 id 覆盖（保留 used / success_signal）
   - 库内不存在 → 新增

支持后台 QThread 模式（progress 回调）。
"""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from .config import Config


@dataclass
class RefreshResult:
    added: int = 0           # 新增条数
    updated: int = 0         # 内容变化更新条数
    skipped_same: int = 0    # 完全相同跳过
    skipped_quality: int = 0  # quality 过滤拒掉（文档碎片 / 纯英文 / 噪声）
    files_scanned: int = 0   # 扫到的 md 文件数
    projects_scanned: int = 0
    errors: list[str] = field(default_factory=list)

    def summary(self) -> str:
        return (
            f"+{self.added} 新增  ·  ~{self.updated} 更新  ·  ={self.skipped_same} 不变  "
            f"·  ✗{self.skipped_quality} 质量拒  "
            f"(扫 {self.projects_scanned} 项目 / {self.files_scanned} 文件)"
        )


def _scan_roots_path(cfg: Config) -> Path:
    return cfg.vault_path / "scan_roots.json"


def load_scan_roots(cfg: Config) -> list[dict]:
    f = _scan_roots_path(cfg)
    if not f.is_file():
        return []
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
        return list(data.get("roots") or [])
    except Exception:
        return []


def save_scan_roots(cfg: Config, roots: list[dict]) -> None:
    f = _scan_roots_path(cfg)
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(
        json.dumps({"roots": roots}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def add_scan_root(cfg: Config, path: Path) -> bool:
    """新增扫描根目录。返回是否真正新增。"""
    roots = load_scan_roots(cfg)
    normalized = str(path.resolve())
    if any(r.get("path") == normalized for r in roots):
        return False
    roots.append({"path": normalized, "added_at": dt.datetime.now(dt.timezone.utc).isoformat()})
    save_scan_roots(cfg, roots)
    return True


def remove_scan_root(cfg: Config, path: str) -> bool:
    roots = load_scan_roots(cfg)
    before = len(roots)
    roots = [r for r in roots if r.get("path") != path]
    if len(roots) < before:
        save_scan_roots(cfg, roots)
        return True
    return False


def refresh_all(
    cfg: Config,
    progress: Optional[Callable[[str], None]] = None,
) -> RefreshResult:
    """扫所有 scan_roots，刷新库内对应条目。

    progress: 可选回调，每开始一个项目 / 文件时调用。
    """
    from ..cli import admin
    from . import indexer, storage
    from .fingerprint import fingerprint as fp_compute, to_dict
    from ulid import ULID

    result = RefreshResult()
    roots = load_scan_roots(cfg)
    if not roots:
        result.errors.append("还没配置任何扫描根目录。在「设置 → 项目扫描根目录」加一个。")
        return result

    conn = indexer.open_db(cfg)
    try:
        for root_entry in roots:
            root_path = Path(root_entry.get("path") or "")
            if not root_path.is_dir():
                result.errors.append(f"目录不存在：{root_path}")
                continue
            try:
                project_roots = admin._find_project_roots(root_path)
                project_roots = [r for r in project_roots if r.name != "Prompt help"]
            except Exception as e:
                result.errors.append(f"扫 {root_path} 失败：{e}")
                continue

            for proj_root in project_roots:
                if progress:
                    progress(f"扫 {proj_root.name}…")
                # 登记项目（顺便更新 fingerprint）
                try:
                    fp = fp_compute(proj_root)
                    fp.project_name = proj_root.name
                    indexer.register_project(
                        conn, name=proj_root.name, cwd_path=str(proj_root),
                        fingerprint_json=json.dumps(to_dict(fp), ensure_ascii=False),
                    )
                except Exception:
                    pass
                result.projects_scanned += 1

                files = admin._collect_files_in_project(proj_root)
                for md_file in files:
                    result.files_scanned += 1
                    try:
                        text = md_file.read_text(encoding="utf-8", errors="ignore")
                    except Exception:
                        continue
                    _process_file(
                        cfg, conn, md_file, text, proj_root.name, result,
                    )

            # 更新 last_scan
            root_entry["last_scan"] = dt.datetime.now(dt.timezone.utc).isoformat()

        # 写回 scan_roots（带最新 last_scan）
        save_scan_roots(cfg, roots)
    finally:
        conn.close()

    return result


def _process_file(
    cfg: Config, conn, md_file: Path, text: str, project_name: str,
    result: RefreshResult,
) -> None:
    """处理单个 md 文件：拆 section → 与库对比 → 增 / 改 / 跳。"""
    from ..cli import admin
    from . import indexer, storage

    name = md_file.name
    is_small_file = (
        name in (".cursorrules", ".clinerules")
        or md_file.parent.name == "prompts"
    )

    if is_small_file and len(text.strip()) >= 80:
        sections = [(f"{project_name} · {md_file.stem or name}", text.strip())]
    else:
        # 大文件按 ## / ### 拆
        sections = []
        for sec_title, body in admin._split_by_headings(text):
            full_title = f"{project_name} · {sec_title}"
            sections.append((full_title, body.strip()))

    # 用户实测：refresh 自动扫项目把 AGENTS.md / CLAUDE.md 章节碎片当 prompt 入库，
    # 导致库里全是"Coding Style"/"Project Structure"等文档段。统一过 quality 过滤
    # （language_pref=zh 默认拒纯英文、reject_doc_fragments 拒章节碎片）。
    from . import quality as _quality
    qc = getattr(cfg, "quality", None) or _quality.QualityConfig()

    for title, body in sections:
        if len(body) < 80:
            continue
        ok, reason = _quality.is_quality_prompt(body, qc)
        if not ok:
            result.skipped_quality = getattr(result, "skipped_quality", 0) + 1
            continue
        _apply_section(cfg, conn, title, body, project_name, md_file, result)


def _apply_section(
    cfg: Config, conn, title: str, body: str, project_name: str,
    md_file: Path, result: RefreshResult,
) -> None:
    """对一条切片：库内对比 title + body 决定增/改/跳。"""
    from ..cli import admin
    from . import indexer, storage

    existing = indexer.get_by_title(conn, title)
    if existing:
        old_body = (existing["body"] or "").strip()
        if old_body == body:
            result.skipped_same += 1
            return
        # 内容变了——用同 id 覆盖，保留 used / success_signal / created
        try:
            old_path = Path(existing["file_path"])
            p = storage.load(old_path) if old_path.is_file() else None
        except Exception:
            p = None
        if p is None:
            # 文件丢失，按新增处理
            p = storage.Prompt.new(title=title, body=body, scope="global",
                                    origin="imported")
        else:
            p.body = body  # 只更新 body，其他字段保留
        # 更新分类相关字段，不动 used / success_signal / created
        scope, project, triggers = admin._classify(title, body, project_name)
        p.scope = scope
        p.project = project
        if triggers:
            p.triggers = triggers
        # 新增 tag「已更新」
        if "已更新" not in p.tags:
            p.tags = list(p.tags) + ["已更新"]
        file_path = storage.save(
            cfg, p, commit_msg=f"refresh: {title[:30]} body changed",
        )
        indexer.upsert(conn, p, file_path)
        result.updated += 1
        return

    # 新增
    scope, project, triggers = admin._classify(title, body, project_name)
    tags = admin._infer_tags(title, body)
    stack = admin._infer_stack(body)
    p = storage.Prompt.new(
        title=title, body=body, scope=scope, project=project,
        tags=tags, stack=stack, triggers=triggers, origin="imported",
    )
    file_path = storage.save(
        cfg, p, commit_msg=f"refresh: add {title[:30]}",
    )
    indexer.upsert(conn, p, file_path)
    result.added += 1
