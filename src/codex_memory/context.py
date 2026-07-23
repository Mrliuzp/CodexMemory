from __future__ import annotations

from .models import Layer, RetrievalResult


class ContextBuilder:
    def build(
        self,
        project_id: str,
        current_task: str,
        results: list[RetrievalResult],
        project_context: str | None = None,
    ) -> str:
        grouped: dict[Layer, list[RetrievalResult]] = {Layer.L3: [], Layer.L2: [], Layer.L1: []}
        for result in results:
            grouped[result.item.layer].append(result)

        return "\n\n".join(
            [
                "[Project Context]\n" + (project_context or f"project_id: {project_id}"),
                "[Error Memory - L3]\n" + self._format_error_group(grouped[Layer.L3]),
                "[Knowledge Base - L2]\n" + self._format_group(grouped[Layer.L2]),
                "[Working Memory - L1]\n" + self._format_group(grouped[Layer.L1]),
                "[Current Task]\n" + current_task,
            ]
        )

    def _format_group(self, results: list[RetrievalResult]) -> str:
        if not results:
            return "- none"
        lines: list[str] = []
        for result in results:
            item = result.item
            lines.append(
                f"- {item.title} (score={result.score:.3f}, type={item.memory_type}, tags={','.join(item.tags)})\n"
                f"  {item.body.replace(chr(10), chr(10) + '  ')}"
            )
        return "\n".join(lines)

    def _format_error_group(self, results: list[RetrievalResult]) -> str:
        if not results:
            return "- none"
        lines: list[str] = []
        for result in results:
            item = result.item
            error_memory = item.metadata.get("error_memory") if isinstance(item.metadata, dict) else None
            if isinstance(error_memory, dict):
                lines.append(
                    f"- {item.title} (score={result.score:.3f}, type={item.memory_type}, tags={','.join(item.tags)})\n"
                    f"  Error: {error_memory.get('error', '')}\n"
                    f"  Context: {error_memory.get('context', '')}\n"
                    f"  Trigger condition: {error_memory.get('trigger_condition', '')}\n"
                    f"  Root cause: {error_memory.get('root_cause', '')}\n"
                    f"  Fix: {error_memory.get('fix', '')}\n"
                    f"  Forbidden anti-pattern: {error_memory.get('anti_pattern', '')}"
                )
                continue
            lines.append(
                f"- {item.title} (score={result.score:.3f}, type={item.memory_type}, tags={','.join(item.tags)})\n"
                f"  {item.body.replace(chr(10), chr(10) + '  ')}"
            )
        return "\n".join(lines)
