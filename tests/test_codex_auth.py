from __future__ import annotations

import json
from pathlib import Path

import pytest

from codex_memory.codex_auth import (
    ACTIVE_GENERATION_FILE,
    AUTH_STATUS_ERROR,
    AUTH_STATUS_LOGIN_IN_PROGRESS,
    AUTH_STATUS_NOT_LOGGED_IN,
    AUTH_STATUS_READY,
    CodexAuthCoordinatorClient,
    CodexAuthGenerationManager,
    DISABLED_GENERATION,
)
from codex_memory.codex_cli_runner import (
    CodexCliAuthenticationError,
    CodexCliRequest,
    CodexCliRunner,
    CodexCliSettings,
    ProcessResult,
)


SCHEMA = {
    "type": "object",
    "properties": {"decision": {"type": "string"}},
    "required": ["decision"],
    "additionalProperties": False,
}


def _request() -> CodexCliRequest:
    return CodexCliRequest(
        project_key="erp",
        task="只读判断",
        context={"project_key": "erp"},
        output_schema=SCHEMA,
    )


class FakeProcess:
    def __init__(self, returncode: int | None = None) -> None:
        self.returncode = returncode
        self.terminated = False
        self.invocations = []

    def poll(self) -> int | None:
        return self.returncode

    def terminate(self) -> None:
        self.terminated = True
        self.returncode = -15

    def kill(self) -> None:
        self.terminated = True
        self.returncode = -9

    def wait(self, timeout: float | None = None) -> int:
        return self.returncode or 0


def test_runner_uses_active_generation_but_keeps_work_and_sqlite_state_temporary(tmp_path: Path, monkeypatch) -> None:
    auth_root = tmp_path / "worker-auth"
    auth_root.mkdir()
    (auth_root / ACTIVE_GENERATION_FILE).write_text("root\n", encoding="ascii")
    (auth_root / "auth.json").write_text("credential-placeholder-not-read", encoding="utf-8")
    linked = {}

    def fake_link(source: Path, runtime_home: Path) -> None:
        linked["source"] = source
        linked["runtime_home"] = runtime_home

    monkeypatch.setattr("codex_memory.codex_cli_runner._link_readonly_auth_file", fake_link)

    class FakeRunner:
        def __init__(self) -> None:
            self.invocations = []

        def run(self, invocation):
            self.invocations.append(invocation)
            output = Path(invocation.argv[invocation.argv.index("--output-last-message") + 1])
            output.write_text(json.dumps({"decision": "keep"}), encoding="utf-8")
            return ProcessResult(0)

    fake = FakeRunner()
    result = CodexCliRunner(
        CodexCliSettings(enabled=True, auth_root=str(auth_root), daily_budget_tokens=0),
        process_runner=fake,
    ).run(_request())

    invocation = fake.invocations[0]
    assert result.data == {"decision": "keep"}
    assert Path(invocation.env["CODEX_HOME"]) == linked["runtime_home"]
    assert linked["source"] == auth_root.resolve()
    assert Path(invocation.env["CODEX_SQLITE_HOME"]) != auth_root.resolve()
    assert Path(invocation.env["CODEX_SQLITE_HOME"]) != Path(invocation.env["CODEX_HOME"])
    assert invocation.cwd != auth_root.resolve()
    assert "--sandbox" in invocation.argv
    assert invocation.argv[invocation.argv.index("--sandbox") + 1] == "read-only"


def test_runner_refuses_disabled_active_generation_without_starting_cli(tmp_path: Path) -> None:
    auth_root = tmp_path / "worker-auth"
    auth_root.mkdir()
    (auth_root / ACTIVE_GENERATION_FILE).write_text(f"{DISABLED_GENERATION}\n", encoding="ascii")
    class FakeRunner:
        def run(self, _invocation):
            pytest.fail("CLI must not start")

    fake = FakeRunner()

    with pytest.raises(CodexCliAuthenticationError):
        CodexCliRunner(
            CodexCliSettings(enabled=True, auth_root=str(auth_root), daily_budget_tokens=0),
            process_runner=fake,
        ).run(_request())


def test_generation_manager_switches_atomically_and_keeps_old_generation_disabled(tmp_path: Path) -> None:
    auth_root = tmp_path / "worker-auth"
    auth_root.mkdir()
    process = FakeProcess()
    captured = {}

    def process_factory(argv, **kwargs):
        captured["argv"] = argv
        captured["kwargs"] = kwargs
        return process

    manager = CodexAuthGenerationManager(
        auth_root,
        process_factory=process_factory,
        status_probe=lambda _home: True,
    )
    assert manager.status() == AUTH_STATUS_READY
    assert (auth_root / ACTIVE_GENERATION_FILE).read_text(encoding="ascii").strip() == "root"

    assert manager.start_login() == AUTH_STATUS_LOGIN_IN_PROGRESS
    assert (auth_root / ACTIVE_GENERATION_FILE).read_text(encoding="ascii").strip() == DISABLED_GENERATION
    assert Path(captured["kwargs"]["env"]["CODEX_HOME"]).name.startswith("generation-")

    process.returncode = 0
    assert manager.status() == AUTH_STATUS_READY
    active = (auth_root / ACTIVE_GENERATION_FILE).read_text(encoding="ascii").strip()
    assert active.startswith("generation-")


def test_generation_manager_cancel_leaves_runner_disabled(tmp_path: Path) -> None:
    process = FakeProcess()
    manager = CodexAuthGenerationManager(
        tmp_path / "worker-auth",
        process_factory=lambda _argv, **_kwargs: process,
        status_probe=lambda _home: True,
    )
    assert manager.start_login() == AUTH_STATUS_LOGIN_IN_PROGRESS
    assert manager.cancel_login() == AUTH_STATUS_NOT_LOGGED_IN
    assert process.terminated is True
    assert manager.status() == AUTH_STATUS_NOT_LOGGED_IN


def test_coordinator_client_exposes_only_finite_status_and_uses_control_token() -> None:
    observed = {}

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return b'{"status":"ready","account":"must-not-cross-boundary"}'

    def opener(request, timeout):
        observed["authorization"] = request.headers.get("Authorization")
        observed["timeout"] = timeout
        return Response()

    client = CodexAuthCoordinatorClient(
        "http://host.docker.internal:1456",
        "coordinator-test-token",
        opener=opener,
    )
    assert client.status() == AUTH_STATUS_READY
    assert observed == {"authorization": "Bearer coordinator-test-token", "timeout": 3.0}
