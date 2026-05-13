"""hook 共用工具：JSON I/O、永不崩溃的日志、ENV 加载。"""

from __future__ import annotations

import datetime as dt
import json
import os
import sys
import traceback
from pathlib import Path
from typing import Any

# Windows 默认 mbcs stdio，写中文会被截断或乱码。所有 hook 入口先把 stdio 切到 UTF-8。
for _stream in (sys.stdin, sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    except Exception:
        pass


def read_input() -> dict:
    """从 stdin 读 hook input JSON；失败返回空 dict。"""
    try:
        raw = sys.stdin.read()
        if not raw.strip():
            return {}
        return json.loads(raw)
    except Exception:
        return {}


def emit_additional_context(hook_event: str, text: str) -> None:
    """通过 stdout 给 CC 注入 system reminder。空文本则什么也不输出。"""
    if not text or not text.strip():
        return
    payload = {
        "hookSpecificOutput": {
            "hookEventName": hook_event,
            "additionalContext": text.strip(),
        }
    }
    json.dump(payload, sys.stdout, ensure_ascii=False)
    sys.stdout.write("\n")
    sys.stdout.flush()


def log_error(vault_path: Path, where: str, exc: BaseException) -> None:
    try:
        logs = vault_path / "logs"
        logs.mkdir(parents=True, exist_ok=True)
        with (logs / "hooks.log").open("a", encoding="utf-8") as f:
            ts = dt.datetime.now().isoformat(timespec="seconds")
            f.write(f"[{ts}] {where}: {type(exc).__name__}: {exc}\n")
            f.write(traceback.format_exc())
            f.write("\n")
    except Exception:
        pass  # 日志都写不动就算了


def safe_main(hook_event: str, runner) -> None:
    """统一的 hook 入口：runner(input_dict) -> str | None；任何异常都吞，永远 exit 0。"""
    # 让 hook 能 import 父包
    pkg_root = Path(__file__).resolve().parents[3]
    if str(pkg_root) not in sys.path:
        sys.path.insert(0, str(pkg_root))

    inp = read_input()
    try:
        from prompt_help.core.config import load_config, load_dotenv_if_present  # noqa: E402
        load_dotenv_if_present()
        cfg = load_config()
    except Exception as e:
        # 配置加载都失败就直接退
        sys.stderr.write(f"prompt-help hook config load failed: {e}\n")
        sys.exit(0)

    try:
        text = runner(inp, cfg)
        if text:
            emit_additional_context(hook_event, text)
    except Exception as e:
        log_error(cfg.vault_path, hook_event, e)

    sys.exit(0)
