# Codex 全局 MCP、Skill 与项目门禁 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 Codex Memory 作为用户级 HTTP MCP 和 Skill 安装，并确保只有声明有效 `AGENTS.md` 参数的项目自动启用。

**Architecture:** 仓库保存 Skill、Hook 模板和 PowerShell 安装器的权威副本。安装器创建用户级运行环境、增量注册 HTTP MCP、安装全局 Skill 并合并全局 Hook 配置；项目参数由共享解析器读取，服务端项目授权仍是最终安全边界。

**Tech Stack:** Python 3.10+、Codex CLI 0.142.5+、PowerShell 7/Windows PowerShell、TOML/JSON、MCP HTTP、pytest、Skill YAML。

## Global Constraints

- 全局 MCP 名称默认为 `codex-memory`，但项目可以通过 `CODEX_MEMORY_MCP_SERVER` 引用其他已注册名称。
- 项目启用参数名固定，参数值由项目配置。
- 参数缺失、格式错误、项目未注册或权限不足时采用失败关闭，不写入默认项目。
- 安装器只能增量修改用户配置，不能覆盖现有 MCP、Skill 或 Hook。
- 用户级 Token 通过环境变量读取，不能作为命令行参数、配置文本或测试快照出现。
- 全局 Skill 和安装器的用户可见内容使用简体中文。

---

## File Structure

| 文件 | 职责 |
| --- | --- |
| `src/codex_memory/project_config.py` | 查找并解析生效的 `AGENTS.md` 参数 |
| `src/codex_memory/doctor.py` | 验证 Codex MCP 注册、Skill 和当前项目门禁 |
| `skills/codex-memory-auto-log/SKILL.md` | 可版本化的全局 Skill 权威源 |
| `skills/codex-memory-auto-log/agents/openai.yaml` | Skill 的中文 UI 元数据和隐式调用策略 |
| `codex/hooks.global.json` | 安装器合并到用户目录的 Hook 模板 |
| `scripts/install-codex-memory.ps1` | 创建运行环境、注册 MCP、安装 Skill 和 Hook |
| `scripts/uninstall-codex-memory.ps1` | 只移除本项目安装的全局组件 |
| `scripts/set-codex-memory-token.ps1` | 交互式设置用户级 Token |
| `tests/test_project_config.py` | 参数解析、层级和禁用契约 |
| `tests/test_codex_install_contract.py` | 安装器、Skill 和 Hook 静态契约 |
| `AGENTS.md` | 当前项目的正式激活声明 |

### Task 1: `AGENTS.md` 项目参数解析器

**Files:**
- Create: `src/codex_memory/project_config.py`
- Create: `tests/test_project_config.py`

**Interfaces:**
- Consumes: 当前工作目录。
- Produces: `ProjectMemoryConfig`、`find_agents_file(cwd) -> Path | None`、`load_project_memory_config(cwd) -> ProjectMemoryConfig`。

- [ ] **Step 1: 写参数解析失败测试**

```python
def test_loads_enabled_project_from_nearest_agents_file(tmp_path: Path) -> None:
    project = tmp_path / "erp"
    nested = project / "src" / "orders"
    nested.mkdir(parents=True)
    (project / "AGENTS.md").write_text(
        """# 约束\nCODEX_MEMORY_AUTO_LOG=required\nCODEX_MEMORY_PROJECT_ID=erp-backend\nCODEX_MEMORY_MCP_SERVER=codex-memory\n""",
        encoding="utf-8",
    )

    config = load_project_memory_config(nested)

    assert config.enabled is True
    assert config.project_id == "erp-backend"
    assert config.mcp_server == "codex-memory"
    assert config.agents_file == project / "AGENTS.md"


def test_missing_marker_is_disabled(tmp_path: Path) -> None:
    (tmp_path / "AGENTS.md").write_text("# 普通约束\n", encoding="utf-8")
    assert load_project_memory_config(tmp_path).enabled is False


def test_required_rejects_invalid_project_id(tmp_path: Path) -> None:
    (tmp_path / "AGENTS.md").write_text(
        "CODEX_MEMORY_AUTO_LOG=required\nCODEX_MEMORY_PROJECT_ID=ERP 中文\nCODEX_MEMORY_MCP_SERVER=codex-memory\n",
        encoding="utf-8",
    )
    with pytest.raises(ProjectConfigError, match="PROJECT_ID"):
        load_project_memory_config(tmp_path)
```

- [ ] **Step 2: 运行测试并确认失败**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_project_config.py -q`

Expected: FAIL，模块不存在。

- [ ] **Step 3: 实现严格解析器**

```python
PROJECT_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
SETTING_PATTERN = re.compile(r"^(CODEX_MEMORY_[A-Z_]+)=(.*)$")


