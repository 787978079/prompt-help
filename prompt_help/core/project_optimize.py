"""项目优化：用 LLM 基于项目上下文重写一条提示词。

输入：
  - 用户复制过来的原始提示词
  - 目标项目仓库路径（已登记 or 任意目录）

处理：
  1. 抽取项目"摘要包"（CLAUDE.md / AGENTS.md / .cursorrules / package.json / pyproject.toml /
     Cargo.toml / go.mod / README.md 前 200 行 + 顶层目录结构）
  2. 总字符上限 ~6000，超出按优先级截断
  3. 拼装 system prompt + user prompt（提示词 + 项目摘要）
  4. 调 LLM（复用 optimizer.py 的三后端：CC CLI / Codex CLI / API）

输出：
  - 优化后的提示词（针对该项目栈和约定调整）

设计原则：
  - LLM 必须明确"不要无中生有添加项目里不存在的工具/库"
  - 优先保留用户原意图，只做"针对项目的具体化 / 操作化"
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from . import proc
from .config import Config
from .optimizer import OptimizeResult, _decide_backend


PROJECT_OPTIMIZE_SYSTEM_PROMPT = """\
你是资深软件架构师 + 提示词工程师。任务：把用户给的通用提示词，结合下面这份"项目摘要"，
改写成针对该具体项目的高质量提示词。

## 改写原则（严格遵守）

1. **保留用户原意图**：用户想做什么，重写后还是做什么；不要无中生有添加新需求
2. **基于项目实际栈做具体化**：
   - 如果项目是 React + TypeScript + Vite，把"跑测试"具体成"`npx vitest run`"
   - 如果项目是 FastAPI + pytest，把"加 endpoint"具体成"用 APIRouter 加到 src/api/*.py，跟现有 pydantic schema 风格一致"
   - 如果项目是 Next.js App Router，区分 server / client component
3. **遵守项目 CLAUDE.md / AGENTS.md / .cursorrules 里的约束**（如果有）
4. **绝不臆造**：项目摘要里没提的工具 / 命令 / 依赖，不要假设它存在
5. **保持原语言**：用户原文中文则中文，原文英文则英文，原文混合保持混合
6. **结构化**：复杂任务用 XML 分块（<task>、<context>、<constraints>、<output_format>）
7. **简洁**：不要冗余客套（"请你"、"我想要"），直接指令

## 输出格式（铁律）

用户会**直接复制结果粘贴到 Claude / Cursor / Codex 跑**，所以你只能输出最终可执行的提示词正文本身。绝对禁止：

