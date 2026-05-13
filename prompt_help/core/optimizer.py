"""提示词润色：双后端可切换。

- `optimize(cfg, text)`：保留原版 polish（XML 结构化、显式约束、清理客套）
- `generalize(cfg, text)`：把项目专属提示词转成可复用模板（抽象 path/UUID/版本号 为占位符）

两个函数都用同一份后端选择逻辑：
  cfg.optimizer.prefer_cc_cli=True 且 PATH 有 claude → 走 CC CLI（用户本机，复用 Opus 订阅免费）
  否则 → 走 OpenAI 兼容 API（DeepSeek 默认，给其他用户）
"""

from __future__ import annotations

import difflib
import json
import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from . import proc
from .config import Config


# ---------------------------------------------------------------------------
# 双后端 system prompts
# ---------------------------------------------------------------------------

POLISH_SYSTEM_PROMPT = """\
你是一名顶级提示词工程师。任务：把用户给的原始提示词改写成更结构化、更精确、更易复用的版本，但保留原作者的核心意图、声调与具体内容。

## 改写规则（严格遵守）

1. **结构化包裹**：复杂提示词用 XML 标签分块（<task>、<context>、<constraints>、<output_format>、<examples>）。简短指令型提示词不强加结构，只做精简。
2. **角色锚定**：若缺乏明确角色描述（"你是一名……"），在开头补一句简洁的角色定义。
3. **显式化约束**：把隐含约束（"要短"、"用中文"）提炼成 <constraints> 列表。
4. **清理冗余客套**：移除 "请"、"我想要你"、"麻烦你"、"please could you" 等填充词。
5. **保留专有术语**：项目名、库名、命令、文件路径、关键词原样保留。
6. **保留语言**：原文中文则输出中文，原文英文则输出英文，原文混合则保持混合。
7. **不要增加新的需求或功能**：你只是重写已有内容，不主动延伸。

## 输出格式（铁律）

**只输出最终可直接粘贴使用的提示词正文本身**。用户拿到结果会立刻粘到 Claude / Cursor / Codex 里跑。任何元信息都属于干扰，绝对禁止：

- ❌ 禁止输出 "使用说明" / "填空位约定" / "占位符含义" / "占位符约定" 等说明表
- ❌ 禁止输出 "相比原版做了什么" / "改动说明" / "改进点" / "对比" / "为什么这样改" 章节
- ❌ 禁止输出 "最小变更版本" / "另一版" / "可选版本" 等额外产出
- ❌ 禁止 markdown 代码块包裹（不要 ``` 围栏）
- ❌ 禁止前缀 "以下是优化后的提示词："、"优化版本如下：" 等开场白
- ❌ 禁止结尾加 "希望对你有帮助"、"以上就是" 类客套

如果你想加占位符（如 `{{产品名称}}`），直接放进正文，**不要单独解释占位符的含义**——用户能看懂上下文。
"""

GENERALIZE_SYSTEM_PROMPT = """\
你是一名提示词工程师。任务：把用户写的、针对某具体项目的提示词，转成可在其他项目复用的模板。

## 抽象规则

1. **项目名 / 文件路径 / 版本号 / UUID / task ID / 具体环境变量名 → 抽象成 [占位符]**
   - 例：`C:\\Users\\lin\\proj\\src\\main.py` → `[项目入口文件路径]`
   - 例：`v1.6` → `[版本号]`
   - 例：`task b7a5nv6zh` → `[task ID]`
   - 例：`MinPEI 项目` → `[项目名]`
2. **保留指令的逻辑结构、约束清单、输出格式要求**
3. **保留原语言**（中文 / 英文 / 混合）
4. **不要增加新需求**——你只是把"具体"换成"抽象"，不是改写

## 输出格式（铁律）

**只输出最终可直接粘贴使用的提示词模板本身**。绝对禁止：
- ❌ 禁止 "使用说明" / "占位符约定" / "填空位约定" 等说明表
- ❌ 禁止 "相比原版做了什么" / "改动说明" / "对比" 章节
- ❌ 禁止 "最小变更版本" / "另一版" 等额外产出
- ❌ 禁止 markdown 代码块包裹（不要 ``` 围栏）
- ❌ 禁止前缀 "以下是模板：" / 结尾客套

占位符直接写进正文（如 `[项目名]`），**不要单独列表解释占位符含义**。
"""