@dataclass(frozen=True)
class ProjectMemoryConfig:
    enabled: bool
    project_id: str | None
    mcp_server: str | None
    agents_file: Path | None


def load_project_memory_config(cwd: str | Path) -> ProjectMemoryConfig:
    agents_file = find_agents_file(Path(cwd).resolve())
    if agents_file is None:
        return ProjectMemoryConfig(False, None, None, None)
    values = _parse_settings(agents_file.read_text(encoding="utf-8-sig"))
    mode = values.get("CODEX_MEMORY_AUTO_LOG", "disabled")
    if mode != "required":
        return ProjectMemoryConfig(False, None, None, agents_file)
    project_id = values.get("CODEX_MEMORY_PROJECT_ID", "")
    mcp_server = values.get("CODEX_MEMORY_MCP_SERVER", "")
    if not PROJECT_ID_PATTERN.fullmatch(project_id):
        raise ProjectConfigError("CODEX_MEMORY_PROJECT_ID 格式无效")
    if not PROJECT_ID_PATTERN.fullmatch(mcp_server):
        raise ProjectConfigError("CODEX_MEMORY_MCP_SERVER 格式无效")
    return ProjectMemoryConfig(True, project_id, mcp_server, agents_file)
```

`find_agents_file` 从 `cwd` 向父目录查找，遇到文件系统根目录停止。重复参数且值不一致时抛出 `ProjectConfigError`。

- [ ] **Step 4: 补齐层级、BOM、禁用和重复参数测试**

增加测试覆盖：最近一层 `AGENTS.md` 优先、UTF-8 BOM、`disabled`、未知值、重复相同值、重复冲突值和 65 字符项目 ID。

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_project_config.py -q`

Expected: PASS。

- [ ] **Step 5: 提交项目门禁解析器**

```powershell
git add src/codex_memory/project_config.py tests/test_project_config.py
git commit -m "feat: parse Codex Memory project activation"
```

### Task 2: 建立可版本化的全局 Skill

**Files:**
- Create: `skills/codex-memory-auto-log/SKILL.md`
- Create: `skills/codex-memory-auto-log/agents/openai.yaml`
- Create: `tests/test_codex_install_contract.py`

**Interfaces:**
- Consumes: MCP 工具 `health`、`append_message`、`retrieve_memory`、`build_context` 和项目参数。
- Produces: 通过 Skill 校验器的全局 Skill 包。

- [ ] **Step 1: 写 Skill 静态契约测试**

```python
def test_skill_requires_agents_activation_marker() -> None:
    content = Path("skills/codex-memory-auto-log/SKILL.md").read_text(encoding="utf-8")
    assert "CODEX_MEMORY_AUTO_LOG=required" in content
    assert "未启用项目不得自动写入" in content
    assert "append_message" in content
    assert "build_context" in content


def test_skill_allows_implicit_invocation() -> None:
    metadata = Path("skills/codex-memory-auto-log/agents/openai.yaml").read_text(encoding="utf-8")
    assert "allow_implicit_invocation: true" in metadata
```

- [ ] **Step 2: 运行测试并确认失败**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_codex_install_contract.py -q`

Expected: FAIL，Skill 文件不存在。

- [ ] **Step 3: 编写 Skill**

`SKILL.md` frontmatter 只包含：

```yaml
---
name: codex-memory-auto-log
description: 仅当当前项目 AGENTS.md 明确声明 CODEX_MEMORY_AUTO_LOG=required 时，自动检索 Codex Memory 并归档用户与助手最终消息；也可在用户显式要求时调用。
---
```

正文必须规定：先解析项目参数；需要历史时调用 `build_context`；生命周期 Hook 是首选写入方；Skill 补录复用同一 `event_key`；未启用项目不写入；失败时中文报告；不归档隐藏推理和工具中间输出。

`agents/openai.yaml` 使用：

```yaml
interface:
  display_name: "Codex Memory 自动归档"
  short_description: "按项目约束检索并归档本地知识"
policy:
  allow_implicit_invocation: true
