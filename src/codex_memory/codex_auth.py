"""Worker 专用 Codex 认证目录协调与 API 客户端。

API 只通过状态接口与本机协调器通信，不读取认证文件；Worker 只读挂载
协调器维护的认证根目录，并在每次 CLI 调用前解析原子替换的 generation 指针。
"""

from __future__ import annotations

import hmac
import argparse
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import uuid
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen


AUTH_STATUS_READY = "ready"
AUTH_STATUS_NOT_LOGGED_IN = "not_logged_in"
AUTH_STATUS_LOGIN_IN_PROGRESS = "login_in_progress"
AUTH_STATUS_ERROR = "error"
AUTH_STATUS_VALUES = frozenset(
    {
        AUTH_STATUS_READY,
        AUTH_STATUS_NOT_LOGGED_IN,
        AUTH_STATUS_LOGIN_IN_PROGRESS,
        AUTH_STATUS_ERROR,
    }
)

AUTH_COORDINATOR_URL_ENV = "CODEX_MEMORY_CODEX_AUTH_COORDINATOR_URL"
AUTH_COORDINATOR_TOKEN_ENV = "CODEX_MEMORY_CODEX_AUTH_COORDINATOR_TOKEN"
AUTH_ROOT_ENV = "CODEX_MEMORY_CODEX_CLI_AUTH_ROOT"
ACTIVE_GENERATION_FILE = ".active-generation"
ROOT_GENERATION = "root"
DISABLED_GENERATION = "disabled"
GENERATION_PATTERN = re.compile(r"generation-[A-Za-z0-9_-]{1,96}\Z")


class CodexAuthCoordinatorError(RuntimeError):
    """协调器不可用或返回了不安全的状态。"""


def normalize_auth_status(value: Any) -> str:
    return value if isinstance(value, str) and value in AUTH_STATUS_VALUES else AUTH_STATUS_ERROR


