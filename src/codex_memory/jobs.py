from __future__ import annotations

import time

from .service import MemoryService


class LayeringJobRunner:
    def __init__(self, service: MemoryService, interval_seconds: int = 10) -> None:
        self.service = service
        self.interval_seconds = interval_seconds

    def run_once(self) -> dict[str, int]:
        return {"created": self.service.process_pending_memories()}

    def run_iterations(self, iterations: int) -> list[dict[str, int]]:
        reports: list[dict[str, int]] = []
        for index in range(iterations):
            reports.append(self.run_once())
            if index < iterations - 1:
                time.sleep(self.interval_seconds)
        return reports

    def run_forever(self) -> None:
        while True:
            self.run_once()
            time.sleep(self.interval_seconds)


class ReflectionJobRunner:
    def __init__(self, service: MemoryService, project_ids: list[str], interval_seconds: int = 3600) -> None:
        self.service = service
        self.project_ids = project_ids
        self.interval_seconds = interval_seconds

    def run_once(self) -> dict[str, dict[str, int]]:
        return {project_id: self.service.run_reflection(project_id) for project_id in self.project_ids}

    def run_iterations(self, iterations: int) -> list[dict[str, dict[str, int]]]:
        reports: list[dict[str, dict[str, int]]] = []
        for index in range(iterations):
            reports.append(self.run_once())
            if index < iterations - 1:
                time.sleep(self.interval_seconds)
        return reports

    def run_forever(self) -> None:
        while True:
            self.run_once()
            time.sleep(self.interval_seconds)
