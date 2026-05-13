"""配置加载：~/.prompt-help/config.toml + 环境变量覆盖。

设计原则：
- API key 永远从环境变量读，绝不写入 config.toml
- 配置加载失败时返回默认值，永不崩溃（hooks 必须能跑）
- vault_path 可被 PROMPT_HELP_VAULT_PATH 覆盖（测试与多账号场景）
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib

import tomli_w


def default_vault_path() -> Path:
    override = os.environ.get("PROMPT_HELP_VAULT_PATH")
    if override:
        return Path(override).expanduser().resolve()
    return Path.home() / ".prompt-help"


@dataclass
class LLMConfig:
    provider: str = "deepseek"
    api_key_env: str = "DEEPSEEK_API_KEY"
    base_url: str = "https://api.deepseek.com"
    model: str = "deepseek-chat"
    max_tokens: int = 2048
    temperature: float = 0.3
    timeout_seconds: int = 30


@dataclass
class MiningConfig:
    enabled: bool = True
    min_chars: int = 200
    max_chars: int = 4000
    dedup_overlap_threshold: float = 0.6
    success_signals: list[str] = field(
        default_factory=lambda: [
            "perfect", "works", "exactly", "ship it", "thanks", "great",
            "搞定", "可以", "对的", "完美", "搞定了", "没问题", "牛", "赞",
        ]
    )


@dataclass
class GitConfig:
    auto_commit: bool = True
    auto_push: bool = False  # init 时若用户开私仓会切为 True
    remote_name: str = "origin"
    remote_url: str | None = None
    commit_user_name: str = "prompt-help"
    commit_user_email: str = "prompt-help@local"


@dataclass
class TrapRecallConfig:
    enabled: bool = True
    max_traps_per_message: int = 2  # 同一条消息最多注入 N 条 trap，避免刷屏


@dataclass
class QualityConfig:
    """挖掘候选的质量过滤配置。字段需与 core/quality.py 的 QualityConfig 保持同步。"""
    # 实测发现 both 让大量纯英文文档碎片入库；中文用户应默认 zh
    language_preference: str = "zh"      # zh | en | both
    min_chars: int = 100
    max_chars: int = 4000
    inter_dedupe_token: float = 0.55
    inter_dedupe_seq_ratio: float = 0.80
    db_dedupe_token: float = 0.60
    mojibake_strict: bool = True
    # 拒"文档碎片"（AGENTS.md / 编码规范章节）。默认开。
    reject_doc_fragments: bool = True


@dataclass
class OptimizerConfig:
    """LLM 后端选择。三选一优先级走 backend 字段；旧 prefer_cc_cli 保留兼容。

    Phase 22：加入 Codex CLI（`codex exec`）作为第三个后端，让 OpenAI
    订阅用户不必额外配 DeepSeek key 也能享受所有 LLM 功能。
    """
    # Phase 22：新版优先用 backend 字段（auto | cc_cli | codex_cli | api）
    # "auto" 时按可用性挑：CC CLI 在 PATH > Codex CLI 在 PATH > API key 存在
    backend: str = "auto"

    # CC CLI
    prefer_cc_cli: bool = True          # 旧字段，仅当 backend="auto" 时影响 CC CLI 排序
    cc_cli_path: str = "claude"
    cc_cli_timeout_seconds: int = 120

    # Codex CLI（Phase 22）
    codex_cli_path: str = "codex"
    codex_cli_timeout_seconds: int = 180  # codex agent 启动比 claude 略慢

    auto_generalize_on_save: bool = True


@dataclass
class PublicLibraryConfig:
    """推荐库行为（Phase 21）。

    中文用户偏好"卡片直接出中文"，所以刷完源后默认自动翻译落缓存；
    关掉的话回到 AIPRM Output Language 模式（按需翻译）。
    """
    auto_translate_on_refresh: bool = True
    # 自动翻译时最大并发条数：每条 ~10s，太高会被 LLM 后端限流
    auto_translate_max_items: int = 100


@dataclass
class EmbeddingConfig:
    """embedding 检索框架（V2 启用；默认关）。"""
    enabled: bool = False                   # 库 100+ 条后再开
    provider: str = "openai_compat"         # openai_compat | local_st
    base_url: str = "https://api.deepseek.com"
    model: str = "deepseek-embedding"       # 用户启用时根据 vendor 改
    api_key_env: str = "DEEPSEEK_API_KEY"   # 用同一份 API key
    dim: int = 1024
    batch_size: int = 32
    cache_by_hash: bool = True


@dataclass
class Config:
    vault_path: Path = field(default_factory=default_vault_path)
    llm: LLMConfig = field(default_factory=LLMConfig)
    mining: MiningConfig = field(default_factory=MiningConfig)
    git: GitConfig = field(default_factory=GitConfig)
    trap_recall: TrapRecallConfig = field(default_factory=TrapRecallConfig)
    quality: QualityConfig = field(default_factory=QualityConfig)
    optimizer: OptimizerConfig = field(default_factory=OptimizerConfig)
    public_library: PublicLibraryConfig = field(default_factory=PublicLibraryConfig)
    embedding: EmbeddingConfig = field(default_factory=EmbeddingConfig)

    @property
    def config_file(self) -> Path:
        return self.vault_path / "config.toml"

    @property
    def prompts_dir(self) -> Path:
        return self.vault_path / "prompts"

    @property
    def index_db(self) -> Path:
        return self.vault_path / "index.sqlite"

    @property
    def inbox_dir(self) -> Path:
        return self.vault_path / "inbox"

    @property
    def briefs_dir(self) -> Path:
        return self.vault_path / "briefs"

    @property
    def pulse_dir(self) -> Path:
        return self.vault_path / "pulse"

    @property
    def logs_dir(self) -> Path:
        return self.vault_path / "logs"

    def get_api_key(self) -> str | None:
        return os.environ.get(self.llm.api_key_env)


def load_config(vault_path: Path | None = None) -> Config:
    """从 vault_path/config.toml 加载；不存在则返回默认值。"""
    cfg = Config(vault_path=vault_path or default_vault_path())
    f = cfg.config_file
    if not f.is_file():
        return cfg
    try:
        with f.open("rb") as fp:
            raw = tomllib.load(fp)
        _apply_dict(cfg, raw)
    except Exception:
        # 配置文件坏了也别让 hook 崩，默认值兜底
        pass
    return cfg


def save_config(cfg: Config) -> None:
    """落盘 config.toml。"""
    cfg.vault_path.mkdir(parents=True, exist_ok=True)
    data = {
        "llm": {
            "provider": cfg.llm.provider,
            "api_key_env": cfg.llm.api_key_env,
            "base_url": cfg.llm.base_url,
            "model": cfg.llm.model,
            "max_tokens": cfg.llm.max_tokens,
            "temperature": cfg.llm.temperature,
            "timeout_seconds": cfg.llm.timeout_seconds,
        },
        "mining": {
            "enabled": cfg.mining.enabled,
            "min_chars": cfg.mining.min_chars,
            "max_chars": cfg.mining.max_chars,
            "dedup_overlap_threshold": cfg.mining.dedup_overlap_threshold,
            "success_signals": cfg.mining.success_signals,
        },
        "git": {
            "auto_commit": cfg.git.auto_commit,
            "auto_push": cfg.git.auto_push,
            "remote_name": cfg.git.remote_name,
            "remote_url": cfg.git.remote_url or "",
            "commit_user_name": cfg.git.commit_user_name,
            "commit_user_email": cfg.git.commit_user_email,
        },
        "trap_recall": {
            "enabled": cfg.trap_recall.enabled,
            "max_traps_per_message": cfg.trap_recall.max_traps_per_message,
        },
        "quality": {
            "language_preference": cfg.quality.language_preference,
            "min_chars": cfg.quality.min_chars,
            "max_chars": cfg.quality.max_chars,
            "inter_dedupe_token": cfg.quality.inter_dedupe_token,
            "inter_dedupe_seq_ratio": cfg.quality.inter_dedupe_seq_ratio,
            "db_dedupe_token": cfg.quality.db_dedupe_token,
            "mojibake_strict": cfg.quality.mojibake_strict,
            "reject_doc_fragments": cfg.quality.reject_doc_fragments,
        },
        "optimizer": {
            "backend": cfg.optimizer.backend,
            "prefer_cc_cli": cfg.optimizer.prefer_cc_cli,
            "cc_cli_path": cfg.optimizer.cc_cli_path,
            "cc_cli_timeout_seconds": cfg.optimizer.cc_cli_timeout_seconds,
            "codex_cli_path": cfg.optimizer.codex_cli_path,
            "codex_cli_timeout_seconds": cfg.optimizer.codex_cli_timeout_seconds,
            "auto_generalize_on_save": cfg.optimizer.auto_generalize_on_save,
        },
        "public_library": {
            "auto_translate_on_refresh": cfg.public_library.auto_translate_on_refresh,
            "auto_translate_max_items": cfg.public_library.auto_translate_max_items,
        },
        "embedding": {
            "enabled": cfg.embedding.enabled,
            "provider": cfg.embedding.provider,
            "base_url": cfg.embedding.base_url,
            "model": cfg.embedding.model,
            "api_key_env": cfg.embedding.api_key_env,
            "dim": cfg.embedding.dim,
            "batch_size": cfg.embedding.batch_size,
            "cache_by_hash": cfg.embedding.cache_by_hash,
        },
    }
    with cfg.config_file.open("wb") as fp:
        tomli_w.dump(data, fp)


def _apply_dict(cfg: Config, raw: dict) -> None:
    """把 toml 字典合并进 cfg，未知字段忽略。"""
    if "llm" in raw:
        for k, v in raw["llm"].items():
            if hasattr(cfg.llm, k):
                setattr(cfg.llm, k, v)
    if "mining" in raw:
        for k, v in raw["mining"].items():
            if hasattr(cfg.mining, k):
                setattr(cfg.mining, k, v)
    if "git" in raw:
        for k, v in raw["git"].items():
            if hasattr(cfg.git, k):
                setattr(cfg.git, k, v if v != "" else None)
    if "trap_recall" in raw:
        for k, v in raw["trap_recall"].items():
            if hasattr(cfg.trap_recall, k):
                setattr(cfg.trap_recall, k, v)
    if "quality" in raw:
        for k, v in raw["quality"].items():
            if hasattr(cfg.quality, k):
                setattr(cfg.quality, k, v)
    if "optimizer" in raw:
        for k, v in raw["optimizer"].items():
            if hasattr(cfg.optimizer, k):
                setattr(cfg.optimizer, k, v)
    if "public_library" in raw:
        for k, v in raw["public_library"].items():
            if hasattr(cfg.public_library, k):
                setattr(cfg.public_library, k, v)
    if "embedding" in raw:
        for k, v in raw["embedding"].items():
            if hasattr(cfg.embedding, k):
                setattr(cfg.embedding, k, v)


def load_dotenv_if_present(start_dir: Path | None = None) -> None:
    """简版 .env 加载：从 vault / 源码目录 / cwd 找 .env，注入 os.environ（已存在的不覆盖）。

    优先级（按顺序找到第一个存在的就用）：
    1. start_dir/.env （显式指定）
    2. vault_path/.env （生产部署的标准位置）
    3. cwd/.env
    4. 源码目录/.env （开发时）
    """
    candidates: list[Path] = []
    if start_dir:
        candidates.append(start_dir / ".env")
    candidates.append(default_vault_path() / ".env")
    candidates.append(Path.cwd() / ".env")
    candidates.append(Path(__file__).resolve().parents[2] / ".env")
    for f in candidates:
        if f.is_file():
            try:
                for line in f.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    k, _, v = line.partition("=")
                    k = k.strip()
                    v = v.strip().strip('"').strip("'")
                    if k and k not in os.environ:
                        os.environ[k] = v
            except Exception:
                pass
            return