SUMMARIZE_SYSTEM_PROMPT = """\
你是一名提示词工程师。基于下面的提示词正文，写一句话描述。

## 规则
1. 中文输出
2. 一句话，30 字以内
3. 说明这条提示词「做什么用」/「适合什么场景」，不复述具体步骤
4. 不要前缀"以下是描述："这种废话
5. 不要句末标点（句号、感叹号都不加）

## 输出
直接输出这一句话，纯文本。
"""


# 通用模板标题：纯粹的 ≤10 字描述，不加任何后缀
TITLE_TEMPLATE_SYSTEM_PROMPT = """\
你是提示词命名专家。基于下面的提示词正文，生成一个**给条目列表用的标题**。

## 规则（绝对严格）
1. **中文输出**
2. **整个标题必须 ≤ 10 个中文字符**（中英混合按字符数；超 10 字视为失败）
3. 只保留**最核心的场景词**，不要技术细节
   - 例：自研蒙层 spotlight 引导 → "新手引导蒙层"
   - 例：用户视角软件评测 → "软件评测方案"
   - 例：金案例对比测试质量 → "金案例对比"
   - 例：用户头像组件上传裁剪 → "头像组件"
   - 例：SQL 终端模拟器 → "SQL 终端"
4. **绝对不要加任何后缀**：不要加「【通用提示词】」「（通用模板）」「[模板]」等任何标识——条目类型靠列表的"类型"列展示，标题里出现重复
5. 不要在标题里出现"通用模板"/"模板"/"通用提示词"/"提示词"等字眼
6. 不要句末标点、引号、括号注释、markdown、解释

## 输出
直接输出这一行标题，**纯文本**，≤ 10 字，不含任何后缀。
"""


# 项目优化标题：纯粹的 ≤10 字描述，不加任何后缀
TITLE_PROJECT_SYSTEM_PROMPT = """\
你是提示词命名专家。基于下面这条**针对某具体项目**的提示词正文，生成给条目列表用的标题。

## 规则（绝对严格）
1. **中文输出**
2. **整个标题必须 ≤ 10 个中文字符**（中英混合按字符数；超 10 字视为失败）
3. 只保留**最核心的动作 + 对象**，不要技术细节
   - 例：写头像上传裁剪组件 → "头像组件"
   - 例：跑完整测试套件 → "测试回归"
   - 例：修登录 bug → "登录异常修复"
   - 例：加新手引导教程 → "新手引导实现"
4. **绝对不要加任何后缀**：不要加「【项目优化】」「（项目化）」等标识
5. 不要在标题里出现"项目优化"/"项目化"/"针对项目"等字眼
6. 不要句末标点、引号、括号注释、markdown、解释

## 输出
直接输出这一行标题，**纯文本**，≤ 10 字，不含任何后缀。
"""


TRANSLATE_TO_ZH_SYSTEM_PROMPT = """\
你是专业的中英翻译。把英文提示词翻译成自然、地道的中文。

## 规则（严格遵守）

1. **保持原结构**：XML 标签、列表编号、代码块、占位符 [...] 全部原样保留
2. **专有名词不翻译**：产品名（React/Tailwind/SQL）、命令名（git push）、库名（pandas）、文件路径全部保留英文
3. **技术术语择优**：常见有标准译法的用中文（"组件"/"路由"/"事务"），生僻或圈内常用英文的保留英文（embedding、prompt、commit）
4. **占位符保持原文**：`{variable}`、`[PLACEHOLDER]`、`<TASK>` 这种插值标记原样不动
5. **第二人称用"你"**：避免"您"，更亲近自然
6. **不要解释、不要前缀**："以下是翻译："这种废话不要

## 输出

直接输出翻译后的提示词正文，纯文本，不带 markdown 包裹。
"""


