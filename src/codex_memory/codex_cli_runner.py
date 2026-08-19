"""安全、可测试且默认禁用的 Codex CLI 执行器。

本模块只负责把同项目的最小上下文交给一次隔离的 ``codex exec`` 运行，
并返回已经通过 JSON Schema 校验的最后一条 JSON。它不访问数据库、不执
行模型返回的内容，也不负责把结果发布到任何持久化层。
"""

from __future__ import annotations

import json
import ntpath
import os
import posixpath
import re
import signal
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol, Sequence

from jsonschema import Draft202012Validator, SchemaError, ValidationError, validators


_ENV_PREFIX = "CODEX_MEMORY_CODEX_CLI_"
_LEGACY_ENV_PREFIX = "CODEX_MEMORY_CLI_"
_MAX_PROFILE_OR_MODEL_LENGTH = 256
_MAX_CLI_PATH_LENGTH = 4096
_MAX_ERROR_TEXT_LENGTH = 2048
_MAX_OUTPUT_BYTES_HARD_LIMIT = 64 * 1024 * 1024
_TOKEN_BYTES = 4
_ACTIVE_GENERATION_FILE = ".active-generation"
_ROOT_GENERATION = "root"
_DISABLED_GENERATION = "disabled"


def _utc_today() -> date:
    return datetime.now(timezone.utc).date()


def _env_value(environ: Mapping[str, str], name: str, default: str | None = None) -> str | None:
    """读取主配置名，并兼容早期不含 ``CODEX`` 段的名称。"""

    value = environ.get(f"{_ENV_PREFIX}{name}")
    if value is None:
        value = environ.get(f"{_LEGACY_ENV_PREFIX}{name}")
    return default if value is None else value


def _parse_bool(value: str, name: str) -> bool:
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} 必须是 true/false")


def _parse_int(value: str, name: str) -> int:
    try:
        return int(value.strip())
    except ValueError as error:
        raise ValueError(f"{name} 必须是整数") from error


def _parse_float(value: str, name: str) -> float:
    try:
        return float(value.strip())
    except ValueError as error:
        raise ValueError(f"{name} 必须是数字") from error


def _optional_text(value: str | None) -> str | None:
    normalized = (value or "").strip()
    return normalized or None


