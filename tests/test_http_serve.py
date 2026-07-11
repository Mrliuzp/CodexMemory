from __future__ import annotations

from codex_memory.cli import main


def test_cli_serve_invokes_uvicorn(monkeypatch):
    captured = {}

    def fake_run(app, host, port, reload, **kwargs):
        captured["app"] = app
        captured["host"] = host
        captured["port"] = port
        captured["reload"] = reload
        captured["kwargs"] = kwargs

    monkeypatch.setattr("uvicorn.run", fake_run)
    monkeypatch.setattr("sys.argv", ["codex-memory", "--db", "memory.db", "serve", "--host", "0.0.0.0", "--port", "8080"])

    main()

    assert captured["host"] == "0.0.0.0"
    assert captured["port"] == 8080
    assert captured["reload"] is False
    assert captured["app"].__class__.__name__ == "FastAPI"
    assert captured["kwargs"]["log_level"] == "info"
