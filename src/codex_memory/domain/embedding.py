from __future__ import annotations

import math
import re
import json
import urllib.request
from collections import Counter
from collections.abc import Mapping, Sequence
from typing import Any, Protocol

TOKEN_RE = re.compile(r"[\w\u4e00-\u9fff]+", re.UNICODE)
Vector = Mapping[str, float] | Sequence[float]


class EmbeddingBackend(Protocol):
    """把文本转换为向量，并计算文本相似度的后端接口。"""

    name: str

    def embed(self, text: str) -> Vector:
        ...

    def similarity(self, left_text: str, right_text: str) -> float:
        ...


class LocalTokenEmbeddingBackend:
    """使用本地词频向量的默认 embedding 后端，便于离线运行和测试。"""

    name = "local-token"

    def embed(self, text: str) -> dict[str, float]:
        counts = Counter(tokenize(text))
        total = sum(counts.values()) or 1
        return {token: count / total for token, count in counts.items()}

    def similarity(self, left_text: str, right_text: str) -> float:
        return cosine_similarity(left_text, right_text, backend=self)


class HttpJsonEmbeddingBackend:
    """通过 HTTP JSON 服务获取稠密 embedding 向量的生产接入后端。"""

    name = "http-json"

    def __init__(
        self,
        endpoint: str,
        api_key: str | None = None,
        model: str | None = None,
        text_field: str = "input",
        vector_path: tuple[str | int, ...] = ("embedding",),
        timeout_seconds: float = 20.0,
    ) -> None:
        self.endpoint = endpoint
        self.api_key = api_key
        self.model = model
        self.text_field = text_field
        self.vector_path = vector_path
        self.timeout_seconds = timeout_seconds

    def embed(self, text: str) -> list[float]:
        payload: dict[str, Any] = {self.text_field: text}
        if self.model is not None:
            payload["model"] = self.model
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        request = urllib.request.Request(
            self.endpoint,
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
            data = json.loads(response.read().decode("utf-8"))
        vector = self._read_path(data, self.vector_path)
        if not isinstance(vector, list) or not all(isinstance(value, (int, float)) for value in vector):
            raise ValueError("embedding response did not contain a numeric vector")
        return [float(value) for value in vector]

    def similarity(self, left_text: str, right_text: str) -> float:
        return cosine_similarity(left_text, right_text, backend=self)

    def _read_path(self, data: Any, path: tuple[str | int, ...]) -> Any:
        value = data
        for key in path:
            value = value[key]
        return value


class CachedEmbeddingBackend:
    """为任意 embedding 后端增加进程内缓存，减少重复向量计算。"""

    def __init__(self, backend: EmbeddingBackend) -> None:
        self.backend = backend
        self.name = f"cached-{backend.name}"
        self._cache: dict[str, Vector] = {}

    def embed(self, text: str) -> Vector:
        if text not in self._cache:
            self._cache[text] = self.backend.embed(text)
        return self._cache[text]

    def similarity(self, left_text: str, right_text: str) -> float:
        return cosine_similarity(left_text, right_text, backend=self)


def tokenize(text: str) -> list[str]:
    return [token.lower() for token in TOKEN_RE.findall(text)]


def vectorize(text: str) -> dict[str, float]:
    return LocalTokenEmbeddingBackend().embed(text)


def cosine_similarity(left_text: str, right_text: str, backend: EmbeddingBackend | None = None) -> float:
    embedding_backend = backend or LocalTokenEmbeddingBackend()
    left = embedding_backend.embed(left_text)
    right = embedding_backend.embed(right_text)
    if not left or not right:
        return 0.0

    dot, left_norm, right_norm = _vector_parts(left, right)
    if left_norm == 0 or right_norm == 0:
        return 0.0
    return dot / (left_norm * right_norm)


def _vector_parts(left: Vector, right: Vector) -> tuple[float, float, float]:
    if isinstance(left, Mapping) and isinstance(right, Mapping):
        common = set(left) & set(right)
        dot = sum(left[token] * right[token] for token in common)
        left_norm = math.sqrt(sum(value * value for value in left.values()))
        right_norm = math.sqrt(sum(value * value for value in right.values()))
        return dot, left_norm, right_norm

    if isinstance(left, Sequence) and isinstance(right, Sequence):
        length = min(len(left), len(right))
        dot = sum(float(left[index]) * float(right[index]) for index in range(length))
        left_norm = math.sqrt(sum(float(value) * float(value) for value in left))
        right_norm = math.sqrt(sum(float(value) * float(value) for value in right))
        return dot, left_norm, right_norm

    raise TypeError("Embedding 向量必须同时为映射或同时为序列")