# ---------------------------------------------------------------------------
# 结果类
# ---------------------------------------------------------------------------

@dataclass
class OptimizeResult:
    original: str
    optimized: str
    diff_text: str
    success: bool
    error: str | None = None
    backend: str = ""  # "cc_cli" | "api" | "skipped"


# ---------------------------------------------------------------------------
# 公开 API
# ---------------------------------------------------------------------------

BackendMode = Literal["auto", "cc_cli", "codex_cli", "api"]


def optimize(cfg: Config, original: str, *, mode: BackendMode = "auto") -> OptimizeResult:
    """polish 一个提示词。"""
    return _run(cfg, original, system_prompt=POLISH_SYSTEM_PROMPT, mode=mode)


def generalize(cfg: Config, original: str, *, mode: BackendMode = "auto") -> OptimizeResult:
    """把项目专属提示词抽象成可复用模板。"""
    return _run(cfg, original, system_prompt=GENERALIZE_SYSTEM_PROMPT, mode=mode)


def summarize(cfg: Config, original: str, *, mode: BackendMode = "auto") -> OptimizeResult:
    """基于正文生成一句话描述（Phase 9 T3：用于 description 字段）。"""
    return _run(cfg, original, system_prompt=SUMMARIZE_SYSTEM_PROMPT, mode=mode)


def generate_title(
    cfg: Config,
    body: str,
    *,
    kind: Literal["template", "project"] = "template",
    mode: BackendMode = "auto",
) -> OptimizeResult:
    """基于正文生成 ≤20 字的精炼标题。

    kind='template' → 加「【通用提示词】」后缀
    kind='project'  → 加「【项目优化】」后缀

    LLM 失败 / 返回过长时由调用方 fallback 到原 title + 后缀。
    """
    sys_prompt = (
        TITLE_TEMPLATE_SYSTEM_PROMPT if kind == "template"
        else TITLE_PROJECT_SYSTEM_PROMPT
    )
    return _run(cfg, body, system_prompt=sys_prompt, mode=mode)


def safe_generate_title(
    cfg: Config,
    body: str,
    fallback: str,
    *,
    kind: Literal["template", "project"] = "template",
    mode: BackendMode = "auto",
) -> str:
    """调 generate_title，失败 / 离谱长都退到 fallback。

    硬保证：返回值 ≤ 10 个中文字符（用户严格要求，不加任何后缀）。
    LLM 输出超 10 字 → 重试一次 → 还超 → 自然标点截断 → 都不行才 fallback。
    """
    max_len = 10

    # LLM 容易加的后缀／标签——返回前必须剥光
    _STRIP_SUFFIXES = (
        "【通用提示词】", "【通用模板】", "【项目优化】", "【模板】",
        "（通用模板）", "（通用提示词）", "（项目优化）", "(通用模板)",
        "(通用提示词)", "(项目优化)", "[通用提示词]", "[通用模板]",
        "[项目优化]", "[模板]",
    )

    def _strip_unwanted(t: str) -> str:
        """剥掉 LLM 加的标签 / 后缀 / 引号 / markdown 标记。"""
        t = t.strip().splitlines()[0].strip()
        t = t.lstrip("#").strip().strip("「」\"'`*")
        for suffix in _STRIP_SUFFIXES:
            t = t.replace(suffix, "").strip()
        # 末尾残留的方括号 / 圆括号
        while t and t[-1] in "）)】]：:、，． ":
            t = t[:-1].strip()
        return t

    def _smart_truncate(b: str) -> str | None:
        if len(b) <= max_len:
            return b
        head = b[:max_len + 2]
        for sep in ("，", "、", "（", "(", " - ", " +", "+ ", ":", "：", " ", "/"):
            idx = head.rfind(sep)
            if 3 <= idx <= max_len:
                cand = head[:idx].rstrip(" -+:/")
                if len(cand) >= 3:
                    return cand
        truncated = b[:max_len].rstrip(" -+:/，、（(")
        return truncated if len(truncated) >= 3 else None

    try:
        # 第 1 次调用
        r = generate_title(cfg, body, kind=kind, mode=mode)
        if not r.success or not r.optimized:
            return fallback
        title = _strip_unwanted(r.optimized)
        if not title or len(title) > 80:
            return fallback
        if len(title) <= max_len:
            return title

        # 超 max_len：先看自然边界
        truncated = _smart_truncate(title)
        if truncated and len(truncated) <= max_len:
            ends_clean = (
                "一" <= truncated[-1] <= "鿿"
                or any(c > "一" for c in truncated)
            )
            if ends_clean and not _looks_truncated_in_word(title, len(truncated)):
                return truncated

        # 重试：告诉 LLM 上次太长
        retry_body = (
            f"上一次给的标题太长（{len(title)} 字）：「{title}」\n\n"
            f"请重新生成，整个标题必须严格 ≤ {max_len} 个中文字符，"
            f"绝对不要加任何后缀 / 标签 / 括号说明。\n\n"
            f"基于这段提示词正文重命名：\n\n{body[:800]}"
        )
        r2 = generate_title(cfg, retry_body, kind=kind, mode=mode)
        if r2.success and r2.optimized:
            t2 = _strip_unwanted(r2.optimized)
            if t2 and len(t2) <= max_len:
                return t2
            t3 = _smart_truncate(t2) if t2 else None
            if t3:
                return t3

        # 走兜底截断
        if truncated:
            return truncated
        return fallback
    except Exception:
        return fallback