- ❌ 禁止 "使用说明" / "填空位约定" / "占位符含义" / "占位符约定" 等表格
- ❌ 禁止 "相比原版做了什么" / "改动说明" / "改进点" / "对比" / "为什么这样改"
- ❌ 禁止 "最小变更版本" / "另一版" / "可选版本" 等额外产出
- ❌ 禁止 markdown 代码块包裹整段正文（不要在最外层套 ``` 围栏）
- ❌ 禁止前缀 "以下是优化后的提示词：" / 结尾 "希望对你有帮助" 等套话
- ❌ 禁止复述 <project> / <user_request> 等 XML 标签

如果想用占位符（如 `{{产品名}}`），直接写进正文不要解释——用户看上下文能懂。
"""


# 文件优先级：CLAUDE.md 是核心，绝不能截断；README 优先级最低
_PRIORITY_FILES = [
    ("CLAUDE.md", 4000),
    ("AGENTS.md", 2000),
    (".cursorrules", 2000),
    ("package.json", 1500),
    ("pyproject.toml", 1500),
    ("Cargo.toml", 1000),
    ("go.mod", 800),
    ("requirements.txt", 800),
    ("README.md", 3000),
]

# 顶层目录结构最多列多少项
_MAX_DIR_ENTRIES = 40
_MAX_SUMMARY_CHARS = 6500  # 总上限（含目录树）


@dataclass
class ProjectSummary:
    project_path: Path
    project_name: str
    sections: list[tuple[str, str]]  # (file_or_section_name, content)
    truncated: bool
    total_chars: int


def extract_project_summary(project_path: Path) -> ProjectSummary:
    """读项目关键元信息文件 + 顶层目录结构，组成 LLM 上下文包。

    返回 ProjectSummary。total_chars 包含所有 sections content 的字符数。
    """
    project_path = Path(project_path).expanduser().resolve()
    sections: list[tuple[str, str]] = []
    total = 0
    truncated = False

    for filename, limit in _PRIORITY_FILES:
        f = project_path / filename
        if not f.is_file():
            continue
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if len(text) > limit:
            text = text[:limit].rstrip() + "\n... [截断]"
        # 总预算约束
        if total + len(text) > _MAX_SUMMARY_CHARS:
            remain = max(0, _MAX_SUMMARY_CHARS - total)
            if remain < 200:  # 太少不如不放
                truncated = True
                break
            text = text[:remain].rstrip() + "\n... [因总预算截断]"
            truncated = True
        sections.append((filename, text))
        total += len(text)

    # 顶层目录结构
    tree_text = _list_top_level(project_path)
    if total + len(tree_text) <= _MAX_SUMMARY_CHARS:
        sections.append(("（顶层目录结构）", tree_text))
        total += len(tree_text)
    else:
        truncated = True

    return ProjectSummary(
        project_path=project_path,
        project_name=project_path.name,
        sections=sections,
        truncated=truncated,
        total_chars=total,
    )


def _list_top_level(project_path: Path, depth: int = 2) -> str:
    """列项目顶层目录结构（不读文件内容，只列名）。"""
    if not project_path.is_dir():
        return ""
    skip_names = {
        "node_modules", ".git", "__pycache__", ".venv", "venv", "dist", "build",
        ".next", ".nuxt", "target", ".cache", ".pytest_cache", ".mypy_cache",
        ".ruff_cache", "coverage", ".tox",
    }
    lines: list[str] = []
    count = 0
    try:
        entries = sorted(project_path.iterdir(), key=lambda p: (p.is_file(), p.name.lower()))
        for entry in entries:
            if entry.name.startswith(".") and entry.name not in (".env.example",):
                continue
            if entry.name in skip_names:
                continue
            if count >= _MAX_DIR_ENTRIES:
                lines.append(f"... 还有 {len(entries) - count} 项未列")
                break
            marker = "/" if entry.is_dir() else ""
            lines.append(f"{entry.name}{marker}")
            count += 1
    except OSError:
        return ""
    return "\n".join(lines)


def render_summary_for_prompt(summary: ProjectSummary) -> str:
    """把 ProjectSummary 渲染成 XML 块给 LLM。"""
    parts = [f"<project name=\"{summary.project_name}\" path=\"{summary.project_path}\">"]
    for name, content in summary.sections:
        parts.append(f"<file path=\"{name}\">")
        parts.append(content.strip())
        parts.append("</file>")
    if summary.truncated:
        parts.append("<note>项目摘要部分内容因总预算被截断；优化时基于已给信息处理。</note>")
    parts.append("</project>")
    return "\n".join(parts)


def optimize_for_project(
    cfg: Config,
    original_prompt: str,
    project_path: Path,
    *,
    mode: Literal["auto", "cc_cli", "codex_cli", "api"] = "auto",
) -> OptimizeResult:
    """核心入口：基于项目上下文优化提示词。"""
    summary = extract_project_summary(project_path)
    project_block = render_summary_for_prompt(summary)

    # 自己组好完整 XML 结构，传 wrap_user_in_xml=False 让 optimizer 不再外包 <original_prompt>
    user_text = (
        f"{project_block}\n\n"
        f"<user_request>\n{original_prompt}\n</user_request>\n\n"
        "请基于上面 <project> 块里的项目信息，把 <user_request> 改写成"
        "针对该项目的高质量提示词。直接输出改写结果纯文本，不要复述任何 XML 标签。"
    )

    from .optimizer import _run
    return _run(
        cfg, user_text,
        system_prompt=PROJECT_OPTIMIZE_SYSTEM_PROMPT,
        mode=mode,
        wrap_user_in_xml=False,
    )
