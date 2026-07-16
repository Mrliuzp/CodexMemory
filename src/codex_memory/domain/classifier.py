from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from .models import Layer, RawLog


BUG_WORDS = {
    "bug",
    "error",
    "exception",
    "fail",
    "failed",
    "failure",
    "traceback",
    "regression",
    "\u9519\u8bef",
    "\u62a5\u9519",
    "\u5f02\u5e38",
    "\u5931\u8d25",
    "\u5d29\u6e83",
    "\u6839\u56e0",
}
SOLUTION_WORDS = {
    "fix",
    "fixed",
    "solution",
    "resolve",
    "resolved",
    "\u89e3\u51b3",
    "\u4fee\u590d",
    "\u65b9\u6848",
    "\u5b9e\u73b0",
}
KNOWLEDGE_WORDS = {
    "rule",
    "policy",
    "architecture",
    "architectural",
    "best practice",
    "standard",
    "spec",
    "specification",
    "design",
    "final conclusion",
    "conclusion",
    "decision",
    "guideline",
    "\u89c4\u8303",
    "\u67b6\u6784",
    "\u6700\u4f73\u5b9e\u8df5",
    "\u7ea6\u5b9a",
    "\u6807\u51c6",
    "\u8bbe\u8ba1",
    "\u6700\u7ec8\u7ed3\u8bba",
    "\u7ed3\u8bba",
    "\u51b3\u7b56",
    "\u6307\u5357",
}
DEBUG_WORDS = {
    "debug",
    "debugging",
    "trace",
    "investigate",
    "investigation",
    "diagnose",
    "\u8c03\u8bd5",
    "\u6392\u67e5",
    "\u8bca\u65ad",
}
TEMPORARY_WORDS = {
    "temporary",
    "tentative",
    "hypothesis",
    "draft",
    "workaround",
    "\u4e34\u65f6",
    "\u5047\u8bbe",
    "\u8349\u6848",
    "\u6743\u5b9c",
}
CODE_RE = re.compile(r"```|def |class |function |import |SELECT |CREATE TABLE", re.IGNORECASE)


@dataclass(frozen=True)
class ClassifiedMemory:
    layer: Layer
    title: str
    body: str
    tags: list[str]
    memory_type: str
    weight: float
    project_id: str | None = None
    metadata: dict[str, Any] | None = None