class CodexAuthCoordinatorClient:
    """API 容器使用的最小状态客户端；永远只解析 status 字段。"""

    def __init__(
        self,
        base_url: str | None = None,
        token: str | None = None,
        *,
        timeout_seconds: float = 3.0,
        opener: Callable[..., Any] = urlopen,
    ) -> None:
        self.base_url = (base_url or "").strip().rstrip("/")
        self.token = (token or "").strip()
        self.timeout_seconds = timeout_seconds
        self.opener = opener

    @classmethod
    def from_env(cls) -> "CodexAuthCoordinatorClient":
        return cls(os.environ.get(AUTH_COORDINATOR_URL_ENV), os.environ.get(AUTH_COORDINATOR_TOKEN_ENV))

    def _request(self, method: str, path: str) -> str:
        if not self.base_url:
            raise CodexAuthCoordinatorError("认证协调器未配置")
        parsed = urlsplit(self.base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise CodexAuthCoordinatorError("认证协调器地址无效")
        headers = {"Accept": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        body = b"{}" if method == "POST" else None
        if body is not None:
            headers["Content-Type"] = "application/json"
        request = Request(f"{self.base_url}{path}", data=body, headers=headers, method=method)
        try:
            with self.opener(request, timeout=self.timeout_seconds) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (HTTPError, URLError, TimeoutError, OSError, ValueError, UnicodeError) as error:
            raise CodexAuthCoordinatorError("认证协调器不可用") from error
        return normalize_auth_status(payload.get("status") if isinstance(payload, dict) else None)

    def status(self) -> str:
        try:
            return self._request("GET", "/status")
        except CodexAuthCoordinatorError:
            return AUTH_STATUS_ERROR

    def start_login(self) -> str:
        return self._request("POST", "/login/start")

    def cancel_login(self) -> str:
        return self._request("POST", "/login/cancel")

    def recheck(self) -> str:
        return self._request("POST", "/login/recheck")


@dataclass(frozen=True, slots=True)
class _LoginProcessState:
    process: Any
    generation: str
    temp_directory: tempfile.TemporaryDirectory[str]


class CodexAuthGenerationManager:
    """协调登录 generation 与 Worker 可见的 active 指针。"""

    def __init__(
        self,
        auth_root: str | Path,
        *,
        cli_path: str = "codex",
        process_factory: Callable[..., Any] | None = None,
        status_probe: Callable[[Path], bool] | None = None,
    ) -> None:
        self.auth_root = Path(auth_root).expanduser().resolve()
        self.auth_root.mkdir(parents=True, exist_ok=True)
        self.cli_path = cli_path
        self.process_factory = process_factory or subprocess.Popen
        self.status_probe = status_probe
        self._lock = threading.RLock()
        self._login: _LoginProcessState | None = None
        self._status = AUTH_STATUS_NOT_LOGGED_IN

    @property
    def pointer_path(self) -> Path:
        return self.auth_root / ACTIVE_GENERATION_FILE

    def _read_active(self) -> str | None:
        try:
            value = self.pointer_path.read_text(encoding="ascii").strip()
        except FileNotFoundError:
            return None
        except (OSError, UnicodeError) as error:
            raise CodexAuthCoordinatorError("认证 generation 指针不可读") from error
        if value == ROOT_GENERATION or value == DISABLED_GENERATION or GENERATION_PATTERN.fullmatch(value):
            return value
        raise CodexAuthCoordinatorError("认证 generation 指针无效")

    def _write_active(self, generation: str) -> None:
        if generation not in {ROOT_GENERATION, DISABLED_GENERATION} and not GENERATION_PATTERN.fullmatch(generation):
            raise ValueError("认证 generation 无效")
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=".active-generation-",
            dir=self.auth_root,
            text=True,
        )
        try:
            with os.fdopen(descriptor, "w", encoding="ascii") as stream:
                stream.write(generation + "\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary_name, self.pointer_path)
        finally:
            try:
                os.unlink(temporary_name)
            except FileNotFoundError:
                pass

    def _generation_path(self, generation: str) -> Path:
        if generation == ROOT_GENERATION:
            return self.auth_root
        if not GENERATION_PATTERN.fullmatch(generation):
            raise CodexAuthCoordinatorError("认证 generation 无效")
        path = (self.auth_root / generation).resolve(strict=True)
        root = self.auth_root.resolve(strict=True)
        if path != root and root not in path.parents:
            raise CodexAuthCoordinatorError("认证 generation 越界")
        if not path.is_dir():
            raise CodexAuthCoordinatorError("认证 generation 不可用")
        return path

    @staticmethod
    def _safe_environment(home: Path, sqlite_home: Path) -> dict[str, str]:
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
            "HTTP_PROXY",
            "HTTPS_PROXY",
            "ALL_PROXY",
            "NO_PROXY",
            "SSL_CERT_FILE",
            "CODEX_CA_CERTIFICATE",
        }
        environment = {key: value for key, value in os.environ.items() if key in allowed}
        # Windows preserves the environment key spelling supplied by the host;
        # Python commonly exposes PATH as ``Path`` there.
        if "PATH" not in environment and os.environ.get("Path"):
            environment["PATH"] = os.environ["Path"]
        environment.update(
            {
                "CODEX_HOME": str(home),
                "CODEX_SQLITE_HOME": str(sqlite_home),
                "NO_COLOR": "1",
            }
        )
        return environment

    def _probe(self, home: Path) -> bool:
        if self.status_probe is not None:
            return bool(self.status_probe(home))
        with tempfile.TemporaryDirectory(prefix="codex-memory-auth-status-") as temporary:
            sqlite_home = Path(temporary) / "sqlite"
            sqlite_home.mkdir()
            try:
                completed = subprocess.run(
                    self._cli_argv("login", "status"),
                    cwd=str(home),
                    env=self._safe_environment(home, sqlite_home),
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    shell=False,
                    check=False,
                    timeout=10,
                )
            except (OSError, subprocess.TimeoutExpired):
                raise CodexAuthCoordinatorError("Codex CLI 状态检查失败") from None
        return completed.returncode == 0

    def _cli_argv(self, *arguments: str) -> list[str]:
        """在 Windows 上通过固定参数调用 npm 的 PowerShell shim。"""

        if os.name == "nt":
            resolved = shutil.which(self.cli_path)
            if resolved and resolved.lower().endswith(".cmd"):
                powershell_shim = str(Path(resolved).with_suffix(".ps1"))
                if Path(powershell_shim).is_file():
                    return [
                        "powershell.exe",
                        "-NoProfile",
                        "-NonInteractive",
                        "-ExecutionPolicy",
                        "Bypass",
                        "-File",
                        powershell_shim,
                        *arguments,
                    ]
        return [self.cli_path, *arguments]

    def _cleanup_login_temp(self, login: _LoginProcessState | None) -> None:
        if login is None:
            return
        login.temp_directory.cleanup()

    def _poll_locked(self) -> None:
        login = self._login
        if login is None:
            return
        returncode = login.process.poll()
        if returncode is None:
            self._status = AUTH_STATUS_LOGIN_IN_PROGRESS
            return
        self._login = None
        self._cleanup_login_temp(login)
        if returncode == 0:
            self._write_active(login.generation)
            self._status = AUTH_STATUS_READY
        else:
            self._write_active(DISABLED_GENERATION)
            self._status = AUTH_STATUS_ERROR

    def status(self) -> str:
        with self._lock:
            self._poll_locked()
            if self._login is not None:
                return AUTH_STATUS_LOGIN_IN_PROGRESS
            try:
                active = self._read_active()
            except CodexAuthCoordinatorError:
                self._status = AUTH_STATUS_ERROR
                return self._status
            if active == DISABLED_GENERATION:
                self._status = AUTH_STATUS_NOT_LOGGED_IN
                return self._status
            if active is None:
                candidate = self.auth_root
                generation = ROOT_GENERATION
            else:
                try:
                    candidate = self._generation_path(active)
                except CodexAuthCoordinatorError:
                    self._status = AUTH_STATUS_ERROR
                    return self._status
                generation = active
            try:
                ready = self._probe(candidate)
            except CodexAuthCoordinatorError:
                self._status = AUTH_STATUS_ERROR
                return self._status
            if ready:
                if active is None:
                    self._write_active(generation)
                self._status = AUTH_STATUS_READY
            else:
                self._status = AUTH_STATUS_NOT_LOGGED_IN
            return self._status

    def start_login(self) -> str:
        with self._lock:
            self._poll_locked()
            if self._login is not None:
                return AUTH_STATUS_LOGIN_IN_PROGRESS
            generation = f"generation-{uuid.uuid4().hex}"
            home = self.auth_root / generation
            home.mkdir()
            # Disable the old account before launching a replacement login.
            self._write_active(DISABLED_GENERATION)
            temporary = tempfile.TemporaryDirectory(prefix="codex-memory-auth-login-")
            sqlite_home = Path(temporary.name) / "sqlite"
            sqlite_home.mkdir()
            try:
                process = self.process_factory(
                    self._cli_argv("login"),
                    cwd=str(home),
                    env=self._safe_environment(home, sqlite_home),
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    shell=False,
                )
            except OSError:
                temporary.cleanup()
                self._write_active(DISABLED_GENERATION)
                self._status = AUTH_STATUS_ERROR
                return self._status
            self._login = _LoginProcessState(process, generation, temporary)
            self._status = AUTH_STATUS_LOGIN_IN_PROGRESS
            return self._status

    def cancel_login(self) -> str:
        with self._lock:
            login = self._login
            if login is not None and login.process.poll() is None:
                try:
                    login.process.terminate()
                    login.process.wait(timeout=2)
                except (OSError, subprocess.TimeoutExpired):
                    try:
                        login.process.kill()
                    except OSError:
                        pass
                self._login = None
                self._cleanup_login_temp(login)
            self._write_active(DISABLED_GENERATION)
            self._status = AUTH_STATUS_NOT_LOGGED_IN
            return self._status

    def recheck(self) -> str:
        return self.status()


class _CoordinatorHandler(BaseHTTPRequestHandler):
    server: "_CoordinatorServer"

    def log_message(self, _format: str, *_args: object) -> None:
        # Never write request paths, authorization headers, or CLI output to logs.
        return

    def _authorized(self) -> bool:
        expected = self.server.coordinator_token
        provided = self.headers.get("Authorization", "")
        return bool(expected) and hmac.compare_digest(provided, f"Bearer {expected}")

    def _send_status(self, status_code: int, value: str) -> None:
        body = json.dumps({"status": normalize_auth_status(value)}, separators=(",", ":")).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        if not self._authorized():
            self._send_status(401, AUTH_STATUS_ERROR)
            return
        if urlsplit(self.path).path != "/status":
            self._send_status(404, AUTH_STATUS_ERROR)
            return
        self._send_status(200, self.server.manager.status())

    def do_POST(self) -> None:  # noqa: N802
        if not self._authorized():
            self._send_status(401, AUTH_STATUS_ERROR)
            return
        path = urlsplit(self.path).path
        if path == "/login/start":
            value = self.server.manager.start_login()
        elif path == "/login/cancel":
            value = self.server.manager.cancel_login()
        elif path == "/login/recheck":
            value = self.server.manager.recheck()
        else:
            self._send_status(404, AUTH_STATUS_ERROR)
            return
        self._send_status(200, value)


class _CoordinatorServer(ThreadingHTTPServer):
    def __init__(self, address: tuple[str, int], manager: CodexAuthGenerationManager, coordinator_token: str) -> None:
        super().__init__(address, _CoordinatorHandler)
        self.manager = manager
        self.coordinator_token = coordinator_token


def run_coordinator_server(
    *,
    auth_root: str | Path,
    coordinator_token: str,
    cli_path: str = "codex",
    bind: str = "127.0.0.1",
    port: int = 1456,
) -> None:
    if not coordinator_token.strip():
        raise ValueError("认证协调器 token 不能为空")
    manager = CodexAuthGenerationManager(auth_root, cli_path=cli_path)
    server = _CoordinatorServer((bind, port), manager, coordinator_token.strip())
    try:
        server.serve_forever()
    finally:
        server.server_close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Codex Memory Worker 本机认证协调器")
    parser.add_argument("--auth-root", required=True, help="仓库外的 Worker 专用认证根目录")
    parser.add_argument("--cli-path", default="codex", help="codex 可执行文件路径")
    parser.add_argument("--bind", default="127.0.0.1", help="协调器监听地址")
    parser.add_argument("--port", type=int, default=1456, help="协调器监听端口")
    parser.add_argument(
        "--token-env",
        default=AUTH_COORDINATOR_TOKEN_ENV,
        help="读取协调器控制 token 的环境变量名",
    )
    args = parser.parse_args(argv)
    token = os.environ.get(args.token_env, "")
    run_coordinator_server(
        auth_root=args.auth_root,
        coordinator_token=token,
        cli_path=args.cli_path,
        bind=args.bind,
        port=args.port,
    )
    return 0


__all__ = [
    "ACTIVE_GENERATION_FILE",
    "AUTH_COORDINATOR_TOKEN_ENV",
    "AUTH_COORDINATOR_URL_ENV",
    "AUTH_ROOT_ENV",
    "AUTH_STATUS_ERROR",
    "AUTH_STATUS_LOGIN_IN_PROGRESS",
    "AUTH_STATUS_NOT_LOGGED_IN",
    "AUTH_STATUS_READY",
    "AUTH_STATUS_VALUES",
    "CodexAuthCoordinatorClient",
    "CodexAuthCoordinatorError",
    "CodexAuthGenerationManager",
    "DISABLED_GENERATION",
    "ROOT_GENERATION",
    "main",
    "run_coordinator_server",
]


if __name__ == "__main__":  # pragma: no cover - 由本机协调器进程调用
    raise SystemExit(main())
