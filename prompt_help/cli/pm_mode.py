"""PM-Mode：产品发现模式（Plan mode 之前的 WHAT 层）。

状态机：草稿存在 ~/.prompt-help/briefs/_active/<slug>.json，7 个阶段：
  problem / users / prior_art / novelty / scope / tech_risks / metric

每阶段是 stages[name]: dict[str, Any]。slash command 通过 set 多次填字段。
brief 命令把状态装配成 PRODUCT_BRIEF.md（cwd 项目根 + briefs/ 归档双写）。

辅助命令：
  prior-art-suggest <topic>     从已登记项目按相似度排序 + 历史栈匹配
  tech-risks-suggest <stack>    从 trap 库 + 栈匹配的提示词召回相关风险
"""

from __future__ import annotations

import datetime as dt
import json
import re
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional

import typer
import yaml
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table

from ..core import indexer
from ..core.config import Config, load_config, load_dotenv_if_present

console = Console()
err_console = Console(stderr=True, style="red")


STAGES = ["problem", "users", "prior_art", "novelty", "scope", "tech_risks", "metric"]

STAGE_TITLES = {
    "problem": "Problem & Motivation",
    "users": "Users & Trigger Moment",
    "prior_art": "Prior Art",
    "novelty": "Novelty & Moat",
    "scope": "Scope (IN / LATER / NEVER)",
    "tech_risks": "Tech Risks & Unknowns",
    "metric": "Success Metric",
}


def _config() -> Config:
    load_dotenv_if_present()
    return load_config()


def _active_dir(cfg: Config) -> Path:
    d = cfg.briefs_dir / "_active"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _slugify(text: str, max_len: int = 40) -> str:
    s = re.sub(r"[^a-zA-Z0-9一-鿿]+", "-", text.strip()).strip("-").lower()
    return s[:max_len] or "untitled"


def register(app: typer.Typer) -> None:
    pm = typer.Typer(help="PM-Mode：产品发现模式（Phase 7 LLM 主导对话）")
    pm.command("start")(start_cmd)
    pm.command("set")(set_cmd)
    pm.command("get")(get_cmd)
    pm.command("list")(list_cmd)
    pm.command("brief")(brief_cmd)
    pm.command("delete")(delete_cmd)
    pm.command("prior-art-suggest")(prior_art_suggest_cmd)
    pm.command("tech-risks-suggest")(tech_risks_suggest_cmd)
    # Phase 7：LLM 主导对话模式
    pm.command("chat")(chat_cmd)
    pm.command("answer")(answer_cmd)
    pm.command("save-bundle")(save_bundle_cmd)
    pm.command("show-history")(show_history_cmd)
    app.add_typer(pm, name="pm-mode")


# ---------------------------------------------------------------------------
# 状态读写
# ---------------------------------------------------------------------------

@dataclass
class BriefDraft:
    slug: str
    idea: str
    created: str = field(default_factory=lambda: dt.datetime.now(dt.timezone.utc).isoformat())
    updated: str = field(default_factory=lambda: dt.datetime.now(dt.timezone.utc).isoformat())
    cwd: str = ""
    stages: dict[str, dict[str, Any]] = field(default_factory=dict)
    confidence: float = 0.5  # express 模式会偏低
    notes: str = ""

    def touch(self) -> None:
        self.updated = dt.datetime.now(dt.timezone.utc).isoformat()


def _load(cfg: Config, slug: str) -> BriefDraft:
    f = _active_dir(cfg) / f"{slug}.json"
    if not f.is_file():
        err_console.print(f"找不到草稿：{slug}（在 {f}）")
        raise typer.Exit(1)
    data = json.loads(f.read_text(encoding="utf-8"))
    return BriefDraft(**data)


def _save(cfg: Config, draft: BriefDraft) -> Path:
    draft.touch()
    f = _active_dir(cfg) / f"{draft.slug}.json"
    f.write_text(json.dumps(asdict(draft), ensure_ascii=False, indent=2), encoding="utf-8")
    return f


