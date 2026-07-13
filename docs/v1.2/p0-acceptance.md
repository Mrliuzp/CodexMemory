# P0 验收清单

- [x] 管理 API 挂载在 `/api/admin/v1`，且不改变 `/api/v1/*` 的行为。
- [x] 每个 P0 只读路由都要求 Bearer 令牌；令牌缺失时返回 `WWW-Authenticate: Bearer` 挑战。
- [x] 项目和作用域检查返回结构化的 403 错误。
- [x] 列表响应包含稳定的分页元数据和请求 ID。
- [x] 排序字段使用允许列表，单页大小上限为 200。
- [x] 候选记忆和已接受记忆的内容在序列化前完成脱敏。
- [x] P0 路由不暴露发布、审核、重试、回放、导入或上传命令。
- [x] 作用域迁移保留历史项目级记录，并创建默认投影。
- [x] 管理后台使用 Vue/Vite/Element Plus 构建，提供概览、项目和只读数据视图。
- [x] 管理后台提供带用户名和密码的登录页，使用签名会话令牌并支持退出登录。
- [x] 管理后台提供带 SPA 回退和 `/api` 代理的生产 Nginx 容器。

验证命令：

```powershell
.\.venv\Scripts\python.exe -m pytest -q
cd apps\admin-web
npm run build
```