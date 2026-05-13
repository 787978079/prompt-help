"""自动扫描服务（T6）。

GUI 启动时立即扫一次，之后每 30 分钟（可配）增量扫描所有适配器（CC/Codex 等）。
新候选写入 inbox 而非直接入库；状态栏 + 系统托盘 toast 通知用户。

关键：
- mtime 增量扫描——只看 last_scan_at 之后修改过的会话
- 后台 QThread——不阻塞 UI
- 永不崩 GUI——异常吞到 logs
"""

from __future__ import annotations

import datetime as dt
import json
import traceback
from pathlib import Path

from PySide6.QtCore import QObject, QThread, QTimer, Signal


def _last_scan_path(cfg) -> Path:
    return cfg.vault_path / ".last_scan_at"


def read_last_scan(cfg) -> dt.datetime | None:
    f = _last_scan_path(cfg)
    if not f.is_file():
        return None
    try:
        return dt.datetime.fromisoformat(f.read_text(encoding="utf-8").strip())
    except Exception:
        return None


def write_last_scan(cfg, t: dt.datetime) -> None:
    try:
        _last_scan_path(cfg).write_text(t.isoformat(), encoding="utf-8")
    except Exception:
        pass


class AutoScanWorker(QThread):
    """后台 worker：跑一次增量扫描，把命中候选写到 inbox。"""

    result = Signal(int, dict)  # (new_count, by_source)
    error = Signal(str)

    def __init__(self, cfg, last_scan: dt.datetime | None):
        super().__init__()
        self.cfg = cfg
        self.last_scan = last_scan

    def run(self) -> None:
        try:
            new_count, by_source = self._do_scan()
            self.result.emit(new_count, by_source)
        except Exception as e:
            try:
                logs_dir = self.cfg.vault_path / "logs"
                logs_dir.mkdir(parents=True, exist_ok=True)
                with (logs_dir / "auto_scan.log").open("a", encoding="utf-8") as f:
                    ts = dt.datetime.now().isoformat(timespec="seconds")
                    f.write(f"[{ts}] {type(e).__name__}: {e}\n")
                    f.write(traceback.format_exc())
                    f.write("\n")
            except Exception:
                pass
            self.error.emit(str(e))

    def _do_scan(self) -> tuple[int, dict[str, int]]:
        from ...cli.adapters import all_adapters
        from ...core import quality

        qc = self.cfg.quality
        new_count = 0
        by_source: dict[str, int] = {}

        # 增量门槛：last_scan 之后修改的文件才扫
        cutoff_ts = self.last_scan.timestamp() if self.last_scan else 0

        # inbox 已有内容的 hash 集合（避免同一会话被重写多次）
        inbox_hashes: set[str] = set()
        if self.cfg.inbox_dir.is_dir():
            for f in self.cfg.inbox_dir.glob("*.md"):
                try:
                    inbox_hashes.add(f.stem.split("-")[-1])
                except Exception:
                    continue

        self.cfg.inbox_dir.mkdir(parents=True, exist_ok=True)

        for adapter in all_adapters():
            if not adapter.detect():
                continue
            for raw in adapter.walk():
                if raw.role != "user":
                    continue
                # mtime 过滤
                try:
                    mtime = raw.source_path.stat().st_mtime
                except Exception:
                    mtime = 0
                if mtime <= cutoff_ts:
                    continue
                # quality 过滤
                passed, _reason = quality.is_quality_prompt(raw.text, qc)
                if not passed:
                    continue
                # 写到 inbox（用 hash 去重）
                body = raw.text.strip()
                h = f"{abs(hash(body)) % 100000:05d}"
                if h in inbox_hashes:
                    continue
                inbox_hashes.add(h)
                ts = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%S")
                out = self.cfg.inbox_dir / f"{ts}-{h}.md"
                # 真实 confidence：基于 (1) 与库重合度 (2) 内容长度
                # 之前硬编码 0.6 让 UI 显示假"匹配度"，现在按 stop.py 同款公式算
                from ...core import scoring as _scoring
                confidence = _scoring.compute_confidence(self.cfg, body)
                try:
                    # 入 inbox 时也打 action_tag（规则识别，不调 LLM 避免拖慢扫描）
                    from ...core import action_tags as _at
                    action_tag = _at.rule_classify(body) or ""
                    out.write_text(
                        f"---\n"
                        f"confidence: {confidence}\n"
                        f"suggested_title: {body.splitlines()[0][:50]}\n"
                        f"created: {ts}\n"
                        f"origin: auto_scan\n"
                        f"source: {adapter.name}\n"
                        f"source_project: {raw.source_project}\n"
                        f"action_tag: {action_tag}\n"
                        f"---\n\n{body}\n",
                        encoding="utf-8",
                    )
                    new_count += 1
                    by_source[adapter.name] = by_source.get(adapter.name, 0) + 1
                except Exception:
                    continue

        return new_count, by_source


class AutoScanService(QObject):
    """主线程持有；负责定时调度 + 转发 worker 信号给 MainWindow。"""

    new_candidates = Signal(int, dict)  # (count, by_source)
    failed = Signal(str)

    def __init__(self, cfg, interval_minutes: int = 30):
        super().__init__()
        self.cfg = cfg
        self.interval_minutes = interval_minutes
        self.timer = QTimer(self)
        self.timer.setInterval(interval_minutes * 60 * 1000)
        self.timer.timeout.connect(self.scan_now)
        self._worker: AutoScanWorker | None = None
        self._last_scan_at = read_last_scan(cfg)

    def start(self) -> None:
        # 启动时立即扫一次
        self.scan_now()
        self.timer.start()

    def stop(self) -> None:
        self.timer.stop()

    def scan_now(self) -> None:
        if self._worker and self._worker.isRunning():
            return
        self._worker = AutoScanWorker(self.cfg, self._last_scan_at)
        self._worker.result.connect(self._on_done)
        self._worker.error.connect(self.failed)
        self._worker.start()

    def _on_done(self, count: int, by_source: dict) -> None:
        now = dt.datetime.now(dt.timezone.utc)
        write_last_scan(self.cfg, now)
        self._last_scan_at = now
        if count > 0:
            self.new_candidates.emit(count, by_source)