```

- [ ] **Step 4: 验证 Skill**

Run: `python C:\Users\lzp59\.codex\skills\.system\skill-creator\scripts\quick_validate.py skills\codex-memory-auto-log`

Expected: 校验成功且退出码为 0。

- [ ] **Step 5: 提交 Skill 权威源**

```powershell
git add skills/codex-memory-auto-log tests/test_codex_install_contract.py
git commit -m "feat: package project-gated memory skill"
```

### Task 3: 全局 Hook 模板与用户级运行环境

**Files:**
- Create: `codex/hooks.global.json`
- Create: `scripts/install-codex-memory.ps1`
- Create: `scripts/uninstall-codex-memory.ps1`
- Create: `scripts/set-codex-memory-token.ps1`
- Modify: `tests/test_codex_install_contract.py`

**Interfaces:**
- Consumes: Codex CLI 0.142.5+、仓库 Python 包和用户目录 `%USERPROFILE%\.codex`。
- Produces: 用户级 `.codex-memory-runtime`、全局 Hook 合并项和安全的 Token 设置流程。

- [ ] **Step 1: 写安装器契约测试**

```python
def test_installer_registers_http_mcp_without_embedding_token() -> None:
    content = Path("scripts/install-codex-memory.ps1").read_text(encoding="utf-8")
    assert "mcp add" in content
    assert "--url" in content
    assert "http://127.0.0.1:8001/mcp" in content
    assert "--bearer-token-env-var" in content
    assert "CODEX_MEMORY_MCP_TOKEN" in content
    assert "Set-Content $CodexConfig" not in content


def test_global_hook_calls_installed_runtime() -> None:
    hooks = json.loads(Path("codex/hooks.global.json").read_text(encoding="utf-8"))
    assert "UserPromptSubmit" in hooks["hooks"]
    assert "Stop" in hooks["hooks"]
```

- [ ] **Step 2: 运行测试并确认失败**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_codex_install_contract.py -q`

Expected: FAIL，安装脚本和 Hook 模板不存在。

- [ ] **Step 3: 创建全局 Hook 模板**

```json
{
  "hooks": {
    "UserPromptSubmit": [{
      "hooks": [{
        "type": "command",
        "commandWindows": "\"%USERPROFILE%\\.codex\\codex-memory-runtime\\Scripts\\codex-memory.exe\" hook-user",
        "timeout": 5
      }]
    }],
    "Stop": [{
      "hooks": [{
        "type": "command",
        "commandWindows": "\"%USERPROFILE%\\.codex\\codex-memory-runtime\\Scripts\\codex-memory.exe\" hook-assistant",
        "timeout": 5
      }]
    }]
  }
}
```

- [ ] **Step 4: 实现增量安装器**

安装器必须依次：

```powershell
$CodexHome = Join-Path $HOME ".codex"
$Runtime = Join-Path $CodexHome "codex-memory-runtime"
py -3 -m venv $Runtime
& (Join-Path $Runtime "Scripts\python.exe") -m pip install --upgrade $PSScriptRoot\..
& $CodexCli mcp remove codex-memory 2>$null
& $CodexCli mcp add codex-memory --url "http://127.0.0.1:8001/mcp" --bearer-token-env-var CODEX_MEMORY_MCP_TOKEN
Copy-Item "$PSScriptRoot\..\skills\codex-memory-auto-log" "$CodexHome\skills\codex-memory-auto-log" -Recurse -Force
```

Hook JSON 使用 `ConvertFrom-Json` 和对象合并，只替换带有 `codex-memory-runtime` 的本项目命令，保留其他 Hook。修改前创建带时间戳备份。

- [ ] **Step 5: 实现交互式 Token 设置**

```powershell
$Secure = Read-Host "请输入 CODEX_MEMORY_MCP_TOKEN" -AsSecureString
$Pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($Secure)
try {
    $Plain = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($Pointer)
    if ([string]::IsNullOrWhiteSpace($Plain) -or $Plain.StartsWith("change-me")) { throw "Token 不能为空或使用占位符" }
    [Environment]::SetEnvironmentVariable("CODEX_MEMORY_MCP_TOKEN", $Plain, "User")
} finally {
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($Pointer)
    Remove-Variable Plain -ErrorAction SilentlyContinue
}
```

- [ ] **Step 6: 实现精确卸载器并运行契约测试**

卸载器只删除 `codex-memory` MCP、安装的 Skill、运行环境和带 `codex-memory-runtime` 的 Hook 项；不删除 Token，除非用户传入 `-RemoveToken`。

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_codex_install_contract.py -q`

Expected: PASS。

- [ ] **Step 7: 提交安装与卸载脚本**

```powershell
git add codex/hooks.global.json scripts/install-codex-memory.ps1 scripts/uninstall-codex-memory.ps1 scripts/set-codex-memory-token.ps1 tests/test_codex_install_contract.py
git commit -m "feat: install Codex Memory integration globally"
```

### Task 4: 当前项目激活声明与诊断

**Files:**
- Modify: `AGENTS.md`
- Create: `src/codex_memory/doctor.py`
- Modify: `src/codex_memory/cli.py`
- Create: `tests/test_doctor.py`
- Create: `docs/v1.2/codex-global-integration.md`

**Interfaces:**
- Consumes: `load_project_memory_config()`、Codex CLI、MCP URL 和环境变量。
- Produces: `codex-memory doctor --cwd <path> --json` 和当前项目激活参数。

- [ ] **Step 1: 写 doctor 失败测试**

```python
def test_doctor_reports_disabled_project(tmp_path: Path) -> None:
    report = run_doctor(tmp_path, env={}, mcp_probe=lambda: {"status": "ok"})
    assert report["project_config"] == "disabled"
    assert report["overall"] == "warning"


