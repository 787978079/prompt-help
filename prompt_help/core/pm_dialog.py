"""PM-Mode 对话引擎（Phase 7 重写）。

设计原则：
- 单一对话窗口，不是 7 阶段固定表单。
- 每轮调 LLM 用完整对话历史 + 三维度（What/Why/How）评分生成下一题。
- 苏格拉底式反问：不给建议，只反问让用户思考。
- 信息饱和度评分（每维度 0-10）≥7 即自动停止；最少 3 轮、最多 15 轮硬约束。
- 用户可编辑前序回答 → 触发后续重生成。
- 信息够时一次性生成 4 件套：brief / user stories / risks / decisions。

LLM 调用：复用 optimizer 双后端（CC CLI 优先、OpenAI 兼容 API fallback），
但走自己的 chat completion 路径（多消息），不复用 optimizer 的 single-text 路径。
"""

from __future__ import annotations

import datetime as dt
import json
import shutil
from dataclasses import dataclass, field
from typing import Optional

from . import proc
from .config import Config


# ---------------------------------------------------------------------------
# System prompts
# ---------------------------------------------------------------------------

INTERVIEW_SYSTEM_PROMPT = """\
你是资深产品经理，用**苏格拉底式访谈**帮一位 vibecoder 想清楚要建什么产品。

# 角色心法

**苏格拉底问答的核心**：你**永远不直接给建议**。你只问。你的问题让对方自己说出答案，
而不是你说出答案让对方点头。

错误示范（不要这样）：「你应该考虑用户的真实痛点」
正确示范（这样做）：「你刚说"用户希望提醒功能"，那他们目前用什么提醒？为什么失败？」

# 评分三维度（0-10）

- **What**（产品定义）：核心场景、典型用户画像、触发时刻
  - 0：只说"做个 app"这种泛词
  - 5：知道用户类型 + 一个具体场景
  - 8：用户画像 + 触发时刻 + 一句话能说清"X 时给 Y 用的 Z"
  - 10：上述都有 + 至少 2 个 user story

- **Why**（用户痛点 / 独特价值）
  - 0：纯"我觉得有意思"
  - 5：知道一个痛点
  - 8：痛点具体 + 已有 1-2 个竞品对比 + 差异点
  - 10：用户痛点 + 替代成本 + Unlike X, this___ 造句

- **How**（技术可行性 / 最大风险）
  - 0：完全没考虑
  - 5：知道大致技术栈
  - 8：识别了 1-2 个具体技术风险（性能、数据、集成）
  - 10：上述都有 + MVP 切片 + Killer risk

# 每轮任务

1. 阅读完整对话历史，理解用户最新一条
2. 给三维度打分
3. 三维度都 ≥ 7 → ready=true，next_question 空
4. 否则找**最弱维度**，生成 ≤ 60 字针对性反问：
   - 不要给建议，只反问
   - 必须含具体名词（用户提到的产品名 / 技术 / 角色）
   - 不要哲学问题（不要"为什么这件事对你重要"）
   - 要求"对比"或"举例"或"假设"

# few-shot 示例（按真实质量调过）

## 示例 1

历史：
- 用户：「想做截止日期提醒 app，帮自由职业工程师追踪客户项目」

正确输出：
{"scores":{"what":6,"why":3,"how":2}, "next_question":"工程师已经在用 Google Calendar / Linear / Notion，你这 app 解决了它们解决不了的什么具体痛点？", "ready":false}

## 示例 2

历史：
- 用户：「Calendar 不能按客户归类，多客户混在一个时间线很乱」

正确输出：
{"scores":{"what":7,"why":7,"how":3}, "next_question":"客户-项目-任务三层数据模型，你最担心的技术难点是离线同步、UI 流畅度、还是多设备一致性？", "ready":false}

## 示例 3

历史：
- 用户：「我想做个 prompt 管理工具」
- 你：「prompt 散落是程序员普遍的痛？还是某个特定群体？」
- 用户：「主要是写 Claude Code 多项目的开发者，5+ 项目时上下文丢失严重」

正确输出：
{"scores":{"what":7,"why":5,"how":2}, "next_question":"这些开发者现在怎么应对？复制粘贴？还是用 Notion 笔记？这个替代行为为什么不够好？", "ready":false}

## 示例 4（信息已饱和）

历史：（用户已经讨论了 5 轮，What/Why/How 都清晰）

正确输出：
{"scores":{"what":8,"why":7,"how":7}, "next_question":"", "ready":true}

# 输出格式（极其重要）

**绝对禁止**：
- 直接输出反问文字（你必须包在 JSON 里）
- markdown 代码块包裹
- 前缀解释（"以下是 JSON："）
- 输出多个 JSON

**唯一允许**的输出格式：

{"scores":{"what":N,"why":N,"how":N}, "next_question":"<60 字反问>", "ready":false}

或饱和时：

{"scores":{"what":N,"why":N,"how":N}, "next_question":"", "ready":true}

第一个字符必须是 `{`，最后一个字符必须是 `}`。
"""