@dataclass(frozen=True, slots=True)
class CodexCliSettings:
    """Codex CLI 执行边界配置。

    ``daily_budget_tokens=0`` 表示不启用本地每日预算限制；生产启用时应
    显式设置正数。默认 ``enabled=False``，因此即使机器安装了 Codex CLI，
    Worker 也不会意外启动模型任务。
    """

    enabled: bool = False
    cli_path: str = "codex"
    auth_root: str | None = None
    profile: str | None = None
    model: str | None = None
    timeout_seconds: float = 60.0
    max_concurrency: int = 1
    context_budget_tokens: int = 4_000
    daily_budget_tokens: int = 20_000
    max_output_bytes: int = 64 * 1024

    def __post_init__(self) -> None:
        if not self.cli_path or len(self.cli_path) > _MAX_CLI_PATH_LENGTH or "\x00" in self.cli_path:
            raise ValueError("cli_path 必须是非空且不包含 NUL 的路径")
        if self.auth_root is not None:
            if (
                not self.auth_root.strip()
                or len(self.auth_root) > _MAX_CLI_PATH_LENGTH
                or "\x00" in self.auth_root
                or not (
                    Path(self.auth_root).is_absolute()
                    or ntpath.isabs(self.auth_root)
                    or posixpath.isabs(self.auth_root)
                )
            ):
                raise ValueError("auth_root 必须是绝对路径且不包含 NUL")
        for name, value in (("profile", self.profile), ("model", self.model)):
            if value is not None and (
                not value.strip()
                or len(value) > _MAX_PROFILE_OR_MODEL_LENGTH
                or any(ord(character) < 32 for character in value)
            ):
                raise ValueError(f"{name} 不能包含控制字符或为空")
        if self.timeout_seconds <= 0 or self.timeout_seconds > 24 * 60 * 60:
            raise ValueError("timeout_seconds 必须在 0 和 86400 之间")
        if self.max_concurrency < 1 or self.max_concurrency > 64:
            raise ValueError("max_concurrency 必须在 1 和 64 之间")
        if self.context_budget_tokens < 1 or self.context_budget_tokens > 1_000_000:
            raise ValueError("context_budget_tokens 必须在 1 和 1000000 之间")
        if self.daily_budget_tokens < 0 or self.daily_budget_tokens > 10_000_000:
            raise ValueError("daily_budget_tokens 必须在 0 和 10000000 之间")
        if self.max_output_bytes < 1 or self.max_output_bytes > _MAX_OUTPUT_BYTES_HARD_LIMIT:
            raise ValueError("max_output_bytes 超出安全范围")

    @property
    def max_output_tokens(self) -> int:
        """按保守的 UTF-8 字节估算可接受的最大输出 token 数。"""

        return max(1, self.max_output_bytes // _TOKEN_BYTES)

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> "CodexCliSettings":
        """从环境读取配置，不读取或修改 Codex 配置文件与凭据。"""

        env = os.environ if environ is None else environ
        enabled_text = _env_value(env, "ENABLED", "false")
        timeout_text = _env_value(env, "TIMEOUT_SECONDS", "60")
        concurrency_text = _env_value(env, "MAX_CONCURRENCY", "1")
        context_text = _env_value(env, "CONTEXT_BUDGET_TOKENS", "4000")
        daily_text = _env_value(env, "DAILY_BUDGET_TOKENS", "20000")
        output_text = _env_value(env, "MAX_OUTPUT_BYTES", str(64 * 1024))
        assert enabled_text is not None
        assert timeout_text is not None
        assert concurrency_text is not None
        assert context_text is not None
        assert daily_text is not None
        assert output_text is not None
        return cls(
            enabled=_parse_bool(enabled_text, "CODEX_MEMORY_CODEX_CLI_ENABLED"),
            cli_path=_env_value(env, "PATH", "codex") or "codex",
            auth_root=_optional_text(_env_value(env, "AUTH_ROOT")),
            profile=_optional_text(_env_value(env, "PROFILE")),
            model=_optional_text(_env_value(env, "MODEL")),
            timeout_seconds=_parse_float(timeout_text, "CODEX_MEMORY_CODEX_CLI_TIMEOUT_SECONDS"),
            max_concurrency=_parse_int(concurrency_text, "CODEX_MEMORY_CODEX_CLI_MAX_CONCURRENCY"),
            context_budget_tokens=_parse_int(context_text, "CODEX_MEMORY_CODEX_CLI_CONTEXT_BUDGET_TOKENS"),
            daily_budget_tokens=_parse_int(daily_text, "CODEX_MEMORY_CODEX_CLI_DAILY_BUDGET_TOKENS"),
            max_output_bytes=_parse_int(output_text, "CODEX_MEMORY_CODEX_CLI_MAX_OUTPUT_BYTES"),
        )


# 方便调用方把配置称为 Config，而不复制一份有歧义的实现。
CodexCliConfig = CodexCliSettings


@dataclass(frozen=True, slots=True)
class CodexCliRequest:
    """已经由上游构造好的同项目最小上下文请求。"""

    project_key: str
    task: str
    context: Mapping[str, Any]
    output_schema: Mapping[str, Any]
    request_id: str | None = None


@dataclass(frozen=True, slots=True)
class CodexCliResult:
    """通过输出 Schema 校验后的模型结果及可观测元数据。"""

    data: Any
    project_key: str
    request_id: str | None
    input_tokens: int
    output_tokens: int
    duration_ms: int
    process_returncode: int
    output_source: str

    def as_dict(self) -> dict[str, Any]:
        """转换为可由 Worker 记录的非敏感结构。"""

        return {
            "data": self.data,
            "project_key": self.project_key,
            "request_id": self.request_id,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "duration_ms": self.duration_ms,
            "process_returncode": self.process_returncode,
            "output_source": self.output_source,
        }


@dataclass(frozen=True, slots=True)
class ProcessInvocation:
    """注入给进程 runner 的不可变调用描述。"""

    argv: tuple[str, ...]
    cwd: Path
    stdin: bytes
    env: Mapping[str, str]
    timeout_seconds: float
    max_output_bytes: int


@dataclass(frozen=True, slots=True)
class ProcessResult:
    """进程 runner 返回的有限结果，不包含任何可执行语义。"""

    returncode: int
    stdout: bytes | str = b""
    stderr: bytes | str = b""


class ProcessRunner(Protocol):
    """可由单元测试替换的进程边界。"""

    def run(self, invocation: ProcessInvocation) -> ProcessResult:
        """运行一次固定参数数组调用。"""


class ProcessExecutionError(RuntimeError):
    """底层进程边界错误。"""


class ProcessStartError(ProcessExecutionError):
    """CLI 无法启动。"""


class ProcessTimeoutError(ProcessExecutionError):
    """CLI 超过执行时限。"""


class ProcessOutputLimitError(ProcessExecutionError):
    """CLI 输出超过上限。"""


class CodexCliError(RuntimeError):
    """所有可观测、可分类的 runner 失败。"""

    code = "codex_cli_error"
    retryable = False

    def __init__(self, message: str, *, details: Mapping[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = dict(details or {})

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "retryable": self.retryable,
            "details": dict(self.details),
        }


class CodexCliDisabledError(CodexCliError):
    code = "codex_cli_disabled"


class CodexCliInvalidRequestError(CodexCliError):
    code = "codex_cli_invalid_request"


class CodexCliInvalidSchemaError(CodexCliError):
    code = "codex_cli_invalid_schema"


class CodexCliContextBudgetExceededError(CodexCliError):
    code = "codex_cli_context_budget_exceeded"


class CodexCliDailyBudgetExceededError(CodexCliError):
    code = "codex_cli_daily_budget_exceeded"


class CodexCliConcurrencyLimitError(CodexCliError):
    code = "codex_cli_concurrency_limit"
    retryable = True


class CodexCliUnavailableError(CodexCliError):
    code = "codex_cli_unavailable"
    retryable = True


class CodexCliAuthenticationError(CodexCliError):
    code = "codex_cli_authentication_unavailable"


class CodexCliTimeoutError(CodexCliError):
    code = "codex_cli_timeout"
    retryable = True


class CodexCliOutputLimitError(CodexCliError):
    code = "codex_cli_output_limit"


class CodexCliProcessError(CodexCliError):
    code = "codex_cli_process_error"
    retryable = True


class CodexCliOutputParseError(CodexCliError):
    code = "codex_cli_output_not_json"


class CodexCliOutputSchemaError(CodexCliError):
    code = "codex_cli_output_schema_mismatch"


@dataclass(frozen=True, slots=True)
class _BudgetReservation:
    input_tokens: int
    output_tokens: int
    unlimited: bool = False


class DailyTokenBudget:
    """进程内 UTC 日预算；持久化任务可注入自己的账本实现。"""

    def __init__(self, limit_tokens: int) -> None:
        if limit_tokens < 0:
            raise ValueError("每日预算不能为负数")
        self.limit_tokens = limit_tokens
        self._day = _utc_today()
        self._allocated_tokens = 0
        self._lock = threading.Lock()

    def reserve(self, input_tokens: int, output_tokens: int) -> _BudgetReservation:
        if input_tokens < 1 or output_tokens < 1:
            raise ValueError("预算预留必须为正数")
        if self.limit_tokens == 0:
            return _BudgetReservation(input_tokens, output_tokens, unlimited=True)
        with self._lock:
            self._rollover_if_needed()
            available = self.limit_tokens - self._allocated_tokens
            if available < input_tokens + 1:
                raise CodexCliDailyBudgetExceededError(
                    "Codex CLI 每日预算不足",
                    details={"limit_tokens": self.limit_tokens, "allocated_tokens": self._allocated_tokens},
                )
            allowed_output = min(output_tokens, available - input_tokens)
            self._allocated_tokens += input_tokens + allowed_output
            return _BudgetReservation(input_tokens, allowed_output)

    def finalize(self, reservation: _BudgetReservation, actual_output_tokens: int) -> None:
        if reservation.unlimited:
            return
        actual = min(max(0, actual_output_tokens), reservation.output_tokens)
        unused = reservation.output_tokens - actual
        with self._lock:
            self._rollover_if_needed()
            self._allocated_tokens = max(0, self._allocated_tokens - unused)

    def release(self, reservation: _BudgetReservation) -> None:
        if reservation.unlimited:
            return
        with self._lock:
            self._rollover_if_needed()
            self._allocated_tokens = max(
                0,
                self._allocated_tokens - reservation.input_tokens - reservation.output_tokens,
            )

    def _rollover_if_needed(self) -> None:
        current_day = _utc_today()
        if current_day != self._day:
            self._day = current_day
            self._allocated_tokens = 0


_SENSITIVE_PATTERNS = (
    re.compile(r"(?i)(authorization\s*[:=]\s*bearer\s+)[^\s,;]+"),
    re.compile(r"(?i)(\b(?:token|secret|password|api[_ -]?key)\b\s*[:=]\s*)[^\s,;]+"),
    re.compile(r"(?i)\b(?:sk|rk|sess)-[a-z0-9_-]{8,}\b"),
    re.compile(r"(?i)\b(?:postgres(?:ql)?(?:\+[^:]+)?|mysql)://[^\s]+"),
)


def redact_sensitive(value: str, *, limit: int = _MAX_ERROR_TEXT_LENGTH) -> str:
    """脱敏并截断错误输出，避免日志写入凭据或完整模型输出。"""

    redacted = value
    for pattern in _SENSITIVE_PATTERNS:
        if pattern.groups:
            redacted = pattern.sub(lambda match: f"{match.group(1)}<已脱敏>", redacted)
        else:
            redacted = pattern.sub("<已脱敏>", redacted)
    if len(redacted) > limit:
        return f"{redacted[:limit]}…"
    return redacted


class SubprocessProcessRunner:
    """使用无 shell 参数数组运行进程，并限制输出与进程树生命周期。"""

    _read_chunk_size = 8192
    _terminate_grace_seconds = 0.5

    def run(self, invocation: ProcessInvocation) -> ProcessResult:
        popen_kwargs: dict[str, Any] = {
            "args": list(invocation.argv),
            "cwd": str(invocation.cwd),
            "env": dict(invocation.env),
            "stdin": subprocess.PIPE,
            "stdout": subprocess.PIPE,
            "stderr": subprocess.PIPE,
            "shell": False,
            "close_fds": True,
        }
        if os.name == "nt":
            popen_kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        else:
            popen_kwargs["start_new_session"] = True

        try:
            process = subprocess.Popen(**popen_kwargs)
        except (FileNotFoundError, PermissionError, OSError) as error:
            raise ProcessStartError(redact_sensitive(str(error))) from error

        output_quota = _OutputQuota(invocation.max_output_bytes)
        stdout_collector = _OutputCollector(output_quota)
        stderr_collector = _OutputCollector(output_quota)
        threads = [
            threading.Thread(
                target=_read_stream,
                args=(process.stdout, stdout_collector),
                name="codex-cli-stdout",
                daemon=True,
            ),
            threading.Thread(
                target=_read_stream,
                args=(process.stderr, stderr_collector),
                name="codex-cli-stderr",
                daemon=True,
            ),
            threading.Thread(
                target=_write_stream,
                args=(process.stdin, invocation.stdin),
                name="codex-cli-stdin",
                daemon=True,
            ),
        ]
        for thread in threads:
            thread.start()

        deadline = time.monotonic() + invocation.timeout_seconds
        timed_out = False
        output_limited = False
        while process.poll() is None:
            if stdout_collector.exceeded or stderr_collector.exceeded:
                output_limited = True
                self._terminate_process_tree(process)
                break
            if time.monotonic() >= deadline:
                timed_out = True
                self._terminate_process_tree(process)
                break
            time.sleep(0.01)

        if timed_out or output_limited:
            try:
                process.wait(timeout=self._terminate_grace_seconds)
            except subprocess.TimeoutExpired:
                self._terminate_process_tree(process, force=True)
        else:
            process.wait()

        for thread in threads:
            thread.join(timeout=self._terminate_grace_seconds)

        if timed_out:
            raise ProcessTimeoutError("Codex CLI 执行超时")
        if output_limited or output_quota.exceeded:
            raise ProcessOutputLimitError("Codex CLI 输出超过上限")
        return ProcessResult(
            returncode=int(process.returncode or 0),
            stdout=stdout_collector.value,
            stderr=stderr_collector.value,
        )

    def _terminate_process_tree(self, process: subprocess.Popen[Any], *, force: bool = False) -> None:
        """先终止进程组，必要时强制结束，避免子进程继续持有管道。"""

        if os.name == "nt":
            if process.pid:
                try:
                    subprocess.run(
                        ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                        check=False,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        shell=False,
                    )
                except OSError:
                    pass
            try:
                process.kill() if force else process.terminate()
            except OSError:
                pass
            return

        try:
            os.killpg(os.getpgid(process.pid), signal.SIGKILL if force else signal.SIGTERM)
        except (OSError, ProcessLookupError):
            try:
                process.kill() if force else process.terminate()
            except OSError:
                pass


class _OutputQuota:
    def __init__(self, limit: int) -> None:
        self.limit = limit
        self.used = 0
        self.exceeded = False
        self._lock = threading.Lock()

    def take(self, size: int) -> int:
        with self._lock:
            remaining = self.limit - self.used
            allowed = max(0, min(size, remaining))
            self.used += allowed
            if allowed < size:
                self.exceeded = True
            return allowed


class _OutputCollector:
    def __init__(self, quota: _OutputQuota) -> None:
        self.quota = quota
        self._buffer = bytearray()
        self._lock = threading.Lock()

    @property
    def exceeded(self) -> bool:
        return self.quota.exceeded

    @property
    def value(self) -> bytes:
        with self._lock:
            return bytes(self._buffer)

    def add(self, chunk: bytes) -> None:
        allowed = self.quota.take(len(chunk))
        with self._lock:
            self._buffer.extend(chunk[:allowed])


def _read_stream(stream: Any, collector: _OutputCollector) -> None:
    if stream is None:
        return
    try:
        while True:
            chunk = stream.read(SubprocessProcessRunner._read_chunk_size)
            if not chunk:
                return
            collector.add(chunk)
    except (OSError, ValueError):
        return


def _write_stream(stream: Any, payload: bytes) -> None:
    if stream is None:
        return
    try:
        stream.write(payload)
        stream.close()
    except (BrokenPipeError, OSError, ValueError):
        return


def _as_bytes(value: bytes | str) -> bytes:
    if isinstance(value, bytes):
        return value
    return value.encode("utf-8", errors="replace")


def _estimate_tokens(value: bytes | str) -> int:
    size = len(value) if isinstance(value, bytes) else len(value.encode("utf-8"))
    return max(1, (size + _TOKEN_BYTES - 1) // _TOKEN_BYTES)


def _read_limited(path: Path, limit: int) -> bytes:
    chunks: list[bytes] = []
    total = 0
    try:
        with path.open("rb") as stream:
            while True:
                chunk = stream.read(min(8192, limit + 1 - total))
                if not chunk:
                    break
                chunks.append(chunk)
                total += len(chunk)
                if total > limit:
                    raise CodexCliOutputLimitError(
                        "Codex CLI 最终输出文件超过上限",
                        details={"max_output_bytes": limit},
                    )
    except FileNotFoundError:
        raise
    return b"".join(chunks)


def _json_schema_validator(schema: Mapping[str, Any]) -> validators.Validator:
    if not isinstance(schema, Mapping):
        raise CodexCliInvalidSchemaError("output_schema 必须是 JSON 对象")
    try:
        _reject_external_refs(schema)
        schema_dict = dict(schema)
        json.dumps(schema_dict, ensure_ascii=False, allow_nan=False)
        validator_class = validators.validator_for(schema_dict, default=Draft202012Validator)
        validator_class.check_schema(schema_dict)
        return validator_class(schema_dict)
    except (SchemaError, TypeError, ValueError) as error:
        raise CodexCliInvalidSchemaError(
            "output_schema 不是有效的 JSON Schema",
            details={"reason": redact_sensitive(str(error))},
        ) from error


def _reject_external_refs(value: Any) -> None:
    if isinstance(value, Mapping):
        reference = value.get("$ref")
        if reference is not None and (not isinstance(reference, str) or not reference.startswith("#")):
            raise CodexCliInvalidSchemaError("output_schema 不允许外部 $ref")
        for child in value.values():
            _reject_external_refs(child)
    elif isinstance(value, list):
        for child in value:
            _reject_external_refs(child)


def _error_path(error: ValidationError) -> str:
    path = list(error.absolute_path)
    if not path:
        return "$"
    return "$" + "".join(f"[{item}]" if isinstance(item, int) else f".{item}" for item in path)


def _iter_json_documents(text: str) -> Sequence[Any]:
    """从最后消息中提取按顺序出现的完整 JSON 文档。"""

    decoder = json.JSONDecoder()
    documents: list[Any] = []
    cursor = 0
    while cursor < len(text):
        while cursor < len(text) and text[cursor].isspace():
            cursor += 1
        if cursor >= len(text):
            break
        try:
            value, end = decoder.raw_decode(text, cursor)
        except json.JSONDecodeError:
            next_object = min(
                (position for position in (text.find("{", cursor + 1), text.find("[", cursor + 1)) if position >= 0),
                default=-1,
            )
            if next_object < 0:
                break
            cursor = next_object
            continue
        documents.append(value)
        cursor = end
    return documents


def _last_verified_json(text: str, validator: validators.Validator) -> tuple[Any, int]:
    documents = _iter_json_documents(text)
    last_valid: Any = None
    valid_count = 0
    last_error: ValidationError | None = None
    for document in documents:
        errors = sorted(validator.iter_errors(document), key=lambda error: list(error.absolute_path))
        if not errors:
            last_valid = document
            valid_count += 1
        else:
            last_error = errors[0]
    if valid_count:
        return last_valid, len(documents)
    if not documents:
        raise CodexCliOutputParseError("Codex CLI 最终消息不是 JSON")
    details: dict[str, Any] = {"candidate_count": len(documents)}
    if last_error is not None:
        details.update({"path": _error_path(last_error), "reason": redact_sensitive(last_error.message)})
    raise CodexCliOutputSchemaError("Codex CLI 最终 JSON 不符合 output_schema", details=details)


def _build_safe_environment() -> dict[str, str]:
    """只传递运行 CLI 所需的环境，拒绝项目数据库与 Docker 环境变量。"""

    allowed = {
        "PATH",
        "PATHEXT",
        "SYSTEMROOT",
        "WINDIR",
        "TEMP",
        "TMP",
        "TMPDIR",
        "LANG",
        "LC_ALL",
    }
    environment = {key: value for key, value in os.environ.items() if key in allowed}
    environment["NO_COLOR"] = "1"
    return environment


_CUSTOM_ENVIRONMENT_KEYS = {
    "PATH",
    "PATHEXT",
    "SYSTEMROOT",
    "WINDIR",
    "TEMP",
    "TMP",
    "TMPDIR",
    "LANG",
    "LC_ALL",
    "CODEX_API_KEY",
    "OPENAI_API_KEY",
}


def _isolate_environment(environment: Mapping[str, str], root: Path) -> dict[str, str]:
    """过滤注入环境，并只把认证目录以受控只读根目录传给 CLI。"""

    safe = {key: value for key, value in environment.items() if key in _CUSTOM_ENVIRONMENT_KEYS}
    isolated_home = root / "home"
    isolated_tmp = root / "tmp"
    isolated_appdata = root / "appdata"
    isolated_local_appdata = root / "local-appdata"
    isolated_codex_home = root / "codex-home"
    isolated_codex_sqlite = root / "codex-sqlite"
    for directory in (
        isolated_home,
        isolated_tmp,
        isolated_appdata,
        isolated_local_appdata,
        isolated_codex_home,
        isolated_codex_sqlite,
    ):
        directory.mkdir()
    configured_auth_root = environment.get("CODEX_MEMORY_CODEX_CLI_AUTH_ROOT")
    if configured_auth_root:
        # The pointer is coordinator-owned metadata. The mounted root itself is
        # read-only for the Worker; never fall back to the default user home.
        auth_root = Path(configured_auth_root)
        pointer_path = auth_root / _ACTIVE_GENERATION_FILE
        try:
            generation = pointer_path.read_text(encoding="ascii").strip()
        except (OSError, UnicodeError) as error:
            raise CodexCliAuthenticationError("Worker 专用 Codex 认证目录不可用") from error
        if generation == _DISABLED_GENERATION or not generation:
            raise CodexCliAuthenticationError("Worker 专用 Codex 认证目录当前未激活")
        if generation == _ROOT_GENERATION:
            selected_home = auth_root
        elif re.fullmatch(r"generation-[A-Za-z0-9_-]{1,96}", generation):
            selected_home = auth_root / generation
        else:
            raise CodexCliAuthenticationError("Worker 专用 Codex 认证目录指针无效")
        try:
            root_resolved = auth_root.resolve(strict=True)
            selected_resolved = selected_home.resolve(strict=True)
        except OSError as error:
            raise CodexCliAuthenticationError("Worker 专用 Codex 认证目录不可用") from error
        if selected_resolved != root_resolved and root_resolved not in selected_resolved.parents:
            raise CodexCliAuthenticationError("Worker 专用 Codex 认证目录指针越界")
        if not selected_resolved.is_dir():
            raise CodexCliAuthenticationError("Worker 专用 Codex 认证目录不可用")
        codex_home = str(selected_resolved)
    else:
        codex_home = str(isolated_codex_home)
    safe.update(
        {
            "HOME": str(isolated_home),
            "USERPROFILE": str(isolated_home),
            "APPDATA": str(isolated_appdata),
            "LOCALAPPDATA": str(isolated_local_appdata),
            "CODEX_HOME": codex_home,
            "CODEX_SQLITE_HOME": str(isolated_codex_sqlite),
            "TEMP": str(isolated_tmp),
            "TMP": str(isolated_tmp),
            "TMPDIR": str(isolated_tmp),
            "NO_COLOR": "1",
        }
    )
    return safe


def _is_authentication_failure(text: str) -> bool:
    normalized = text.lower()
    return any(
        marker in normalized
        for marker in (
            "not logged in",
            "authentication required",
            "authentication failed",
            "unauthorized",
            "api key",
            "login required",
            "please log in",
            "401",
        )
    )


class CodexCliRunner:
    """调用一次隔离的 Codex CLI 任务，不执行返回内容。"""

    def __init__(
        self,
        settings: CodexCliSettings | None = None,
        *,
        process_runner: ProcessRunner | Callable[[ProcessInvocation], ProcessResult] | None = None,
        budget: DailyTokenBudget | None = None,
        environment_factory: Callable[[], Mapping[str, str]] | None = None,
    ) -> None:
        self.settings = settings or CodexCliSettings.from_env()
        self.process_runner = process_runner or SubprocessProcessRunner()
        self.budget = budget or DailyTokenBudget(self.settings.daily_budget_tokens)
        self.environment_factory = environment_factory or _build_safe_environment
        self._concurrency = threading.BoundedSemaphore(self.settings.max_concurrency)

    def run(self, request: CodexCliRequest) -> CodexCliResult:
        """执行请求并返回严格校验后的结果；失败统一抛出可分类异常。"""

        if not self.settings.enabled:
            raise CodexCliDisabledError("Codex CLI runner 已禁用")

        validator = self._validate_request(request)
        prompt = self._serialize_request(request)
        input_tokens = _estimate_tokens(prompt)
        if input_tokens > self.settings.context_budget_tokens:
            raise CodexCliContextBudgetExceededError(
                "请求上下文超过 Codex CLI budget",
                details={
                    "input_tokens": input_tokens,
                    "context_budget_tokens": self.settings.context_budget_tokens,
                },
            )

        if not self._concurrency.acquire(blocking=False):
            raise CodexCliConcurrencyLimitError("Codex CLI 并发数已达上限")

        reservation: _BudgetReservation | None = None
        succeeded = False
        started_at = time.monotonic()
        try:
            try:
                reservation = self.budget.reserve(input_tokens, self.settings.max_output_tokens)
            except CodexCliDailyBudgetExceededError:
                raise

            with tempfile.TemporaryDirectory(prefix="codex-memory-cli-") as temporary_directory:
                root = Path(temporary_directory)
                schema_path = root / "output-schema.json"
                output_path = root / "output.json"
                schema_path.write_text(
                    json.dumps(request.output_schema, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
                    encoding="utf-8",
                )
                invocation = ProcessInvocation(
                    argv=self._build_argv(schema_path, output_path),
                    cwd=root,
                    stdin=prompt,
                    env=_isolate_environment(
                        {**self.environment_factory(), "CODEX_MEMORY_CODEX_CLI_AUTH_ROOT": self.settings.auth_root or ""},
                        root,
                    ),
                    timeout_seconds=self.settings.timeout_seconds,
                    max_output_bytes=min(
                        self.settings.max_output_bytes,
                        reservation.output_tokens * _TOKEN_BYTES,
                    ),
                )
                process_result = self._run_process(invocation)
                stdout = _as_bytes(process_result.stdout)
                stderr = _as_bytes(process_result.stderr)
                effective_output_limit = invocation.max_output_bytes
                if len(stdout) + len(stderr) > effective_output_limit:
                    raise CodexCliOutputLimitError(
                        "Codex CLI 输出超过上限",
                        details={"max_output_bytes": effective_output_limit},
                    )
                if process_result.returncode != 0:
                    self._raise_process_failure(process_result.returncode, stdout, stderr)

                if output_path.exists():
                    final_bytes = _read_limited(output_path, effective_output_limit)
                    output_source = "output_file"
                else:
                    final_bytes = stdout
                    output_source = "stdout"
                if len(final_bytes) > effective_output_limit:
                    raise CodexCliOutputLimitError(
                        "Codex CLI 最终消息超过上限",
                        details={"max_output_bytes": effective_output_limit},
                    )
                try:
                    final_text = final_bytes.decode("utf-8")
                except UnicodeDecodeError as error:
                    raise CodexCliOutputParseError("Codex CLI 最终消息不是 UTF-8") from error
                data, _candidate_count = _last_verified_json(final_text, validator)
                output_tokens = _estimate_tokens(final_bytes)
                duration_ms = int((time.monotonic() - started_at) * 1000)
                result = CodexCliResult(
                    data=data,
                    project_key=request.project_key,
                    request_id=request.request_id,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    duration_ms=duration_ms,
                    process_returncode=process_result.returncode,
                    output_source=output_source,
                )
                succeeded = True
                return result
        finally:
            if reservation is not None:
                if succeeded:
                    self.budget.finalize(reservation, _estimate_tokens(final_bytes))
                else:
                    self.budget.release(reservation)
            self._concurrency.release()

    def _validate_request(self, request: CodexCliRequest) -> validators.Validator:
        if not isinstance(request, CodexCliRequest):
            raise CodexCliInvalidRequestError("request 必须是 CodexCliRequest")
        if not isinstance(request.project_key, str) or not request.project_key or request.project_key == "*" or any(
            ord(character) < 32 for character in request.project_key
        ):
            raise CodexCliInvalidRequestError("project_key 必须是明确的单项目标识")
        if not isinstance(request.task, str) or not request.task.strip():
            raise CodexCliInvalidRequestError("task 不能为空")
        if not isinstance(request.context, Mapping):
            raise CodexCliInvalidRequestError("context 必须是 JSON 对象")
        context_project = request.context.get("project_key")
        if context_project is not None and context_project != request.project_key:
            raise CodexCliInvalidRequestError("context.project_key 与 project_key 不一致")
        try:
            json.dumps(request.context, ensure_ascii=False, allow_nan=False)
        except (TypeError, ValueError) as error:
            raise CodexCliInvalidRequestError("context 必须可序列化为有限 JSON") from error
        return _json_schema_validator(request.output_schema)

    @staticmethod
    def _serialize_request(request: CodexCliRequest) -> bytes:
        try:
            payload = {
                "schema_version": 1,
                "project_key": request.project_key,
                "task": request.task,
                "context": request.context,
            }
            return json.dumps(payload, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
        except (TypeError, ValueError) as error:
            raise CodexCliInvalidRequestError("请求无法编码为 JSON") from error

    def _build_argv(self, schema_path: Path, output_path: Path) -> tuple[str, ...]:
        argv: list[str] = [
            self.settings.cli_path,
            "exec",
            "--ephemeral",
            "--ignore-user-config",
            "--ignore-rules",
            "--sandbox",
            "read-only",
            "--ask-for-approval",
            "never",
            "--skip-git-repo-check",
            "--color",
            "never",
            "--output-schema",
            str(schema_path),
            "--output-last-message",
            str(output_path),
        ]
        if self.settings.profile:
            argv.extend(("--profile", self.settings.profile))
        if self.settings.model:
            argv.extend(("--model", self.settings.model))
        argv.append("-")
        return tuple(argv)

    def _run_process(self, invocation: ProcessInvocation) -> ProcessResult:
        try:
            runner = self.process_runner
            run_method = getattr(runner, "run", None)
            return run_method(invocation) if run_method is not None else runner(invocation)  # type: ignore[misc]
        except ProcessTimeoutError as error:
            raise CodexCliTimeoutError("Codex CLI 执行超时") from error
        except ProcessOutputLimitError as error:
            raise CodexCliOutputLimitError(
                "Codex CLI 输出超过上限",
                details={"max_output_bytes": self.settings.max_output_bytes},
            ) from error
        except ProcessStartError as error:
            raise CodexCliUnavailableError(
                "Codex CLI 不可用或无法启动",
                details={"reason": redact_sensitive(str(error))},
            ) from error
        except FileNotFoundError as error:
            raise CodexCliUnavailableError("Codex CLI 不可用或无法启动") from error
        except PermissionError as error:
            raise CodexCliUnavailableError("Codex CLI 无执行权限") from error
        except subprocess.TimeoutExpired as error:
            raise CodexCliTimeoutError("Codex CLI 执行超时") from error
        except OSError as error:
            raise CodexCliUnavailableError(
                "Codex CLI 不可用或无法启动",
                details={"reason": redact_sensitive(str(error))},
            ) from error

    def _raise_process_failure(self, returncode: int, stdout: bytes, stderr: bytes) -> None:
        combined = redact_sensitive((stderr + b"\n" + stdout).decode("utf-8", errors="replace"))
        details = {"returncode": returncode, "output": combined}
        if _is_authentication_failure(combined):
            raise CodexCliAuthenticationError("Codex CLI 没有可用认证", details=details)
        raise CodexCliProcessError("Codex CLI 以非零状态退出", details=details)


__all__ = [
    "CodexCliAuthenticationError",
    "CodexCliConcurrencyLimitError",
    "CodexCliConfig",
    "CodexCliContextBudgetExceededError",
    "CodexCliDailyBudgetExceededError",
    "CodexCliDisabledError",
    "CodexCliError",
    "CodexCliInvalidRequestError",
    "CodexCliInvalidSchemaError",
    "CodexCliOutputLimitError",
    "CodexCliOutputParseError",
    "CodexCliOutputSchemaError",
    "CodexCliProcessError",
    "CodexCliRequest",
    "CodexCliResult",
    "CodexCliRunner",
    "CodexCliSettings",
    "DailyTokenBudget",
    "ProcessExecutionError",
    "ProcessInvocation",
    "ProcessOutputLimitError",
    "ProcessResult",
    "ProcessRunner",
    "ProcessStartError",
    "ProcessTimeoutError",
    "SubprocessProcessRunner",
    "redact_sensitive",
]
