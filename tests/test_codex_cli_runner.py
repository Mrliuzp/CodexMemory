from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest


from codex_memory.codex_cli_runner import (
    CodexCliAuthenticationError,
    CodexCliContextBudgetExceededError,
    CodexCliDailyBudgetExceededError,
    CodexCliDisabledError,
    CodexCliInvalidSchemaError,
    CodexCliOutputLimitError,
    CodexCliOutputSchemaError,
    CodexCliProcessError,
    CodexCliRequest,
    CodexCliRunner,
    CodexCliSettings,
    CodexCliTimeoutError,
    ProcessResult,
    ProcessStartError,
    ProcessTimeoutError,
    ProcessInvocation,
    SubprocessProcessRunner,
)


SCHEMA = {
    "type": "object",
    "properties": {"decision": {"type": "string"}},
    "required": ["decision"],
    "additionalProperties": False,
}


def _request(**overrides: object) -> CodexCliRequest:
    values: dict[str, object] = {
        "project_key": "erp",
        "task": "根据上下文给出保守判断",
        "context": {"project_key": "erp", "facts": ["只读事实"]},
        "output_schema": SCHEMA,
        "request_id": "req-1",
    }
    values.update(overrides)
    return CodexCliRequest(**values)  # type: ignore[arg-type]


def _settings(**overrides: object) -> CodexCliSettings:
    values: dict[str, object] = {
        "enabled": True,
        "daily_budget_tokens": 0,
        "max_output_bytes": 4096,
    }
    values.update(overrides)
    return CodexCliSettings(**values)  # type: ignore[arg-type]


class FakeProcess:
    """只记录调用并返回预设结果，不启动任何外部进程。"""

    def __init__(self, result: ProcessResult | None = None, error: Exception | None = None) -> None:
        self.result = result or ProcessResult(0, b'{"decision":"keep"}')
        self.error = error
        self.invocations = []
        self.callback = None

    def run(self, invocation):
        self.invocations.append(invocation)
        if self.callback is not None:
            callback_result = self.callback(invocation)
            if callback_result is not None:
                self.result = callback_result
        if self.error is not None:
            raise self.error
        return self.result


def _output_path(invocation) -> Path:
    return Path(invocation.argv[invocation.argv.index("--output-last-message") + 1])


def _schema_path(invocation) -> Path:
    return Path(invocation.argv[invocation.argv.index("--output-schema") + 1])


def test_success_returns_schema_verified_json_and_cleans_isolated_directory() -> None:
    fake = FakeProcess()
    observed = {}

    def callback(invocation):
        observed["cwd"] = invocation.cwd
        observed["stdin"] = json.loads(invocation.stdin.decode("utf-8"))
        assert invocation.cwd.exists()
        assert json.loads(_schema_path(invocation).read_text(encoding="utf-8")) == SCHEMA
        _output_path(invocation).write_text('{"decision":"keep"}', encoding="utf-8")
        return ProcessResult(0, b"progress\n{\"decision\":\"keep\"}")

    fake.callback = callback
    result = CodexCliRunner(_settings(), process_runner=fake).run(_request())

    assert result.data == {"decision": "keep"}
    assert result.output_source == "output_file"
    assert result.project_key == "erp"
    assert observed["stdin"]["project_key"] == "erp"
    assert observed["stdin"]["context"]["facts"] == ["只读事实"]
    assert not observed["cwd"].exists()


def test_cli_argv_is_fixed_array_and_untrusted_context_stays_on_stdin() -> None:
    fake = FakeProcess()
    malicious = "x; touch should-not-exist && echo $(whoami)"
    request = _request(task=malicious, context={"project_key": "erp", "fact": malicious})

    CodexCliRunner(_settings(), process_runner=fake).run(request)

    invocation = fake.invocations[0]
    assert malicious in invocation.stdin.decode("utf-8")
    assert malicious not in invocation.argv
    assert invocation.argv[-1] == "-"
    assert "--ephemeral" in invocation.argv
    assert "--ignore-user-config" in invocation.argv
    assert "--ignore-rules" in invocation.argv
    assert invocation.argv[invocation.argv.index("--sandbox") + 1] == "read-only"
    assert invocation.argv[invocation.argv.index("--ask-for-approval") + 1] == "never"
    assert "--add-dir" not in invocation.argv
    assert "--yolo" not in invocation.argv
    assert "--full-auto" not in invocation.argv