class MemoryClassifier:
    def classify(self, logs: list[RawLog]) -> list[ClassifiedMemory]:
        items: list[ClassifiedMemory] = []
        for log in logs:
            text = log.content.strip()
            if not text:
                continue
            lowered = text.lower()
            tags = self._tags_for(text, log.metadata)

            if self._has_any(lowered, BUG_WORDS):
                error_memory = self._error_fields(text)
                items.append(
                    ClassifiedMemory(
                        layer=Layer.L3,
                        title=self._title("Error", text),
                        body=self._error_body(error_memory),
                        tags=sorted(set(tags + ["error", "anti-pattern"])),
                        memory_type="error",
                        weight=3.0,
                        project_id=log.project_id,
                        metadata={"error_memory": error_memory},
                    )
                )
                if self._has_any(lowered, SOLUTION_WORDS) or CODE_RE.search(text):
                    items.append(
                        ClassifiedMemory(
                            layer=Layer.L1,
                            title=self._title("Working", text),
                            body=text,
                            tags=sorted(set(tags + ["working"])),
                            memory_type=self._working_type(text, lowered),
                            weight=1.0,
                            project_id=log.project_id,
                        )
                    )
                else:
                    items.append(
                        ClassifiedMemory(
                            layer=Layer.L1,
                            title=self._title("Problem", text),
                            body=text,
                            tags=sorted(set(tags + ["working", "problem"])),
                            memory_type="problem",
                            weight=0.8,
                            project_id=log.project_id,
                        )
                    )
                continue

            if self._has_any(lowered, KNOWLEDGE_WORDS):
                items.append(
                    ClassifiedMemory(
                        layer=Layer.L2,
                        title=self._title("Knowledge", text),
                        body=text,
                        tags=sorted(set(tags + ["knowledge"])),
                        memory_type="knowledge",
                        weight=2.0,
                        project_id=log.project_id,
                    )
                )
                continue

            if (
                self._has_any(lowered, SOLUTION_WORDS)
                or CODE_RE.search(text)
                or self._has_any(lowered, DEBUG_WORDS)
                or self._has_any(lowered, TEMPORARY_WORDS)
            ):
                working_type = self._working_type(text, lowered)
                items.append(
                    ClassifiedMemory(
                        layer=Layer.L1,
                        title=self._title("Working", text),
                        body=text,
                        tags=sorted(set(tags + ["working"])),
                        memory_type=working_type,
                        weight=0.7 if working_type == "temporary" else 1.0,
                        project_id=log.project_id,
                    )
                )
                continue

            if log.role == "user":
                items.append(
                    ClassifiedMemory(
                        layer=Layer.L1,
                        title=self._title("Conversation", text),
                        body=text,
                        tags=sorted(set(tags + ["conversation"])),
                        memory_type="conversation",
                        weight=0.4,
                        project_id=log.project_id,
                    )
                )
        return items

    def _title(self, prefix: str, text: str) -> str:
        compact = " ".join(text.split())
        return f"{prefix}: {compact[:80]}"

    def _tags_for(self, text: str, metadata: dict[str, Any] | None = None) -> list[str]:
        tags: list[str] = []
        for marker in re.findall(r"#([A-Za-z0-9_\-\u4e00-\u9fff]+)", text):
            tags.append(marker)
        module_match = re.search(r"(?:module|\u6a21\u5757)[:= ]([A-Za-z0-9_\-/\.]+)", text, re.IGNORECASE)
        if module_match:
            tags.append(f"module:{module_match.group(1)}")
        type_match = re.search(r"(?:type|\u7c7b\u578b)[:= ]([A-Za-z0-9_\-/\.]+)", text, re.IGNORECASE)
        if type_match:
            tags.append(f"type:{type_match.group(1)}")
        metadata = metadata or {}
        for marker in metadata.get("tags", []):
            if isinstance(marker, str):
                tags.append(marker)
        module = metadata.get("module")
        if isinstance(module, str):
            tags.append(f"module:{module}")
        type_tag = metadata.get("type") or metadata.get("type_tag")
        if isinstance(type_tag, str):
            tags.append(f"type:{type_tag}")
        if CODE_RE.search(text):
            tags.append("code")
        return tags

    def _working_type(self, text: str, lowered: str) -> str:
        if self._has_any(lowered, SOLUTION_WORDS):
            return "solution"
        if CODE_RE.search(text):
            return "code"
        if self._has_any(lowered, DEBUG_WORDS):
            return "debug"
        if self._has_any(lowered, TEMPORARY_WORDS):
            return "temporary"
        return "solution"

    def _has_any(self, lowered: str, words: set[str]) -> bool:
        for word in words:
            if word.isascii() and re.search(r"[A-Za-z0-9_]", word):
                pattern = rf"(?<![A-Za-z0-9_]){re.escape(word)}(?![A-Za-z0-9_])"
                if re.search(pattern, lowered):
                    return True
                continue
            if word in lowered:
                return True
        return False

    def _error_body(self, fields: dict[str, str]) -> str:
        return "\n".join(f"{key}: {value}" for key, value in fields.items())

    def _error_fields(self, text: str) -> dict[str, str]:
        return {
            "error": self._extract_field(text, ["error", "bug", "\u9519\u8bef", "\u62a5\u9519"]) or text,
            "context": self._extract_field(text, ["context", "\u4e0a\u4e0b\u6587"]) or "captured from project conversation",
            "trigger_condition": self._extract_field(
                text,
                ["trigger", "trigger condition", "when", "condition", "\u89e6\u53d1", "\u89e6\u53d1\u6761\u4ef6"],
            )
            or "reproduce under the captured context before applying the fix",
            "root_cause": self._extract_field(text, ["root cause", "root_cause", "cause", "\u6839\u56e0", "\u539f\u56e0"])
            or "pending analysis",
            "fix": self._extract_field(
                text,
                ["fix", "solution", "repair", "\u4fee\u590d", "\u4fee\u590d\u65b9\u6848", "\u89e3\u51b3", "\u89e3\u51b3\u65b9\u6848", "\u5904\u7406\u65b9\u6848"],
            )
            or "pending or described in source log",
            "anti_pattern": self._extract_field(
                text,
                ["anti-pattern", "anti_pattern", "avoid", "forbid", "\u7981\u6b62\u6a21\u5f0f", "\u53cd\u6a21\u5f0f", "\u907f\u514d", "\u7981\u6b62"],
            )
            or "do not repeat the triggering pattern before checking this memory",
        }

    def _extract_field(self, text: str, names: list[str]) -> str | None:
        for name in names:
            pattern = re.compile(
                rf"(?:^|[\n.;])\s*{re.escape(name)}\s*[:=]\s*(.+?)(?=(?:[\n.;]\s*[\w\-\u4e00-\u9fff ]+\s*[:=])|$)",
                re.IGNORECASE | re.DOTALL,
            )
            match = pattern.search(text)
            if match:
                return " ".join(match.group(1).split()).rstrip(" .。")
        return None