def _looks_truncated_in_word(body: str, cut_pos: int) -> bool:
    """cut_pos 处截断是否在英文词中间？"""
    if cut_pos <= 0 or cut_pos >= len(body):
        return False
    prev = body[cut_pos - 1]
    nxt = body[cut_pos]
    return (prev.isalnum() and nxt.isalnum() and prev.isascii() and nxt.isascii())


def translate_to_zh(cfg: Config, original: str, *, mode: BackendMode = "auto") -> OptimizeResult:
    """把英文提示词翻译成中文，保留结构和占位符。命中缓存秒回。"""
    # 已经是中文（CJK 占比 > 30%）就别浪费 API
    cjk_count = sum(1 for c in original if "一" <= c <= "鿿")
    if cjk_count / max(len(original), 1) > 0.30:
        return OptimizeResult(
            original=original, optimized=original,
            diff_text="", success=True, error="already_zh",
            backend="skipped",
        )

    # Phase 7：先查缓存
    from .translation_cache import TranslationCache, hash_text
    cache = TranslationCache(cfg)
    h = hash_text(original)
    cached = cache.get(h)
    if cached is not None:
        return OptimizeResult(
            original=original, optimized=cached,
            diff_text="", success=True, backend="cache",
        )

    result = _run(cfg, original, system_prompt=TRANSLATE_TO_ZH_SYSTEM_PROMPT, mode=mode)
    if result.success and result.optimized and result.optimized != original:
        cache.put(h, result.optimized)
    return result


# ---------------------------------------------------------------------------
# 后端选择
# ---------------------------------------------------------------------------

def _decide_backend(cfg: Config, mode: str) -> str:
    """Phase 22：支持三后端。显式 mode 优先；auto 时按配置 + 可用性挑。

    auto 优先级：
      cfg.optimizer.backend == "cc_cli|codex_cli|api"   显式锁定
      cfg.optimizer.backend == "auto":
        prefer_cc_cli=True 且 claude 在 PATH        → cc_cli
        codex 在 PATH（无论 prefer_cc_cli）          → codex_cli
        else                                          → api
    """
    if mode in ("cc_cli", "codex_cli", "api"):
        return mode
    explicit = (cfg.optimizer.backend or "auto").lower()
    if explicit in ("cc_cli", "codex_cli", "api"):
        return explicit
    if cfg.optimizer.prefer_cc_cli and shutil.which(cfg.optimizer.cc_cli_path):
        return "cc_cli"
    if shutil.which(cfg.optimizer.codex_cli_path):
        return "codex_cli"
    return "api"