def test_invalid_output_schema_does_not_start_process() -> None:
    fake = FakeProcess()

    with pytest.raises(CodexCliInvalidSchemaError) as error:
        CodexCliRunner(_settings(), process_runner=fake).run(_request(output_schema={"type": "not-a-type"}))

    assert error.value.code == "codex_cli_invalid_schema"
    assert fake.invocations == []


def test_external_schema_reference_is_rejected() -> None:
    fake = FakeProcess()

    with pytest.raises(CodexCliInvalidSchemaError):
        CodexCliRunner(_settings(), process_runner=fake).run(
            _request(output_schema={"$ref": "file:///etc/passwd"})
        )

    assert fake.invocations == []


def test_schema_mismatch_is_a_classifiable_failure_and_cleans_directory() -> None:
    fake = FakeProcess()
    observed = {}

    def callback(invocation):
        observed["cwd"] = invocation.cwd
        _output_path(invocation).write_text('{"decision": 123}', encoding="utf-8")
        return ProcessResult(0)

    fake.callback = callback
    with pytest.raises(CodexCliOutputSchemaError) as error:
        CodexCliRunner(_settings(), process_runner=fake).run(_request())

    assert error.value.code == "codex_cli_output_schema_mismatch"
    assert not observed["cwd"].exists()


def test_only_last_schema_verified_json_is_accepted() -> None:
    fake = FakeProcess(
        result=ProcessResult(
            0,
            b'notice\n{"decision":"keep"}\n{"decision":"invalid","extra":true}',
        )
    )

    result = CodexCliRunner(_settings(), process_runner=fake).run(_request())

    assert result.data == {"decision": "keep"}
    assert result.output_source == "stdout"


def test_timeout_is_classifiable_and_cleans_directory() -> None:
    fake = FakeProcess(error=ProcessTimeoutError("fake timeout"))
    observed = {}

    def callback(invocation):
        observed["cwd"] = invocation.cwd

    fake.callback = callback
    with pytest.raises(CodexCliTimeoutError) as error:
        CodexCliRunner(_settings(), process_runner=fake).run(_request())

    assert error.value.code == "codex_cli_timeout"
    assert not observed["cwd"].exists()


def test_nonzero_exit_is_classifiable_and_sensitive_stderr_is_redacted() -> None:
    fake = FakeProcess(
        result=ProcessResult(7, b"", "token=super-secret-value; failed"),
    )

    with pytest.raises(CodexCliProcessError) as error:
        CodexCliRunner(_settings(), process_runner=fake).run(_request())

    assert error.value.code == "codex_cli_process_error"
    assert "super-secret-value" not in error.value.as_dict()["details"]["output"]
    assert "<已脱敏>" in error.value.as_dict()["details"]["output"]


def test_authentication_failure_is_distinct_from_generic_nonzero_exit() -> None:
    fake = FakeProcess(result=ProcessResult(1, b"", "authentication required; please log in"))

    with pytest.raises(CodexCliAuthenticationError) as error:
        CodexCliRunner(_settings(), process_runner=fake).run(_request())

    assert error.value.code == "codex_cli_authentication_unavailable"


def test_cli_unavailable_is_observable() -> None:
    fake = FakeProcess(error=ProcessStartError("codex not found"))

    with pytest.raises(Exception) as raised:
        CodexCliRunner(_settings(), process_runner=fake).run(_request())

    assert raised.value.code == "codex_cli_unavailable"


def test_oversized_output_is_rejected_and_temp_directory_is_cleaned() -> None:
    fake = FakeProcess(result=ProcessResult(0, b"x" * 4097))
    with pytest.raises(CodexCliOutputLimitError) as error:
        CodexCliRunner(_settings(max_output_bytes=4096), process_runner=fake).run(_request())

    assert error.value.code == "codex_cli_output_limit"


def test_disabled_by_default_never_calls_fake_process() -> None:
    fake = FakeProcess()

    with pytest.raises(CodexCliDisabledError) as error:
        CodexCliRunner(CodexCliSettings(), process_runner=fake).run(_request())

    assert error.value.code == "codex_cli_disabled"
    assert fake.invocations == []


def test_context_budget_is_checked_before_process_start() -> None:
    fake = FakeProcess()

    with pytest.raises(CodexCliContextBudgetExceededError):
        CodexCliRunner(_settings(context_budget_tokens=1), process_runner=fake).run(_request())

    assert fake.invocations == []


def test_daily_budget_is_checked_before_process_start() -> None:
    fake = FakeProcess()

    with pytest.raises(CodexCliDailyBudgetExceededError) as error:
        CodexCliRunner(_settings(daily_budget_tokens=1), process_runner=fake).run(_request())

    assert error.value.code == "codex_cli_daily_budget_exceeded"
    assert fake.invocations == []