BRIEF_GENERATION_SYSTEM_PROMPT = """\
你是资深产品经理。根据下面这段产品访谈对话，生成 4 个 markdown 文档（用 JSON 包装）。

要求：
- 严格基于对话内容，**不要编造**用户没说过的细节
- 中文输出
- 每个文档独立完整、可直接放进项目根目录用

输出 JSON schema：
{
  "brief": "PRODUCT_BRIEF.md 完整 markdown 内容",
  "user_stories": "USER_STORIES.md 完整 markdown 内容",
  "risks": "RISKS.md 完整 markdown 内容",
  "decisions": "DECISIONS.md 完整 markdown 内容"
}

## brief 结构（PRODUCT_BRIEF.md）

```
# <一句话产品名 / 定位>

## 一句话定位
<X 时给 Y 用的 Z>

## 用户与痛点
- **典型用户**：
- **触发时刻**：
- **当前如何解决**：
- **未解决的痛点**：

## 独特价值
Unlike <竞品>，this <差异点>。

## MVP 范围
- **IN**：
- **LATER**：
- **NEVER**：

## 成功指标
<KPI + 验证方式>
```

## user_stories 结构（USER_STORIES.md）

5-10 条 user story，每条：
```
### US-N：<标题>
As a <用户角色>, I want to <行为>, so that <价值>.

**Acceptance Criteria**：
- ...
```

## risks 结构（RISKS.md）

```
# 风险清单

## 技术风险
- **<具体风险>**：影响 / 缓解
- ...

## 市场风险
- ...

## 运营风险
- ...

## Killer Risk
<最大的一条 + 验证方式>
```

## decisions 结构（DECISIONS.md）

```
# 关键决策日志

## D1：<决策点>
**问题**：
**选择**：
**理由**：
**替代方案**：

## D2：...
```

只输出 JSON，不要 markdown 包裹整个 JSON。
"""


# ---------------------------------------------------------------------------
# Session 数据
# ---------------------------------------------------------------------------

@dataclass
class PMSession:
    slug: str
    idea: str
    history: list[dict] = field(default_factory=list)  # [{role: "user"|"assistant", content: str}, ...]
    scores: dict = field(default_factory=lambda: {"what": 0, "why": 0, "how": 0})
    turn_count: int = 0
    ready: bool = False
    created: str = field(default_factory=lambda: dt.datetime.now(dt.timezone.utc).isoformat())
    updated: str = field(default_factory=lambda: dt.datetime.now(dt.timezone.utc).isoformat())
    cwd: str = ""

    def touch(self) -> None:
        self.updated = dt.datetime.now(dt.timezone.utc).isoformat()

    def append_user(self, text: str) -> None:
        self.history.append({"role": "user", "content": text})
        self.touch()

    def append_assistant(self, text: str) -> None:
        self.history.append({"role": "assistant", "content": text})
        self.touch()

    def edit_user_at(self, index: int, new_text: str) -> None:
        """改第 index 条 user 消息，后续所有 assistant 反问失效（移除）。"""
        if index < 0 or index >= len(self.history):
            raise IndexError(f"history index out of range: {index}")
        if self.history[index]["role"] != "user":
            raise ValueError(f"index {index} is not a user message")
        self.history[index]["content"] = new_text
        self.history = self.history[: index + 1]
        # turn_count 重新计数：user 消息条数
        self.turn_count = sum(1 for m in self.history if m["role"] == "user")
        self.ready = False
        self.touch()