def _run(
    cfg: Config,
    original: str,
    *,
    system_prompt: str,
    mode: str,
    wrap_user_in_xml: bool = True,
) -> OptimizeResult:
    # wrap_user_in_xml=False：调用方已经在 `original` 里组好结构（如 project_optimize 的
    # `<project>` + `<user_request>`），不要再外包一层 `<original_prompt>`，避免嵌套污染
    backend = _decide_backend(cfg, mode)

    try:
        if backend == "cc_cli":
            optimized = _call_cc_cli(cfg, original, system_prompt, wrap_user_in_xml=wrap_user_in_xml)
        elif backend == "codex_cli":
            optimized = _call_codex_cli(cfg, original, system_prompt, wrap_user_in_xml=wrap_user_in_xml)
        else:
            optimized = _call_api(cfg, original, system_prompt, wrap_user_in_xml=wrap_user_in_xml)
    except Exception as e:
        return OptimizeResult(
            original=original, optimized=original, diff_text="",
            success=False, error=str(e), backend=backend,
        )

    optimized = (optimized or "").strip()
    if not optimized:
        return OptimizeResult(
            original=original, optimized=original, diff_text="",
            success=False, error="后端返回空", backend=backend,
        )

    # 兜底：剥离元说明块（"使用说明" / "相比原版" / "最小变更版本" 等）
    optimized = _strip_meta_blocks(optimized)

    return OptimizeResult(
        original=original, optimized=optimized,
        diff_text=render_diff(original, optimized),
        success=True, backend=backend,
    )


# 元说明块关键词：LLM 会用这些 markdown 标题输出"附加产物"——用户不想要
_META_HEADING_KEYWORDS = (
    "使用说明", "用法说明", "占位符约定", "填空位约定", "占位符含义", "占位符说明",
    "相比原版", "相比原始", "改动说明", "改动点", "改进点", "改进说明", "为什么这样改",
    "为什么这么改", "对比", "diff",
    "最小变更版本", "最小变更示例", "另一版", "可选版本", "可选版", "另一个版本",
    "备注", "说明", "Notes", "Note:", "说明:",
)

# markdown 标题行（# / ## / ### ...）
_HEADING_LINE_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$", re.MULTILINE)


def _strip_meta_blocks(text: str) -> str:
    """剥离 LLM 输出的元说明章节。

    LLM 即使被 system prompt 警告，也常顺手加"## 使用说明"/"## 相比原版做了什么"等
    元章节——用户拿到提示词是要直接粘贴用，这些必须删干净。

    策略：找第一个标题匹配元关键词的位置，截断后面所有内容。"""
    if not text or len(text) < 20:
        return text
    cut_at: int | None = None
    for m in _HEADING_LINE_RE.finditer(text):
        title = m.group(2).strip().lstrip("*`").rstrip("*`")
        if any(kw.lower() in title.lower() for kw in _META_HEADING_KEYWORDS):
            cut_at = m.start()
            break
    if cut_at is not None:
        return text[:cut_at].rstrip()

    # 兜底：处理无 markdown 标题但有"相比原版做了什么"等粗体行
    lines = text.splitlines()
    for i, line in enumerate(lines):
        stripped = line.strip().lstrip("*`").rstrip("*`：:").strip()
        if stripped and any(kw in stripped for kw in (
            "相比原版", "相比原始", "改动说明", "最小变更版本", "最小变更示例",
            "使用说明", "用法说明", "占位符约定", "填空位约定",
        )):
            # 这行像章节标题（短且独占一行）才截
            if len(stripped) < 20 and (i == 0 or not lines[i - 1].strip() or i > 0):
                return "\n".join(lines[:i]).rstrip()
    return text


# ---------------------------------------------------------------------------
# 后端实现：CC CLI（claude -p 子进程）
# ---------------------------------------------------------------------------

