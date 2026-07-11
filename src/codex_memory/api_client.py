from __future__ import annotations

from typing import Any

import httpx


class MemoryApiClient:
    def __init__(self, base_url: str, bearer_token: str, timeout_seconds: float = 10.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.bearer_token = bearer_token
        self.timeout_seconds = timeout_seconds

    def post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        response = httpx.post(
            f"{self.base_url}{path}",
            json=payload,
            headers={"Authorization": f"Bearer {self.bearer_token}"},
            timeout=self.timeout_seconds,
        )
        response.raise_for_status()
        return response.json()

    def get(self, path: str) -> dict[str, Any]:
        response = httpx.get(
            f"{self.base_url}{path}",
            headers={"Authorization": f"Bearer {self.bearer_token}"},
            timeout=self.timeout_seconds,
        )
        response.raise_for_status()
        return response.json()