def test_doctor_reports_enabled_project(tmp_path: Path) -> None:
    (tmp_path / "AGENTS.md").write_text(
        "CODEX_MEMORY_AUTO_LOG=required\nCODEX_MEMORY_PROJECT_ID=erp\nCODEX_MEMORY_MCP_SERVER=codex-memory\n",
        encoding="utf-8",
    )
    report = run_doctor(tmp_path, env={"CODEX_MEMORY_MCP_TOKEN": "secret"}, mcp_probe=lambda: {"status": "ok"})
    assert report["project_id"] == "erp"
    assert report["overall"] == "ok"
```

- [ ] **Step 2: 运行测试并确认失败**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_doctor.py -q`

Expected: FAIL，`doctor` 模块不存在。

- [ ] **Step 3: 实现 doctor 与 CLI 命令**

`DoctorReport` 至少返回 `codex_cli`、`mcp_registration`、`mcp_health`、`skill`、`project_config`、`project_id`、`token_env`、`overall` 和中文 `messages`。CLI 使用：

```powershell
codex-memory doctor --cwd "G:\Codex Project\20260703-codex-memory-system" --json
```

退出码：全绿为 0；可恢复警告为 1；配置错误或服务不可用为 2。

- [ ] **Step 4: 更新当前项目 `AGENTS.md`**

在现有中文约束顶部加入：

```text
CODEX_MEMORY_AUTO_LOG=required
CODEX_MEMORY_PROJECT_ID=20260703-codex-memory-system
CODEX_MEMORY_MCP_SERVER=codex-memory
```

保留原有中文化约束，并修复文件中已有乱码；使用 UTF-8 写回。

- [ ] **Step 5: 编写接入文档并运行测试**

文档包含：安装、Token 设置、启动 Compose、项目参数示例、停用、项目改名规则、doctor 退出码和卸载。

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_project_config.py tests/test_doctor.py tests/test_codex_install_contract.py -q`

Expected: PASS。

- [ ] **Step 6: 提交项目激活和诊断**

```powershell
git add AGENTS.md src/codex_memory/doctor.py src/codex_memory/cli.py tests/test_doctor.py docs/v1.2/codex-global-integration.md
git commit -m "feat: activate and diagnose project memory integration"
```

### Task 5: 安装与隔离验收

**Files:**
- Modify: `docs/v1.2/codex-global-integration.md`

**Interfaces:**
- Consumes: 本计划全部提交和第一部分运行服务。
- Produces: 全局接入验收记录。

- [ ] **Step 1: 安装用户级组件**

Run: `powershell -ExecutionPolicy Bypass -File scripts\set-codex-memory-token.ps1`

Expected: 用户级 `CODEX_MEMORY_MCP_TOKEN` 已设置，控制台不回显 Token。

Run: `powershell -ExecutionPolicy Bypass -File scripts\install-codex-memory.ps1`

Expected: MCP、Skill、运行环境和 Hook 安装成功。

- [ ] **Step 2: 验证全局 MCP**

Run: `& 'C:\Users\lzp59\AppData\Roaming\npm\codex.cmd' mcp get codex-memory`

Expected: URL 为 `http://127.0.0.1:8001/mcp`，Bearer Token 环境变量名为 `CODEX_MEMORY_MCP_TOKEN`。

- [ ] **Step 3: 验证启用和未启用项目**

在当前仓库运行 doctor，预期 `overall=ok`。创建临时目录且不放置 `AGENTS.md`，运行 doctor，预期 `project_config=disabled` 且不会调用 append。

- [ ] **Step 4: 重启 Codex 并执行 MCP health**

启动新的 Codex 任务，要求调用 `codex-memory` 的 `health`。预期返回 PostgreSQL、schema 和 vector 均可用。

- [ ] **Step 5: 记录验收并提交**

```powershell
git add docs/v1.2/codex-global-integration.md
git commit -m "docs: record Codex global integration acceptance"
```