def _call_cc_cli(cfg: Config, text: str, system_prompt: str, *, wrap_user_in_xml: bool = True) -> str:
    """走 claude CLI 的 -p 非交互模式。"""
    if not shutil.which(cfg.optimizer.cc_cli_path):
        raise RuntimeError(
            f"Claude Code CLI 未在 PATH 中（查找 '{cfg.optimizer.cc_cli_path}'）。"
            "安装：npm i -g @anthropic-ai/claude-code，然后跑一次 `claude` 完成登录。"
        )
    cli_path = shutil.which(cfg.optimizer.cc_cli_path) or cfg.optimizer.cc_cli_path
    cmd = [
        cli_path,
        "-p",
        "--output-format", "json",
        "--max-turns", "1",
        "--no-session-persistence",
        "--append-system-prompt", system_prompt,
    ]
    user_payload = f"<original_prompt>\n{text}\n</original_prompt>" if wrap_user_in_xml else text
    result = proc.run(
        cmd,
        input=user_payload,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=cfg.optimizer.cc_cli_timeout_seconds,
    )
    if result.returncode != 0:
        raise RuntimeError(f"claude CLI 退出码 {result.returncode}: {result.stderr[:500]}")

    # CC CLI JSON 输出 schema 大致：{"type": "result", "result": "<text>", ...}
    # 兼容多个版本；找最大的 "result" 字段或合并所有 text
    raw = result.stdout.strip()
    if not raw:
        raise RuntimeError("claude CLI 返回空 stdout")

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        # 不是 JSON，可能是纯文本输出（旧版本）
        return raw

    # 统一抽出"主回复"
    if isinstance(data, dict):
        if "result" in data and isinstance(data["result"], str):
            return data["result"]
        if "content" in data and isinstance(data["content"], str):
            return data["content"]
        # 多回合：找最后一个 assistant message
        msgs = data.get("messages") or data.get("transcript") or []
        if isinstance(msgs, list):
            for m in reversed(msgs):
                if isinstance(m, dict) and m.get("role") == "assistant":
                    c = m.get("content")
                    if isinstance(c, str):
                        return c
                    if isinstance(c, list):
                        return "\n".join(
                            blk.get("text", "") for blk in c
                            if isinstance(blk, dict) and blk.get("type") == "text"
                        )
    if isinstance(data, list):
        # 一些版本返回事件流数组
        texts = []
        for evt in data:
            if isinstance(evt, dict) and evt.get("type") == "text":
                texts.append(evt.get("text", ""))
        if texts:
            return "".join(texts)

    raise RuntimeError(f"claude CLI 返回无法解析的格式：{raw[:200]}")


# ---------------------------------------------------------------------------
# 后端实现：Codex CLI（codex exec 子进程）— Phase 22
# ---------------------------------------------------------------------------