# ---------------------------------------------------------------------------
# 公开 API：next_question / generate_brief_bundle
# ---------------------------------------------------------------------------

MIN_TURNS = 3
MAX_TURNS = 15
SATURATION_THRESHOLD = 7


def next_question(cfg: Config, session: PMSession) -> dict:
    """根据当前 session 历史调 LLM 生成下一题 + 三维度评分。

    返回：{"scores": {...}, "next_question": str, "ready": bool, "error": Optional[str]}
    """
    if session.turn_count >= MAX_TURNS:
        return {
            "scores": session.scores,
            "next_question": "",
            "ready": True,
            "error": None,
            "stop_reason": "max_turns_reached",
        }

    history_text = _format_history_for_llm(session)
    try:
        raw = _llm_chat(
            cfg,
            system_prompt=INTERVIEW_SYSTEM_PROMPT,
            user_content=history_text,
        )
    except Exception as e:
        return {
            "scores": session.scores,
            "next_question": "",
            "ready": False,
            "error": f"{type(e).__name__}: {e}",
        }

    parsed = _parse_interview_response(raw)
    if parsed is None:
        # A3 Fallback：LLM 没按 JSON 输出但返了一段反问文本。
        # 改进：scores 按 turn_count 估算（每轮 +1 到三维度，避免永远卡 1/10）。
        clean = (raw or "").strip()
        if clean:
            estimated_floor = min(session.turn_count, 8)  # 上限 8，留余地
            cur = dict(session.scores)
            for k in ("what", "why", "how"):
                cur[k] = max(cur.get(k, 0), estimated_floor)
            return {
                "scores": cur,
                "next_question": clean[:800],
                "ready": False,
                "error": None,
            }
        return {
            "scores": session.scores,
            "next_question": "",
            "ready": False,
            "error": "LLM 返回空内容。检查后端可达性。",
        }

    session.scores = parsed.get("scores") or session.scores

    forced_ready = (
        all(session.scores.get(k, 0) >= SATURATION_THRESHOLD for k in ("what", "why", "how"))
        and session.turn_count >= MIN_TURNS
    )
    ready = bool(parsed.get("ready")) or forced_ready
    if session.turn_count < MIN_TURNS:
        ready = False  # 强制至少跑 3 轮

    session.ready = ready
    q = parsed.get("next_question", "") if not ready else ""
    return {
        "scores": session.scores,
        "next_question": q,
        "ready": ready,
        "error": None,
    }


def generate_brief_bundle(cfg: Config, session: PMSession) -> dict:
    """生成 4 件套 markdown。返回 {brief, user_stories, risks, decisions, error?}。"""
    history_text = _format_history_for_llm(session)
    try:
        raw = _llm_chat(
            cfg,
            system_prompt=BRIEF_GENERATION_SYSTEM_PROMPT,
            user_content=f"产品想法：{session.idea}\n\n访谈对话：\n{history_text}",
        )
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}

    parsed = _parse_brief_response(raw)
    if parsed is None:
        return {"error": f"LLM 返回无法解析为 4 件套 JSON：{raw[:300]}"}

    for key in ("brief", "user_stories", "risks", "decisions"):
        parsed.setdefault(key, f"# {key}\n\n（生成失败，待手动补充）\n")
    parsed["error"] = None
    return parsed


# ---------------------------------------------------------------------------
# LLM chat 调用（多消息，区别于 optimizer 的 single-text 路径）
# ---------------------------------------------------------------------------

