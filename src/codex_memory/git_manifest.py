from __future__ import annotations

import hashlib
import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


@dataclass(frozen=True)
class GitSnapshot:
    available: bool
    root: str | None = None
    branch: str | None = None
    head: str | None = None
    porcelain: str = ""
    diff_hash: str | None = None
    untracked: tuple[dict[str, Any], ...] = ()
    error: str | None = None

    @property
    def dirty(self) -> bool:
        return bool(self.porcelain)

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "available": self.available,
            "branch": self.branch,
            "head": self.head,
            "porcelain": self.porcelain,
            "diff_hash": self.diff_hash,
            "untracked": [dict(item) for item in self.untracked],
        }
        if self.root is not None:
            result["root"] = self.root
        if self.error is not None:
            result["error"] = self.error
        return result


def collect_git_snapshot(cwd: str | Path, *, timeout: float = 2.0) -> GitSnapshot:
    """只采集 Git 元数据，不读取 transcript，也不写入目标仓库。"""
    path = Path(cwd).resolve()
    root_output = _git(path, ["rev-parse", "--show-toplevel"], timeout)
    if root_output is None:
        return GitSnapshot(available=False, error="不可用")
    root = Path(root_output.strip()).resolve()
    branch_output = _git(root, ["branch", "--show-current"], timeout)
    head_output = _git(root, ["rev-parse", "HEAD"], timeout)
    status_output = _git(root, ["status", "--porcelain=v1", "-z", "--untracked-files=all"], timeout)
    if branch_output is None or head_output is None or status_output is None:
        return GitSnapshot(available=False, root=str(root), error="Git 状态不可用")

    status_entries = _status_entries(status_output)
    porcelain = "\n".join(status_entries)
    untracked = tuple(_untracked_metadata(root, status_entries))
    diff_hash = _diff_hash(root, status_output, timeout)
    return GitSnapshot(
        available=True,
        root=str(root),
        branch=branch_output.strip() or None,
        head=head_output.strip() or None,
        porcelain=porcelain,
        diff_hash=diff_hash,
        untracked=untracked,
    )


def build_change_manifest(baseline: GitSnapshot, current: GitSnapshot, *, cwd: str | Path | None = None) -> dict[str, Any]:
    """根据固定快照和当前 Git 状态生成稳定排序的 ChangeManifest。"""
    uncertain = not baseline.available or not current.available or baseline.dirty
    files: list[dict[str, Any]] = []
    if not uncertain:
        files = _manifest_files(current)
    files.sort(key=lambda item: (str(item.get("path", "")), str(item.get("old_path", ""))))
    manifest = {
        "schema_version": 1,
        "baseline": baseline.to_dict(),
        "current": current.to_dict(),
        "files": files,
        "uncertain": uncertain,
        "attribution": "uncertain" if uncertain else "current_run",
    }
    manifest["manifest_hash"] = hashlib.sha256(manifest_json(manifest).encode("utf-8")).hexdigest()
    return manifest


def manifest_json(manifest: dict[str, Any]) -> str:
    """以固定键顺序和排序输出清单，便于重放和校验。"""
    return json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _git(cwd: Path, args: list[str], timeout: float) -> str | None:
    try:
        completed = subprocess.run(
            ["git", "-C", str(cwd), *args],
            check=True,
            capture_output=True,
            timeout=timeout,
        )
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return None
    return completed.stdout.decode("utf-8", errors="replace")


def _status_entries(raw: str) -> list[str]:
    tokens = [token for token in raw.split("\0") if token]
    entries: list[str] = []
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if len(token) >= 3 and token[2] == " " and token[:2].startswith("R") and index + 1 < len(tokens):
            entries.append(f"{token[:2]} {token[3:]} -> {tokens[index + 1]}")
            index += 2
            continue
        entries.append(token)
        index += 1
    return sorted(entries)


def _untracked_metadata(root: Path, entries: Iterable[str]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for entry in entries:
        if not entry.startswith("?? "):
            continue
        relative = entry[3:]
        absolute = root / Path(relative)
        try:
            stat = absolute.stat()
        except OSError:
            continue
        result.append({"path": relative.replace(os.sep, "/"), "bytes": stat.st_size, "directory": absolute.is_dir()})
    return sorted(result, key=lambda item: str(item["path"]))


def _diff_hash(root: Path, status_output: str, timeout: float) -> str:
    parts = [status_output.encode("utf-8")]
    for args in (("diff", "--no-ext-diff", "--binary"), ("diff", "--cached", "--no-ext-diff", "--binary")):
        output = _git_bytes(root, list(args), timeout)
        if output is not None:
            parts.append(output)
    digest = hashlib.sha256()
    for part in parts:
        digest.update(len(part).to_bytes(8, "big"))
        digest.update(part)
    return digest.hexdigest()


def _git_bytes(cwd: Path, args: list[str], timeout: float) -> bytes | None:
    try:
        completed = subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True, timeout=timeout)
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return None
    return completed.stdout


def _manifest_files(current: GitSnapshot) -> list[dict[str, Any]]:
    if not current.root:
        return []
    root = Path(current.root).resolve()
    entries: list[dict[str, Any]] = []
    for status, old_path, new_path in _diff_name_status(root):
        status_code = status[:1]
        if status_code == "R":
            entries.append({"path": new_path, "old_path": old_path, "change": "renamed"})
        elif status_code == "A":
            entries.append({"path": new_path, "change": "added"})
        elif status_code == "D":
            entries.append({"path": new_path, "change": "deleted"})
        else:
            entries.append({"path": new_path, "change": "modified"})
    known = {str(item["path"]) for item in entries}
    for item in current.untracked:
        path = str(item["path"])
        if path not in known:
            entries.append({"path": path, "change": "untracked", "metadata": dict(item)})
    return entries


def _diff_name_status(root: Path) -> list[tuple[str, str, str]]:
    values: list[tuple[str, str, str]] = []
    for args in (("diff", "--name-status", "-z", "HEAD"), ("diff", "--cached", "--name-status", "-z")):
        output = _git_bytes(root, list(args), 2.0)
        if output is None:
            continue
        tokens = output.decode("utf-8", errors="replace").split("\0")
        index = 0
        while index < len(tokens):
            status = tokens[index]
            if not status:
                index += 1
                continue
            if status.startswith("R") and index + 2 < len(tokens):
                values.append((status, tokens[index + 1], tokens[index + 2]))
                index += 3
            elif index + 1 < len(tokens):
                values.append((status, tokens[index + 1], tokens[index + 1]))
                index += 2
            else:
                break
    deduplicated: dict[tuple[str, str, str], None] = {}
    for value in values:
        deduplicated[value] = None
    return sorted(deduplicated, key=lambda value: (value[2], value[1], value[0]))