def _call_codex_cli(cfg: Config, text: str, system_prompt: str, *, wrap_user_in_xml: bool = True) -> str:
    """走 `codex exec` 非交互模式。

    Codex CLI 默认 stdout 只输出最终 assistant 消息（纯文本），不需要 JSON 解析；
    stderr 流进度日志，我们 capture 但不用。

    认证：靠 CODEX_API_KEY 环境变量 或 `codex login` 后的 ~/.codex/auth。
    PH 不在代码里塞 key，依赖用户自己配好（settings 页提示）。

    关键 flag：
      --skip-git-repo-check  Codex 默认要求在 git repo 内，PH 调用时不强求
    """
    if not shutil.which(cfg.optimizer.codex_cli_path):
        raise RuntimeError(
            f"Codex CLI 未在 PATH 中（查找 '{cfg.optimizer.codex_cli_path}'）。"
            "安装：npm i -g @openai/codex，然后跑 `codex login` 完成认证。"
        )
    cli_path = shutil.which(cfg.optimizer.codex_cli_path) or cfg.optimizer.codex_cli_path

    # Codex CLI 没有 --system / --append-system-prompt，只能把 system + user 拼成一个 prompt。
    # ⚠ 两个已踩过的坑（不要回退）：
    #   1. prompt 当 cmd 参数传 → codex agent 把它当"开场白"，输出 "What would you like
    #      me to work on" 后就停。**必须走 stdin**（不传 PROMPT 位置参数时 stdin 作 initial
    #      instructions，见 `codex exec --help`）
    #   2. stdout 含 banner / "user" / "codex" / "tokens used" 杂讯，用 `-o` 把 final
    #      assistant message 单独写文件最干净
    user_block = f"<user_input>\n{text}\n</user_input>" if wrap_user_in_xml else text
    stdin_prompt = (
        "**这是一次性文本处理任务。立即输出结果，不要探索任何文件、不要执行工具调用、不要询问澄清问题。**\n\n"
        f"<instructions>\n{system_prompt}\n</instructions>\n\n"
        f"{user_block}\n\n"
        "现在按 <instructions> 处理上面的输入，直接输出处理结果纯文本（不要 markdown 围栏，不要复述 XML 标签）："
    )

    import tempfile
    with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".txt", encoding="utf-8") as tf:
        tmp_out = tf.name
    try:
        cmd = [
            cli_path, "exec",
            "--skip-git-repo-check",
            "--ephemeral",            # 不持久化到 ~/.codex/sessions
            "--ignore-user-config",   # 忽略用户 config.toml 自定义 system prompt
            "-o", tmp_out,            # final message 写文件
            # 不传 PROMPT 位置参数 → stdin 作 initial instructions
        ]
        result = proc.run(
            cmd,
            input=stdin_prompt,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=cfg.optimizer.codex_cli_timeout_seconds,
        )
        if result.returncode != 0:
            stderr_tail = (result.stderr or "")[-500:]
            raise RuntimeError(f"codex CLI 退出码 {result.returncode}: {stderr_tail}")
        try:
            out = Path(tmp_out).read_text(encoding="utf-8", errors="replace").strip()
        except OSError as e:
            raise RuntimeError(f"读 codex 输出文件失败: {e}") from e
        if not out:
            raise RuntimeError("codex CLI 返回空（可能未登录、quota 超限或网络异常）")
        return out
    finally:
        try:
            Path(tmp_out).unlink(missing_ok=True)
        except OSError:
            pass


# ---------------------------------------------------------------------------
# 后端实现：OpenAI 兼容 API（DeepSeek 默认）
# ---------------------------------------------------------------------------

def _call_api(cfg: Config, text: str, system_prompt: str, *, wrap_user_in_xml: bool = True) -> str:
    api_key = cfg.get_api_key()
    if not api_key:
        raise RuntimeError(
            f"环境变量 {cfg.llm.api_key_env} 未设置。"
            "在「设置 → LLM 配置」里填入 key，或切换到 Claude Code / Codex CLI 后端。"
        )

    try:
        from openai import OpenAI
    except ImportError as e:
        raise RuntimeError("openai 包未安装：pip install openai") from e

    client = OpenAI(
        api_key=api_key,
        base_url=cfg.llm.base_url,
        timeout=cfg.llm.timeout_seconds,
    )
    user_content = f"<original_prompt>\n{text}\n</original_prompt>" if wrap_user_in_xml else text
    resp = client.chat.completions.create(
        model=cfg.llm.model,
        max_tokens=cfg.llm.max_tokens,
        temperature=cfg.llm.temperature,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ],
    )
    return (resp.choices[0].message.content or "").strip()


# ---------------------------------------------------------------------------
# Diff 渲染
# ---------------------------------------------------------------------------

def render_diff(a: str, b: str, *, fromfile: str = "原版", tofile: str = "优化版") -> str:
    diff = difflib.unified_diff(
        a.splitlines(keepends=False),
        b.splitlines(keepends=False),
        fromfile=fromfile,
        tofile=tofile,
        lineterm="",
        n=3,
    )
    return "\n".join(diff)


def render_diff_rich(a: str, b: str) -> str:
    diff = render_diff(a, b)
    if not diff:
        return ""
    lines = []
    for line in diff.splitlines():
        if line.startswith("+++") or line.startswith("---"):
            lines.append(f"[bold cyan]{line}[/bold cyan]")
        elif line.startswith("@@"):
            lines.append(f"[yellow]{line}[/yellow]")
        elif line.startswith("+"):
            lines.append(f"[green]{line}[/green]")
        elif line.startswith("-"):
            lines.append(f"[red]{line}[/red]")
        else:
            lines.append(line)
    return "\n".join(lines)
