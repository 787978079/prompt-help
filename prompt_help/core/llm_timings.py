"""LLM 调用耗时记录 + ETA 估算。

为什么需要：LLM 调用是黑盒，没有真实进度信号；但同一个 (backend, kind) 的耗时分布
比较稳定。用最近 N 次平均值 + 50% 缓冲做 ETA，再让 UI 进度条按 elapsed/est 推进，
比 marquee busy 动画更有信息量。

存储：`~/.prompt-help/llm_timings.json`，schema：
    {"<backend>:<kind>": [seconds, seconds, ...]}  # 最近 20 次
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from .config import Config

_FILE = "llm_timings.json"
_KEEP = 20  # 保留每个 key 的最近 N 次

# 没有历史数据时的兜底估算（秒）
_DEFAULT_ETA: dict[str, float] = {
    "cc_cli:optimize": 18.0,        # claude -p 单条改写
    "cc_cli:generate_title": 8.0,
    "cc_cli:project_optimize": 25.0,
    "cc_cli:test": 12.0,
    "codex_cli:optimize": 12.0,
    "codex_cli:generate_title": 7.0,
    "codex_cli:project_optimize": 18.0,
    "codex_cli:test": 10.0,
    "api:optimize": 6.0,
    "api:generate_title": 4.0,
    "api:project_optimize": 10.0,
    "api:test": 5.0,
}


def _path(cfg: Config) -> Path:
    return cfg.vault_path / _FILE


def _load(cfg: Config) -> dict[str, list[float]]:
    p = _path(cfg)
    if not p.is_file():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _save(cfg: Config, data: dict[str, list[float]]) -> None:
    try:
        _path(cfg).write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except OSError:
        pass


def record(cfg: Config, backend: str, kind: str, seconds: float) -> None:
    """记录一次调用耗时。kind 用语义化字符串：optimize / generate_title /
    project_optimize / test 等。"""
    if seconds <= 0 or seconds > 600:
        return
    key = f"{backend}:{kind}"
    data = _load(cfg)
    arr = data.get(key, [])
    arr.append(round(seconds, 2))
    data[key] = arr[-_KEEP:]
    _save(cfg, data)


def estimate(cfg: Config, backend: str, kind: str) -> float:
    """估算下一次调用的耗时（秒）。

    没历史 → 用 _DEFAULT_ETA 默认值
    有历史 → 平均值 × 1.2（20% 缓冲，避免 progress 跑太快撞 95% 卡死）
    """
    key = f"{backend}:{kind}"
    data = _load(cfg)
    arr = data.get(key, [])
    if not arr:
        return _DEFAULT_ETA.get(key, 15.0)
    avg = sum(arr) / len(arr)
    return max(2.0, avg * 1.2)


def stats(cfg: Config) -> dict[str, dict[str, float]]:
    """返回每个 (backend, kind) 的 count / min / max / avg，供 settings / 调试用。"""
    data = _load(cfg)
    out: dict[str, dict[str, float]] = {}
    for key, arr in data.items():
        if not arr:
            continue
        out[key] = {
            "count": len(arr),
            "min": min(arr),
            "max": max(arr),
            "avg": round(sum(arr) / len(arr), 2),
        }
    return out
