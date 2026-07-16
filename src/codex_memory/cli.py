from .entrypoints import cli as _implementation
from .entrypoints.cli import *


def main():
    # 保留旧模块级 monkeypatch/集成调用点的兼容性。
    _implementation.create_app = globals().get("create_app", _implementation.create_app)
    _implementation.create_mcp_server = globals().get("create_mcp_server", _implementation.create_mcp_server)
    return _implementation.main()

if __name__ == "__main__":
    main()