def _format_history_for_llm(session: PMSession) -> str:
    """把对话历史拍平成 LLM 可读的文本。"""
    lines = [f"## 产品想法\n{session.idea}", "", "## 对话历史"]
    for m in session.history:
        role = "用户" if m["role"] == "user" else "你（产品经理）"
        lines.append(f"\n### {role}\n{m['content']}")
    lines.append(f"\n## 当前轮数：{session.turn_count}")
    return "\n".join(lines)


def _llm_chat(cfg: Config, *, system_prompt: str, user_content: str) -> str:
    """调 LLM。CC CLI 优先、OpenAI 兼容 API fallback。"""
    cli_path = shutil.which(cfg.optimizer.cc_cli_path) if cfg.optimizer.prefer_cc_cli else None
    if cli_path:
        return _llm_chat_cc_cli(cfg, cli_path, system_prompt, user_content)
    return _llm_chat_api(cfg, system_prompt, user_content)


def _llm_chat_cc_cli(cfg: Config, cli_path: str, system_prompt: str, user_content: str) -> str:
    cmd = [
        cli_path,
        "-p",
        "--output-format", "json",
        "--max-turns", "1",
        "--no-session-persistence",
        "--append-system-prompt", system_prompt,
    ]
    result = proc.run(
        cmd,
        input=user_content,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=cfg.optimizer.cc_cli_timeout_seconds,
    )
    if result.returncode != 0:
        raise RuntimeError(f"claude CLI 退出码 {result.returncode}: {result.stderr[:500]}")
    raw = result.stdout.strip()
    if not raw:
        raise RuntimeError("claude CLI 返回空 stdout")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return raw
    if isinstance(data, dict):
        if isinstance(data.get("result"), str):
            return data["result"]
        if isinstance(data.get("content"), str):
            return data["content"]
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
    raise RuntimeError(f"claude CLI 返回无法解析：{raw[:200]}")


def _llm_chat_api(cfg: Config, system_prompt: str, user_content: str) -> str:
    api_key = cfg.get_api_key()
    if not api_key:
        raise RuntimeError(f"环境变量 {cfg.llm.api_key_env} 未设置（CC CLI 也不可用，无可用后端）")
    try:
        from openai import OpenAI
    except ImportError as e:
        raise RuntimeError("openai 包未安装：pip install openai") from e
    client = OpenAI(
        api_key=api_key,
        base_url=cfg.llm.base_url,
        timeout=cfg.llm.timeout_seconds,
    )
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
# 响应解析
# ---------------------------------------------------------------------------

def _extract_json_block(text: str) -> Optional[str]:
    """从可能含 markdown 包裹的文本里抽出第一个完整 JSON 对象。

    策略：直接定位第一个 `{` 和最后一个 `}`——LLM 输出常带 ```json 包裹，但只要
    JSON 对象内部不含未转义的 `{` `}`，这个范围就是完整 JSON。
    """
    if not text:
        return None
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        return None
    return text[start : end + 1]


def _parse_interview_response(raw: str) -> Optional[dict]:
    block = _extract_json_block(raw)
    if not block:
        return None
    try:
        data = json.loads(block)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    scores = data.get("scores") or {}
    if not isinstance(scores, dict):
        return None
    normalized_scores = {}
    for k in ("what", "why", "how"):
        try:
            normalized_scores[k] = max(0, min(10, int(scores.get(k, 0))))
        except (TypeError, ValueError):
            normalized_scores[k] = 0
    return {
        "scores": normalized_scores,
        "next_question": str(data.get("next_question") or "").strip(),
        "ready": bool(data.get("ready")),
    }


def _parse_brief_response(raw: str) -> Optional[dict]:
    block = _extract_json_block(raw)
    if not block:
        return None
    try:
        data = json.loads(block)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    return {
        "brief": str(data.get("brief") or ""),
        "user_stories": str(data.get("user_stories") or ""),
        "risks": str(data.get("risks") or ""),
        "decisions": str(data.get("decisions") or ""),
    }
