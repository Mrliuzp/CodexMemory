# 项目目录结构

当前代码按职责分为以下模块：

```text
src/codex_memory/
├── domain/          核心记忆领域：模型、存储、检索、分类、反思和运行时
├── persistence/     数据库连接、SQLAlchemy 模型、配置和幂等基础设施
├── api/             HTTP API、MCP、认证、Bootstrap 和版本化接口
├── pipelines/       V1.1/V1.3 异步 Worker、候选策略、Embedding、导入流水线
├── entrypoints/     CLI、Worker、Codex Hook 等进程入口
├── admin/           管理后台路由和管理查询
└── *.py             兼容转发层
```

顶层 `codex_memory/*.py` 中保留的短文件不是第二份实现，而是兼容转发。例如：

```python
from codex_memory.pipelines.v131_import import KnowledgeImportService
```

旧代码仍可以继续使用：

```python
from codex_memory.v131_import import KnowledgeImportService
```

这样可以先整理内部结构，再逐步迁移外部调用方，避免 API、CLI、Worker、Hook 和测试同时发生破坏性变更。

## 模块边界

- `domain` 不依赖 FastAPI、CLI 或具体部署入口。
- `persistence` 负责数据库和基础配置，不承载业务流程编排。
- `api` 负责协议、认证和请求响应，不直接实现 Worker 业务。
- `pipelines` 负责异步任务执行、导入和派生候选，不绕过审核直接发布 Memory。
- `entrypoints` 只负责启动进程和参数解析。
- Alembic 迁移继续集中在 `alembic/versions/`，前端继续集中在 `apps/admin-web/`。
