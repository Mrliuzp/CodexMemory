---
name: codex-memory-auto-log
description: 仅当当前项目 AGENTS.md 明确声明 CODEX_MEMORY_AUTO_LOG=required 时，自动检索 Codex Memory 并归档用户与助手最终消息；也可在用户显式要求时调用。
---

# Codex Memory 自动归档

## 启用判断

1. 先解析当前项目参数，并读取当前项目的 `AGENTS.md`。
2. 仅当其中明确包含 `CODEX_MEMORY_AUTO_LOG=required` 时，允许自动检索或写入。
3. 未启用项目不得自动写入。用户显式要求检索或归档时，才可为未启用项目调用相应工具。

## 检索

1. 先调用 `health` 确认 Codex Memory 可用。
2. 需要历史上下文时，调用 `build_context` 并传入已解析的项目参数。
3. 只有在需要针对具体问题补充检索时，调用 `retrieve_memory`；不要用检索结果代替本轮事实。

## 归档

1. 生命周期 Hook 是首选写入方式。它可用时，不要由 Skill 重复归档。
2. Hook 无法写入时，Skill 才作为补录调用 `append_message`。为同一消息复用 Hook 使用的稳定 `event_key`，避免重复归档。
3. 仅归档用户消息和助手的最终可见消息。不得归档隐藏推理、工具调用参数、工具中间输出或其他内部过程。
4. 写入失败时，用简体中文报告失败的操作与原因；不要伪称已归档，也不要因此中断用户的主要请求。
