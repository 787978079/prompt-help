"""从 Claude Code 历史会话挖真实提示词。

CC 把每次会话存成 JSONL，路径是：
  ~/.claude/projects/<encoded-cwd>/<session-id>.jsonl

`encoded-cwd` 是把项目目录的斜杠/盘符冒号替换成 `--` / `-`。
每行一条事件，含 user/assistant 消息以及各种 hook/permission 元数据。

我们要的是：用户真正写过的、可复用的提示词。启发式过滤：
- 长度 150-4000 字符（短的多是闲聊、长的多是 AI 回放）
- 有结构（编号列表 / 代码块 / 多段 / "你的任务" 等）
- 不是 slash 命令调用（以 / 开头）
- 不是闲聊连接词（"继续"、"好"、"不对"、"嗯"）
- 与库里现有提示词 token 重合 < 60%（去重）
"""

from __future__ import annotations

import datetime as dt
import json
import re
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

import typer
from rich.console import Console
from rich.table import Table

from ..core import indexer, storage, quality
from ..core.config import Config, load_config, load_dotenv_if_present
from ..core.transcript import parse_jsonl

console = Console()
err_console = Console(stderr=True, style="red")


# ---------------------------------------------------------------------------
# 质量过滤入口（统一调 core.quality）
# ---------------------------------------------------------------------------

def _is_quality_prompt(text: str, qc: quality.QualityConfig) -> tuple[bool, str | None]:
    """委托给 core.quality；保留旧签名只是为了向后兼容（旧测试可能还引用）。"""
    return quality.is_quality_prompt(text, qc)


def _token_set(text: str) -> set[str]:
    return quality.token_set(text)


