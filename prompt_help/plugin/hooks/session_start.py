"""SessionStart hook：CC 启动时打印库状态 + 当前项目栈匹配的提示词 + 相似项目召回。

三类召回（合并去重 + top-5 总量上限，避免刷屏）：
1. 栈匹配的 global / project 提示词（按 stack overlap）
2. 历史相似项目下的 project-scope 提示词（按 fingerprint Jaccard）
3. inbox 待审提醒
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

_pkg_root = Path(__file__).resolve().parents[3]
if str(_pkg_root) not in sys.path:
    sys.path.insert(0, str(_pkg_root))

from prompt_help.plugin.hooks._runtime import safe_main  # noqa: E402


_TOTAL_CAP = 5
_SIMILARITY_THRESHOLD = 0.30


def run(inp: dict, cfg) -> str | None:
    cwd = Path(inp.get("cwd") or Path.cwd())

    from prompt_help.core import indexer
    from prompt_help.core.fingerprint import (
        fingerprint, from_dict, overlap_coefficient, stack_overlap,
    )

    if not cfg.index_db.is_file():
        return None

    try:
        conn = indexer.open_db(cfg)
        counts = indexer.count_all(conn)
    except Exception:
        return None

    if counts["total"] == 0:
        conn.close()
        return None

    fp = fingerprint(cwd)

    # ---- 1) 栈匹配召回 —— P15：通用模板优先（is_template=True 加分） ----
    rows = list(conn.execute(
        "SELECT * FROM prompts WHERE scope IN ('global', 'project') "
        "ORDER BY is_template DESC, used DESC LIMIT 300"
    ))
    stack_matches: list[tuple] = []
    for r in rows:
        stack = [s for s in (r["stack_csv"] or "").split(",") if s.strip()]
        if not stack:
            continue
        ovr = stack_overlap(stack, fp)
        if ovr > 0.0:
            # 通用模板 + 0.1 加分，让它在排序中靠前
            try:
                if r["is_template"]:
                    ovr += 0.1
            except (KeyError, IndexError):
                pass
            stack_matches.append((r, ovr, "stack"))
    stack_matches.sort(key=lambda x: x[1], reverse=True)

    # ---- 2) 相似项目召回 ----
    similar_projects: list[tuple] = []  # [(project_name, similarity, fp_other)]
    try:
        for proj_row in indexer.list_projects(conn):
            if proj_row["name"] == fp.project_name:
                continue
            try:
                other = from_dict(json.loads(proj_row["fingerprint_json"]))
            except Exception:
                continue
            sim = overlap_coefficient(fp, other)
            if sim >= _SIMILARITY_THRESHOLD:
                similar_projects.append((proj_row["name"], sim, other))
    except Exception:
        pass
    similar_projects.sort(key=lambda x: x[1], reverse=True)

    # 从相似项目里捞 project-scope 提示词
    project_recalls: list[tuple] = []
    for proj_name, sim, _other in similar_projects[:2]:
        proj_rows = list(conn.execute(
            "SELECT * FROM prompts WHERE scope='project' AND project=? "
            "ORDER BY used*2 + success_signal*3 DESC LIMIT 3",
            (proj_name,),
        ))
        for r in proj_rows:
            project_recalls.append((r, sim, f"~{proj_name}"))

    # ---- 合并 + 去重 + 截顶 ----
    seen_ids: set = set()
    merged: list[tuple] = []
    for r, score, source in stack_matches + project_recalls:
        if r["id"] in seen_ids:
            continue
        seen_ids.add(r["id"])
        merged.append((r, score, source))
        if len(merged) >= _TOTAL_CAP:
            break

    # ---- inbox 数 ----
    inbox_n = 0
    try:
        if cfg.inbox_dir.is_dir():
            inbox_n = len(list(cfg.inbox_dir.glob("*.md")))
    except Exception:
        pass

    conn.close()

    lines = [
        f"[prompt-help] 库内 {counts['total']} 条提示词（global={counts.get('global', 0)}，"
        f"project={counts.get('project', 0)}，trap={counts.get('trap', 0)}）"
    ]

    if merged:
        lang_str = ", ".join(sorted(fp.langs)[:3]) if fp.langs else "未知栈"
        lines.append(f"匹配当前项目 {fp.project_name}（{lang_str}）：")
        for r, score, source in merged:
            tag = f"[{source}]" if source != "stack" else ""
            tpl_mark = ""
            try:
                if r["is_template"]:
                    tpl_mark = "🎯 "
            except (KeyError, IndexError):
                pass
            lines.append(
                f"  · {tpl_mark}{r['title']} {tag} [{r['scope']}, used={r['used']}]  score={score:.2f}"
            )

    if similar_projects:
        sp_str = ", ".join(f"{n}({s:.2f})" for n, s, _ in similar_projects[:3])
        lines.append(f"相似历史项目：{sp_str}")

    if merged or similar_projects:
        lines.append("用 `/prompt-find <关键词>` 检索，`/prompt-show <title>` 看完整内容。")

    if inbox_n > 0:
        lines.append(f"⚠ 有 {inbox_n} 条 mining 候选待审：`/prompt-review`")

    # pulse 未读提醒
    try:
        from prompt_help.cli.pulse import latest_unread_digest_summary
        pulse_msg = latest_unread_digest_summary(cfg)
        if pulse_msg:
            lines.append(pulse_msg)
    except Exception:
        pass

    return "\n".join(lines) if len(lines) > 1 else None


if __name__ == "__main__":
    safe_main("SessionStart", run)
