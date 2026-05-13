"""embedding 检索升级框架（V2 启用）。

设计原则：
- vendor-agnostic：通过 OpenAI 兼容 API（DeepSeek / OpenAI / Voyage / Together 都支持）
- 可选本地后端：sentence-transformers（无网时用，需额外装 torch）
- 内容 hash 缓存：同一段文字不重算
- SQLite BLOB 存向量（np.float32 序列化）

接入逻辑（待 indexer.search 改造时启用）：
  1. 用户查询 → embed → 余弦匹配 top 50 → 与 FTS5 结果合并 → 重排 top 10
  2. 默认 enabled=False；库到 100+ 条且用户在「设置」开启后才用

CLI：
  prompt-help embed compute        # 给所有提示词算 embedding 入库
  prompt-help embed query <text>   # 测试语义检索
  prompt-help embed status         # 看进度
"""

from __future__ import annotations

import hashlib
import os
from abc import ABC, abstractmethod
from typing import Iterable

from .config import Config


# ---------------------------------------------------------------------------
# 抽象后端
# ---------------------------------------------------------------------------

class EmbeddingBackend(ABC):
    """生成 embedding 的后端。"""

    @abstractmethod
    def embed(self, texts: list[str]) -> list[list[float]]:
        """批量 embed；返回 [vec, vec, ...]。"""

    @property
    @abstractmethod
    def dim(self) -> int:
        """向量维度。"""


# ---------------------------------------------------------------------------
# OpenAI 兼容（DeepSeek / OpenAI / Voyage / Together 等）
# ---------------------------------------------------------------------------

class OpenAICompatBackend(EmbeddingBackend):
    """所有 OpenAI 兼容 endpoint 都能用。

    DeepSeek 当前（2026-05）embedding 端点支持情况待用户启用时验证；
    若 DeepSeek 不支持，可改 base_url 到 OpenAI / Voyage 等任何兼容 vendor。
    """

    def __init__(self, cfg: Config):
        self.cfg = cfg
        api_key = os.environ.get(cfg.embedding.api_key_env)
        if not api_key:
            raise RuntimeError(f"环境变量 {cfg.embedding.api_key_env} 未设置")
        from openai import OpenAI  # 延迟 import
        self._client = OpenAI(
            api_key=api_key,
            base_url=cfg.embedding.base_url,
        )
        self._model = cfg.embedding.model
        self._dim = cfg.embedding.dim

    @property
    def dim(self) -> int:
        return self._dim

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        resp = self._client.embeddings.create(
            model=self._model,
            input=texts,
        )
        return [d.embedding for d in resp.data]


# ---------------------------------------------------------------------------
# 本地 sentence-transformers
# ---------------------------------------------------------------------------

class LocalSentenceTransformersBackend(EmbeddingBackend):
    """全本地，零 API 费用；但首次 import torch 慢、占盘 500MB+。"""

    def __init__(self, cfg: Config):
        self.cfg = cfg
        try:
            from sentence_transformers import SentenceTransformer  # type: ignore
        except ImportError as e:
            raise RuntimeError(
                "本地后端需要 sentence-transformers + torch："
                "pip install sentence-transformers"
            ) from e
        # 中英双语小模型（默认，可改）
        model_name = cfg.embedding.model or "paraphrase-multilingual-MiniLM-L12-v2"
        self._model = SentenceTransformer(model_name)
        self._dim = self._model.get_sentence_embedding_dimension() or 384

    @property
    def dim(self) -> int:
        return self._dim

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        vecs = self._model.encode(texts, normalize_embeddings=True)
        return [v.tolist() for v in vecs]


# ---------------------------------------------------------------------------
# 工厂
# ---------------------------------------------------------------------------

def get_backend(cfg: Config) -> EmbeddingBackend:
    """按 cfg.embedding.provider 选后端。"""
    provider = cfg.embedding.provider.lower()
    if provider in ("openai_compat", "openai", "deepseek", "voyage"):
        return OpenAICompatBackend(cfg)
    if provider in ("local_st", "local", "sentence_transformers"):
        return LocalSentenceTransformersBackend(cfg)
    raise ValueError(f"未知 embedding provider: {cfg.embedding.provider}")


# ---------------------------------------------------------------------------
# 工具：内容 hash + 余弦相似度
# ---------------------------------------------------------------------------

def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def cosine(a: list[float], b: list[float]) -> float:
    """余弦相似度（向量已归一化时退化为点积）。"""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(x * x for x in b) ** 0.5
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


# ---------------------------------------------------------------------------
# SQLite 持久化（待 indexer 加 embedding_vec 列后接入）
# ---------------------------------------------------------------------------

def vec_to_bytes(vec: list[float]) -> bytes:
    """float32 little-endian 序列化。"""
    import struct
    return struct.pack(f"<{len(vec)}f", *vec)


def vec_from_bytes(blob: bytes) -> list[float]:
    import struct
    n = len(blob) // 4
    return list(struct.unpack(f"<{n}f", blob))


def chunked(iterable: Iterable, n: int) -> Iterable[list]:
    batch: list = []
    for x in iterable:
        batch.append(x)
        if len(batch) >= n:
            yield batch
            batch = []
    if batch:
        yield batch