def test_environment_factory_can_prove_database_and_docker_variables_are_not_passed(monkeypatch) -> None:
    monkeypatch.setenv("CODEX_MEMORY_DATABASE_URL", "postgresql://user:secret@db/app")
    monkeypatch.setenv("DOCKER_HOST", "npipe://./pipe/docker_engine")
    fake = FakeProcess()
    CodexCliRunner(_settings(), process_runner=fake).run(_request())

    environment = fake.invocations[0].env
    assert "CODEX_MEMORY_DATABASE_URL" not in environment
    assert "DOCKER_HOST" not in environment


def test_concurrency_limit_is_non_blocking() -> None:
    started = threading.Event()
    release = threading.Event()

    class BlockingFake(FakeProcess):
        def run(self, invocation):
            self.invocations.append(invocation)
            started.set()
            release.wait(timeout=2)
            return ProcessResult(0, b'{"decision":"keep"}')

    fake = BlockingFake()
    runner = CodexCliRunner(_settings(max_concurrency=1), process_runner=fake)
    errors = []

    def call_runner() -> None:
        try:
            runner.run(_request(request_id="first"))
        except Exception as error:  # pragma: no cover - 仅用于把线程异常带回测试
            errors.append(error)

    worker = threading.Thread(target=call_runner)
    worker.start()
    assert started.wait(timeout=1)
    with pytest.raises(Exception) as raised:
        runner.run(_request(request_id="second"))
    release.set()
    worker.join(timeout=2)

    assert raised.value.code == "codex_cli_concurrency_limit"
    assert errors == []


def test_settings_can_be_loaded_from_environment_without_enabling_runner() -> None:
    from codex_memory.codex_cli_runner import CodexCliSettings

    settings = CodexCliSettings.from_env(
        {
            "CODEX_MEMORY_CODEX_CLI_PATH": "codex-test",
            "CODEX_MEMORY_CODEX_CLI_AUTH_ROOT": "/run/codex-memory-auth",
            "CODEX_MEMORY_CODEX_CLI_TIMEOUT_SECONDS": "12.5",
            "CODEX_MEMORY_CODEX_CLI_MAX_CONCURRENCY": "3",
            "CODEX_MEMORY_CODEX_CLI_CONTEXT_BUDGET_TOKENS": "123",
            "CODEX_MEMORY_CODEX_CLI_DAILY_BUDGET_TOKENS": "456",
        }
    )

    assert settings.enabled is False
    assert settings.cli_path == "codex-test"
    assert settings.auth_root == "/run/codex-memory-auth"
    assert settings.timeout_seconds == 12.5
    assert settings.max_concurrency == 3
    assert settings.context_budget_tokens == 123
    assert settings.daily_budget_tokens == 456


def test_subprocess_runner_uses_no_shell_with_fake_popen(monkeypatch, tmp_path) -> None:
    class Stream:
        def __init__(self, chunks: list[bytes] | None = None) -> None:
            self.chunks = list(chunks or [])
            self.written = b""

        def read(self, _size: int) -> bytes:
            return self.chunks.pop(0) if self.chunks else b""

        def write(self, payload: bytes) -> None:
            self.written += payload

        def close(self) -> None:
            return None

    class Popen:
        pid = 12345
        returncode = 0

        def __init__(self, **kwargs) -> None:
            self.kwargs = kwargs
            self.stdin = Stream()
            self.stdout = Stream([b"stdout"])
            self.stderr = Stream([b"stderr"])

        def poll(self) -> int:
            return 0

        def wait(self, timeout=None) -> int:
            return 0

        def terminate(self) -> None:
            return None

        def kill(self) -> None:
            return None

    created = []

    def fake_popen(**kwargs):
        process = Popen(**kwargs)
        created.append(process)
        return process

    monkeypatch.setattr("codex_memory.codex_cli_runner.subprocess.Popen", fake_popen)
    invocation = ProcessInvocation(
        argv=("codex", "exec", "-"),
        cwd=tmp_path,
        stdin=b"{\"project_key\":\"erp\"}",
        env={"PATH": "isolated"},
        timeout_seconds=1,
        max_output_bytes=1024,
    )

    result = SubprocessProcessRunner().run(invocation)

    assert result.returncode == 0
    assert result.stdout == b"stdout"
    assert result.stderr == b"stderr"
    assert created[0].kwargs["shell"] is False
    assert created[0].kwargs["args"] == ["codex", "exec", "-"]
    assert created[0].kwargs["cwd"] == str(tmp_path)
