from __future__ import annotations

import threading
from queue import Queue

from .classifier import MemoryClassifier
from .models import Layer
from .storage import MemoryStore


class LayeringProcessor:
    def __init__(self, store: MemoryStore, classifier: MemoryClassifier | None = None) -> None:
        self.store = store
        self.classifier = classifier or MemoryClassifier()
        self._queue: Queue[str] = Queue()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def process_project(self, project_id: str) -> int:
        job_ids = self.store.mark_layering_jobs_running(project_id)
        raw_log_ids = self.store.list_job_raw_log_ids(job_ids)
        logs = self.store.list_raw_logs_by_ids(project_id=project_id, ids=raw_log_ids, unprocessed_only=True)
        try:
            created = 0
            for log in logs:
                items = self.classifier.classify([log])
                for item in items:
                    self.store.upsert_memory(
                        project_id=item.project_id,
                        layer=item.layer,
                        title=item.title,
                        body=item.body,
                        tags=item.tags,
                        memory_type=item.memory_type,
                        source_log_ids=[log.id],
                        metadata={**(item.metadata or {}), "source": "l0-layering"},
                        weight=item.weight,
                    )
                    created += 1
            self.store.mark_raw_processed([log.id for log in logs])
            self.store.complete_jobs(job_ids)
            return created
        except Exception as error:
            self.store.fail_jobs(job_ids, str(error))
            raise

    def process_pending(self) -> int:
        created = 0
        for project_id in self.store.list_pending_layering_projects():
            try:
                created += self.process_project(project_id)
            except Exception:
                continue
        return created

    def retry_failed(self, project_id: str) -> int:
        return self.store.retry_failed_layering_jobs(project_id)

    def reset_stale_running(self, project_id: str, older_than_minutes: int = 30) -> int:
        return self.store.reset_stale_running_layering_jobs(project_id, older_than_minutes)

    def rebuild_project_from_l0(self, project_id: str) -> dict[str, int]:
        deleted = self.store.delete_project_derived_memories(project_id, [Layer.L1, Layer.L2])
        created = 0
        logs = self.store.list_raw_logs(project_id=project_id)
        for log in logs:
            items = self.classifier.classify([log])
            for item in items:
                self.store.upsert_memory(
                    project_id=item.project_id,
                    layer=item.layer,
                    title=item.title,
                    body=item.body,
                    tags=item.tags,
                    memory_type=item.memory_type,
                    source_log_ids=[log.id],
                    metadata={**(item.metadata or {}), "source": "l0-rebuild"},
                    weight=item.weight,
                )
                created += 1
        return {"deleted": deleted, "created": created}

    def enqueue(self, project_id: str) -> None:
        self._queue.put(project_id)

    def drain(self) -> None:
        self._queue.join()

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._queue.put("")
        if self._thread:
            self._thread.join(timeout=2)

    def _run(self) -> None:
        while not self._stop.is_set():
            project_id = self._queue.get()
            try:
                if not project_id:
                    continue
                try:
                    self.process_project(project_id)
                except Exception:
                    continue
            finally:
                self._queue.task_done()
