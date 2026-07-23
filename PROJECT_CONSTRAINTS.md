# 项目语言与工程约束

## 语言规范

- 项目默认语言为简体中文。
- 新增或修改的前端标题、导航、按钮、表格列、表单标签、提示、错误信息、登录页和空状态必须使用中文。
- 新增或修改的代码注释必须使用中文；注释中的技术标识可以保留原文。
- Markdown 标题、正文、表格、示例说明、验收记录和交接内容默认使用中文。
- `Codex Memory`、FastAPI、PostgreSQL、pgvector、SQLAlchemy、Vue、Vite、Element Plus、Pinia、MCP、RAG、API、HTTP、Bearer、Outbox、Worker、Scope、Profile、L0/L1/L2/L3 等技术名称、协议名称、枚举值和品牌名称保持原样。
- API 路径、数据库表名、字段名、类名、函数名、环境变量、命令、状态值、JSON 键和日志原文不得为了中文化而改名。

## 编码与文件范围

- 源码、文档和配置统一使用 UTF-8；发现中文乱码时必须按 UTF-8 读取和写回。
- 不修改第三方依赖、`node_modules`、`.venv`、缓存目录、构建产物和外部生成文件中的语言内容。
- 不因中文化任务重排无关代码、改动业务契约或翻译可执行技术标识。
- 前端自然语言应集中在中文文案、配置或常量中，禁止新增直接面向用户的英文自然语言。

## 提交前检查

- 使用 `rg` 检查英文自然语言、乱码和意外残留的旧文案。
- 运行前端测试和构建：`cd apps/admin-web` 后执行 `npm test`、`npm run build`。
- 运行后端测试：`.\.venv\Scripts\python.exe -m pytest -q`。
- 运行 `git diff --check`，确认无空白错误；只提交本次任务涉及的文件。
- 每次提交使用清晰的中文说明或保留项目既有的 Conventional Commit 格式，并通过 Git 版本记录变更。