def _overlap(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / min(len(a), len(b))


# ---------------------------------------------------------------------------
# 项目名解码
# ---------------------------------------------------------------------------

def _decode_project(encoded: str) -> str:
    """把 'D--My-Project-Likner-likner-app' 还原成项目名 'likner-app'。

    CC 编码规则：路径里的 `\\`、`:`、`_`、原本的 `-` 全部映射成 `-`，所以无损还原不了。
    用"已知项目名最长子串匹配"兜底。
    """
    enc_lower = encoded.lower()

    # 已知项目（按长度倒序，长名字优先匹配，避免被短前缀截胡）
    known_projects = [
        "software-generation-orchestrator",
        "bp-generation-orchestrator",
        "kt-generation-orchestrator",
        "sr-generation-orchestrator",
        "sgo-mainline",
        "likner-app",
        "prompt-help",
        "minpei",
        "wangye",
        "zhuanli",
        "sgo",
        "pmo",
        "mb",
    ]
    for kp in known_projects:
        # 用 -kp- 或 -kp 结尾 来定位
        if f"-{kp}-" in f"-{enc_lower}-" or enc_lower.endswith("-" + kp) or enc_lower == kp:
            return kp

    # workspace / writing-workspace 这种衍生路径
    if "writing-workspace" in enc_lower:
        return "writing-workspace"
    if "workspace" in enc_lower:
        return "workspace"

    # 全局会话（C:\Users\lin）
    if enc_lower.startswith("c--users-"):
        return "全局会话"

    # 兜底：取最后两段拼接
    parts = [p for p in encoded.split("-") if p]
    if len(parts) >= 2:
        return f"{parts[-2]}-{parts[-1]}"
    return parts[-1] if parts else "unknown"


# ---------------------------------------------------------------------------
# 候选挖掘
# ---------------------------------------------------------------------------

@dataclass
class Candidate:
    body: str
    source_project: str
    source_session: str  # session id (.jsonl 文件名 stem)
    source_date: str  # YYYY-MM-DD（文件 mtime）
    source_jsonl: Path
    line_index: int


def _walk_sessions(cc_root: Path) -> Iterable[tuple[str, Path]]:
    """产出 (project_encoded, jsonl_path) 流。跳过 subagents 子目录。"""
    if not cc_root.is_dir():
        return
    for proj_dir in cc_root.iterdir():
        if not proj_dir.is_dir():
            continue
        for jsonl in proj_dir.glob("*.jsonl"):
            yield proj_dir.name, jsonl


def _extract_candidates(
    jsonl: Path,
    project_name: str,
    qc: quality.QualityConfig,
    *,
    reasons: dict[str, int] | None = None,
) -> list[Candidate]:
    """从一个会话 jsonl 抽出符合质量的 user 消息。

    可选 reasons：传入一个 dict，会累计各类拒因的计数（供 dry-run 报告）。
    """
    msgs = parse_jsonl(jsonl)
    out: list[Candidate] = []
    try:
        date_str = dt.datetime.fromtimestamp(jsonl.stat().st_mtime).strftime("%Y-%m-%d")
    except Exception:
        date_str = "0000-00-00"
    for m in msgs:
        if m.role != "user":
            continue
        passed, reason = quality.is_quality_prompt(m.text, qc)
        if not passed:
            if reasons is not None and reason:
                reasons[reason] = reasons.get(reason, 0) + 1
            continue
        out.append(Candidate(
            body=m.text.strip(),
            source_project=project_name,
            source_session=jsonl.stem,
            source_date=date_str,
            source_jsonl=jsonl,
            line_index=m.raw_index,
        ))
    return out


def _dedupe_within(
    candidates: list[Candidate],
    *,
    token_threshold: float = 0.55,
    seq_threshold: float = 0.80,
) -> list[Candidate]:
    """候选间去重：双信号（token 重合 + SequenceMatcher）。保留长度最大的同源候选。"""
    return quality.dedupe_candidates(
        candidates,
        get_text=lambda c: c.body,
        token_threshold=token_threshold,
        seq_threshold=seq_threshold,
    )


def _dedupe_against_db(
    candidates: list[Candidate],
    cfg: Config,
    threshold: float | None = None,
) -> list[Candidate]:
    """与库里现有提示词去重。threshold=None 时用 cfg.quality.db_dedupe_token。"""
    if threshold is None:
        threshold = cfg.quality.db_dedupe_token
    conn = indexer.open_db(cfg)
    existing = [
        (title, _token_set((title or "") + " " + (body or "")))
        for _id, title, body in indexer.existing_titles_and_bodies(conn)
    ]
    conn.close()
    if not existing:
        return candidates
    out: list[Candidate] = []
    for c in candidates:
        ts = _token_set(c.body)
        max_ov = 0.0
        for _t, ets in existing:
            ov = _overlap(ts, ets)
            if ov > max_ov:
                max_ov = ov
            if max_ov >= 0.95:
                break
        if max_ov < threshold:
            out.append(c)
    return out


def _auto_title(text: str, max_len: int = 50) -> str:
    """从正文抽标题：第一行（去掉 markdown 标记），截断到 max_len。"""
    first = text.strip().splitlines()[0].strip()
    first = re.sub(r"^[#*\-\d\.\s]+", "", first)
    first = re.sub(r"`[^`]+`", lambda m: m.group(0).strip("`"), first)
    if len(first) > max_len:
        first = first[:max_len - 1] + "…"
    return first or "未命名提示词"


# ---------------------------------------------------------------------------
# CLI 入口
# ---------------------------------------------------------------------------

def register(app: typer.Typer) -> None:
    app.command(name="import-from-transcripts")(import_from_transcripts_cmd)
    app.command(name="scan-transcripts")(scan_transcripts_cmd)
    app.command(name="import-from-codex")(import_from_codex_cmd)
    app.command(name="import-from-files")(import_from_files_cmd)
    app.command(name="detect-tools")(detect_tools_cmd)


def _config() -> Config:
    load_dotenv_if_present()
    return load_config()


# ---------------------------------------------------------------------------
# 适配器通用导入流程（T2）
# ---------------------------------------------------------------------------

def _candidates_from_adapter(adapter, qc: quality.QualityConfig,
                              reasons: dict[str, int] | None = None) -> list[Candidate]:
    """走 adapter.walk()，过 quality 过滤，返回候选列表。"""
    out: list[Candidate] = []
    for raw in adapter.walk():
        if raw.role != "user":
            continue
        passed, reason = quality.is_quality_prompt(raw.text, qc)
        if not passed:
            if reasons is not None and reason:
                reasons[reason] = reasons.get(reason, 0) + 1
            continue
        out.append(Candidate(
            body=raw.text.strip(),
            source_project=raw.source_project,
            source_session=raw.source_session,
            source_date=raw.source_date,
            source_jsonl=raw.source_path,
            line_index=raw.line_index,
        ))
    return out


def _import_candidates(cfg: Config, candidates: list[Candidate],
                        polish: bool = False) -> int:
    """把候选写入库 + SQLite。返回入库数。"""
    if not candidates:
        return 0
    deduped = _dedupe_within(
        candidates,
        token_threshold=cfg.quality.inter_dedupe_token,
        seq_threshold=cfg.quality.inter_dedupe_seq_ratio,
    )
    final = _dedupe_against_db(deduped, cfg)
    from ..core import classify
    saved = 0
    conn = indexer.open_db(cfg)
    for c in final:
        title = _auto_title(c.body)
        body = c.body
        if polish:
            from ..core import optimizer
            r = optimizer.optimize(cfg, body)
            if r.success:
                body = r.optimized
        tags = [f"来自-{c.source_project}", f"于-{c.source_date[:7]}"]
        cats = classify.rule_classify(body)
        p = storage.Prompt.new(
            title=title, body=body, scope="project", project=c.source_project,
            tags=tags, origin="imported",
        )
        p.categories = cats
        file_path = storage.save(
            cfg, p,
            commit_msg=f"adapter import: {c.source_project}/{c.source_session[:8]}",
        )
        indexer.upsert(conn, p, file_path)
        saved += 1
    conn.close()
    return saved


# ---------------------------------------------------------------------------
# detect-tools：探测哪些工具的历史可以扫
# ---------------------------------------------------------------------------

def detect_tools_cmd(json_out: bool = typer.Option(False, "--json")):
    """探测当前系统装了哪些 AI 编程工具，能从哪些路径自动扫历史。"""
    from .adapters import all_adapters
    rows: list[tuple[str, str, bool, str]] = []
    for ad in all_adapters():
        rows.append((
            ad.name,
            ad.display_name,
            ad.detect(),
            str(getattr(ad, "root", None) or "—"),
        ))

    if json_out:
        out = [{"name": n, "display": d, "detected": ok, "root": r} for n, d, ok, r in rows]
        console.print_json(json.dumps(out, ensure_ascii=False))
        return

    table = Table(title="检测到的 AI 编程工具历史")
    table.add_column("工具")
    table.add_column("显示名")
    table.add_column("检测到", justify="center")
    table.add_column("路径")
    for n, d, ok, r in rows:
        status = "[green]✓[/green]" if ok else "[dim]—[/dim]"
        table.add_row(n, d, status, r)
    console.print(table)
    console.print(
        "\n[dim]其他工具（Cursor / Cline / Aider / Continue 等）请把会话文件拖进 GUI，"
        "或用 prompt-help import-from-files <文件>...[/dim]"
    )


# ---------------------------------------------------------------------------
# Codex 导入
# ---------------------------------------------------------------------------

def import_from_codex_cmd(
    polish: bool = typer.Option(False, "--polish/--no-polish"),
):
    """从 Codex CLI 历史会话挖真实提示词入库。"""
    from .adapters.codex import CodexAdapter
    cfg = _config()
    adapter = CodexAdapter()
    if not adapter.detect():
        console.print(
            "[yellow]未检测到 Codex 历史[/yellow]\n"
            "可能的原因：1) 没装 Codex CLI；2) 历史文件在非标准位置。\n"
            "可手动用 prompt-help import-from-files <你导出的 jsonl/json> 导入。"
        )
        raise typer.Exit(1)
    cands = _candidates_from_adapter(adapter, cfg.quality)
    saved = _import_candidates(cfg, cands, polish=polish)
    console.print(
        f"[green]✓[/green] 从 Codex 历史导入 [bold]{saved}[/bold] 条提示词"
        f"（路径：{adapter.root}）"
    )


# ---------------------------------------------------------------------------
# 手动文件导入
# ---------------------------------------------------------------------------

def import_from_files_cmd(
    files: list[Path] = typer.Argument(..., help="一个或多个 .md / .json / .jsonl / .txt"),
    polish: bool = typer.Option(False, "--polish/--no-polish"),
):
    """从手动指定的文件（任何 AI 工具的导出）解析 + 入库。"""
    from .adapters.manual_drop import ManualDropAdapter
    cfg = _config()
    adapter = ManualDropAdapter(files)
    if not adapter.detect():
        err_console.print("没有有效文件")
        raise typer.Exit(1)
    cands = _candidates_from_adapter(adapter, cfg.quality)
    saved = _import_candidates(cfg, cands, polish=polish)
    console.print(f"[green]✓[/green] 从 {len(files)} 个文件导入 [bold]{saved}[/bold] 条")


def scan_transcripts_cmd(
    cc_root: Path = typer.Option(
        Path.home() / ".claude" / "projects", "--cc-root",
        help="Claude Code transcripts 根目录",
    ),
    min_chars: int | None = typer.Option(None, "--min-chars",
                                          help="覆盖 cfg.quality.min_chars"),
    max_chars: int | None = typer.Option(None, "--max-chars"),
    show_reasons: bool = typer.Option(False, "--show-reasons",
                                       help="打印各类拒因的累计计数"),
    json_out: bool = typer.Option(False, "--json"),
):
    """只扫不入库：报告 CC 历史会话里能挖出多少候选，按项目分组 + 拒因统计。"""
    cfg = _config()
    qc = cfg.quality
    if min_chars is not None:
        qc.min_chars = min_chars
    if max_chars is not None:
        qc.max_chars = max_chars
    cc_root = cc_root.expanduser().resolve()

    reasons: dict[str, int] = {}
    by_project: dict[str, list[Candidate]] = defaultdict(list)
    sessions_seen = 0
    for proj_encoded, jsonl in _walk_sessions(cc_root):
        sessions_seen += 1
        proj_name = _decode_project(proj_encoded)
        cands = _extract_candidates(jsonl, proj_name, qc, reasons=reasons)
        if cands:
            by_project[proj_name].extend(cands)

    # 候选间去重（双信号：token + SequenceMatcher）
    deduped: dict[str, list[Candidate]] = {}
    for proj, cands in by_project.items():
        deduped[proj] = _dedupe_within(
            cands,
            token_threshold=qc.inter_dedupe_token,
            seq_threshold=qc.inter_dedupe_seq_ratio,
        )

    # 库内去重
    final: dict[str, list[Candidate]] = {}
    for proj, cands in deduped.items():
        final[proj] = _dedupe_against_db(cands, cfg)

    total_raw = sum(len(c) for c in by_project.values())
    total_after_dedupe = sum(len(c) for c in deduped.values())
    total_final = sum(len(c) for c in final.values())

    if json_out:
        out = {
            "sessions_scanned": sessions_seen,
            "total_raw_candidates": total_raw,
            "after_internal_dedupe": total_after_dedupe,
            "after_db_dedupe": total_final,
            "by_project": {p: len(c) for p, c in final.items()},
            "rejection_reasons": reasons,
        }
        console.print_json(json.dumps(out, ensure_ascii=False))
        return

    table = Table(title=f"扫描 {cc_root} 结果")
    table.add_column("项目")
    table.add_column("候选数", justify="right")
    table.add_column("最近会话")
    for proj, cands in sorted(final.items(), key=lambda x: -len(x[1])):
        if not cands:
            continue
        latest_date = max(c.source_date for c in cands)
        table.add_row(proj, str(len(cands)), latest_date)
    console.print(table)
    console.print(
        f"\n总计：{sessions_seen} 个会话，{total_raw} 条原始候选 → "
        f"{total_after_dedupe} 条候选间去重 → [bold]{total_final}[/bold] 条排除库内重复后。"
    )

    if show_reasons and reasons:
        rt = Table(title="过滤拒因统计")
        rt.add_column("拒因")
        rt.add_column("条数", justify="right")
        for r, n in sorted(reasons.items(), key=lambda x: -x[1]):
            rt.add_row(r, str(n))
        console.print(rt)

    console.print("[dim]运行 prompt-help import-from-transcripts 真入库[/dim]")


def import_from_transcripts_cmd(
    cc_root: Path = typer.Option(
        Path.home() / ".claude" / "projects", "--cc-root",
    ),
    min_chars: int | None = typer.Option(None, "--min-chars"),
    max_chars: int | None = typer.Option(None, "--max-chars"),
    polish: bool = typer.Option(False, "--polish/--no-polish",
                                 help="逐条调 LLM 优化（耗 API 费用）"),
    project_filter: Optional[str] = typer.Option(
        None, "--project", help="只导入某个项目的会话"
    ),
):
    """从 CC 历史会话挖真实提示词入库。"""
    cfg = _config()
    qc = cfg.quality
    if min_chars is not None:
        qc.min_chars = min_chars
    if max_chars is not None:
        qc.max_chars = max_chars
    cc_root = cc_root.expanduser().resolve()

    by_project: dict[str, list[Candidate]] = defaultdict(list)
    sessions_seen = 0
    for proj_encoded, jsonl in _walk_sessions(cc_root):
        sessions_seen += 1
        proj_name = _decode_project(proj_encoded)
        if project_filter and proj_name != project_filter:
            continue
        for c in _extract_candidates(jsonl, proj_name, qc):
            by_project[proj_name].append(c)

    final: dict[str, list[Candidate]] = {}
    for proj, cands in by_project.items():
        deduped = _dedupe_within(
            cands,
            token_threshold=qc.inter_dedupe_token,
            seq_threshold=qc.inter_dedupe_seq_ratio,
        )
        final[proj] = _dedupe_against_db(deduped, cfg)

    total = sum(len(c) for c in final.values())
    if total == 0:
        console.print("[yellow]没找到值得入库的候选[/yellow]")
        return

    # 入库
    saved = 0
    conn = indexer.open_db(cfg)
    for proj, cands in final.items():
        for c in cands:
            title = _auto_title(c.body)
            body = c.body
            if polish:
                from ..core import optimizer
                r = optimizer.optimize(cfg, body)
                if r.success:
                    body = r.optimized
            tags = [f"来自-{proj}", f"于-{c.source_date[:7]}"]
            p = storage.Prompt.new(
                title=title, body=body, scope="project", project=proj,
                tags=tags, origin="imported",
            )
            file_path = storage.save(
                cfg, p,
                commit_msg=f"transcript: {proj}/{c.source_session[:8]} → {title[:30]}",
            )
            indexer.upsert(conn, p, file_path)
            saved += 1
    conn.close()

    console.print(
        f"[green]✓[/green] 已导入 [bold]{saved}[/bold] 条来自你 "
        f"{sessions_seen} 个 CC 历史会话的真实提示词。"
    )