def _resolve_slug(cfg: Config, slug: Optional[str]) -> str:
    """没传 slug 时，取最近修改的活跃草稿。"""
    if slug:
        return slug
    drafts = sorted(_active_dir(cfg).glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not drafts:
        err_console.print("当前没有活跃草稿。先运行 pm-mode start <idea>")
        raise typer.Exit(1)
    return drafts[0].stem


# ---------------------------------------------------------------------------
# start
# ---------------------------------------------------------------------------

def start_cmd(
    idea: str = typer.Argument(..., help="产品想法的一句话描述"),
    slug: Optional[str] = typer.Option(None, "--slug", "-s"),
    cwd: Optional[Path] = typer.Option(None, "--cwd", help="项目目录，默认当前 cwd"),
    confidence: float = typer.Option(0.5, "--confidence", help="express 模式可降到 0.3"),
):
    """创建一个新的 PM-Mode 草稿。"""
    cfg = _config()
    final_slug = slug or _slugify(idea)
    f = _active_dir(cfg) / f"{final_slug}.json"
    if f.is_file():
        err_console.print(f"草稿 {final_slug} 已存在，用 pm-mode get {final_slug} 看现状或换 --slug")
        raise typer.Exit(1)

    draft = BriefDraft(
        slug=final_slug,
        idea=idea,
        cwd=str((cwd or Path.cwd()).resolve()),
        confidence=confidence,
        stages={s: {} for s in STAGES},
    )
    _save(cfg, draft)
    console.print(f"[green]✓[/green] 草稿已创建：[bold]{final_slug}[/bold]")
    console.print(f"  idea: {idea}")
    console.print(f"  cwd:  {draft.cwd}")
    console.print(f"  state: {f}")
    console.print()
    console.print("下一步：在 Claude Code 跑 /pm-mode 走 7 阶段访谈，或手动 pm-mode set <stage> <k>=<v>")


# ---------------------------------------------------------------------------
# set
# ---------------------------------------------------------------------------

def set_cmd(
    stage: str = typer.Argument(..., help=f"阶段：{', '.join(STAGES)}"),
    pairs: list[str] = typer.Argument(..., help="多个 key=value 对（value 可为 JSON 数组）"),
    slug: Optional[str] = typer.Option(None, "--slug", "-s"),
):
    """记录一个或多个阶段答案。

    示例：
      pm-mode set problem pain_one_liner='Grad students drowning' motivation=b
      pm-mode set scope in='["arxiv-fetch","pdf-render"]' time_budget=1_week
    """
    if stage not in STAGES:
        err_console.print(f"未知阶段 '{stage}'。可选：{STAGES}")
        raise typer.Exit(1)

    cfg = _config()
    s = _resolve_slug(cfg, slug)
    draft = _load(cfg, s)
    if stage not in draft.stages:
        draft.stages[stage] = {}

    for pair in pairs:
        if "=" not in pair:
            err_console.print(f"忽略格式错误的对：{pair}（应为 key=value）")
            continue
        k, _, raw = pair.partition("=")
        k = k.strip()
        # 尝试解析 JSON（数组、布尔、数字）；失败保留字符串
        v: Any
        try:
            v = json.loads(raw)
        except Exception:
            v = raw
        draft.stages[stage][k] = v

    _save(cfg, draft)
    console.print(f"[green]✓[/green] {s} · {stage}: {list(draft.stages[stage].keys())}")


# ---------------------------------------------------------------------------
# get / list
# ---------------------------------------------------------------------------

def get_cmd(
    stage: Optional[str] = typer.Argument(None),
    slug: Optional[str] = typer.Option(None, "--slug", "-s"),
    json_out: bool = typer.Option(False, "--json"),
):
    """查看一个或所有阶段的状态。"""
    cfg = _config()
    s = _resolve_slug(cfg, slug)
    draft = _load(cfg, s)

    if json_out:
        if stage:
            console.print_json(json.dumps(draft.stages.get(stage, {}), ensure_ascii=False))
        else:
            console.print_json(json.dumps(asdict(draft), ensure_ascii=False))
        return

    console.print(f"[bold]{s}[/bold] · idea: [italic]{draft.idea}[/italic]")
    console.print(f"  cwd: {draft.cwd}")
    console.print(f"  conf: {draft.confidence}  updated: {draft.updated[:19]}")
    console.print()

    target = [stage] if stage else STAGES
    for st in target:
        ans = draft.stages.get(st, {})
        title = STAGE_TITLES.get(st, st)
        if not ans:
            console.print(f"[dim]· {st} ({title})  [未填][/dim]")
            continue
        console.print(f"[cyan]· {st} ({title})[/cyan]")
        for k, v in ans.items():
            v_str = json.dumps(v, ensure_ascii=False) if isinstance(v, (list, dict)) else str(v)
            console.print(f"    {k}: {v_str}")


def list_cmd(json_out: bool = typer.Option(False, "--json")):
    """列出所有活跃草稿。"""
    cfg = _config()
    drafts = sorted(_active_dir(cfg).glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not drafts:
        console.print("[dim]当前没有活跃草稿[/dim]")
        return

    rows: list[tuple[str, str, str, int]] = []
    for f in drafts:
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
            filled = sum(1 for st in STAGES if d.get("stages", {}).get(st))
            rows.append((d.get("slug", f.stem), d.get("idea", "")[:50],
                         d.get("updated", "")[:19], filled))
        except Exception:
            continue

    if json_out:
        console.print_json(json.dumps([{"slug": s, "idea": i, "updated": u, "filled": fi}
                                        for s, i, u, fi in rows], ensure_ascii=False))
        return

    table = Table(title=f"活跃草稿（{len(rows)} 条）")
    table.add_column("slug")
    table.add_column("idea")
    table.add_column("updated")
    table.add_column("filled", justify="right")
    for s, i, u, fi in rows:
        table.add_row(s, i, u, f"{fi}/7")
    console.print(table)


# ---------------------------------------------------------------------------
# delete
# ---------------------------------------------------------------------------

def delete_cmd(slug: str = typer.Argument(...)):
    """删除一个草稿（不影响已生成的 PRODUCT_BRIEF.md）。"""
    cfg = _config()
    f = _active_dir(cfg) / f"{slug}.json"
    if not f.is_file():
        err_console.print(f"草稿不存在：{slug}")
        raise typer.Exit(1)
    f.unlink()
    console.print(f"[green]✓[/green] 已删：{slug}")


# ---------------------------------------------------------------------------
# brief（assemble + write）
# ---------------------------------------------------------------------------

def brief_cmd(
    slug: Optional[str] = typer.Option(None, "--slug", "-s"),
    out: Optional[Path] = typer.Option(
        None, "--out",
        help="输出路径，默认写到 cwd/PRODUCT_BRIEF.md + briefs/<date>-<slug>.md 归档",
    ),
    archive_only: bool = typer.Option(False, "--archive-only", help="只写归档不写到 cwd"),
    print_only: bool = typer.Option(False, "--print-only", help="只打印不落盘"),
):
    """把状态装配成 PRODUCT_BRIEF.md。"""
    cfg = _config()
    s = _resolve_slug(cfg, slug)
    draft = _load(cfg, s)

    md = _assemble_brief(draft)

    if print_only:
        console.print(Markdown(md))
        return

    written: list[Path] = []
    # 归档
    cfg.briefs_dir.mkdir(parents=True, exist_ok=True)
    archive_name = f"{draft.created[:10]}-{draft.slug}.md"
    archive_path = cfg.briefs_dir / archive_name
    archive_path.write_text(md, encoding="utf-8")
    written.append(archive_path)

    # cwd 项目根
    if not archive_only:
        if out:
            target = out
        else:
            cwd = Path(draft.cwd) if draft.cwd else Path.cwd()
            target = cwd / "PRODUCT_BRIEF.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(md, encoding="utf-8")
        written.append(target)

    for p in written:
        console.print(f"[green]✓[/green] {p}")
    console.print()
    console.print("[bold]下一步[/bold]：进入 Plan 模式（/plan 或 plan mode），Brief 会被作为 cwd 上下文读到。")
    console.print("如需迭代某阶段：[dim]pm-mode set <stage> <key>=<value>[/dim] 后再 brief。")


def _assemble_brief(draft: BriefDraft) -> str:
    """把 BriefDraft 组装成 markdown + frontmatter。"""
    fm = {
        "kind": "product_brief",
        "slug": draft.slug,
        "idea": draft.idea,
        "created": draft.created,
        "updated": draft.updated,
        "confidence": draft.confidence,
        "cwd": draft.cwd,
        "motivation_risk": _classify_motivation_risk(draft),
        "moat_type": draft.stages.get("novelty", {}).get("moat_type"),
        "time_budget": draft.stages.get("scope", {}).get("time_budget"),
        "success_metric": draft.stages.get("metric", {}).get("kpi"),
    }
    fm = {k: v for k, v in fm.items() if v is not None}
    fm_yaml = yaml.safe_dump(fm, allow_unicode=True, sort_keys=False).strip()

    body_parts = [f"---\n{fm_yaml}\n---", "", f"# {draft.idea}", "", "## Problem & Motivation"]
    p = draft.stages.get("problem", {})
    if p.get("pain_one_liner"):
        body_parts.append(p["pain_one_liner"])
    if p.get("motivation"):
        body_parts.append(f"**动机类型**：{_motivation_label(p.get('motivation'))}")
    if p.get("freshness"):
        body_parts.append(f"**痛点新鲜度**：{p['freshness']}")

    body_parts += ["", "## Users & Trigger Moment"]
    u = draft.stages.get("users", {})
    if u.get("archetype"):
        body_parts.append(f"**典型用户**：{u['archetype']}")
    if u.get("trigger_moment"):
        body_parts.append(f"**触发时刻**：{u['trigger_moment']}")
    if u.get("current_solution"):
        body_parts.append(f"**当前解法**：{u['current_solution']}")

    body_parts += ["", "## Prior Art"]
    pa = draft.stages.get("prior_art", {})
    if pa.get("found"):
        body_parts.append("| 工具 | 做的事 | 缺口 |")
        body_parts.append("|---|---|---|")
        for it in pa["found"]:
            if isinstance(it, dict):
                body_parts.append(
                    f"| {it.get('name', '?')} | {it.get('does', '?')} | {it.get('gap', '?')} |"
                )
            else:
                body_parts.append(f"| {it} | - | - |")
    if pa.get("positioning"):
        body_parts.append(f"\n**竞争定位**：{pa['positioning']}")

    body_parts += ["", "## Novelty & Moat"]
    n = draft.stages.get("novelty", {})
    if n.get("unlike_x_this"):
        body_parts.append(f"Unlike 现有竞品，this **{n['unlike_x_this']}**")
    if n.get("moat_type"):
        body_parts.append(f"\n**护城河类型**：{_moat_label(n.get('moat_type'))}")

    body_parts += ["", "## Scope (IN / LATER / NEVER)"]
    sc = draft.stages.get("scope", {})
    body_parts.append(f"**IN**：{', '.join(sc.get('in') or []) or '—'}")
    body_parts.append(f"**LATER**：{', '.join(sc.get('later') or []) or '—'}")
    body_parts.append(f"**NEVER**：{', '.join(sc.get('never') or []) or '—'}")
    if sc.get("time_budget"):
        body_parts.append(f"\n**时间预算**：{sc['time_budget']}")
    if sc.get("success"):
        body_parts.append(f"**MVP 成功定义**：{sc['success']}")

    body_parts += ["", "## Tech Risks & Unknowns"]
    tr = draft.stages.get("tech_risks", {})
    if tr.get("selected"):
        for r in tr["selected"]:
            body_parts.append(f"- {r}")
    if tr.get("from_traps"):
        body_parts.append("\n**历史踩坑提醒**：")
        for r in tr["from_traps"]:
            body_parts.append(f"- {r}")
    if tr.get("killer_risk"):
        body_parts.append(f"\n**Killer risk**：{tr['killer_risk']}")

    body_parts += ["", "## Success Metric"]
    m = draft.stages.get("metric", {})
    if m.get("kpi"):
        body_parts.append(f"**KPI**：{m['kpi']}")
    if m.get("validation"):
        body_parts.append(f"**验证方式**：{m['validation']}")

    if draft.notes:
        body_parts += ["", "## Notes", draft.notes]

    body_parts += ["", "## Open Questions",
                    "- 具体技术选型（库、模型、部署）→ Plan mode 决定",
                    "- 详细时间排期 → Plan mode 决定"]
    return "\n".join(body_parts) + "\n"


def _motivation_label(m: Any) -> str:
    return {
        "a": "a) 真实个人痛",
        "b": "b) 看到他人痛",
        "c": "c) 技术驱动（找应用场景）",
        "d": "d) 商业机会",
    }.get(str(m), str(m))


def _moat_label(m: Any) -> str:
    return {
        "data": "独占数据",
        "lockin": "用户锁定",
        "feature_not_moat": "feature 不是 moat（个人项目可接受）",
        "personal_only": "仅个人使用，无需护城河",
    }.get(str(m), str(m))


def _classify_motivation_risk(draft: BriefDraft) -> str:
    """motivation=c（技术驱动）需要更多审视；其他默认 low。"""
    p = draft.stages.get("problem", {})
    if str(p.get("motivation", "")).lower() == "c":
        return "solution-in-search-of-problem"
    return "low"


# ---------------------------------------------------------------------------
# 辅助命令
# ---------------------------------------------------------------------------

def prior_art_suggest_cmd(
    topic: str = typer.Argument(..., help="产品主题"),
    json_out: bool = typer.Option(False, "--json"),
):
    """根据已登记项目按相似度排序，建议 Stage 3 的优先调研对象。"""
    cfg = _config()
    conn = indexer.open_db(cfg)
    rows = indexer.list_projects(conn)
    conn.close()

    # 简单：取每个项目的 keywords 与 topic token 重合度
    topic_tokens = {t.lower() for t in re.findall(r"[A-Za-z]{3,}|[一-鿿]{2,}", topic)}
    suggestions: list[dict] = []
    for r in rows:
        try:
            fp = json.loads(r["fingerprint_json"])
        except Exception:
            continue
        all_t = set(fp.get("langs") or []) | set(fp.get("frameworks") or []) | set(fp.get("keywords") or [])
        inter = topic_tokens & all_t
        if inter:
            suggestions.append({
                "name": r["name"],
                "cwd": r["cwd_path"],
                "shared_tokens": sorted(inter),
                "overlap": round(len(inter) / max(len(topic_tokens), 1), 3),
            })
    suggestions.sort(key=lambda x: x["overlap"], reverse=True)

    if json_out:
        console.print_json(json.dumps({
            "topic": topic,
            "your_past_projects": suggestions[:5],
            "external_search_suggestions": [
                f"site:github.com {topic} stars:>100",
                f"awesome {topic}",
                f"\"{topic}\" alternatives",
            ],
        }, ensure_ascii=False))
        return

    if suggestions:
        console.print(f"[bold]你的历史项目里和 '{topic}' 相关的：[/bold]")
        for s in suggestions[:5]:
            console.print(f"  · {s['name']}  (overlap={s['overlap']:.2f})  共享：{', '.join(s['shared_tokens'][:5])}")
    else:
        console.print(f"[dim]历史项目里没有和 '{topic}' 直接相关的[/dim]")
    console.print()
    console.print("[bold]建议外部搜索：[/bold]")
    for q in [f"site:github.com {topic} stars:>100", f"awesome {topic}",
               f'"{topic}" alternatives']:
        console.print(f"  · {q}")


# ---------------------------------------------------------------------------
# Phase 7：LLM 主导对话模式
# ---------------------------------------------------------------------------

def _dialog_dir(cfg: Config) -> Path:
    d = cfg.briefs_dir / "_active_dialog"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _load_session(cfg: Config, slug: str):
    from ..core.pm_dialog import PMSession
    f = _dialog_dir(cfg) / f"{slug}.json"
    if not f.is_file():
        err_console.print(f"找不到对话：{slug}（在 {f}）")
        raise typer.Exit(1)
    data = json.loads(f.read_text(encoding="utf-8"))
    return PMSession(**data)


def _save_session(cfg: Config, session) -> Path:
    from dataclasses import asdict
    session.touch()
    f = _dialog_dir(cfg) / f"{session.slug}.json"
    f.write_text(
        json.dumps(asdict(session), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return f


def _resolve_dialog_slug(cfg: Config, slug: Optional[str]) -> str:
    if slug:
        return slug
    drafts = sorted(_dialog_dir(cfg).glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not drafts:
        err_console.print("当前没有活跃对话。先运行 pm-mode chat <idea>")
        raise typer.Exit(1)
    return drafts[0].stem


def chat_cmd(
    idea: str = typer.Argument(..., help="产品想法的一句话描述"),
    slug: Optional[str] = typer.Option(None, "--slug", "-s"),
    cwd: Optional[Path] = typer.Option(None, "--cwd"),
):
    """启动一个 LLM 主导的产品发现对话。

    示例：
      prompt-help pm-mode chat "做个截止日期提醒 app"
      → 创建对话，输出第一题（AI 反问）
      → 用 pm-mode answer "..." 回答
    """
    from ..core.pm_dialog import PMSession, next_question
    cfg = _config()
    final_slug = slug or _slugify(idea)
    f = _dialog_dir(cfg) / f"{final_slug}.json"
    if f.is_file():
        err_console.print(
            f"对话 {final_slug} 已存在。pm-mode show-history --slug {final_slug} 看现状，"
            "或换 --slug。"
        )
        raise typer.Exit(1)

    session = PMSession(
        slug=final_slug,
        idea=idea,
        cwd=str((cwd or Path.cwd()).resolve()),
    )
    # 第一轮：用户的"想法"作为第一条 user 消息
    session.append_user(idea)
    session.turn_count = 1

    console.print(f"[green]✓[/green] 对话已创建：[bold]{final_slug}[/bold]")
    console.print(f"  idea: {idea}\n")
    console.print("[dim]AI 正在思考第一个反问…（CC CLI 冷启动约 15-20s）[/dim]\n")

    r = next_question(cfg, session)
    if r.get("error"):
        err_console.print(f"LLM 失败：{r['error']}")
        _save_session(cfg, session)
        raise typer.Exit(1)

    if r["ready"]:
        console.print("[green]信息已饱和，可直接生成 brief。[/green]")
        console.print(f"  pm-mode save-bundle --slug {final_slug}")
    else:
        session.append_assistant(r["next_question"])
        console.print(Panel(r["next_question"], title="AI 反问", border_style="cyan"))
        console.print(
            f"\n[dim]评分 What:{r['scores']['what']} / Why:{r['scores']['why']} / How:{r['scores']['how']}[/dim]"
        )
        console.print(f"\n回答：[bold]pm-mode answer \"你的回答\" --slug {final_slug}[/bold]")

    _save_session(cfg, session)


def answer_cmd(
    text: str = typer.Argument(..., help="对最近一个 AI 反问的回答"),
    slug: Optional[str] = typer.Option(None, "--slug", "-s"),
):
    """回答上一轮 AI 反问，AI 会生成下一题或宣布信息饱和。"""
    from ..core.pm_dialog import next_question
    cfg = _config()
    s = _resolve_dialog_slug(cfg, slug)
    session = _load_session(cfg, s)

    session.append_user(text)
    session.turn_count = sum(1 for m in session.history if m["role"] == "user")

    console.print("[dim]AI 正在生成下一题…[/dim]\n")
    r = next_question(cfg, session)
    if r.get("error"):
        err_console.print(f"LLM 失败：{r['error']}")
        _save_session(cfg, session)
        raise typer.Exit(1)

    if r["ready"]:
        console.print(
            f"[green]✓[/green] 信息已饱和（What:{r['scores']['what']} / Why:{r['scores']['why']} / How:{r['scores']['how']}）"
        )
        console.print(f"\n生成 4 件套：[bold]pm-mode save-bundle --slug {s}[/bold]")
        session.ready = True
    else:
        session.append_assistant(r["next_question"])
        console.print(Panel(r["next_question"], title=f"AI 反问 #{session.turn_count}", border_style="cyan"))
        console.print(
            f"\n[dim]评分 What:{r['scores']['what']} / Why:{r['scores']['why']} / How:{r['scores']['how']} · 第 {session.turn_count} 轮[/dim]"
        )

    _save_session(cfg, session)


def save_bundle_cmd(
    slug: Optional[str] = typer.Option(None, "--slug", "-s"),
    archive_only: bool = typer.Option(False, "--archive-only"),
):
    """信息饱和后生成 4 件套（brief / stories / risks / decisions）。"""
    from ..core.pm_dialog import generate_brief_bundle
    cfg = _config()
    s = _resolve_dialog_slug(cfg, slug)
    session = _load_session(cfg, s)

    if not session.ready and session.turn_count < 3:
        err_console.print(
            f"对话只跑了 {session.turn_count} 轮，建议先用 pm-mode answer 继续访谈。"
            "硬要生成可加 --force（暂未实现，先继续对话）。"
        )
        raise typer.Exit(1)

    console.print("[dim]LLM 正在生成 4 件套（brief / stories / risks / decisions）…[/dim]\n")
    bundle = generate_brief_bundle(cfg, session)
    if bundle.get("error"):
        err_console.print(f"生成失败：{bundle['error']}")
        raise typer.Exit(1)

    # 写到 briefs/<date>-<slug>/<file>.md
    date = session.created[:10]
    out_dir = cfg.briefs_dir / f"{date}-{session.slug}"
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for fname_key, fname in [
        ("brief", "PRODUCT_BRIEF.md"),
        ("user_stories", "USER_STORIES.md"),
        ("risks", "RISKS.md"),
        ("decisions", "DECISIONS.md"),
    ]:
        p = out_dir / fname
        p.write_text(bundle[fname_key], encoding="utf-8")
        written.append(p)

    # 写 cwd 项目根（archive_only 跳过）
    if not archive_only and session.cwd:
        cwd_path = Path(session.cwd)
        if cwd_path.is_dir():
            (cwd_path / "PRODUCT_BRIEF.md").write_text(bundle["brief"], encoding="utf-8")
            written.append(cwd_path / "PRODUCT_BRIEF.md")

    for p in written:
        console.print(f"[green]✓[/green] {p}")
    console.print(f"\n下一步：进入 Plan 模式，Brief 已在 {out_dir}")


def show_history_cmd(
    slug: Optional[str] = typer.Option(None, "--slug", "-s"),
    json_out: bool = typer.Option(False, "--json"),
):
    """看对话完整历史 + 三维度评分。"""
    from dataclasses import asdict
    cfg = _config()
    s = _resolve_dialog_slug(cfg, slug)
    session = _load_session(cfg, s)
    if json_out:
        console.print_json(json.dumps(asdict(session), ensure_ascii=False))
        return
    console.print(f"[bold]{s}[/bold] · idea: [italic]{session.idea}[/italic]")
    console.print(
        f"  评分 What:{session.scores.get('what',0)} / "
        f"Why:{session.scores.get('why',0)} / How:{session.scores.get('how',0)}"
    )
    console.print(f"  第 {session.turn_count} 轮 · ready={session.ready}\n")
    for i, m in enumerate(session.history):
        role_label = "[blue]你[/blue]" if m["role"] == "user" else "[cyan]AI[/cyan]"
        console.print(f"{role_label} #{i}: {m['content']}\n")


# ---------------------------------------------------------------------------
# 保留：原 7-stage 兼容路径（Phase 6 遗留）
# ---------------------------------------------------------------------------

def tech_risks_suggest_cmd(
    stack: str = typer.Argument(..., help="逗号分隔技术栈，如 nextjs,react,sqlite"),
    json_out: bool = typer.Option(False, "--json"),
):
    """从 trap 库 + 栈匹配的提示词召回相关风险。"""
    cfg = _config()
    conn = indexer.open_db(cfg)

    stack_list = [s.strip().lower() for s in stack.split(",") if s.strip()]
    risks: list[dict] = []

    # 1) trap：栈或 triggers 含相关词
    traps = list(conn.execute("SELECT * FROM prompts WHERE scope = 'trap'"))
    for r in traps:
        stack_csv = (r["stack_csv"] or "").lower()
        triggers_csv = (r["triggers_csv"] or "").lower()
        haystack = stack_csv + " " + triggers_csv + " " + (r["title"] or "").lower()
        if any(s in haystack for s in stack_list):
            risks.append({
                "title": r["title"],
                "source": "trap",
                "preview": (r["body"] or "")[:200],
            })

    # 2) global 提示词中含 "踩坑/陷阱/risk/gotcha" 关键词且 stack 匹配
    for r in conn.execute(
        "SELECT * FROM prompts WHERE scope IN ('global','project') ORDER BY used DESC LIMIT 100"
    ):
        text = ((r["title"] or "") + " " + (r["body"] or "")[:500]).lower()
        if any(kw in text for kw in ["踩坑", "陷阱", "risk", "gotcha", "pitfall", "避免"]):
            stack_csv = (r["stack_csv"] or "").lower()
            if any(s in stack_csv for s in stack_list):
                risks.append({
                    "title": r["title"],
                    "source": r["scope"],
                    "preview": (r["body"] or "")[:200],
                })

    conn.close()

    if json_out:
        console.print_json(json.dumps({"stack": stack_list, "risks": risks[:8]}, ensure_ascii=False))
        return

    if not risks:
        console.print(f"[dim]从你历史 trap / 提示词里没有和 {stack_list} 直接相关的风险[/dim]")
        return

    console.print(f"[bold]栈 {stack_list} 的相关风险（来自你的历史踩坑库）：[/bold]")
    for r in risks[:8]:
        console.print(f"\n[yellow]⚠ {r['title']}[/yellow] [dim]({r['source']})[/dim]")
        console.print(f"  {r['preview']}")
